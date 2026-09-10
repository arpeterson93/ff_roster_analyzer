"""Builds one training table, one row per historical FAAB bid, by joining
tools/faab_history/o-league-bids.json against nflverse data (via nflreadpy)
and this league's own scoring rules.

Run after pull_o_league_bids.py (and its manual corrections) have produced
o-league-bids.json. Does NOT need ESPN credentials - everything here is
either already in that file or comes from nflreadpy, which is public.

For each bid, adds:
  - position                     from the ESPN<->nflverse id crosswalk
  - num_competing_bidders        distinct teams that placed a WAIVER bid on
                                  this same player in this same week (from
                                  our own bid data, not nflverse)
  - prior_week_actual_points     the player's real fantasy points (in THIS
                                  league's own scoring) the week before the
                                  bid, from nflreadpy's weekly player stats
  - prior_week_had_stat_row      whether he even had a stat line that week
                                  (false = bye/inactive/practice squad,
                                  distinct from "played and scored 0")
  - team_that_week                his NFL team as of the bid week (from the
                                  stat row itself, not today's roster)
  - own_injury_status             his own injury report status that week,
                                  if any
  - teammate_position_injury_flag whether another FANTASY-RELEVANT player
                                  (recent offense snap share above
                                  FANTASY_RELEVANT_SNAP_PCT) at his own
                                  team+position had an Out/Doubtful report
                                  that same week (an "opportunity opened up"
                                  signal, distinct from his own status) -
                                  deep bench/camp bodies on the injury
                                  report don't count, they're not why anyone
                                  bid on this player
  - snap_pct_prior_week / snap_pct_two_weeks_prior
                                  his offense snap share trend going into
                                  the bid, from nflverse snap counts (joined
                                  by pfr_id, not name string - see README)
  - pct_of_bidder_season_spend    bid_amount_dollars / that bidding team's
                                  total effective spend that season
  - pct_of_league_season_spend    bid_amount_dollars / the whole league's
                                  total effective spend that season

Rows where the player id doesn't resolve against nflverse (rare - mostly
very deep camp bodies) keep position=None and every nflverse-derived field
None, rather than guessing via a fragile name match; a later analysis step
should decide whether to drop them.

Also appends synthetic "no-bid" rows (signal="no_bid", bid_amount_dollars=0):
one per (season, week, player) where the player was a genuine free agent
that week (per pull_weekly_rosters.py's actual weekly rosters - run that
script first), nobody bid on him, and his prior-week usage/production
cleared a relevance bar (NO_BID_MIN_SNAP_PCT / NO_BID_MIN_PRIOR_POINTS).
Without these, the dataset only ever shows players someone wanted, and a
model trained on it could never learn what "not worth a bid" looks like.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import nflreadpy as nfl
import polars as pl
from espn_api.football import League as EspnLeague

from engine.faab_estimate import FANTASY_RELEVANT_SNAP_PCT, NO_BID_MIN_PRIOR_POINTS, NO_BID_MIN_SNAP_PCT, POSITIONS as RELEVANT_POSITIONS
from engine.scoring import ScoringRules
from ingest import nfl_data as nd
from ingest.ids import build_id_map

BIDS_PATH = Path(__file__).parent / "o-league-bids.json"
OUT_PATH = Path(__file__).parent / "o-league-training-table.json"
LEAGUE_ID = 355398

INJURY_FLAG_STATUSES = {"Out", "Doubtful"}

ROSTERED_BY_WEEK_PATH = Path(__file__).parent / "o-league-rostered-by-week.json"


def load_scoring_rules() -> ScoringRules:
    # No auth needed - current season is public. Scoring rules are assumed
    # stable across the FAAB-history seasons (2019-2025); flag to Alex if
    # that ever turns out not to be true for this league.
    lg = EspnLeague(league_id=LEAGUE_ID, year=2026)
    return ScoringRules.from_espn(lg.settings.scoring_format, is_dst=False)


def _recent_snap_pct(pfr_id: str | None, week: int, snaps: pl.DataFrame) -> float | None:
    """This player's offense_pct in the most recent of week-1/week-2 that has
    a row - the same short lookback used for snap_pct_prior_week below."""
    if not pfr_id:
        return None
    hist = snaps.filter(pl.col("pfr_player_id") == pfr_id)
    for wk in (week - 1, week - 2):
        if wk < 1:
            continue
        row = hist.filter(pl.col("week") == wk)
        if row.height:
            return row.to_dicts()[0].get("offense_pct")
    return None


def build_gsis_to_pfr_map() -> dict[str, str]:
    """ids.py's IdMap records don't carry pfr_id (it strips down to gsis/fp/
    espn/name/position/team) - build a separate gsis_id -> pfr_id lookup
    straight from the raw crosswalk frame for the snap_counts join."""
    frame = nd.playerids()
    result = {}
    for row in frame.iter_rows(named=True):
        if row.get("gsis_id") and row.get("pfr_id"):
            result[row["gsis_id"]] = row["pfr_id"]
    return result


def enrich_player_week(
    *,
    gsis_id: str | None,
    position: str | None,
    week: int,
    stats: pl.DataFrame,
    injuries: pl.DataFrame,
    snaps: pl.DataFrame,
    scoring: ScoringRules,
    idmap,
    gsis_to_pfr: dict[str, str],
) -> dict:
    """Every nflverse-derived field shared by both a real bid row and a
    synthetic no-bid row - factored out so the two code paths can't drift."""
    this_week_row = None
    prior_week_row = None
    team_that_week = None
    if gsis_id:
        this_week_df = stats.filter((pl.col("player_id") == gsis_id) & (pl.col("week") == week))
        if this_week_df.height:
            this_week_row = this_week_df.to_dicts()[0]
            team_that_week = this_week_row.get("team")
        if week > 1:
            prior_df = stats.filter((pl.col("player_id") == gsis_id) & (pl.col("week") == week - 1))
            if prior_df.height:
                prior_week_row = prior_df.to_dicts()[0]
                if team_that_week is None:
                    team_that_week = prior_week_row.get("team")

    prior_week_actual_points = scoring.points_for_row(prior_week_row) if prior_week_row else None

    own_injury_status = None
    teammate_position_injury_flag = None
    if gsis_id:
        own_row = injuries.filter((pl.col("gsis_id") == gsis_id) & (pl.col("week") == week))
        if own_row.height:
            own_injury_status = own_row.to_dicts()[0].get("report_status")
    if team_that_week and position:
        teammates = injuries.filter(
            (pl.col("team") == team_that_week) & (pl.col("week") == week) & (pl.col("position") == position) & (pl.col("gsis_id") != gsis_id)
        )
        teammate_position_injury_flag = False
        for row in teammates.to_dicts():
            if row.get("report_status") not in INJURY_FLAG_STATUSES:
                continue
            teammate_pfr_id = gsis_to_pfr.get(row.get("gsis_id"))
            snap_pct = _recent_snap_pct(teammate_pfr_id, week, snaps)
            if snap_pct is not None and snap_pct >= FANTASY_RELEVANT_SNAP_PCT:
                teammate_position_injury_flag = True
                break

    snap_pct_prior = None
    snap_pct_two_prior = None
    pfr_id = gsis_to_pfr.get(gsis_id) if gsis_id else None
    if pfr_id:
        snap_hist = snaps.filter(pl.col("pfr_player_id") == pfr_id)
        for wk, target in [(week - 1, "prior"), (week - 2, "two_prior")]:
            if wk < 1:
                continue
            snap_row = snap_hist.filter(pl.col("week") == wk)
            if snap_row.height:
                pct = snap_row.to_dicts()[0].get("offense_pct")
                if target == "prior":
                    snap_pct_prior = pct
                else:
                    snap_pct_two_prior = pct

    return {
        "prior_week_actual_points": prior_week_actual_points,
        "prior_week_had_stat_row": prior_week_row is not None,
        "team_that_week": team_that_week,
        "own_injury_status": own_injury_status,
        "teammate_position_injury_flag": teammate_position_injury_flag,
        "snap_pct_prior_week": snap_pct_prior,
        "snap_pct_two_weeks_prior": snap_pct_two_prior,
    }


def build_no_bid_rows(
    *,
    bids: list[dict],
    idmap,
    gsis_to_pfr: dict[str, str],
    scoring: ScoringRules,
    stats_by_season: dict[int, pl.DataFrame],
    injuries_by_season: dict[int, pl.DataFrame],
    snaps_by_season: dict[int, pl.DataFrame],
) -> list[dict]:
    """One row per (season, week, player) where the player was a genuine free
    agent that week (per pull_weekly_rosters.py's actual rosters, NOT an
    assumption), nobody placed any bid on him, and his prior-week usage or
    production cleared the NO_BID_MIN_* relevance bar. Week 1 of each season
    is skipped - there's no prior week within the season to judge relevance
    from."""
    if not ROSTERED_BY_WEEK_PATH.exists():
        print(f"WARNING: {ROSTERED_BY_WEEK_PATH} not found - run pull_weekly_rosters.py first. Skipping no-bid rows.")
        return []
    rostered_by_week: dict[str, dict[str, list[int]]] = json.loads(ROSTERED_BY_WEEK_PATH.read_text())

    existing_keys = {(r["season"], r["week"], r["add_player_id"]) for r in bids}

    out = []
    for season_str, weeks in rostered_by_week.items():
        season = int(season_str)
        if season not in stats_by_season:
            continue
        stats = stats_by_season[season]
        injuries = injuries_by_season[season]
        snaps = snaps_by_season[season]

        for week_str, rostered_ids in weeks.items():
            week = int(week_str)
            if week <= 1:
                continue
            rostered_set = set(rostered_ids)

            prior_rows = stats.filter(pl.col("week") == week - 1).to_dicts()
            for row in prior_rows:
                position = row.get("position")
                if position not in RELEVANT_POSITIONS:
                    continue
                gsis_id = row.get("player_id")
                prior_points = scoring.points_for_row(row)
                snap_pct = _recent_snap_pct(gsis_to_pfr.get(gsis_id), week, snaps)
                if prior_points < NO_BID_MIN_PRIOR_POINTS and (snap_pct is None or snap_pct < NO_BID_MIN_SNAP_PCT):
                    continue  # not relevant enough to plausibly have been considered

                record = idmap.by_gsis.get(gsis_id)
                espn_id = record.get("espn_id") if record else None
                if espn_id is None:
                    continue  # can't cross-check against the rostered set without an ESPN id
                if espn_id in rostered_set:
                    continue  # actually owned by a fantasy team that week, not a free agent
                if (season, week, espn_id) in existing_keys:
                    continue  # already has a real bid row this week

                enriched = enrich_player_week(
                    gsis_id=gsis_id, position=position, week=week,
                    stats=stats, injuries=injuries, snaps=snaps,
                    scoring=scoring, idmap=idmap, gsis_to_pfr=gsis_to_pfr,
                )
                out.append(
                    {
                        "season": season,
                        "week": week,
                        "transaction_id": None,
                        "team_id": None,
                        "type": "NONE",
                        "status": "NO_BID",
                        "bid_amount_raw": 0,
                        "bid_amount_dollars": 0.0,
                        "add_player_id": espn_id,
                        "add_player_name": record.get("name"),
                        "drop_player_ids": [],
                        "date": None,
                        "contingent_variants_dropped": 0,
                        "signal": "no_bid",
                        "effective_cost_dollars": 0.0,
                        "gsis_id": gsis_id,
                        "position": position,
                        "num_competing_bidders": 0,
                        **enriched,
                        "pct_of_bidder_season_spend": None,
                        "pct_of_league_season_spend": None,
                    }
                )
    return out


def compute_bidder_counts(bids: list[dict]) -> dict[tuple, int]:
    groups: dict[tuple, set] = defaultdict(set)
    for r in bids:
        if r["type"] not in ("WAIVER", "WAIVER_ERROR"):
            continue
        key = (r["season"], r["week"], r["add_player_id"])
        groups[key].add(r["team_id"])
    return {k: len(v) for k, v in groups.items()}


def compute_spend_denominators(bids: list[dict]) -> tuple[dict[tuple, float], dict[int, float]]:
    by_team_season: dict[tuple, float] = defaultdict(float)
    by_season: dict[int, float] = defaultdict(float)
    for r in bids:
        by_team_season[(r["season"], r["team_id"])] += r["effective_cost_dollars"]
        by_season[r["season"]] += r["effective_cost_dollars"]
    return dict(by_team_season), dict(by_season)


def main():
    bids = json.loads(BIDS_PATH.read_text())
    seasons = sorted({r["season"] for r in bids})
    idmap = build_id_map()
    gsis_to_pfr = build_gsis_to_pfr_map()
    scoring = load_scoring_rules()

    bidder_counts = compute_bidder_counts(bids)
    team_season_spend, season_spend = compute_spend_denominators(bids)

    # Load each season's nflverse data ONCE, not per-row.
    stats_by_season: dict[int, pl.DataFrame] = {}
    injuries_by_season: dict[int, pl.DataFrame] = {}
    snaps_by_season: dict[int, pl.DataFrame] = {}
    for season in seasons:
        print(f"loading nflverse data for {season}...")
        stats_by_season[season] = nfl.load_player_stats([season])
        injuries_by_season[season] = nfl.load_injuries([season])
        snaps_by_season[season] = nfl.load_snap_counts([season])

    unresolved_espn_ids: set[int] = set()
    out_rows = []
    for r in bids:
        season, week = r["season"], r["week"]
        record = idmap.by_espn.get(r["add_player_id"])
        gsis_id = record["gsis_id"] if record else None
        position = record["position"] if record else None
        if record is None:
            unresolved_espn_ids.add(r["add_player_id"])

        enriched = enrich_player_week(
            gsis_id=gsis_id, position=position, week=week,
            stats=stats_by_season[season], injuries=injuries_by_season[season], snaps=snaps_by_season[season],
            scoring=scoring, idmap=idmap, gsis_to_pfr=gsis_to_pfr,
        )

        team_spend = team_season_spend.get((season, r["team_id"]), 0.0)
        league_spend = season_spend.get(season, 0.0)

        out_rows.append(
            {
                **r,
                "gsis_id": gsis_id,
                "position": position,
                "num_competing_bidders": bidder_counts.get((season, week, r["add_player_id"])),
                **enriched,
                "pct_of_bidder_season_spend": (r["bid_amount_dollars"] / team_spend) if team_spend else None,
                "pct_of_league_season_spend": (r["bid_amount_dollars"] / league_spend) if league_spend else None,
            }
        )

    print(f"bid rows: {len(out_rows)}")
    no_bid_rows = build_no_bid_rows(
        bids=bids, idmap=idmap, gsis_to_pfr=gsis_to_pfr, scoring=scoring,
        stats_by_season=stats_by_season, injuries_by_season=injuries_by_season, snaps_by_season=snaps_by_season,
    )
    print(f"no-bid rows: {len(no_bid_rows)}")
    out_rows.extend(no_bid_rows)

    OUT_PATH.write_text(json.dumps(out_rows, indent=2))
    print(f"\nwrote {len(out_rows)} total rows to {OUT_PATH}")
    print(f"unresolved ESPN player ids (no nflverse match): {len(unresolved_espn_ids)}")
    resolved_count = sum(1 for r in out_rows if r["gsis_id"])
    print(f"resolved to a gsis_id: {resolved_count}/{len(out_rows)}")
    with_prior_points = sum(1 for r in out_rows if r["prior_week_actual_points"] is not None)
    print(f"have prior_week_actual_points: {with_prior_points}/{len(out_rows)}")


if __name__ == "__main__":
    main()
