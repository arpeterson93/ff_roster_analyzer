"""Builds training tables, one row per historical FAAB bid, by joining
tools/faab_history/o-league-bids.json (and, once pulled,
other-leagues-bids.json) against nflverse data and each source league's OWN
scoring rules.

Two output files:
  - o-league-training-table.json    The O League only - same shape/contract
                                     as before (engine/faab_estimate.py's
                                     live pipeline reads this path), plus
                                     the new source_league_*/budget-
                                     remaining fields added additively.
  - combined-training-table.json    The O League PLUS every other public
                                     league pulled by pull_public_league_bids.py.
                                     Not read by the live pipeline yet - see
                                     the conversation this was built from:
                                     the plan is to validate via
                                     evaluate_model.py's leave-one-league-out
                                     backtest before switching the live
                                     estimator over to it.

WHY EACH LEAGUE'S OWN SCORING RULES, NOT OURS: a bid reflects what THAT
league's bidders saw under THEIR scoring format when they decided how much
to spend - recomputing a PPR league's points under our Standard rules would
erase the exact signal (raw receptions) that drove those bids. So every
league's points-based features (prior_week_actual_points,
trailing_2_3_avg_points, season_avg_points) are computed by loading that
league's own scoring_format from ESPN and running it through
engine.scoring.ScoringRules.points_for_row, same as the O-League-only
version always did for its own one league. This also makes cross-league
pooling correct even though vet_candidates.py never required an exact PPR
match: once points are denominated in each bidder's own terms, the
feature -> bid% relationship is scoring-format-agnostic.

WHY THE INTEREST STAGE ONLY POOLS LEAGUES WITH ROSTER DATA:
engine/faab_estimate.py's two-stage model needs real no_bid (true negative)
rows to train P(anyone bids at all) - "not a single genuine free agent"
reconstruction requires a full weekly-roster pull (see
pull_weekly_rosters.py / pull_public_league_rosters.py), so a league only
contributes no_bid rows once that pull has actually been run for it. A
league with bid data but no roster pull yet contributes real bid activity
(won/outbid/other_failure) with no counterbalancing no_bid examples -
pooling those into the interest stage would just teach the model "someone
always bids", a real bias, not a data-scarcity problem more data would
fix. Callers (evaluate_model.py, and eventually the live pipeline if it
switches to the combined table) build the interest-stage pool from
whichever leagues actually produced no_bid rows (dynamically - see
evaluate_model.py's interest_eligible_leagues), while the PRICE stage (this
is exactly what "adds to the market-picture distribution" means - see the
conversation this was built from) uses the full combined pool regardless.

WHY *_pct_budget_remaining_at_bid: how much FAAB is left in the tank,
league-wide and for the specific bidder, as of the start of the week a bid
happened - a market with little money left behaves differently than a flush
one, independent of any one player's merits. Computed at week-level
granularity (not exact within-week bid ordering) straight from each
league-season's own bid rows - no new data source needed. Stored on every
row; NOT yet wired into engine/faab_estimate.py's FEATURE_NAMES - that's a
modeling decision that should be validated via evaluate_model.py first.

For each bid, adds:
  - position                     from the ESPN<->nflverse id crosswalk
  - num_competing_bidders        distinct teams that placed a WAIVER bid on
                                  this same player in this same league+week
  - prior_week_actual_points     the player's real fantasy points, in THIS
                                  ROW'S OWN SOURCE LEAGUE'S scoring rules,
                                  the week before the bid (from nflreadpy)
  - prior_week_had_stat_row      whether he even had a stat line that week
  - team_that_week               his NFL team as of the bid week
  - own_injury_status             his own injury report status that week
  - teammate_position_injury_flag whether another fantasy-relevant player at
                                  his own team+position had an Out/Doubtful
                                  report that same week
  - snap_pct_prior_week / snap_pct_two_weeks_prior
                                  his offense snap share trend
  - trailing_2_3_avg_points      real fantasy points (this row's own
                                  league's scoring) averaged over
                                  (bid_week-2) and (bid_week-3) only
  - season_avg_points            real fantasy points (this row's own
                                  league's scoring) averaged over every
                                  prior week this season
  - pct_of_bidder_season_spend   bid_amount_dollars / that bidding team's
                                  total effective spend that (league,season)
  - pct_of_league_season_spend   bid_amount_dollars / that (league,season)'s
                                  total effective spend
  - bidder_pct_budget_remaining_at_bid / league_avg_pct_budget_remaining_at_bid
                                  see above
  - source_league_id / source_league_name / source_league_size / source_league_ppr_label
                                  provenance - which league this bid came
                                  from and its settings, for eval grouping
                                  and the player-modal comp breakdown

Also appends synthetic "no-bid" rows for every league that has a weekly-
roster pull available (The O League always; any other league once
pull_public_league_rosters.py has run for it - see WHY THE INTEREST STAGE
above) - one per (season, week, player) where the player was a genuine free
agent that week, nobody bid on him, and his prior-week usage/production
cleared a relevance bar.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import nflreadpy as nfl
import polars as pl
from espn_api.football import League as EspnLeague

from engine.faab_estimate import FANTASY_RELEVANT_SNAP_PCT, NO_BID_MIN_PRIOR_POINTS, NO_BID_MIN_SNAP_PCT, POSITIONS as RELEVANT_POSITIONS
from engine.scoring import ScoringRules
from ingest import nfl_data as nd
from ingest.ids import build_id_map
from league_profile import fetch_settings, profile_settings, scoring_format_items

O_LEAGUE_ID = 355398
BIDS_PATH = Path(__file__).parent / "o-league-bids.json"
OTHER_BIDS_PATH = Path(__file__).parent / "other-leagues-bids.json"
VETTED_PATH = Path(__file__).parent / "vetted_candidates.json"
OUT_PATH = Path(__file__).parent / "o-league-training-table.json"
COMBINED_OUT_PATH = Path(__file__).parent / "combined-training-table.json"

INJURY_FLAG_STATUSES = {"Out", "Doubtful"}

ROSTERED_BY_WEEK_PATH = Path(__file__).parent / "o-league-rostered-by-week.json"
OTHER_ROSTERED_BY_WEEK_PATH = Path(__file__).parent / "other-leagues-rostered-by-week.json"


def load_scoring_rules_for_league(league_id: int, year: int = 2026) -> ScoringRules:
    """Any league's OWN scoring rules, straight from its live (public)
    settings - not ours. See module docstring for why this matters. Scoring
    rules are assumed stable across a league's FAAB-history seasons; flag to
    Alex if that ever turns out untrue for a specific league."""
    status, raw_settings = fetch_settings(league_id, year)
    if status != "ok":
        raise RuntimeError(f"couldn't fetch league {league_id}'s settings to build scoring rules ({status})")
    return ScoringRules.from_espn(scoring_format_items(raw_settings), is_dst=False)


def load_league_provenance() -> dict[int, dict]:
    """{league_id: {name, size, ppr_label, acquisition_budget}} for The O
    League plus every compatible league in vetted_candidates.json - used to
    tag every row's source_league_* fields and to compute
    *_pct_budget_remaining_at_bid (needs each league's own budget)."""
    status, raw_settings = fetch_settings(O_LEAGUE_ID, 2026)
    if status != "ok":
        raise RuntimeError(f"couldn't fetch our own league's settings ({status})")
    o_league_profile = profile_settings(raw_settings)
    result = {
        O_LEAGUE_ID: {
            "name": o_league_profile["name"],
            "size": o_league_profile["size"],
            "ppr_label": o_league_profile["ppr_label"],
            "acquisition_budget": o_league_profile["acquisition_budget"],
        }
    }
    if VETTED_PATH.exists():
        for c in json.loads(VETTED_PATH.read_text()):
            if c.get("compatible"):
                result[c["league_id"]] = {
                    "name": c["name"],
                    "size": c["size"],
                    "ppr_label": c["ppr_label"],
                    "acquisition_budget": c["acquisition_budget"],
                }
    return result


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


# ---------------------------------------------------------------------------
# Forward-looking rank features: real historical FantasyPros consensus-rank
# snapshots (nflreadpy's load_ff_rankings("all") - a genuine point-in-time
# scrape_date per snapshot, not a live-only page), joined via each player's
# fantasypros_id (already sitting on idmap.by_gsis[gsis_id] - no name
# matching needed). See the conversation this was built from: recent real
# output (prior_week_actual_points etc.) is backward-looking and can't
# capture a role/opportunity change real bidders would have seen coming; a
# point-in-time consensus rank is what other humans, working from more than
# just this one player's box score, thought about him heading INTO that
# week - a genuinely forward-looking signal recent output alone can't give.
#
# No coverage before 2020 (nflreadpy's archive has zero snapshots for any
# position in all of 2019 - confirmed live 2026-09-11) - those rows get
# None for these fields, same missing-data handling as every other
# optional feature here.
#
# "redraft-{pos}" is used as the ROS-rank proxy - continuously updated
# through the season (51-64 snapshots/season, vs weekly's 12-19), unlike a
# one-time preseason snapshot. The archive doesn't preserve FantasyPros'
# separate live "ros-*.php" page under its own label, so this is the
# closest available signal, not a confirmed exact match - worth a spot
# check against the live site if this feature earns its way into the model.
# "weekly-{pos}" is the upcoming-week, matchup-specific rank.
# ---------------------------------------------------------------------------
_RANK_POSITIONS = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k"}
_RANK_PAGE_TYPES = [f"weekly-{p}" for p in _RANK_POSITIONS.values()] + [f"redraft-{p}" for p in _RANK_POSITIONS.values()]


def build_rank_index(seasons: list[int]) -> dict:
    """{"snapshots": {(page_type, fp_id): sorted [(date, ecr), ...]},
        "week_starts": {(season, week): date of that week's first real
        game}}. week_starts is the boundary a rank lookup must stay
    STRICTLY BEFORE - real leagues process FAAB before that week's games
    even start (see pull_o_league_bids.py's own docstring on FAAB timing),
    so a rank snapshot dated on/after kickoff was never actually available
    to a bidder deciding that week."""
    rankings = nfl.load_ff_rankings("all").filter(pl.col("page_type").is_in(_RANK_PAGE_TYPES))
    snapshots: dict[tuple, list[tuple]] = defaultdict(list)
    for row in rankings.iter_rows(named=True):
        if row["id"] is None or row["ecr"] is None or not row["scrape_date"]:
            continue
        try:
            # This dataset's own "id" column is a numeric STRING - idmap's
            # fantasypros_id is stored as an int (see ingest/ids.py). A
            # naive same-type join here would silently match nothing and
            # every rank feature would just look like "no coverage" for
            # every player, not fail loud - caught live 2026-09 by testing
            # a known real player (Zach Evans) and getting an unexpected
            # None back.
            fp_id = int(row["id"])
        except (TypeError, ValueError):
            continue
        date = dt.date.fromisoformat(row["scrape_date"][:10])
        snapshots[(row["page_type"], fp_id)].append((date, row["ecr"]))
    for key in snapshots:
        snapshots[key].sort()

    week_starts: dict[tuple, dt.date] = {}
    for season in seasons:
        sched = nfl.load_schedules([season])
        for row in sched.iter_rows(named=True):
            if not row["gameday"]:
                continue
            date = row["gameday"] if isinstance(row["gameday"], dt.date) else dt.date.fromisoformat(str(row["gameday"])[:10])
            key = (season, row["week"])
            if key not in week_starts or date < week_starts[key]:
                week_starts[key] = date

    return {"snapshots": dict(snapshots), "week_starts": week_starts}


def _rank_lookup(rank_index: dict, page_type: str, fp_id: int | None, season: int, week: int) -> int | None:
    """The raw ECR rank NUMBER (1 = best) within this position's own page -
    already position-specific since page_type is "weekly-rb"/"redraft-wr"/
    etc, never an overall cross-position ranking. Deliberately NOT
    converted to a percentile: the total number of players FantasyPros
    ranks in a given snapshot varies by up to ~3x across real snapshots
    (confirmed live 2026-09: redraft-wr ranges 109-329 ranked players) -
    percentile would make the SAME real rank (e.g. "the 24th-best RB")
    read as a different number purely depending on how deep that
    particular snapshot happened to go, which has nothing to do with the
    player's actual value. A raw rank stays anchored to real, stable
    tiers (RB1-12 elite starter, RB13-24 solid starter, RB25-36 flex/
    streaming, etc) regardless of list depth. None if no qualifying
    snapshot exists (before 2020, an unranked/practice-squad player, or a
    missing fantasypros_id)."""
    if fp_id is None:
        return None
    cutoff = rank_index["week_starts"].get((season, week))
    if cutoff is None:
        return None
    snaps = rank_index["snapshots"].get((page_type, fp_id))
    if not snaps:
        return None
    dates = [d for d, _ in snaps]
    idx = bisect.bisect_left(dates, cutoff) - 1
    if idx < 0:
        return None
    return snaps[idx][1]


def forward_rank_features(gsis_id: str | None, position: str | None, season: int, week: int, idmap, rank_index: dict) -> dict:
    pos = _RANK_POSITIONS.get(position) if position else None
    fp_id = None
    if gsis_id and pos:
        record = idmap.by_gsis.get(gsis_id)
        fp_id = record.get("fantasypros_id") if record else None
    if pos is None:
        return {"weekly_rank": None, "ros_rank": None}
    return {
        "weekly_rank": _rank_lookup(rank_index, f"weekly-{pos}", fp_id, season, week),
        "ros_rank": _rank_lookup(rank_index, f"redraft-{pos}", fp_id, season, week),
    }


def _points_by_week(gsis_id: str | None, stats: pl.DataFrame, scoring: ScoringRules) -> dict[int, float]:
    """This player's real fantasy points for every week he has a stat row
    THIS season, under the given league's OWN scoring rules - computed once
    per (player, season, league) and reused for both the trailing-2/3 and
    season-to-date windows below, rather than re-filtering stats per window."""
    if not gsis_id:
        return {}
    df = stats.filter(pl.col("player_id") == gsis_id)
    return {row["week"]: scoring.points_for_row(row) for row in df.to_dicts()}


def enrich_player_week(
    *,
    gsis_id: str | None,
    position: str | None,
    season: int,
    week: int,
    stats: pl.DataFrame,
    injuries: pl.DataFrame,
    snaps: pl.DataFrame,
    scoring: ScoringRules,
    idmap,
    gsis_to_pfr: dict[str, str],
    rank_index: dict,
) -> dict:
    """Every nflverse-derived field shared by both a real bid row and a
    synthetic no-bid row - factored out so the two code paths can't drift.
    `scoring` is THIS ROW'S OWN SOURCE LEAGUE's rules - see module docstring."""
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

    points_by_week = _points_by_week(gsis_id, stats, scoring)
    trailing_weeks = [wk for wk in (week - 2, week - 3) if wk >= 1 and wk in points_by_week]
    trailing_2_3_avg_points = (sum(points_by_week[wk] for wk in trailing_weeks) / len(trailing_weeks)) if trailing_weeks else None
    season_weeks = [wk for wk in points_by_week if wk < week]
    season_avg_points = (sum(points_by_week[wk] for wk in season_weeks) / len(season_weeks)) if season_weeks else None

    return {
        "prior_week_actual_points": prior_week_actual_points,
        "prior_week_had_stat_row": prior_week_row is not None,
        "team_that_week": team_that_week,
        "own_injury_status": own_injury_status,
        "teammate_position_injury_flag": teammate_position_injury_flag,
        "snap_pct_prior_week": snap_pct_prior,
        "snap_pct_two_weeks_prior": snap_pct_two_prior,
        "trailing_2_3_avg_points": trailing_2_3_avg_points,
        "season_avg_points": season_avg_points,
        **forward_rank_features(gsis_id, position, season, week, idmap, rank_index),
    }


def build_no_bid_rows(
    *,
    league_id: int,
    bids: list[dict],
    rostered_by_week: dict[str, dict[str, list[int]]],
    idmap,
    gsis_to_pfr: dict[str, str],
    scoring: ScoringRules,
    stats_by_season: dict[int, pl.DataFrame],
    injuries_by_season: dict[int, pl.DataFrame],
    snaps_by_season: dict[int, pl.DataFrame],
    rank_index: dict,
) -> list[dict]:
    """One row per (season, week, player) where the player was a genuine
    free agent that week in THIS league (per pull_weekly_rosters.py's /
    pull_public_league_rosters.py's actual weekly rosters, NOT an
    assumption), nobody placed any bid on him, and his prior-week usage or
    production cleared the NO_BID_MIN_* relevance bar. Week 1 of each
    season is skipped - there's no prior week within the season to judge
    relevance from.

    Generalized to any league with a weekly-roster snapshot file, not just
    The O League - see module docstring's WHY POOLING NOW EXTENDS TO THE
    INTEREST STAGE. Caller is responsible for only calling this when
    rostered_by_week data actually exists for league_id (main() checks)."""
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
                    gsis_id=gsis_id, position=position, season=season, week=week,
                    stats=stats, injuries=injuries, snaps=snaps,
                    scoring=scoring, idmap=idmap, gsis_to_pfr=gsis_to_pfr, rank_index=rank_index,
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
                        "source_league_id": league_id,
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
    """Keyed by (source_league_id, season, week, add_player_id) - NOT just
    (season, week, add_player_id): ESPN player ids are global across every
    league on the platform, so two different real leagues bidding on the
    same real player in the same week are NOT "competing bidders" on each
    other and must not be merged."""
    groups: dict[tuple, set] = defaultdict(set)
    for r in bids:
        if r["type"] not in ("WAIVER", "WAIVER_ERROR"):
            continue
        key = (r["source_league_id"], r["season"], r["week"], r["add_player_id"])
        groups[key].add(r["team_id"])
    return {k: len(v) for k, v in groups.items()}


def compute_spend_denominators(bids: list[dict]) -> tuple[dict[tuple, float], dict[tuple, float]]:
    """Both keyed by (source_league_id, season, ...) - each league has its
    own budget scale and its own team spend, not comparable across leagues
    without this key (see compute_bidder_counts for the same reasoning)."""
    by_team_season: dict[tuple, float] = defaultdict(float)
    by_league_season: dict[tuple, float] = defaultdict(float)
    for r in bids:
        by_team_season[(r["source_league_id"], r["season"], r["team_id"])] += r["effective_cost_dollars"]
        by_league_season[(r["source_league_id"], r["season"])] += r["effective_cost_dollars"]
    return dict(by_team_season), dict(by_league_season)


def annotate_budget_remaining(rows: list[dict], league_season_budgets: dict[tuple, float]) -> None:
    """Mutates every row in place, adding:
      - effective_starting_budget: the (league, season) budget league_season_
        budgets actually used for this row - stored so target_pct can later
        turn bidder_pct_budget_remaining_at_bid back into a real dollar
        denominator without needing this dict again.
      - league_avg_pct_budget_remaining_at_bid: averaged, across every team
        that placed at least one transaction in that (league, season), of
        1 - (that team's real cumulative spend THROUGH THE PRIOR WEEK /
        that (league, season)'s effective starting budget). Week-level
        granularity, not exact within-week bid ordering - a rough market-
        scarcity signal, not a precise one (see module docstring).
      - bidder_pct_budget_remaining_at_bid: same idea for the specific
        bidding team on a real bid row - None for no_bid rows (team_id is
        None; there's no bidder to ask about) or when the (league, season)'s
        budget isn't known.

    league_season_budgets is keyed by (source_league_id, season), not just
    league - The O League has no real enforced dollar cap (its reported
    ESPN acquisitionBudget, $1000, is essentially fictional - real teams
    spend $37-143 a season, nowhere close), so its caller passes a
    DIFFERENT ESTIMATED effective budget per season, derived from that
    season's own real observed spend - not a single constant, since real
    spend varies close to 4x across its seasons (see build_effective_
    budgets). Every other pooled league (assumed to have a real, enforced
    ESPN-reported cap) gets that same flat number repeated across all its
    seasons.

    A team that never made a single transaction all season is invisible to
    this (there's nothing to compute its spend from) and is left out of the
    league-wide average - a reasonable approximation, not exact.
    Idempotent/safe to call again after appending more rows (e.g. no-bid
    rows) - everything here is recomputed from the row set passed in, not
    accumulated across calls."""
    weekly_spend: dict[tuple, float] = defaultdict(float)  # (lid, season, team_id, week) -> spend that week
    teams_by_league_season: dict[tuple, set] = defaultdict(set)
    for r in rows:
        if r["team_id"] is not None:
            key = (r["source_league_id"], r["season"])
            weekly_spend[(*key, r["team_id"], r["week"])] += r["effective_cost_dollars"]
            teams_by_league_season[key].add(r["team_id"])

    def cumulative_through(lid, season, team_id, week_exclusive):
        return sum(weekly_spend.get((lid, season, team_id, wk), 0.0) for wk in range(1, week_exclusive))

    league_avg_cache: dict[tuple, float] = {}
    for r in rows:
        lid, season, week = r["source_league_id"], r["season"], r["week"]
        budget = league_season_budgets.get((lid, season))
        teams = teams_by_league_season.get((lid, season))
        r["effective_starting_budget"] = budget
        if not budget or not teams:
            r["league_avg_pct_budget_remaining_at_bid"] = None
            r["bidder_pct_budget_remaining_at_bid"] = None
            continue
        cache_key = (lid, season, week)
        if cache_key not in league_avg_cache:
            remainings = [1.0 - cumulative_through(lid, season, t, week) / budget for t in teams]
            league_avg_cache[cache_key] = sum(max(0.0, min(1.0, x)) for x in remainings) / len(remainings)
        r["league_avg_pct_budget_remaining_at_bid"] = league_avg_cache[cache_key]
        if r["team_id"] is not None:
            spent = cumulative_through(lid, season, r["team_id"], week)
            r["bidder_pct_budget_remaining_at_bid"] = max(0.0, min(1.0, 1.0 - spent / budget))
        else:
            r["bidder_pct_budget_remaining_at_bid"] = None


# Real historical bids known to be data-entry errors, not real spend -
# excluded from The O League's effective-budget estimation so one fat-
# fingered bid doesn't distort that whole season's assumed budget scale.
# Confirmed 2026-09: J.D. McKissic, 2020 wk10, bid $1003.25 - team 2's
# entire 2020 total was $1035.50, 97% of it this one row, while every
# other team that season spent $16-116 (in line with every other season).
# Keyed by transaction_id, the one truly stable identifier for a bid.
KNOWN_BAD_BID_TRANSACTION_IDS = {"d598e6ff-563e-4e03-b512-91216518fa59"}

# The O League's own "no real enforced dollar cap, real $ but a soft cap
# from people not wanting to spend much" era is CONFIRMED (per the human
# who runs this league) to be specific to its 2019-2025 history - 2026
# onward, it gets treated exactly like any other pooled public league (its
# own real acquisitionBudget, its own nominal - not cents-scaled -
# bidAmount). Do not extend either special case past this season.
O_LEAGUE_LEGACY_LAST_SEASON = 2025


def build_effective_budgets(bids: list[dict], provenance: dict) -> dict[tuple, float]:
    """{(source_league_id, season): effective starting budget} for
    annotate_budget_remaining. The O League's 2019-2025 seasons ONLY get
    that season's own real total-team-spend average (see
    annotate_budget_remaining's docstring for why - those seasons have no
    real enforced dollar cap), computed straight from its own bids
    (excluding KNOWN_BAD_BID_TRANSACTION_IDS), including real FREEAGENT
    house-fee dollars.

    Every OTHER league - and The O League's own 2026+ seasons, once they
    exist - gets its REAL historical acquisitionBudget for THAT specific
    season, fetched live from ESPN (same fetch_settings() used for per-
    league scoring rules, just called with each historical year instead of
    always 2026) - not assumed stable from its current setting. A league
    that changed its budget between 2019 and now would otherwise silently
    get every season's rows normalized against the wrong number. Falls back
    to the league's current (2026) provenance budget if a specific
    season's historical settings can't be fetched (private that year,
    network error, unexpected shape) - a real fallback, not a silent
    correctness gap, since check_candidate_history.py already established
    these years are publicly readable for the same leagues' transactions,
    so a settings fetch failing here is the exception, not the rule."""
    result: dict[tuple, float] = {}
    o_league_season_totals: dict[int, float] = defaultdict(float)
    for r in bids:
        if (
            r["source_league_id"] == O_LEAGUE_ID
            and r["season"] <= O_LEAGUE_LEGACY_LAST_SEASON
            and r["transaction_id"] not in KNOWN_BAD_BID_TRANSACTION_IDS
        ):
            o_league_season_totals[r["season"]] += r["effective_cost_dollars"]
    o_league_size = provenance[O_LEAGUE_ID]["size"]
    for season, total in o_league_season_totals.items():
        result[(O_LEAGUE_ID, season)] = total / o_league_size

    other_league_seasons: dict[int, set] = defaultdict(set)
    for r in bids:
        if r["source_league_id"] != O_LEAGUE_ID or r["season"] > O_LEAGUE_LEGACY_LAST_SEASON:
            other_league_seasons[r["source_league_id"]].add(r["season"])

    for lid, seasons in other_league_seasons.items():
        for season in sorted(seasons):
            status, raw_settings = fetch_settings(lid, season)
            if status == "ok":
                result[(lid, season)] = profile_settings(raw_settings)["acquisition_budget"]
            else:
                print(f"  WARNING: couldn't fetch league {lid}'s real {season} budget ({status}) - using its current setting instead", file=sys.stderr)
                result[(lid, season)] = provenance[lid]["acquisition_budget"]
            time.sleep(random.uniform(0.3, 0.9))
    return result


def build_rows_for_source(
    *,
    bids: list[dict],
    provenance: dict,
    idmap,
    gsis_to_pfr: dict[str, str],
    scoring: ScoringRules,
    stats_by_season: dict[int, pl.DataFrame],
    injuries_by_season: dict[int, pl.DataFrame],
    snaps_by_season: dict[int, pl.DataFrame],
    bidder_counts: dict[tuple, int],
    team_season_spend: dict[tuple, float],
    league_season_spend: dict[tuple, float],
    rank_index: dict,
) -> tuple[list[dict], set[int]]:
    """One source league's bid rows, enriched. Returns (rows, unresolved ESPN ids)."""
    unresolved_espn_ids: set[int] = set()
    out_rows = []
    for r in bids:
        season, week, lid = r["season"], r["week"], r["source_league_id"]
        if season not in stats_by_season:
            continue  # no nflverse data loaded for this season - shouldn't happen, but don't crash the whole run over one bad row
        record = idmap.by_espn.get(r["add_player_id"])
        gsis_id = record["gsis_id"] if record else None
        position = record["position"] if record else None
        if record is None:
            unresolved_espn_ids.add(r["add_player_id"])

        enriched = enrich_player_week(
            gsis_id=gsis_id, position=position, season=season, week=week,
            stats=stats_by_season[season], injuries=injuries_by_season[season], snaps=snaps_by_season[season],
            scoring=scoring, idmap=idmap, gsis_to_pfr=gsis_to_pfr, rank_index=rank_index,
        )

        team_spend = team_season_spend.get((lid, season, r["team_id"]), 0.0)
        spend_total = league_season_spend.get((lid, season), 0.0)

        out_rows.append(
            {
                **r,
                "gsis_id": gsis_id,
                "position": position,
                "num_competing_bidders": bidder_counts.get((lid, season, week, r["add_player_id"])),
                **enriched,
                "pct_of_bidder_season_spend": (r["bid_amount_dollars"] / team_spend) if team_spend else None,
                "pct_of_league_season_spend": (r["bid_amount_dollars"] / spend_total) if spend_total else None,
                "source_league_name": provenance["name"],
                "source_league_size": provenance["size"],
                "source_league_ppr_label": provenance["ppr_label"],
            }
        )
    return out_rows, unresolved_espn_ids


def main():
    # 100+ real, user-created league names hit this script now - some
    # contain characters Windows' default console encoding (cp1252) can't
    # print at all (crashed live 2026-09 on a smart-quote character).
    # Real UTF-8 output with unmappable characters replaced, not crashed on.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    o_league_bids = json.loads(BIDS_PATH.read_text())
    for r in o_league_bids:
        r["source_league_id"] = O_LEAGUE_ID

    other_bids: list[dict] = []
    if OTHER_BIDS_PATH.exists():
        other_bids = json.loads(OTHER_BIDS_PATH.read_text())
    print(f"The O League: {len(o_league_bids)} bid rows. Other public leagues: {len(other_bids)} bid rows across {len({r['source_league_id'] for r in other_bids})} leagues.")

    provenance = load_league_provenance()
    # pull_public_league_bids.py pulls (and keeps) bid data for every league
    # that was EVER compatible at pull time - vetted_candidates.json's own
    # compatible set can only shrink later (a stricter scoring-ledger rule,
    # a re-review) without anything re-pulling or pruning the already-
    # pulled bid files. Filter here, not just trust the file on disk, so a
    # league that's since been disqualified doesn't reach provenance[lid]
    # below (a real KeyError crash, hit live 2026-09 right after tightening
    # the scoring ledger and shrinking the compatible set 144->53) and,
    # more importantly, doesn't quietly keep contributing training rows
    # it's no longer supposed to be trusted for.
    before = len(other_bids)
    other_bids = [r for r in other_bids if r["source_league_id"] in provenance]
    if before != len(other_bids):
        print(
            f"dropped {before - len(other_bids)} bid rows from leagues no longer compatible "
            f"(currently-compatible pooled leagues: {len(provenance) - 1})"
        )

    all_bids = o_league_bids + other_bids
    seasons = sorted({r["season"] for r in all_bids})
    idmap = build_id_map()
    gsis_to_pfr = build_gsis_to_pfr_map()

    print("fetching real per-season effective budgets...")
    league_season_budgets = build_effective_budgets(all_bids, provenance)

    # Every league except The O League is auto-discovered (see
    # discover_public_leagues.py) - real, idiosyncratic scoring categories
    # show up that engine/scoring.py has never needed a mapping for before
    # (e.g. "every N yards" bonus tiers - added live 2026-09 the first time
    # one was hit). ScoringRules.from_espn fails LOUD on those on purpose
    # (see its own docstring) rather than silently mis-scoring - correct
    # behavior for a single-league run, but with 100+ pooled leagues one
    # bad apple shouldn't crash the whole build. So: skip that ONE league
    # (and all its bids, below) with a clear warning, keep going for
    # everyone else. The O League itself is never skipped this way - if
    # ITS OWN scoring can't be mapped, that's a real bug worth crashing
    # loudly for, not silently dropping our own league's data.
    scoring_by_league: dict[int, ScoringRules] = {}
    excluded_leagues: set[int] = set()
    for lid in {r["source_league_id"] for r in all_bids}:
        print(f"loading scoring rules for league {lid} ({provenance.get(lid, {}).get('name')!r})...")
        try:
            scoring_by_league[lid] = load_scoring_rules_for_league(lid)
        except ValueError as exc:
            if lid == O_LEAGUE_ID:
                raise
            print(f"  WARNING: skipping league {lid} entirely - {exc}", file=sys.stderr)
            excluded_leagues.add(lid)

    if excluded_leagues:
        all_bids = [r for r in all_bids if r["source_league_id"] not in excluded_leagues]
        other_bids = [r for r in other_bids if r["source_league_id"] not in excluded_leagues]
        print(f"excluded {len(excluded_leagues)} league(s) with unmappable scoring: {sorted(excluded_leagues)}")

    bidder_counts = compute_bidder_counts(all_bids)
    team_season_spend, league_season_spend = compute_spend_denominators(all_bids)

    # Load each season's nflverse data ONCE, not per-row - shared across
    # every league, since these describe real NFL players, not any one
    # fantasy league.
    stats_by_season: dict[int, pl.DataFrame] = {}
    injuries_by_season: dict[int, pl.DataFrame] = {}
    snaps_by_season: dict[int, pl.DataFrame] = {}
    for season in seasons:
        print(f"loading nflverse data for {season}...")
        stats_by_season[season] = nfl.load_player_stats([season])
        injuries_by_season[season] = nfl.load_injuries([season])
        snaps_by_season[season] = nfl.load_snap_counts([season])

    print("building forward-looking rank index (FantasyPros historical consensus ranks)...")
    rank_index = build_rank_index(seasons)

    all_unresolved: set[int] = set()
    all_out_rows: list[dict] = []
    for lid, bids_group in [(lid, [r for r in all_bids if r["source_league_id"] == lid]) for lid in {r["source_league_id"] for r in all_bids}]:
        rows, unresolved = build_rows_for_source(
            bids=bids_group, provenance=provenance[lid], idmap=idmap, gsis_to_pfr=gsis_to_pfr,
            scoring=scoring_by_league[lid], stats_by_season=stats_by_season,
            injuries_by_season=injuries_by_season, snaps_by_season=snaps_by_season,
            bidder_counts=bidder_counts, team_season_spend=team_season_spend, league_season_spend=league_season_spend,
            rank_index=rank_index,
        )
        print(f"league {lid} ({provenance[lid]['name']!r}): {len(rows)} bid rows, {len(unresolved)} unresolved player ids")
        all_out_rows.extend(rows)
        all_unresolved |= unresolved

    print(f"\ntotal bid rows: {len(all_out_rows)}")

    other_rostered: dict[str, dict] = {}
    if OTHER_ROSTERED_BY_WEEK_PATH.exists():
        other_rostered = json.loads(OTHER_ROSTERED_BY_WEEK_PATH.read_text())

    no_bid_sources: list[tuple[int, list[dict], dict]] = []
    if ROSTERED_BY_WEEK_PATH.exists():
        no_bid_sources.append((O_LEAGUE_ID, o_league_bids, json.loads(ROSTERED_BY_WEEK_PATH.read_text())))
    else:
        print(f"WARNING: {ROSTERED_BY_WEEK_PATH} not found - run pull_weekly_rosters.py first. Skipping The O League's no-bid rows.")
    for lid_str, rostered_by_week in other_rostered.items():
        lid = int(lid_str)
        if lid not in provenance:
            continue  # shouldn't happen (pull_public_league_rosters.py only pulls vetted-compatible leagues), but don't crash the run over it
        if lid in excluded_leagues:
            continue  # unmappable scoring - no scoring_by_league[lid] entry to enrich with, see above
        league_bids = [r for r in other_bids if r["source_league_id"] == lid]
        no_bid_sources.append((lid, league_bids, rostered_by_week))

    no_bid_rows: list[dict] = []
    for lid, league_bids, rostered_by_week in no_bid_sources:
        rows = build_no_bid_rows(
            league_id=lid, bids=league_bids, rostered_by_week=rostered_by_week,
            idmap=idmap, gsis_to_pfr=gsis_to_pfr, scoring=scoring_by_league[lid],
            stats_by_season=stats_by_season, injuries_by_season=injuries_by_season, snaps_by_season=snaps_by_season,
            rank_index=rank_index,
        )
        for r in rows:
            r["source_league_name"] = provenance[lid]["name"]
            r["source_league_size"] = provenance[lid]["size"]
            r["source_league_ppr_label"] = provenance[lid]["ppr_label"]
        print(f"league {lid} ({provenance[lid]['name']!r}): {len(rows)} no-bid rows")
        no_bid_rows.extend(rows)
    print(f"total no-bid rows across {len(no_bid_sources)} leagues with roster data: {len(no_bid_rows)}")
    all_out_rows.extend(no_bid_rows)

    # One pass over the FULL merged row set (real bids across every league +
    # The O League's no-bid rows) - a no_bid row's own team_id is always
    # None (it's not any specific team's decision), so it contributes no
    # spend data of its own, but it still needs the REAL bid rows' spend
    # data present in this same list to compute a meaningful
    # league_avg_pct_budget_remaining_at_bid for the week it describes.
    # Annotating no_bid_rows in isolation earlier left every no-bid row's
    # budget fields as None - this single combined pass is the fix.
    annotate_budget_remaining(all_out_rows, league_season_budgets)

    OUT_PATH.write_text(json.dumps([r for r in all_out_rows if r["source_league_id"] == O_LEAGUE_ID], indent=2))
    COMBINED_OUT_PATH.write_text(json.dumps(all_out_rows, indent=2))

    print(f"\nwrote {sum(1 for r in all_out_rows if r['source_league_id'] == O_LEAGUE_ID)} rows to {OUT_PATH}")
    print(f"wrote {len(all_out_rows)} rows ({len({r['source_league_id'] for r in all_out_rows})} leagues) to {COMBINED_OUT_PATH}")
    print(f"unresolved ESPN player ids (no nflverse match) across all leagues: {len(all_unresolved)}")
    resolved_count = sum(1 for r in all_out_rows if r["gsis_id"])
    print(f"resolved to a gsis_id: {resolved_count}/{len(all_out_rows)}")
    with_prior_points = sum(1 for r in all_out_rows if r["prior_week_actual_points"] is not None)
    print(f"have prior_week_actual_points: {with_prior_points}/{len(all_out_rows)}")


if __name__ == "__main__":
    main()
