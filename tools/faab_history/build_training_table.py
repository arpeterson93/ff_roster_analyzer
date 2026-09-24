"""Builds training tables, one row per historical FAAB bid, by joining
tools/faab_history/o-league-bids.json (and, once pulled,
other-leagues-bids.json) against nflverse data and each source league's OWN
scoring rules.

Two output files:
  - o-league-training-table.json     The O League only - same shape/contract
                                      as before (evaluate_model.py's own
                                      default --table reads this path), plus
                                      the new source_league_*/budget-
                                      remaining fields added additively.
  - combined-training-table.parquet  The O League PLUS every other public
                                      league pulled by pull_public_league_bids.py -
                                      what engine/faab_estimate.py's live
                                      pipeline actually reads (confirmed via
                                      backtest that pooling helps - see the
                                      conversation this was built from).
                                      .parquet, not .json - see this
                                      constant's own comment (COMBINED_OUT_PATH,
                                      below) for why.

WHY EVERY ROW'S POINTS-BASED FEATURES USE ONE SHARED BASELINE SCORING, NOT
EACH ROW'S OWN SOURCE LEAGUE'S: an earlier version of this file computed
prior_week_actual_points/trailing_2_3_avg_points/season_avg_points under
each row's OWN source league's real scoring rules, on the theory that a bid
reflects what THAT league's bidders saw when they decided how much to spend.
That's true of the BID - it stays denominated in that league's own real
dollars (target_pct never touches scoring at all, see engine/faab_estimate.py)
- but it's the wrong call for the FEATURES. A 28-point PPR game and a
20-point Standard game can be the exact same real box score; computing
"points" two different ways for the same underlying performance trains the
k-NN comp search and the regression on a feature whose meaning silently
shifts by source league, which a LIVE query (computed under whichever site
league is actually asking, see engine/pipeline.py's player_rules) has no way
to match. See the conversation this was built from (2026-09-21). Every
pooled row's points-based features are now computed under ONE shared
baseline_scoring (tools/faab_history/league_profile.load_baseline_scoring_
items - user-edited via a "Baseline Scoring" artifact, deliberately NOT
pinned to any one real league's own settings since a real league's rules
reflect that league's own idiosyncrasies, e.g. The O League doesn't score
defenses at all), regardless of which league actually placed the bid. This
also makes cross-league pooling correct even though vet_candidates.py never
required an exact PPR match: once every row's points are denominated in the
SAME terms, the feature -> bid% relationship no longer depends on comparing
like-for-like scoring formats across leagues.

scoring_by_league (below) is now used ONLY as a data-integrity check - each
candidate league's own live settings still have to actually parse into a
ScoringRules object (see the excluded_leagues handling in main()) - not for
computing any row's own features anymore.

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
  - snap_pct_prior_week           his offense snap share, most recent of
                                  week-1/week-2 that has a real row
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

Also appends synthetic "no-bid" rows for every league-SEASON that has both a
weekly-roster pull available (The O League always; any other league once
pull_public_league_rosters.py has run for it - see WHY THE INTEREST STAGE
above) AND at least one real transaction that season (see build_no_bid_rows'
own docstring, "TIER 1 LEAGUE-SEASON VALIDITY GATE") - one per (season, week,
player) where the player was a genuine free agent that week, nobody bid on
him, and his prior-week usage/production cleared a relevance bar.
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
from typing import NamedTuple

import nflreadpy as nfl
import polars as pl
from espn_api.football import League as EspnLeague

from engine.faab_estimate import (
    INJURY_FLAG_STATUSES,
    POSITIONS as RELEVANT_POSITIONS,
    build_injury_indices,
    build_snap_pct_index,
    is_faab_relevant,
    recent_carry_share,
    recent_snap_pct,
    recent_target_share,
    team_position_totals,
)
from engine.scoring import ScoringRules
from ingest import nfl_data as nd
from ingest.ids import build_id_map
from tools.faab_history.atomic_json import write_json
from tools.faab_history.league_profile import (
    fetch_settings, load_baseline_scoring_items, profile_settings, scoring_format_items,
)

O_LEAGUE_ID = 355398
BIDS_PATH = Path(__file__).parent / "o-league-bids.json"
OTHER_BIDS_PATH = Path(__file__).parent / "other-leagues-bids.json"
VETTED_PATH = Path(__file__).parent / "vetted_candidates.json"
OUT_PATH = Path(__file__).parent / "o-league-training-table.json"
# .parquet, not .json - see engine/faab_estimate.py's load_pools/
# POOLED_TRAINING_TABLE_PATH. The pooled table's row count (5.6M+ as of
# 2026-09 and growing as more leagues get pulled in) makes
# json.loads(path.read_text()) OOM a GitHub-hosted CI runner outright;
# polars can load and reduce a Parquet file columnar the whole way down.
# The O-League-only table above stays JSON - it's tiny (one league) and
# was never the problem.
COMBINED_OUT_PATH = Path(__file__).parent / "combined-training-table.parquet"
UNRESOLVED_PLAYERS_PATH = Path(__file__).parent / "unresolved_players.json"

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


class SeasonIndex(NamedTuple):
    """Every per-player/per-week lookup enrich_player_week and build_no_bid_
    rows need for one season, pre-indexed ONCE (see build_season_index)
    instead of each doing its own fresh polars .filter() per player-week -
    see the conversation this was built from: profiling a real enrich_
    player_week call showed ~80% of its time was pure polars query-
    collection overhead (not any actual work), from calling .filter() on
    the same season-long DataFrame 9+ times per row. With a training table
    in the hundreds of thousands of rows, that overhead alone ran into
    hours."""

    stats_by_player: dict[str, dict[int, dict]]  # player_id -> week -> row
    stats_by_week: dict[int, list[dict]]  # week -> every row that week (any player)
    injury_by_player_week: dict[tuple[str, int], str]  # (gsis_id, week) -> report_status
    teammates_by_team_week_pos: dict[tuple[str, int, str], list[dict]]  # (team, week, position) -> rows
    snap_pct_by_player: dict[str, dict[int, float]]  # pfr_player_id -> week -> offense_pct
    team_rb_carries: dict[tuple[str, int], float]  # (team, week) -> total RB-position carries


def _build_stats_indices(stats_df: pl.DataFrame) -> tuple[dict[str, dict[int, dict]], dict[int, list[dict]]]:
    """(stats_by_player, stats_by_week), both built from a SINGLE pass over
    stats_df.to_dicts() - build_rows_for_source/enrich_player_week need the
    former, build_no_bid_rows' own week-by-week scan for free-agent
    candidates needs the latter; building each separately would convert the
    same DataFrame to Python dicts twice for no reason."""
    by_player: dict[str, dict[int, dict]] = defaultdict(dict)
    by_week: dict[int, list[dict]] = defaultdict(list)
    for row in stats_df.to_dicts():
        by_player[row["player_id"]][row["week"]] = row
        by_week[row["week"]].append(row)
    return dict(by_player), dict(by_week)


def build_season_index(
    stats_df: pl.DataFrame, injuries_df: pl.DataFrame, snaps_df: pl.DataFrame, rosters_weekly_df: pl.DataFrame
) -> SeasonIndex:
    """Builds every index enrich_player_week/build_no_bid_rows need for one
    season - see SeasonIndex's own docstring for why. A season's worth of
    these DataFrames (a few thousand rows each) is small enough that
    building every index up front is negligible next to the hundreds of
    thousands of per-row lookups it replaces."""
    stats_by_player, stats_by_week = _build_stats_indices(stats_df)
    injury_by_player_week, teammates_by_team_week_pos = build_injury_indices(injuries_df, rosters_weekly_df)
    snap_pct_by_player = build_snap_pct_index(snaps_df)
    team_rb_carries = team_position_totals(stats_df, "RB", "carries")
    return SeasonIndex(stats_by_player, stats_by_week, injury_by_player_week, teammates_by_team_week_pos, snap_pct_by_player, team_rb_carries)


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
        game}, "blackout": {(page_type, season, week): bool}}. week_starts
    is the boundary a rank lookup must stay STRICTLY BEFORE - real leagues
    process FAAB before that week's games even start (see
    pull_o_league_bids.py's own docstring on FAAB timing), so a rank
    snapshot dated on/after kickoff was never actually available to a
    bidder deciding that week. blackout flags a genuine leaguewide "nobody
    at this position has a real weekly rank yet this season" window (see
    _compute_weekly_blackouts) - forward_rank_features consults it to
    substitute ROS rank for a player's own missing weekly rank ONLY during
    a real blackout, not whenever any one player happens to lack one."""
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

    return {"snapshots": dict(snapshots), "week_starts": week_starts, "blackout": _compute_weekly_blackouts(snapshots, week_starts, seasons)}


# How far before/after a season's own week-1 kickoff to look for that
# season's OWN weekly-rank snapshots, when finding the first one - wide
# enough to catch an early preseason weekly snapshot if one exists, and to
# span a full season through its playoffs, but never wide enough to reach
# into a NEIGHBORING season's own snapshots (which would make a genuine
# blackout look like real coverage, or vice versa).
_SEASON_WINDOW_BEFORE_DAYS = 45
_SEASON_WINDOW_AFTER_DAYS = 200


def _compute_weekly_blackouts(snapshots: dict, week_starts: dict, seasons: list[int]) -> dict[tuple, bool]:
    """{(page_type, season, week): True} for every (weekly-{pos}, season,
    week) where NO player at all had a real weekly-{pos} rank snapshot yet
    THIS season, as of that week's own cutoff - a genuine leaguewide
    "FantasyPros hasn't started publishing weekly numbers yet" blackout, not
    one specific player simply missing that week's cheat sheet (which is
    real signal, not a data gap - see forward_rank_features).

    This window varies a lot by season and is NOT a fixed "weeks 1-3"
    guess - confirmed empirically (see the conversation this was built
    from): 2020 had no real weekly-RB snapshot until week 6, 2024 not until
    week 4, other years more like week 2. Computed once here, upfront, for
    every (page_type, season, week) combo the caller might ask about,
    rather than re-scanning every snapshot per lookup."""
    weekly_page_types = {pt for (pt, _fp_id) in snapshots if pt.startswith("weekly-")}

    earliest_this_season: dict[tuple, dt.date] = {}
    for season in seasons:
        season_start = week_starts.get((season, 1))
        if season_start is None:
            continue
        window_start = season_start - dt.timedelta(days=_SEASON_WINDOW_BEFORE_DAYS)
        window_end = season_start + dt.timedelta(days=_SEASON_WINDOW_AFTER_DAYS)
        for page_type in weekly_page_types:
            dates_this_season = [
                d
                for (pt, _fp_id), snaps in snapshots.items()
                if pt == page_type
                for d, _ecr in snaps
                if window_start <= d <= window_end
            ]
            if dates_this_season:
                earliest_this_season[(page_type, season)] = min(dates_this_season)

    blackout: dict[tuple, bool] = {}
    for (season, week), cutoff in week_starts.items():
        for page_type in weekly_page_types:
            first = earliest_this_season.get((page_type, season))
            blackout[(page_type, season, week)] = first is None or cutoff <= first
    return blackout


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
    weekly_page = f"weekly-{pos}"
    weekly_rank = _rank_lookup(rank_index, weekly_page, fp_id, season, week)
    ros_rank = _rank_lookup(rank_index, f"redraft-{pos}", fp_id, season, week)
    # A real leaguewide blackout (see _compute_weekly_blackouts) means this
    # player's own missing weekly rank carries no information at all - EVERY
    # player at this position is unranked right now, stars included, so his
    # ROS rank is the best available stand-in rather than the flat
    # MISSING_RANK_SENTINEL every other blacked-out player would also
    # collapse onto (which would make them all look artificially identical
    # in feature-vector distance regardless of true talent - see the
    # conversation this was built from). Left alone outside a real blackout:
    # one specific player missing the weekly cheat sheet while others at his
    # position DO have one is real signal, not a data gap.
    if weekly_rank is None and ros_rank is not None and rank_index["blackout"].get((weekly_page, season, week), False):
        weekly_rank = ros_rank
    return {"weekly_rank": weekly_rank, "ros_rank": ros_rank}


def _ros_ranked_candidates(rank_index: dict, idmap, position: str, season: int, week: int) -> dict[str, float]:
    """gsis_id -> ROS rank, for every player with a real ROS rank as of this
    week's cutoff at this position - the population build_no_bid_rows widens
    into for players with NO stats row at all that week (a bye, an
    inactive, hasn't debuted yet - see is_faab_relevant's own docstring:
    real historical bids happen on players like this, rank-rescued despite
    zero recent usage, so a comparable "same profile, genuinely no
    interest" no_bid example needs to be able to exist too, not just the
    (usage-bearing) population a stats-row-based scan alone can see)."""
    pos = _RANK_POSITIONS.get(position)
    if pos is None:
        return {}
    page_type = f"redraft-{pos}"
    out: dict[str, float] = {}
    for pt, fp_id in rank_index["snapshots"]:
        if pt != page_type:
            continue
        rank = _rank_lookup(rank_index, page_type, fp_id, season, week)
        if rank is None:
            continue
        record = idmap.by_fp.get(fp_id)
        gsis_id = record.get("gsis_id") if record else None
        if gsis_id:
            out[gsis_id] = rank
    return out


def _points_by_week(gsis_id: str | None, stats_by_player: dict[str, dict[int, dict]], scoring: ScoringRules) -> dict[int, float]:
    """This player's real fantasy points for every week he has a stat row
    THIS season, under the given league's OWN scoring rules - computed once
    per (player, season, league) and reused for both the trailing-2/3 and
    season-to-date windows below. stats_by_player is build_season_index's
    own SeasonIndex.stats_by_player."""
    if not gsis_id:
        return {}
    return {week: scoring.points_for_row(row) for week, row in stats_by_player.get(gsis_id, {}).items()}


def enrich_player_week(
    *,
    gsis_id: str | None,
    position: str | None,
    season: int,
    week: int,
    season_index: SeasonIndex,
    scoring: ScoringRules,
    idmap,
    gsis_to_pfr: dict[str, str],
    rank_index: dict,
) -> dict:
    """Every nflverse-derived field shared by both a real bid row and a
    synthetic no-bid row - factored out so the two code paths can't drift.
    `scoring` is the shared baseline_scoring (main()'s single ScoringRules
    instance) - see module docstring.
    season_index is build_season_index's own output - see that function and
    SeasonIndex's own docstring for why every lookup here goes through a
    pre-built dict instead of a fresh polars .filter() per call."""
    this_week_row = None
    prior_week_row = None
    team_that_week = None
    if gsis_id:
        player_weeks = season_index.stats_by_player.get(gsis_id)
        if player_weeks:
            this_week_row = player_weeks.get(week)
            if this_week_row is not None:
                team_that_week = this_week_row.get("team")
            if week > 1:
                prior_week_row = player_weeks.get(week - 1)
                if prior_week_row is not None and team_that_week is None:
                    team_that_week = prior_week_row.get("team")

    prior_week_actual_points = scoring.points_for_row(prior_week_row) if prior_week_row else None

    own_injury_status = None
    teammate_position_injury_flag = None
    teammate_position_injury_is_new = None
    if gsis_id:
        raw_own_status = season_index.injury_by_player_week.get((gsis_id, week))
        own_injury_status = raw_own_status if raw_own_status in INJURY_FLAG_STATUSES else None
    if team_that_week and position:
        teammates = season_index.teammates_by_team_week_pos.get((team_that_week, week, position), [])
        teammate_position_injury_flag = False
        teammate_position_injury_is_new = False
        for row in teammates:
            teammate_gsis_id = row.get("gsis_id")
            if teammate_gsis_id == gsis_id:
                continue
            if row.get("report_status") not in INJURY_FLAG_STATUSES:
                continue
            teammate_pfr_id = gsis_to_pfr.get(teammate_gsis_id)
            snap_pct = recent_snap_pct(teammate_pfr_id, week, season_index.snap_pct_by_player)
            # Same shared relevance gate the bid TARGET's own row already
            # has to clear (is_faab_relevant) - a real usage bar OR a good
            # enough ROS rank, applied here to the TEAMMATE instead: a
            # committee back with a real, well-known ROS ranking can have a
            # genuinely quiet recent-usage week (bye-adjacent, a timeshare)
            # without that making his injury any less of a real crowding-
            # out event for the backup behind him - see the conversation
            # this was built from. None for prior_points - a teammate's own
            # RECENT POINTS aren't computed here (only his snap share and
            # rank are already in hand at this point in the loop), so usage_
            # ok falls back to snap_pct alone, same as the previous version,
            # while rank_ok is a genuinely new second path.
            teammate_ros_rank = forward_rank_features(teammate_gsis_id, position, season, week, idmap, rank_index)["ros_rank"]
            if not is_faab_relevant(None, snap_pct, teammate_ros_rank, position):
                continue
            teammate_position_injury_flag = True
            # "New" this week specifically for THIS teammate - was he ALSO
            # flagged (real practice-report status or the synthetic RESERVE
            # tag - see build_injury_indices) the week before, or does his
            # own trail start right here? Anecdotally, FAAB bids on the
            # newly-relevant backup spike hardest the very first week a
            # starter goes down (maximum uncertainty, everyone bidding at
            # once) and cool off once the market's had a week to price the
            # backup in - see the conversation this was built from. No
            # `break` here (unlike the old version) - keeps scanning every
            # qualifying teammate rather than stopping at the first, so a
            # SECOND teammate's own fresh injury isn't missed just because
            # an earlier one in the list happened to be an old, ongoing one.
            if season_index.injury_by_player_week.get((teammate_gsis_id, week - 1)) not in INJURY_FLAG_STATUSES:
                teammate_position_injury_is_new = True

    # Most recent of week-1/week-2 that has a real row - see recent_snap_pct
    # (the SAME function, and the same fallback, the LIVE query's own
    # snap_pct_prior_week uses). An earlier version tracked week-1 and
    # week-2 as two separate, non-falling-back fields here, which meant a
    # real bye in the immediate prior week produced None (later defaulting
    # to a misleading 0.0) even when week-2 had real data sitting right
    # there on the same row - a genuine train/predict mismatch, since the
    # live path already fell back in this exact situation. See the
    # conversation this was built from.
    pfr_id = gsis_to_pfr.get(gsis_id) if gsis_id else None
    snap_pct_prior = recent_snap_pct(pfr_id, week, season_index.snap_pct_by_player)

    points_by_week = _points_by_week(gsis_id, season_index.stats_by_player, scoring)
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
        "teammate_position_injury_is_new": teammate_position_injury_is_new,
        "snap_pct_prior_week": snap_pct_prior,
        "trailing_2_3_avg_points": trailing_2_3_avg_points,
        "season_avg_points": season_avg_points,
        "carry_share_prior_week": recent_carry_share(gsis_id, week, season_index.stats_by_player, season_index.team_rb_carries),
        "target_share_prior_week": recent_target_share(gsis_id, week, season_index.stats_by_player),
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
    season_index_by_season: dict[int, SeasonIndex],
    rank_index: dict,
) -> list[dict]:
    """One row per (season, week, player) where the player was a genuine
    free agent that week in THIS league (per pull_weekly_rosters.py's /
    pull_public_league_rosters.py's actual weekly rosters, NOT an
    assumption), nobody placed any bid on him, and engine.faab_estimate.
    is_faab_relevant says his prior-week usage OR ROS rank cleared the bar
    to plausibly have been considered. Week 1 of each season is skipped -
    there's no prior week within the season to judge relevance from.

    Candidates come from TWO populations, scanned separately: every player
    with a real stats row for the prior week (whatever his own usage was),
    and - see _ros_ranked_candidates - every ROS-ranked player at each
    position who has NO stats row at all that week (a bye, an inactive,
    hasn't debuted yet), so a good-enough ROS rank alone can still surface
    him too. Without the second scan, the historical no_bid population
    could never represent "a well-ranked player who was genuinely quiet and
    drew no interest" - even though real bid rows for that exact shape of
    situation exist (a rank-rescued player with no recent usage), leaving
    no comparable "no" example to weigh a real "yes" against.

    Generalized to any league with a weekly-roster snapshot file, not just
    The O League - see module docstring's WHY POOLING NOW EXTENDS TO THE
    INTEREST STAGE. Caller is responsible for only calling this when
    rostered_by_week data actually exists for league_id (main() checks).

    TIER 1 LEAGUE-SEASON VALIDITY GATE: a season with a roster pull but ZERO
    real transactions anywhere in `bids` never generates no_bid rows at all
    (see seasons_with_activity below) - see the conversation this was built
    from (2026-09-21). Without this, a league that folded mid-season, or
    whose commissioner just never processed FAAB that year, would still
    contribute a full season of synthetic "nobody bid on this" rows to the
    interest stage - every single one backed by real roster data but ZERO
    real "someone bid" evidence to weigh against them, which teaches the
    model "this exact usage profile never gets bid on" from what's actually
    an inactive market, not a real demonstrated lack of interest. Deliberately
    permissive for now (ANY real transaction that season - won, outbid,
    other_failure, or even an uncontested FREEAGENT pickup - counts as
    "active"), not a bid-rate or participation threshold - see the
    conversation this was built from: easy to tighten later once a real
    stricter bar is worth the complexity, hard to walk back if an overly
    strict one silently drops real seasons nobody actually reviewed missing."""
    existing_keys = {(r["season"], r["week"], r["add_player_id"]) for r in bids}
    seasons_with_activity = {r["season"] for r in bids}

    out = []
    for season_str, weeks in rostered_by_week.items():
        season = int(season_str)
        if season not in season_index_by_season:
            continue
        if season not in seasons_with_activity:
            continue  # Tier 1 gate - see this function's own docstring
        season_index = season_index_by_season[season]

        for week_str, rostered_ids in weeks.items():
            week = int(week_str)
            if week <= 1:
                continue
            rostered_set = set(rostered_ids)

            def _try_add_no_bid_row(gsis_id, position, prior_points, snap_pct, ros_rank):
                if not is_faab_relevant(prior_points, snap_pct, ros_rank, position):
                    return  # not relevant enough to plausibly have been considered
                record = idmap.by_gsis.get(gsis_id)
                espn_id = record.get("espn_id") if record else None
                if espn_id is None:
                    return  # can't cross-check against the rostered set without an ESPN id
                if espn_id in rostered_set:
                    return  # actually owned by a fantasy team that week, not a free agent
                if (season, week, espn_id) in existing_keys:
                    return  # already has a real bid row this week

                enriched = enrich_player_week(
                    gsis_id=gsis_id, position=position, season=season, week=week,
                    season_index=season_index,
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

            prior_rows = season_index.stats_by_week.get(week - 1, [])
            gsis_ids_with_stat_row: set[str] = set()
            for row in prior_rows:
                position = row.get("position")
                if position not in RELEVANT_POSITIONS:
                    continue
                gsis_id = row.get("player_id")
                gsis_ids_with_stat_row.add(gsis_id)
                prior_points = scoring.points_for_row(row)
                snap_pct = recent_snap_pct(gsis_to_pfr.get(gsis_id), week, season_index.snap_pct_by_player)
                ros_rank = forward_rank_features(gsis_id, position, season, week, idmap, rank_index)["ros_rank"]
                _try_add_no_bid_row(gsis_id, position, prior_points, snap_pct, ros_rank)

            # Widen to players with NO stats row at all this week - a bye, an
            # inactive, hasn't debuted yet - who is_faab_relevant can still
            # rank-rescue. Without this, the historical no_bid population
            # could never represent "a well-ranked player who was genuinely
            # quiet and drew no interest," even though real bid rows for
            # that exact profile do exist (see is_faab_relevant's own
            # docstring) - the model would have real "yes" examples for this
            # shape of situation but no comparable "no" ones to weigh them
            # against. gsis_ids_with_stat_row is excluded since those
            # players' own real usage already decided the question above.
            for position in RELEVANT_POSITIONS:
                for gsis_id, ros_rank in _ros_ranked_candidates(rank_index, idmap, position, season, week).items():
                    if gsis_id in gsis_ids_with_stat_row:
                        continue
                    snap_pct = recent_snap_pct(gsis_to_pfr.get(gsis_id), week, season_index.snap_pct_by_player)
                    _try_add_no_bid_row(gsis_id, position, None, snap_pct, ros_rank)
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
    season_index_by_season: dict[int, SeasonIndex],
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
        if season not in season_index_by_season:
            continue  # no nflverse data loaded for this season - shouldn't happen, but don't crash the whole run over one bad row
        record = idmap.by_espn.get(r["add_player_id"])
        gsis_id = record["gsis_id"] if record else None
        position = record["position"] if record else None
        if record is None:
            unresolved_espn_ids.add(r["add_player_id"])

        enriched = enrich_player_week(
            gsis_id=gsis_id, position=position, season=season, week=week,
            season_index=season_index_by_season[season],
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
    # discover_public_leagues.py) - two independent ways a single pooled
    # league can fail here, neither of which should crash a 100+-league
    # build over one bad apple: (1) real, idiosyncratic scoring categories
    # engine/scoring.py has never needed a mapping for before (e.g. "every N
    # yards" bonus tiers - added live 2026-09 the first time one was hit) -
    # ScoringRules.from_espn fails LOUD on those on purpose (see its own
    # docstring), raising ValueError; (2) a league that was public when
    # discover_public_leagues.py/vet_candidates.py last checked it has since
    # gone private or been deleted (a commissioner changed settings, the
    # league folded) - load_scoring_rules_for_league's own settings fetch
    # then fails, raising RuntimeError (confirmed live 2026-09-16: league
    # 112132, "CFL", vetted as compatible earlier, came back
    # exists_but_private(401) on a real build run months later). Skip that
    # ONE league (and all its bids, below) with a clear warning either way,
    # keep going for everyone else. The O League itself is never skipped
    # this way for either failure - if ITS OWN scoring can't be mapped, or
    # ITS OWN settings can't be fetched, that's a real bug (or a credentials
    # problem) worth crashing loudly for, not silently dropping our own
    # league's data.
    scoring_by_league: dict[int, ScoringRules] = {}
    excluded_leagues: set[int] = set()
    for lid in {r["source_league_id"] for r in all_bids}:
        print(f"loading scoring rules for league {lid} ({provenance.get(lid, {}).get('name')!r})...")
        try:
            scoring_by_league[lid] = load_scoring_rules_for_league(lid)
        except (ValueError, RuntimeError) as exc:
            if lid == O_LEAGUE_ID:
                raise
            print(f"  WARNING: skipping league {lid} entirely - {exc}", file=sys.stderr)
            excluded_leagues.add(lid)

    if excluded_leagues:
        all_bids = [r for r in all_bids if r["source_league_id"] not in excluded_leagues]
        other_bids = [r for r in other_bids if r["source_league_id"] not in excluded_leagues]
        print(f"excluded {len(excluded_leagues)} league(s) with unmappable scoring or no-longer-fetchable settings: {sorted(excluded_leagues)}")

    bidder_counts = compute_bidder_counts(all_bids)
    team_season_spend, league_season_spend = compute_spend_denominators(all_bids)

    # ONE scoring standard, shared by every row regardless of source league -
    # see the module docstring's WHY EVERY ROW'S POINTS-BASED FEATURES USE ONE
    # SHARED BASELINE SCORING. scoring_by_league above stays a per-league
    # data-integrity check only; this is what actually computes every row's
    # prior_week_actual_points/trailing_2_3_avg_points/season_avg_points.
    baseline_scoring = ScoringRules.from_espn(load_baseline_scoring_items())

    # Load each season's nflverse data ONCE, not per-row - shared across
    # every league, since these describe real NFL players, not any one
    # fantasy league. Immediately indexed (see build_season_index/
    # SeasonIndex) rather than kept as raw DataFrames - enrich_player_week/
    # build_no_bid_rows do hundreds of thousands of per-player-week lookups
    # against this data, and a fresh polars .filter() per lookup was, by
    # far, the single biggest cost in this whole script (see the
    # conversation this was built from: ~80% of a real enrich_player_week
    # call's own time was pure polars query-collection overhead, not any
    # actual work).
    season_index_by_season: dict[int, SeasonIndex] = {}
    for season in seasons:
        print(f"loading nflverse data for {season}...")
        season_index_by_season[season] = build_season_index(
            nfl.load_player_stats([season]), nfl.load_injuries([season]), nfl.load_snap_counts([season]),
            nfl.load_rosters_weekly([season]),
        )

    print("building forward-looking rank index (FantasyPros historical consensus ranks)...")
    rank_index = build_rank_index(seasons)

    all_unresolved: set[int] = set()
    all_out_rows: list[dict] = []
    for lid, bids_group in [(lid, [r for r in all_bids if r["source_league_id"] == lid]) for lid in {r["source_league_id"] for r in all_bids}]:
        rows, unresolved = build_rows_for_source(
            bids=bids_group, provenance=provenance[lid], idmap=idmap, gsis_to_pfr=gsis_to_pfr,
            scoring=baseline_scoring, season_index_by_season=season_index_by_season,
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
            continue  # failed the scoring_by_league data-integrity check above - see main()'s comment there
        league_bids = [r for r in other_bids if r["source_league_id"] == lid]
        no_bid_sources.append((lid, league_bids, rostered_by_week))

    no_bid_rows: list[dict] = []
    for lid, league_bids, rostered_by_week in no_bid_sources:
        rows = build_no_bid_rows(
            league_id=lid, bids=league_bids, rostered_by_week=rostered_by_week,
            idmap=idmap, gsis_to_pfr=gsis_to_pfr, scoring=baseline_scoring,
            season_index_by_season=season_index_by_season,
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

    write_json(OUT_PATH, [r for r in all_out_rows if r["source_league_id"] == O_LEAGUE_ID], indent=2)
    # write_parquet, not write_json - see COMBINED_OUT_PATH's own comment.
    # Written atomically the same way write_json is (temp file + rename),
    # so a run that dies partway through never leaves a truncated file that
    # a concurrent fetch_release_data.py could pick up mid-write.
    combined_tmp = COMBINED_OUT_PATH.with_suffix(COMBINED_OUT_PATH.suffix + ".part")
    pl.DataFrame(all_out_rows).write_parquet(combined_tmp)
    combined_tmp.replace(COMBINED_OUT_PATH)

    print(f"\nwrote {sum(1 for r in all_out_rows if r['source_league_id'] == O_LEAGUE_ID)} rows to {OUT_PATH}")
    print(f"wrote {len(all_out_rows)} rows ({len({r['source_league_id'] for r in all_out_rows})} leagues) to {COMBINED_OUT_PATH}")

    # {espn_id: name} for every unresolved id, not just a bare count - the
    # name comes straight off the bid row's own add_player_name (ESPN's own
    # spelling), the same field idmap.by_espn failed to match against, so
    # this is exactly the raw material needed to spot a real name-spelling/
    # crosswalk mismatch (see the conversation this was built from) rather
    # than re-deriving player identity from scratch to investigate one.
    unresolved_names: dict[int, str] = {}
    for r in all_bids:
        eid = r["add_player_id"]
        if eid in all_unresolved and eid not in unresolved_names:
            unresolved_names[eid] = r["add_player_name"]
    write_json(UNRESOLVED_PLAYERS_PATH, unresolved_names, indent=2, sort_keys=True)
    print(f"unresolved ESPN player ids (no nflverse match) across all leagues: {len(all_unresolved)} - see {UNRESOLVED_PLAYERS_PATH}")
    resolved_count = sum(1 for r in all_out_rows if r["gsis_id"])
    print(f"resolved to a gsis_id: {resolved_count}/{len(all_out_rows)}")
    with_prior_points = sum(1 for r in all_out_rows if r["prior_week_actual_points"] is not None)
    print(f"have prior_week_actual_points: {with_prior_points}/{len(all_out_rows)}")


if __name__ == "__main__":
    main()
