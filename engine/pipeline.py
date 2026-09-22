"""Orchestrates ingestion + engine computation per configured league and
writes docs/data/<slug>/*.json plus the top-level docs/data/leagues.json."""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from engine.curve import Curve, blend_current, build_curve, build_dst_curve
from engine.faab_estimate import (
    INJURY_FLAG_STATUSES,
    POOLED_TRAINING_TABLE_PATH,
    TEAMMATE_INJURY_FLAG_STATUSES,
    FaabModel,
    build_gsis_to_pfr_map,
    build_injury_indices,
    build_snap_counts_index,
    build_snap_pct_index,
    build_stats_index,
    is_faab_relevant,
    recent_carry_share,
    recent_snap_pct,
    recent_target_share,
    team_position_totals,
    team_snap_totals,
)
from engine.matchups import (
    allowed_by_team_week_pos,
    compute_matchup_index,
    compute_schedule_strength,
    dst_points_by_team_week_pos,
    points_by_team_week_pos,
    team_weeks_from_opponent,
)
from engine.play_log import (
    build_game_play_index, game_durations_by_game_id, incomplete_targets_for_player,
    scoring_plays_for_player, zero_point_plays_for_player,
)
from engine.points_against import dst_points_against_detail, points_against_detail
from engine.scoring import ScoringRules
from engine.standings import (
    PlayedMatchup,
    RemainingMatchup,
    SeedConfig,
    SeedTeamState,
    TeamState,
    compute_current_seeds,
    matchup_win_probability,
    simulate_playoffs,
)
from engine.team_strength import (
    PlayerCtx,
    depth_values_by_week,
    fa_values,
    lineup_total,
    optimal_lineup_for_week,
    optimal_lineup_for_week_with_bye_fill,
    position_strength,
    rank_and_compare,
    slot_strength,
    trade_targets,
)
from engine.valuation import project_player
from ingest import ids as ids_mod
from ingest import nfl_data as nd
from ingest import rankings as rk
from ingest.config import load_all_league_configs
from ingest.base import Matchup
from ingest.espn_client import EspnClient
from ingest.espn_injuries import EspnInjuriesFetchError, fetch_ir_return_weeks
from ingest.espn_scoreboard import fetch_remaining_game_fraction
from ingest.settings_sheet import SettingsSheetError, apply_remote_settings, fetch_remote_settings, parse_seeding_config
from ingest.weather import fetch_game_weather

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_IR_SLOTS = {"IR"}

# ESPN's raw box-score lineupSlot strings -> the same base labels
# engine/lineup.py's display_label uses for computed-lineup slot keys, so a
# real past-week ESPN slot and a computed-optimal slot instance line up under
# one label space in the frontend (mirrors docs/js/schedule.js's slotBase).
_PAST_LINEUP_SLOT_BASE = {"D/ST": "DST", "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX", "TQB": "QB"}
_PAST_LINEUP_BENCH_SLOTS = {"BE", "IR", "", None}

# Raw actual stat lines for the player-detail modal's game log (position-aware
# columns are a frontend concern - this just exposes everything relevant).
_OFFENSE_STAT_FIELDS = [
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "receptions", "targets", "receiving_yards", "receiving_tds",
    "fumbles_lost_total", "special_teams_tds",
    "fg_made_0_19", "fg_made_20_29", "fg_made_30_39", "fg_made_40_49", "fg_made_50_59", "fg_made_60_", "pat_made",
]
_DST_STAT_FIELDS = [
    "def_sacks", "def_interceptions", "fumble_recovery_opp", "def_tds", "def_safeties", "special_teams_tds",
    "points_allowed", "blocked_kicks",
]

# nflverse's player_stats only includes a row for a player who recorded at
# least one offensive stat that week - a player who was active but drew zero
# targets/carries/etc. (e.g. Mike Gesicki, 2026 wk2: active, zero targets)
# has NO row at all, indistinguishable from a bye/inactive/practice-squad
# week unless cross-checked against his own roster status. "ACT" on
# nflverse's weekly roster status is the closest available signal for
# "should count as a real 0", not "didn't play" - see _actual_weekly_stats.
ACTIVE_ROSTER_STATUS = "ACT"


def _actual_stats_row(row: dict, fields: list[str]) -> dict:
    stats = {f: row.get(f) for f in fields}
    stats["two_pt_conversions"] = (
        (row.get("passing_2pt_conversions") or 0)
        + (row.get("rushing_2pt_conversions") or 0)
        + (row.get("receiving_2pt_conversions") or 0)
    )
    return stats


def _actual_weekly_stats(
    position, nfl_team, pid, week, weeks_played_, offense_lookup, dst_lookup, active_lookup, opponent, player_rules
):
    """Real (not projected) stat line + points for one already-played week -
    powers both the Game Log and every FAAB "prior week"/season-average
    feature (see run_league's own weekly[] comment). None means a genuinely
    missing week (future, bye, inactive, practice squad); a played week with
    zero recorded production is a real 0, not None - see active_lookup."""
    if week > weeks_played_:
        return None
    if position == "DST":
        row = dst_lookup.get((nfl_team, week))
        if not row:
            return None
        stats = _actual_stats_row(row, _DST_STAT_FIELDS)
        stats["xpr"] = None  # not available from nflverse - see engine/points_against.py
        return {"stats": stats, "points": row["_points"]}
    row = offense_lookup.get((pid, week))
    if row:
        return {"stats": _actual_stats_row(row, _OFFENSE_STAT_FIELDS), "points": player_rules.points_for_row(row)}
    if opponent.get((nfl_team, week)) is not None and (pid, week) in active_lookup:
        # Active roster status, team actually played that week (not a bye),
        # but no player_stats row at all - a real 0 (e.g. zero targets), not
        # a missing week the way a bye/inactive/practice-squad week
        # legitimately is.
        zero_row = {f: 0 for f in _OFFENSE_STAT_FIELDS}
        return {"stats": _actual_stats_row(zero_row, _OFFENSE_STAT_FIELDS), "points": 0.0}
    return None


def _id_lookup(ranks_df, id_map: ids_mod.IdMap) -> dict[str, dict]:
    """canonical id -> FantasyPros ranking row, for either the ROS or weekly frame."""
    lookup = {}
    for row in ranks_df.iter_rows(named=True):
        res = id_map.resolve(fp_id=row.get("fp_id"), name=row["name"], pos=row["pos"], team=row.get("team"))
        lookup[res.id] = row
    return lookup


def _positional_ranks_from_overall(players: list[dict]) -> None:
    """Recomputes each player's ros_pos_rank IN PLACE, from where they land
    among same-position players within ros_overall_rank's own order - not
    fetch_ros's independent per-position page (ros-<format>-<pos>.php),
    which draws its own separate expert-vote consensus and doesn't have to
    (and often doesn't) nest into the same order as the combined overall
    page. Rankings' primary "Rank" column already shows ros_overall_rank -
    the positional figure shown beside it should be consistent with THAT
    ordering, not silently sourced from a different one.

    A player absent from the overall list (outside FantasyPros' own cutoff
    there) keeps whatever ros_pos_rank fetch_ros already gave them from
    their own position's page - the two lists don't cover equal depth, and
    losing a positional rank entirely for everyone outside the overall
    Top-N would be a worse regression than leaving them with a figure from
    a slightly different consensus."""
    by_position: dict[str, list[dict]] = {}
    for p in players:
        if p.get("ros_overall_rank") is not None:
            by_position.setdefault(p["position"], []).append(p)
    for plist in by_position.values():
        plist.sort(key=lambda p: p["ros_overall_rank"])
        for i, p in enumerate(plist, start=1):
            p["ros_pos_rank"] = i


def _faab_week_override(current_week: int, week_started: int | None) -> int:
    """FAAB gets its OWN week number, one ahead of ESPN's real current_week -
    but ONLY once current_week's own games have actually KICKED OFF (the
    earliest real kickoff instant that week - see ingest/nfl_data.py's
    week_for_kickoff - not ESPN's scoringPeriodId, which doesn't flip until
    the whole week is over). Deliberately NOT current_week + 1 unconditionally:
    once ESPN's own current_week catches up to what this already bumped to,
    it correctly stops advancing further until THAT week's games start in
    turn. State machine: current_week=1 mid-week -> returns 2; ESPN then
    flips to 2 before week 2's games start -> still returns 2 (week_started
    is still 1, not yet equal to current_week); week 2's games start ->
    returns 3. Deliberately scoped to the FAAB estimate call only -
    current_week itself, and everything derived from it (Rankings, Start/
    Sit's default week, projections), stays on ESPN's real value untouched."""
    return current_week + 1 if week_started == current_week else current_week


def _compute_faab_estimates(
    cfg: dict, client: EspnClient, players_out: list[dict], season: int, current_week: int,
    team_rosters: dict[int, list[str]], fa_values_out: dict[str, dict[str, float]],
    snap_pct_index: dict[str, dict[int, float]], gsis_to_pfr: dict[str, str],
    stats_index: dict[str, dict[int, dict]], team_rb_carries: dict[tuple[str, int], float],
) -> dict[str, dict]:
    """FAAB bid estimates for currently-unrostered players on ESPN "WAIVERS"
    status (need a real bid, unlike an instant-add "FREEAGENT") - see
    engine/faab_estimate.py for the three estimation methods and
    tools/faab_history/ for how the historical training data was built.
    Opt-in per league via faab_model.enabled in config/leagues/<slug>.yml -
    the historical dataset only exists for The O League.

    current_week here is NOT necessarily ESPN's real current_week - the
    caller passes _faab_week_override's result, which runs one week ahead
    once the real current week's own games have started (see that
    function). That's intentional: it lets this treat the in-progress
    week's own partial stats as "prior week" data, giving an early,
    progressively-filling-in read on the upcoming waiver picture instead of
    waiting for ESPN's own scoringPeriodId to flip once the week is fully over.

    Also attaches team_interest (see below) per candidate - team_rosters
    (team_id -> that team's own player ids, already built earlier in
    run_league for the standings/lineup passes) and fa_values_out (team_id
    str -> {player_id: lineup-point gain if added, from
    engine.team_strength.fa_values - already computed per team for the
    Rankings NMD column) are both already sitting in run_league's scope by
    the time this is called, at no extra computation cost here.
    snap_pct_index/gsis_to_pfr/stats_index/team_rb_carries are ALSO already
    sitting in run_league's scope, built early (before _register's own
    per-player `weekly[]` construction needs them for the Rankings Stats
    tab's usage columns) rather than reloaded a second time here.

    Week 1 is a hard cutoff, not just a quiet edge case: there is no PRIOR
    completed week yet (the lookback window is always current_week - 1, by
    design - real FAAB claims process the Tuesday after a week wraps, using
    that week's now-final results, matching how the training data itself is
    shaped - see build_training_table.py). The historical dataset has no
    no-bid rows for week 1 either, for the same reason. Computing anything
    here in week 1 would only ever match real historical BID comps (nothing
    to represent "nobody wanted this guy" exists yet for week 1), silently
    inflating every single estimate - so don't."""
    if current_week <= 1:
        return {}
    if not cfg.get("faab_model", {}).get("enabled"):
        return {}
    try:
        waiver_espn_ids = client.get_waiver_status_espn_ids()
    except Exception as exc:
        logger.warning("faab_model: failed to fetch waiver-status players: %s", exc)
        return {}
    if not waiver_espn_ids:
        return {}

    by_espn_id = {p["espn_id"]: p for p in players_out}
    candidates = [by_espn_id[eid] for eid in waiver_espn_ids if eid in by_espn_id and by_espn_id[eid]["position"] in {"QB", "RB", "WR", "TE", "K"}]
    if not candidates:
        return {}

    # nflverse's own weekly injury/roster-status history - NOT used to
    # decide whether a teammate is CURRENTLY hurt (that's still ESPN's own
    # live roster injury_status below, the more accurate real-time signal)
    # - only to check whether a qualifying teammate was ALSO flagged the
    # week before, for teammate_position_injury_is_new. See the
    # conversation this was built from: FAAB bids on a newly-relevant
    # backup anecdotally spike hardest the very first week a starter goes
    # down and cool off once the market's had a week to price him in.
    injury_by_player_week, _ = build_injury_indices(
        nd.injuries(season, current_season=season), nd.rosters_weekly(season, current_season=season)
    )

    by_team_pos: dict[tuple[str, str], list[dict]] = {}
    for p in players_out:
        by_team_pos.setdefault((p["nfl_team"], p["position"]), []).append(p)

    def prior_week_actual(p: dict) -> dict | None:
        entry = next((w for w in p["weekly"] if w["week"] == current_week - 1), None)
        return entry["actual"] if entry else None

    def points_by_week(p: dict) -> dict[int, float]:
        """This player's real points for every week he's already played -
        mirrors tools/faab_history/build_training_table.py's
        _points_by_week, same source (weekly actuals), so the live
        trailing_2_3_avg_points/season_avg_points features are computed
        identically to the historical training data."""
        return {w["week"]: w["actual"]["points"] for w in p["weekly"] if w["actual"] is not None}

    def recent_form(p: dict) -> tuple[float | None, float | None]:
        """(trailing_2_3_avg_points, season_avg_points) - same disjoint-
        window definitions as build_training_table.py: weeks (t-2, t-3)
        only for the trailing window, all weeks before t-1 (cumulative) for
        the season average."""
        pts = points_by_week(p)
        trailing_weeks = [wk for wk in (current_week - 2, current_week - 3) if wk >= 1 and wk in pts]
        trailing_2_3_avg_points = (sum(pts[wk] for wk in trailing_weeks) / len(trailing_weeks)) if trailing_weeks else None
        season_weeks = [wk for wk in pts if wk < current_week]
        season_avg_points = (sum(pts[wk] for wk in season_weeks) / len(season_weeks)) if season_weeks else None
        return trailing_2_3_avg_points, season_avg_points

    # The pooled, scoring-ledger-filtered multi-league table, not just The O
    # League's own history - confirmed in backtesting to lower held-out
    # error 45-52% over the O-League-only table (see the conversation this
    # was built from), and the only way any league OTHER than The O League
    # gets real comps at all, since none of them have their own FAAB
    # history to train on.
    model = FaabModel(POOLED_TRAINING_TABLE_PATH)

    # A genuine leaguewide "FantasyPros hasn't published ANY real weekly
    # rank for this position yet this week" blackout - the live analog of
    # tools/faab_history/build_training_table.py's _compute_weekly_blackouts.
    # Checked against the FULL players_out population (not just this week's
    # waiver candidates), since it's a fact about whether the rankings
    # SOURCE has data yet, not about who happens to be on waivers. Used
    # ONLY below, for the FAAB query's own weekly_rank input - deliberately
    # never touches p["week_pos_rank"] itself, which project_player's
    # current-week valuation logic also reads and has its own, unrelated,
    # already-established behavior for a missing weekly rank.
    position_weekly_blackout = {
        pos: not any(p["week_pos_rank"] is not None for p in players_out if p["position"] == pos)
        for pos in {"QB", "RB", "WR", "TE", "K"}
    }

    def faab_weekly_rank(p: dict) -> float | None:
        """This candidate's weekly rank for FAAB purposes only - substitutes
        his own ROS rank when NO player at this position has a real weekly
        rank yet this week (see position_weekly_blackout above), leaving a
        real "just not on the weekly cheat sheet while others are" gap
        alone otherwise - see the conversation this was built from (Dylan
        Laube, 2026 wk2, and Carson Steele/Jordan Mason/Isaac Guerendo, all
        real historical comps whose OWN weekly rank was missing purely
        because their season's weekly rankings hadn't started publishing
        yet, not because they were individually obscure)."""
        wk = p.get("week_pos_rank")
        if wk is not None:
            return wk
        if position_weekly_blackout.get(p["position"]) and p.get("ros_pos_rank") is not None:
            return p["ros_pos_rank"]
        return None

    # First pass: compute every candidate's own inputs, INCLUDING relevance,
    # before any per-player estimate.
    per_player: dict[str, dict] = {}
    for p in candidates:
        prior_actual = prior_week_actual(p)
        pfr_id = gsis_to_pfr.get(p["id"])

        # Every OTHER same-team/same-position player with a real injury and
        # relevant recent snap share - both the market-wide teammate_flag
        # feature (below) and the per-team handcuff attribution (team_
        # interest, further down) are keyed off this SAME qualifying set, so
        # it's computed once here instead of twice (once per purpose).
        # is_faab_relevant is the SAME shared gate a bid TARGET's own row
        # already has to clear - a real usage bar OR a good enough ROS rank
        # - applied here to the TEAMMATE instead: a committee back with a
        # real, well-known ROS ranking can have a genuinely quiet recent-
        # usage week without that making his injury any less of a real
        # crowding-out event for the backup behind him. See the
        # conversation this was built from.
        qualifying_mates = []
        for mate in by_team_pos.get((p["nfl_team"], p["position"]), []):
            if mate["id"] == p["id"] or mate.get("injury_status") not in TEAMMATE_INJURY_FLAG_STATUSES:
                continue
            mate_snap_pct = recent_snap_pct(gsis_to_pfr.get(mate["id"]), current_week, snap_pct_index)
            if is_faab_relevant(None, mate_snap_pct, mate.get("ros_pos_rank"), mate["position"]):
                qualifying_mates.append(mate)

        # Was ANY qualifying mate ALSO flagged (by nflverse's own weekly
        # data - see injury_by_player_week above) the week before, or is
        # this genuinely the first week his absence shows up at all? See
        # tools/faab_history/build_training_table.py's identical historical
        # feature for the full reasoning.
        teammate_injury_is_new = any(
            injury_by_player_week.get((mate["id"], current_week - 1)) not in INJURY_FLAG_STATUSES for mate in qualifying_mates
        )

        prior_points = prior_actual["points"] if prior_actual else None
        snap_pct = recent_snap_pct(pfr_id, current_week, snap_pct_index)
        # RB-position-scoped carry share and team-wide (any position) target
        # share - see engine.faab_estimate.recent_carry_share/recent_target_
        # share. Computed for every candidate regardless of position (cheap,
        # and matches how snap_pct is computed unconditionally too) - the UI
        # decides which to actually show based on the player's own position.
        carry_share = recent_carry_share(p["id"], current_week, stats_index, team_rb_carries)
        target_share = recent_target_share(p["id"], current_week, stats_index)
        trailing_2_3_avg_points, season_avg_points = recent_form(p)

        # Same relevance bar the historical no_bid rows had to clear to even
        # be included as training data (see engine.faab_estimate.
        # is_faab_relevant, the ONE shared gate both this file and
        # tools/faab_history/build_training_table.py call) - a player below
        # both usage bars AND without a good enough ROS rank isn't
        # represented by any comparable "genuinely uninteresting" comp in
        # the training set either, so running them through the model would
        # just extrapolate from whatever's nearest and silently inflate a
        # should-be-$0 case.
        is_relevant = is_faab_relevant(prior_points, snap_pct, p.get("ros_pos_rank"), p["position"])
        per_player[p["id"]] = {
            "player": p, "prior_actual": prior_actual, "prior_points": prior_points,
            "snap_pct": snap_pct, "carry_share": carry_share, "target_share": target_share,
            "teammate_flag": bool(qualifying_mates), "teammate_injury_is_new": teammate_injury_is_new,
            "qualifying_mates": qualifying_mates,
            "trailing_2_3_avg_points": trailing_2_3_avg_points, "season_avg_points": season_avg_points,
            "is_relevant": is_relevant,
        }

    # team_interest: "which of the OTHER owners in the league would actually
    # want this guy, and why" - the two things a single market-wide FAAB
    # estimate can't tell you (see the conversation this was built from). A
    # Low/Med/High bucket rather than a raw number on purpose - fa_values'
    # gain is real lineup points, but "is 3.2 points a lot" only means
    # anything relative to this week's actual spread of add-value across the
    # league, which varies by scoring format/roster construction - so bucket
    # by PERCENTILE of this week's own real gain distribution instead of a
    # fixed constant that would drift out of calibration league to league.
    all_positive_gains = sorted(v for gains in fa_values_out.values() for v in gains.values() if v is not None and v > 0)

    def gain_level(gain: float | None) -> str | None:
        if not all_positive_gains or gain is None or gain <= 0:
            return None
        pct = bisect.bisect_right(all_positive_gains, gain) / len(all_positive_gains)
        if pct >= 0.85:
            return "high"
        if pct >= 0.5:
            return "medium"
        return "low"

    def team_interest_for(p: dict, qualifying_mates: list[dict]) -> list[dict]:
        # Handcuff attribution reads straight off each qualifying mate's own
        # fantasy_team_id (already resolved onto every players_out record) -
        # team_rosters is only used here for the canonical set of team ids
        # to evaluate (including ones with no roster-tie to this player at
        # all, who still get a gain-only row below).
        handcuff_owner_ids = {mate.get("fantasy_team_id") for mate in qualifying_mates if mate.get("fantasy_team_id") is not None}
        rows = []
        for team_id in team_rosters:
            gain = fa_values_out.get(str(team_id), {}).get(p["id"])
            is_handcuff = team_id in handcuff_owner_ids
            level = "high" if is_handcuff else gain_level(gain)
            if level is None:
                continue
            handcuff_name = next((m["name"] for m in qualifying_mates if m.get("fantasy_team_id") == team_id), None)
            rows.append({"team_id": team_id, "level": level, "nmd_gain": gain, "handcuff_of": handcuff_name})
        level_rank = {"high": 0, "medium": 1, "low": 2}
        rows.sort(key=lambda r: (level_rank[r["level"]], -(r["nmd_gain"] or 0)))
        return rows

    estimates: dict[str, dict] = {}
    for pid, info in per_player.items():
        p = info["player"]
        prior_actual, prior_points, snap_pct = info["prior_actual"], info["prior_points"], info["snap_pct"]
        carry_share, target_share = info["carry_share"], info["target_share"]
        teammate_flag = info["teammate_flag"]
        teammate_injury_is_new = info["teammate_injury_is_new"]
        trailing_2_3_avg_points, season_avg_points = info["trailing_2_3_avg_points"], info["season_avg_points"]
        # Roster-fit context is orthogonal to whether the broader MARKET
        # should bid - a below-threshold player can still be a real personal
        # handcuff stash for one specific owner, so this is computed and
        # attached regardless of is_relevant below.
        team_interest = team_interest_for(p, info["qualifying_mates"])

        if not info["is_relevant"]:
            estimates[pid] = {
                "bid_probability": {"comp_based_mean": 0.0, "comp_based_median": 0.0, "regression": 0.0},
                "conditional_price": {"comp_based_mean": 0.0, "comp_based_median": 0.0, "regression": 0.0},
                "comps": [], "interest_comps": [], "distribution": None,
                "below_relevance_threshold": True,
                "team_interest": team_interest,
                "inputs": {
                    "position": p["position"], "week": current_week,
                    "prior_week_actual_points": prior_points, "prior_week_had_stat_row": prior_actual is not None,
                    "own_injury_flag": p.get("injury_status") in TEAMMATE_INJURY_FLAG_STATUSES,
                    "teammate_position_injury_flag": teammate_flag, "teammate_position_injury_is_new": teammate_injury_is_new,
                    "snap_pct_prior_week": snap_pct,
                    "trailing_2_3_avg_points": trailing_2_3_avg_points, "season_avg_points": season_avg_points,
                    "weekly_rank": faab_weekly_rank(p), "ros_rank": p.get("ros_pos_rank"),
                    "carry_share_prior_week": carry_share, "had_carry_share_prior_week": carry_share is not None,
                    "target_share_prior_week": target_share, "had_target_share_prior_week": target_share is not None,
                },
            }
            continue

        query = {
            "position": p["position"],
            "week": current_week,
            "prior_week_actual_points": prior_points,
            "prior_week_had_stat_row": prior_actual is not None,
            "own_injury_status": p.get("injury_status") if p.get("injury_status") in TEAMMATE_INJURY_FLAG_STATUSES else None,
            "teammate_position_injury_flag": teammate_flag,
            "teammate_position_injury_is_new": teammate_injury_is_new,
            "snap_pct_prior_week": snap_pct,
            "trailing_2_3_avg_points": trailing_2_3_avg_points,
            "season_avg_points": season_avg_points,
            # Live FantasyPros positional ranks - already fetched for the
            # whole roster/free-agent pool earlier in this pipeline run (see
            # ros_lookup/weekly_lookup above), same ECR-style rank number as
            # tools/faab_history/build_training_table.py's forward_rank_
            # features computes historically (lower = better; None if
            # FantasyPros doesn't rank this player at all this week).
            "weekly_rank": faab_weekly_rank(p),
            "ros_rank": p.get("ros_pos_rank"),
            "carry_share_prior_week": carry_share,
            "target_share_prior_week": target_share,
        }
        estimates[pid] = model.estimate(query) | {"team_interest": team_interest}

    return estimates


def _build_curve_for_league(cfg: dict, offense_positions: list[str], player_rules: ScoringRules, weeks_played: int, season: int) -> Curve:
    val_cfg = cfg["valuation"]
    curve_seasons = val_cfg["curve_seasons"]
    starter_pool = val_cfg["starter_pool"]

    hist_by_season = {s: nd.player_stats(s) for s in curve_seasons}
    hist_curve = build_curve(hist_by_season, player_rules, val_cfg["curve_min_games"], offense_positions, starter_pool)

    if weeks_played <= 0:
        return hist_curve

    current_stats = nd.player_stats(season, current_season=season)
    cur_min_games = max(1, min(val_cfg["curve_min_games"], weeks_played // 2))
    cur_curve = build_curve({season: current_stats}, player_rules, cur_min_games, offense_positions, starter_pool)
    return blend_current(
        hist_curve, cur_curve, weeks_played,
        max_weight=val_cfg["curve_current_max_weight"], full_weight_weeks=val_cfg["curve_current_full_weight_weeks"],
    )


def _build_dst_curve_for_league(cfg: dict, dst_rules: ScoringRules, weeks_played: int, season: int) -> Curve:
    val_cfg = cfg["valuation"]
    curve_seasons = val_cfg["curve_seasons"]
    starter_pool = val_cfg["starter_pool"].get("DST", 32)

    hist_team_stats = {s: nd.team_stats(s) for s in curve_seasons}
    hist_schedules = {s: nd.schedules(s) for s in curve_seasons}
    hist_curve = build_dst_curve(hist_team_stats, hist_schedules, dst_rules, val_cfg["curve_min_games"], starter_pool)

    if weeks_played <= 0:
        return hist_curve

    current_team_stats = nd.team_stats(season, current_season=season)
    current_schedules = nd.schedules(season, current_season=season)
    cur_min_games = max(1, min(val_cfg["curve_min_games"], weeks_played // 2))
    cur_curve = build_dst_curve({season: current_team_stats}, {season: current_schedules}, dst_rules, cur_min_games, starter_pool)
    return blend_current(
        hist_curve, cur_curve, weeks_played,
        max_weight=val_cfg["curve_current_max_weight"], full_weight_weeks=val_cfg["curve_current_full_weight_weeks"],
    )


def run_league(cfg: dict) -> dict:
    """Runs the full pipeline for one league. Returns {filename: json-serializable-object}."""
    slug = cfg["slug"]
    season = cfg["season"]
    warnings: list[str] = []

    logger.info("=== %s (%s) ===", cfg["name"], slug)

    settings_sheet_id = cfg.get("settings_sheet_id")
    settings_overrides: list[str] = []
    if settings_sheet_id:
        try:
            remote = fetch_remote_settings(settings_sheet_id)
            settings_overrides = apply_remote_settings(cfg, remote.get(slug, {}))
            for change in settings_overrides:
                logger.info("settings sheet override: %s", change)
        except SettingsSheetError as exc:
            logger.warning("could not apply settings sheet overrides: %s", exc)
            warnings.append(f"settings sheet unavailable, using config/leagues/{slug}.yml defaults: {exc}")

    val_cfg = cfg["valuation"]
    strength_cfg = cfg["strength"]
    sim_cfg = cfg["sim"]

    client = EspnClient(cfg["league_id"], season, cfg.get("credentials"))
    settings = client.get_settings()
    # Sort by standings (wins desc, then points_for desc) once, up front -
    # ESPN's own team list order is arbitrary (internal team_id order), and
    # every downstream list/dropdown should reflect this same order.
    espn_teams = sorted(client.get_teams(), key=lambda t: (-t.wins, -t.points_for))

    has_dst = "DST" in settings.positions
    offense_positions = [p for p in settings.positions if p != "DST"]

    player_rules = ScoringRules.from_espn(settings.scoring_items, is_dst=False)
    dst_rules = ScoringRules.from_espn(settings.scoring_items, is_dst=True) if has_dst else None

    # --- rankings ---
    try:
        ros_ranks = rk.fetch_ros(cfg["scoring_format"])
        rankings_source = "fantasypros"
    except rk.RankingsFetchError as exc:
        logger.warning("FantasyPros ROS scrape failed (%s); using fallback", exc)
        warnings.append(f"FantasyPros ROS scrape failed: {exc}")
        ros_ranks = rk.fetch_ros_fallback()
        rankings_source = "dynastyprocess-ppr-fallback"

    try:
        weekly_ranks = rk.fetch_weekly(cfg["scoring_format"])
    except rk.RankingsFetchError as exc:
        logger.warning("FantasyPros weekly scrape failed (%s); using fallback", exc)
        warnings.append(f"FantasyPros weekly scrape failed: {exc}")
        weekly_ranks = rk.fetch_weekly_fallback()

    overall_lookup: dict[str, int] = {}
    try:
        overall_ranks = rk.fetch_ros_overall(cfg["scoring_format"])
    except rk.RankingsFetchError as exc:
        logger.warning("FantasyPros ROS overall scrape failed (%s); falling back to our own ros_total ranking", exc)
        warnings.append(f"FantasyPros ROS overall scrape failed: {exc}")
        overall_ranks = None

    id_map = ids_mod.build_id_map()
    ros_lookup = _id_lookup(ros_ranks, id_map)
    weekly_lookup = _id_lookup(weekly_ranks, id_map)
    if overall_ranks is not None:
        for row in overall_ranks.iter_rows(named=True):
            res = id_map.resolve(fp_id=row["fp_id"], name=row["name"], pos=row["pos"], team=row.get("team"))
            overall_lookup[res.id] = row["overall_rank"]

    # --- schedule / week context ---
    schedules_current = nd.schedules(season, current_season=season)
    weeks_played, bye_weeks, opponent = nd.nfl_week_context(season, schedules_current)
    is_home = nd.home_away_from_schedule(schedules_current, season)
    kickoff = nd.kickoff_utc_from_schedule(schedules_current, season)
    current_week = settings.current_week
    final_week = settings.final_week
    reg_season_count = settings.reg_season_count
    prior_season = season - 1
    weeks = list(range(current_week, final_week + 1))

    # Vegas-implied team totals (from nflverse's own spread_line/total_line -
    # see ingest.nfl_data.game_context_from_schedule) for every week a line
    # has been posted, plus a real hourly forecast (ingest.weather) for
    # outdoor/retractable-roof stadiums, but ONLY for the current week - a
    # forecast for a future week would be silently stale noise (NWS's own
    # hourly horizon is ~7 days) or None anyway, so there's nothing useful to
    # fetch there. One NWS call per unique stadium this week, not per team.
    game_context = nd.game_context_from_schedule(schedules_current, season)
    _weather_by_stadium: dict[str, dict | None] = {}
    weather_by_team: dict[str, dict | None] = {}
    current_week_games = schedules_current.filter(
        (pl.col("season") == season) & (pl.col("game_type") == "REG") & (pl.col("week") == current_week)
    )
    for row in current_week_games.iter_rows(named=True):
        sid = row.get("stadium_id")
        if sid not in _weather_by_stadium:
            _weather_by_stadium[sid] = fetch_game_weather(sid, row.get("roof"), kickoff.get((row["home_team"], current_week)))
        w = _weather_by_stadium[sid]
        weather_by_team[row["home_team"]] = w
        weather_by_team[row["away_team"]] = w

    # Best-effort: espn.com/nfl/injuries' own return-date estimates, used
    # below to zero an IR player's proprietary projection for the weeks
    # before they're expected back (see engine.valuation.project_player's
    # ir_return_week param) rather than just the current week. A failure
    # (retried a few times inside fetch_ir_return_weeks first) surfaces the
    # actual cause in the warning banner, since every IR player then
    # silently falls back to current-week-only zeroing.
    try:
        ir_return_weeks_by_espn_id = fetch_ir_return_weeks(season, schedules_current)
    except EspnInjuriesFetchError as exc:
        logger.warning("espn.com/nfl/injuries fetch failed: %s", exc, exc_info=True)
        warnings.append(f"espn.com/nfl/injuries fetch failed ({exc}); IR players only zeroed for the current week")
        ir_return_weeks_by_espn_id = {}

    # ESPN's own per-week projection for every remaining week, shown
    # alongside our proprietary number as a second, independent data point
    # (see docs/js/playermodal.js's "ESPN wk" column) - never fed into
    # project_player below, which stays our own baseline*matchup method for
    # every week beyond the current one. The current week already has this
    # from espn_projected_week (get_teams()), so only future weeks need it.
    # Best-effort: ~2x(final_week - current_week) extra ESPN requests, so a
    # transient failure here shouldn't take down the whole build.
    try:
        espn_future_projections = client.get_future_espn_projections(weeks[1:])
    except Exception as exc:
        logger.warning("ESPN future-week projections fetch failed: %s", exc, exc_info=True)
        warnings.append(f"ESPN future-week projections fetch failed: {exc}")
        espn_future_projections = {}

    # --- curves ---
    curve = _build_curve_for_league(cfg, offense_positions, player_rules, weeks_played, season)
    if has_dst:
        dst_curve = _build_dst_curve_for_league(cfg, dst_rules, weeks_played, season)
        curve.ppg.update(dst_curve.ppg)
        curve.sd.update(dst_curve.sd)
        curve.max_rank.update(dst_curve.max_rank)
        curve.position_avg.update(dst_curve.position_avg)

    # --- matchup index (offense positions + DST share one code path) ---
    current_stats = nd.player_stats(season, current_season=season)

    # Usage-share raw material (Snap %/Att %/Tgt % - see docs/js's Rankings
    # Stats tab): indexed once here, up front, rather than inside
    # _compute_faab_estimates below (which used to load all of this itself,
    # but only ever runs for the small FAAB-candidate pool AFTER every
    # player's own `weekly[]` is already built by _register - these numbers
    # need to be available WHILE `weekly[]` is built, for every player, not
    # just candidates). current_stats above is the exact same player_stats
    # DataFrame _compute_faab_estimates used to reload a second time as its
    # own `stats_df` - reused here instead of fetching it twice.
    stats_index = build_stats_index(current_stats)
    team_rb_carries = team_position_totals(current_stats, "RB", "carries")
    team_targets = team_position_totals(current_stats, None, "targets")
    snaps_df = nd.snap_counts([season], current_season=season)
    snap_pct_index = build_snap_pct_index(snaps_df)
    offense_snaps_index = build_snap_counts_index(snaps_df)
    team_offense_snaps = team_snap_totals(snaps_df)
    gsis_to_pfr = build_gsis_to_pfr_map(nd.playerids())

    prior_stats = nd.player_stats(prior_season)
    prior_schedules = nd.schedules(prior_season)
    prior_is_home = nd.home_away_from_schedule(prior_schedules, prior_season)
    current_points = points_by_team_week_pos(current_stats, season, offense_positions, player_rules)
    prior_points = points_by_team_week_pos(prior_stats, prior_season, offense_positions, player_rules)
    matchup_positions = list(offense_positions)
    if has_dst:
        current_team_stats = nd.team_stats(season, current_season=season)
        prior_team_stats = nd.team_stats(prior_season)
        current_points.update(dst_points_by_team_week_pos(current_team_stats, schedules_current, season, dst_rules))
        prior_points.update(dst_points_by_team_week_pos(prior_team_stats, prior_schedules, prior_season, dst_rules))
        matchup_positions.append("DST")

    # Actual (not projected) per-week stat lines for weeks already played,
    # keyed for O(1) lookup while building each player's `weekly` array below.
    actual_offense_by_id_week: dict[tuple[str, int], dict] = {}
    gsis_by_pid: dict[str, str] = {}
    for row in current_stats.iter_rows(named=True):
        pid = id_map.resolve(gsis_id=row["player_id"], name=row["player_display_name"], pos=row["position"], team=row["team"]).id
        actual_offense_by_id_week[(pid, row["week"])] = row
        gsis_by_pid[pid] = row["player_id"]

    # Active-roster-but-no-stats-row weeks (see ACTIVE_ROSTER_STATUS above) -
    # keyed the same way as actual_offense_by_id_week so _actual_weekly_stats
    # can tell "genuinely didn't play" apart from "played, just produced
    # nothing player_stats records".
    active_by_id_week: set[tuple[str, int]] = set()
    for row in nd.rosters_weekly(season, current_season=season).iter_rows(named=True):
        if row["status"] != ACTIVE_ROSTER_STATUS or row["position"] not in offense_positions or not row["gsis_id"]:
            continue
        pid = id_map.resolve(gsis_id=row["gsis_id"], name=row["full_name"], pos=row["position"], team=row["team"]).id
        active_by_id_week.add((pid, row["week"]))

    # DST's own weekly output needs the game's final score to compute PA
    # (points allowed) and fantasy points - not present on the team_stats row
    # itself, so pull each week's opponent score in the same way
    # dst_points_by_team_week_pos does.
    actual_dst_by_team_week: dict[tuple[str, int], dict] = {}
    if has_dst:
        current_game_scores = nd.game_scores(schedules_current, season)
        current_team_stats_by_game: dict[tuple[str, str], dict] = {
            (row["game_id"], row["team"]): row for row in current_team_stats.iter_rows(named=True)
        }
        for row in current_team_stats.iter_rows(named=True):
            game = current_game_scores.get(row["game_id"])
            opp_row = current_team_stats_by_game.get((row["game_id"], row["opponent_team"]))
            if game is None or opp_row is None or row["opponent_team"] not in game:
                continue
            points_allowed = game[row["opponent_team"]]
            yards_allowed = (opp_row.get("passing_yards") or 0) + (opp_row.get("rushing_yards") or 0)
            entry = dict(row)
            entry["points_allowed"] = points_allowed
            entry["blocked_kicks"] = (row.get("def_punt_blocks") or 0) + (row.get("def_pat_blocks") or 0) + (row.get("def_fg_blocks") or 0)
            entry["_points"] = dst_rules.dst_points_for_row(row, points_allowed=points_allowed, yards_allowed=yards_allowed)
            actual_dst_by_team_week[(row["team"], row["week"])] = entry

    # Each season's own schedule - who a team played in a given week differs
    # year to year, so the prior-season matchup ratios must be built from the
    # prior season's own opponent map, never the current season's.
    _, prior_byes, prior_opponent = nd.nfl_week_context(prior_season, prior_schedules)

    matchup_index = compute_matchup_index(
        current_points, prior_points, season, prior_season, weeks_played, opponent, prior_opponent, matchup_positions,
        pa_basis=val_cfg["pa_basis"], pa_l5_weight=val_cfg["pa_l5_weight"],
        pa_prior_season_weeks=val_cfg["pa_prior_season_weeks"], index_clamp=tuple(val_cfg["index_clamp"]),
    )

    # --- recent results: actual (not projected) points allowed by position,
    # per team, per week - "Yahoo-style" trailing performance, current season
    # plus prior season for context before/early in the year.
    all_nfl_teams = sorted(set(prior_byes.keys()) | {t for (t, _) in opponent.keys()})
    current_team_weeks = {
        t: [w for w in ws if w <= weeks_played] for t, ws in team_weeks_from_opponent(opponent, all_nfl_teams, list(range(1, final_week + 1))).items()
    }
    prior_all_weeks = sorted({w for (_, w) in prior_opponent.keys()})
    prior_team_weeks = team_weeks_from_opponent(prior_opponent, all_nfl_teams, prior_all_weeks)

    # "Best remaining schedule" per team/position, over the REMAINING regular
    # season and separately over the fantasy playoff weeks - a team already
    # past reg_season_count (mid-playoffs) simply has no "reg" weeks left, so
    # it's absent from that window's rank rather than assigned an arbitrary
    # one (see compute_schedule_strength).
    schedule_strength = compute_schedule_strength(
        matchup_index, opponent, all_nfl_teams, matchup_positions,
        {
            "reg": list(range(current_week, reg_season_count + 1)),
            "playoffs": list(range(reg_season_count + 1, final_week + 1)),
        },
        tuple(val_cfg["index_clamp"]),
    )

    current_allowed = allowed_by_team_week_pos(current_points, opponent, current_team_weeks, matchup_positions)
    prior_allowed = allowed_by_team_week_pos(prior_points, prior_opponent, prior_team_weeks, matchup_positions)

    recent_results = {"current_season": season, "prior_season": prior_season, "by_position": {}}
    for pos in matchup_positions:
        recent_results["by_position"][pos] = {
            team: {
                "current": {str(w): current_allowed.get((team, w, pos), 0.0) for w in current_team_weeks.get(team, [])},
                "prior": {str(w): prior_allowed.get((team, w, pos), 0.0) for w in prior_team_weeks.get(team, [])},
                "pa_factor": matchup_index.pa_factor.get(pos, {}).get(team, 1.0),
            }
            for team in all_nfl_teams
        }

    # Player-level points-against detail (Yahoo-style), current + prior season.
    points_against = {
        pos: {
            "current": points_against_detail(current_stats, season, pos, player_rules, is_home),
            "prior": points_against_detail(prior_stats, prior_season, pos, player_rules, prior_is_home),
        }
        for pos in offense_positions
    }
    if has_dst:
        points_against["DST"] = {
            "current": dst_points_against_detail(current_team_stats, schedules_current, is_home, season, dst_rules),
            "prior": dst_points_against_detail(prior_team_stats, prior_schedules, prior_is_home, prior_season, dst_rules),
        }

    # --- player universe: every rostered player + every fetched free agent ---
    fa_size = {"QB": 40, "RB": 60, "WR": 60, "TE": 40, "K": 32, "DST": 32}
    free_agents_espn = {pos: client.get_free_agents(pos, fa_size.get(pos)) for pos in settings.positions}

    players_out: list[dict] = []
    players_ctx: dict[str, PlayerCtx] = {}
    espn_lineup_slot: dict[str, str] = {}
    ir_ids: set[str] = set()
    unmapped: list[dict] = []

    def _usage_stats_for_week(position, nfl_team, pid, week, weeks_played_):
        """Raw counts (not pre-divided percentages) for the Rankings Stats
        tab's Snap %/Att %/Tgt % columns - the frontend pairs these with
        their team totals to derive BOTH a single-week % and a true season
        split (sum of numerators over weeks played / sum of denominators
        over those same weeks - not an average of weekly percentages, which
        would let a low-snap week count exactly as much as a full game and
        skew the season number). All four None for a week not yet played or
        a DST (no individual snap_counts row exists for the DST construct -
        same "no nflverse stat to source it from" precedent as DST's own
        xpr field above); team_rb_carries specifically stays None (not 0.0)
        for a team-week with zero real RB carries - see
        engine.faab_estimate.recent_carry_share's docstring for why that's
        a real 0/0, not a real 0% share."""
        if week > weeks_played_ or position == "DST":
            return {"offense_snaps": None, "team_offense_snaps": None, "team_rb_carries": None, "team_targets": None}
        pfr_id = gsis_to_pfr.get(pid)
        return {
            "offense_snaps": offense_snaps_index.get(pfr_id, {}).get(week) if pfr_id else None,
            "team_offense_snaps": team_offense_snaps.get((nfl_team, week)),
            "team_rb_carries": team_rb_carries.get((nfl_team, week)),
            "team_targets": team_targets.get((nfl_team, week)),
        }

    def _register(p, fantasy_team_id: int | None):
        res = id_map.resolve(espn_id=p.espn_id, name=p.name, pos=p.position, team=p.nfl_team)
        if res.source == "unmapped":
            unmapped.append({"name": p.name, "pos": p.position, "team": p.nfl_team, "source": "espn"})

        ros_row = ros_lookup.get(res.id)
        weekly_row = weekly_lookup.get(res.id)
        ros_pos_rank = ros_row["ros_pos_rank"] if ros_row else None
        week_pos_rank = weekly_row["week_pos_rank"] if weekly_row else None
        # Applies to any player with an espn.com/nfl/injuries return-date
        # estimate, not just those sitting in a fantasy roster's IR slot -
        # the injury is real regardless of whether a fantasy owner has
        # actually moved them to IR (or rosters them at all).
        ir_return_week = ir_return_weeks_by_espn_id.get(p.espn_id)

        proj = project_player(
            position=p.position, nfl_team=p.nfl_team, ros_pos_rank=ros_pos_rank,
            injury_status=p.injury_status, week_pos_rank=week_pos_rank,
            curve=curve, matchup_index=matchup_index, current_week=current_week, final_week=final_week,
            reg_season_count=reg_season_count, opponent=opponent, cfg=val_cfg,
            espn_week_projection=p.espn_projected_week, ir_return_week=ir_return_week,
        )

        players_ctx[res.id] = PlayerCtx(
            id=res.id, position=p.position, ros_total=proj.ros_total,
            weekly={wp.week: wp.projected for wp in proj.weekly},
        )
        if fantasy_team_id is not None:
            espn_lineup_slot[res.id] = p.lineup_slot or "BE"
            if p.lineup_slot in _IR_SLOTS:
                ir_ids.add(res.id)

        position_avg = curve.position_avg.get(p.position, 0.0)
        # This player's NFL team's own 1-32 "best remaining schedule" rank at
        # his position (see compute_schedule_strength) - None if that window
        # has no weeks left for his team (e.g. already past reg_season_count
        # for "reg"), same as any other not-yet-ranked field on this site.
        reg_schedule_rank = schedule_strength.rank.get("reg", {}).get(p.position, {}).get(p.nfl_team)
        playoff_schedule_rank = schedule_strength.rank.get("playoffs", {}).get(p.position, {}).get(p.nfl_team)
        players_out.append(
            {
                "id": res.id, "espn_id": p.espn_id, "fp_id": ros_row["fp_id"] if ros_row else None,
                "name": p.name, "position": p.position, "nfl_team": p.nfl_team,
                "bye": bye_weeks.get(p.nfl_team), "fantasy_team_id": fantasy_team_id,
                "lineup_slot": p.lineup_slot, "injury_status": p.injury_status,
                "zeroed_this_week": proj.zeroed_this_week, "zero_reason": proj.zero_reason,
                "ros_pos_rank": ros_pos_rank, "rank_ave": ros_row["rank_ave"] if ros_row else None,
                "rank_std": ros_row["rank_std"] if ros_row else None, "week_pos_rank": week_pos_rank,
                "baseline_ppg": proj.baseline_ppg, "ros_total": proj.ros_total, "reg_total": proj.reg_total,
                "playoff_total": proj.playoff_total, "this_week": proj.this_week,
                "reg_schedule_rank": reg_schedule_rank, "playoff_schedule_rank": playoff_schedule_rank,
                "reg_schedule_index": schedule_strength.avg_index.get("reg", {}).get(p.position, {}).get(p.nfl_team),
                "playoff_schedule_index": schedule_strength.avg_index.get("playoffs", {}).get(p.position, {}).get(p.nfl_team),
                "value_delta": None,
                "position_avg_ratio": (proj.baseline_ppg / position_avg) if position_avg else None,
                # project_player only ever projects weeks >= current_week
                # (see its own "5-step ROS valuation" docstring - it has no
                # reason to "project" a week that's already happened), so
                # proj.weekly alone never carries a played week's real stat
                # line. Past weeks (1..current_week-1) are built here
                # separately, straight from real per-week data (opponent/
                # home/kickoff/game_context are already computed off the
                # FULL season schedule, not just the remaining one, so
                # reusing them for a past week is safe) with no projected/
                # sd/espn_projected (nothing to project once it's already
                # happened) - just the real actual, when there is one (None
                # for a bye or a week with no stat row, same as any other
                # week). This is what powers the Game Log tab/chart AND
                # every FAAB "prior week" feature (prior_week_actual_points,
                # trailing_2_3_avg_points, season_avg_points in
                # _compute_faab_estimates below) - before this, a played
                # week fell out of `weekly` the moment current_week advanced
                # past it, silently starving both of real recent data.
                "weekly": [
                    {
                        "week": w, "opponent": opponent.get((p.nfl_team, w)), "home": is_home.get((p.nfl_team, w)),
                        "kickoff": kickoff.get((p.nfl_team, w)),
                        "index": None, "rank": None, "projected": None, "sd": None,
                        "espn_projected": None,
                        "actual": _actual_weekly_stats(p.position, p.nfl_team, res.id, w, weeks_played, actual_offense_by_id_week, actual_dst_by_team_week, active_by_id_week, opponent, player_rules),
                        "implied_total": (game_context.get((p.nfl_team, w)) or {}).get("implied_total"),
                        "opponent_implied_total": (game_context.get((p.nfl_team, w)) or {}).get("opponent_implied_total"),
                        "weather": None,
                        **_usage_stats_for_week(p.position, p.nfl_team, res.id, w, weeks_played),
                    }
                    for w in range(1, current_week)
                ] + [
                    {
                        "week": wp.week, "opponent": wp.opponent, "home": is_home.get((p.nfl_team, wp.week)),
                        "kickoff": kickoff.get((p.nfl_team, wp.week)),
                        "index": wp.index, "rank": wp.rank, "projected": wp.projected, "sd": wp.sd,
                        "espn_projected": espn_future_projections.get(p.espn_id, {}).get(wp.week),
                        "actual": _actual_weekly_stats(p.position, p.nfl_team, res.id, wp.week, weeks_played, actual_offense_by_id_week, actual_dst_by_team_week, active_by_id_week, opponent, player_rules),
                        # implied_total is this player's OWN team; opponent_implied_total
                        # is the team they're facing that week - for a DST, the opponent's
                        # number is the one that actually matters (how many points the
                        # offense they're facing is expected to put up), so the frontend
                        # picks whichever field fits the player's position rather than
                        # this always meaning "the number to headline".
                        "implied_total": (game_context.get((p.nfl_team, wp.week)) or {}).get("implied_total"),
                        "opponent_implied_total": (game_context.get((p.nfl_team, wp.week)) or {}).get("opponent_implied_total"),
                        # Only ever populated for the CURRENT week - see the
                        # weather_by_team comment above for why a future week
                        # is never worth fetching.
                        "weather": weather_by_team.get(p.nfl_team) if wp.week == current_week else None,
                        **_usage_stats_for_week(p.position, p.nfl_team, res.id, wp.week, weeks_played),
                    }
                    for wp in proj.weekly
                ],
                "espn_projected_total": p.espn_projected_total, "espn_projected_week": p.espn_projected_week,
                "percent_owned": p.percent_owned, "percent_owned_delta": p.percent_owned_delta,
                "percent_started": p.percent_started,
                "fp_week_pos_rank_label": weekly_row["pos_rank"] if weekly_row else None,
                "fp_week_projected_pts": weekly_row["r2p_pts"] if weekly_row else None,
            }
        )
        return res.id

    team_rosters: dict[int, list[str]] = {t.team_id: [] for t in espn_teams}
    for t in espn_teams:
        for p in t.roster:
            pid = _register(p, t.team_id)
            team_rosters[t.team_id].append(pid)
    free_agents_ctx: dict[str, list[PlayerCtx]] = {pos: [] for pos in settings.positions}
    for pos, players in free_agents_espn.items():
        for p in players:
            pid = _register(p, None)
            free_agents_ctx[pos].append(players_ctx[pid])

    # All-position ROS rank, for Rankings' default "Rank" column/sort -
    # distinct from ros_pos_rank (FantasyPros' positional rank, e.g. "WR12").
    # Prefer FantasyPros' own overall consensus (ros-overall.php) over our own
    # points-based ranking, which skews QB-heavy in standard scoring since it
    # ignores positional scarcity.
    if overall_lookup:
        for p in players_out:
            p["ros_overall_rank"] = overall_lookup.get(p["id"])
        _positional_ranks_from_overall(players_out)
    else:
        ranked = sorted((p for p in players_out if p["ros_pos_rank"] is not None), key=lambda p: p["ros_total"], reverse=True)
        for i, p in enumerate(ranked, start=1):
            p["ros_overall_rank"] = i
        for p in players_out:
            p.setdefault("ros_overall_rank", None)

    players_by_id = {p["id"]: p for p in players_out}

    # Per-play scoring breakdown for the player modal's expandable Game Log
    # row (see engine/play_log.py) - offense/kicker only, already-played
    # weeks only. Best-effort: play-by-play is a "nice to have" detail view,
    # never worth failing the whole pipeline over - a fetch/schema problem
    # just means no play-level breakdown for this build, same degrade-
    # gracefully pattern as espn_future_projections/ir_return_weeks above.
    # Each week's value is {"plays": [...scoring plays...], "incompletions":
    # [...targets with 0 points...], "zero_point_plays": [...real carries/
    # catches worth 0 points...]} - neither incompletions nor zero-point
    # plays are scoring plays (see incomplete_targets_for_player /
    # zero_point_plays_for_player), but both are still shown, as markers
    # rather than bars, so real usage (a stuffed goal-line carry, a drop)
    # stays visible on the same time axis instead of vanishing entirely.
    game_log_plays_out: dict[str, dict[str, dict[str, list[dict]]]] = {}
    try:
        pbp_current = nd.play_by_play(season, current_season=season)
        for w in range(1, weeks_played + 1):
            week_rows = list(pbp_current.filter(pl.col("season") == season, pl.col("week") == w).iter_rows(named=True))
            if not week_rows:
                continue
            play_index = build_game_play_index(week_rows)
            game_durations = game_durations_by_game_id(week_rows)
            for p in players_out:
                if p["position"] not in offense_positions:
                    continue
                gsis_id = gsis_by_pid.get(p["id"])
                plays_for_player = play_index.get(gsis_id) if gsis_id else None
                if not plays_for_player:
                    continue
                scoring_plays = scoring_plays_for_player(plays_for_player, gsis_id, player_rules)
                incompletions = incomplete_targets_for_player(plays_for_player, gsis_id)
                zero_point_plays = zero_point_plays_for_player(plays_for_player, gsis_id, player_rules)
                if scoring_plays or incompletions or zero_point_plays:
                    game_id = plays_for_player[0].get("game_id")
                    duration = game_durations.get(game_id, 60.0)
                    game_log_plays_out.setdefault(p["id"], {})[str(w)] = {
                        "plays": scoring_plays, "incompletions": incompletions,
                        "zero_point_plays": zero_point_plays, "game_duration_min": round(duration, 2),
                    }
    except Exception:
        logger.warning("play-by-play fetch/processing failed - Game Log per-play breakdown disabled for this build", exc_info=True)
        warnings.append("Play-by-play fetch failed; per-play Game Log breakdown unavailable this build")
        game_log_plays_out = {}

    # Which week (if any) each player is on bye - the trigger for
    # optimal_lineup_for_week_with_bye_fill's streaming fill-in. Covers free
    # agents too, not just rostered players (harmless/unused there).
    bye_week_by_player: dict[str, int | None] = {pid: p["bye"] for pid, p in players_by_id.items()}

    # --- team strength ---

    team_ppw: dict[int, dict] = {}
    team_slot_ppw: dict[int, dict] = {}
    slot_labels: list[str] = []
    for t in espn_teams:
        roster_ids = team_rosters[t.team_id]
        team_ppw[t.team_id] = position_strength(
            roster_ids, players_ctx, weeks, settings.slots, settings.slot_eligibility, settings.positions,
            free_agents_ctx, bye_week_by_player,
        )
        team_slot_ppw[t.team_id], slot_labels = slot_strength(
            roster_ids, players_ctx, weeks, settings.slots, settings.slot_eligibility, free_agents_ctx, bye_week_by_player,
        )
    strength_by_team = rank_and_compare(team_ppw, settings.positions)
    slot_strength_by_team = rank_and_compare(team_slot_ppw, slot_labels)

    teams_out = []
    fa_values_out: dict[str, dict[str, float]] = {}
    fa_values_detail_out: dict[str, dict[str, dict]] = {}
    for t in espn_teams:
        roster_ids = team_rosters[t.team_id]
        depth_by_week = depth_values_by_week(roster_ids, players_ctx, free_agents_ctx, weeks, settings.slots, settings.slot_eligibility)
        for pid, vals in depth_by_week.items():
            players_by_id[pid]["value_delta"] = sum(vals["value_delta"].values())

        depth_table: dict[str, list[dict]] = {pos: [] for pos in settings.positions}
        for pid in roster_ids:
            pos = players_by_id[pid]["position"]
            vals = depth_by_week.get(pid, {"value_delta": {}, "replacement_id": {}})
            depth_table.setdefault(pos, []).append(
                {
                    "id": pid,
                    "value_delta": sum(vals["value_delta"].values()),
                    # The replacement is picked FRESH each week (see
                    # depth_values_by_week) - a real teammate OR the best
                    # streaming free agent, whichever the optimizer actually
                    # prefers, can be a different specific player week to
                    # week, so it lives in the per-week list below, not as
                    # one season-long name at the top level.
                    "weekly": [
                        {"week": w, "value_delta": vals["value_delta"].get(w, 0.0), "replacement_id": vals["replacement_id"].get(w)}
                        for w in weeks
                    ],
                }
            )
        for pos_list in depth_table.values():
            pos_list.sort(key=lambda x: x["value_delta"], reverse=True)

        team_ir_ids = frozenset(pid for pid in roster_ids if pid in ir_ids)
        # Compute once and derive both the "recommended pickups" shortlist and
        # the full per-FA value map (all_fa_values.json) from it, rather than
        # calling pickups() separately and redoing the same lineup work.
        team_fa_values = fa_values(
            roster_ids, players_ctx, free_agents_ctx, weeks, settings.slots, settings.slot_eligibility,
            ir_player_ids=team_ir_ids,
        )
        pickup_list = sorted(
            (
                {"add": fa_id, "drop": v["drop"], "gain": v["gain"]}
                for fa_id, v in team_fa_values.items() if v["gain"] > 0
            ),
            key=lambda c: c["gain"], reverse=True,
        )[: strength_cfg["max_pickups"]]
        fa_values_out[str(t.team_id)] = {fa_id: v["gain"] for fa_id, v in team_fa_values.items()}
        # Week-by-week detail (drop identity + per-week swing) behind the FAAB
        # modal's NMD breakdown - only kept for candidates that actually beat
        # this team's worst droppable player (gain > 0, same bar pickups()
        # already uses), since a negative-gain add's per-week detail isn't
        # something a manager would ever want to inspect, and every team's
        # full pool of considered free agents would otherwise multiply this
        # file's size by roughly the number of weeks left in the season for
        # no real benefit.
        fa_values_detail_out[str(t.team_id)] = {
            fa_id: {"drop": v["drop"], "weekly": v["weekly"]} for fa_id, v in team_fa_values.items() if v["gain"] > 0
        }

        other_rosters = {ot.team_id: team_rosters[ot.team_id] for ot in espn_teams if ot.team_id != t.team_id}
        targets = trade_targets(
            t.team_id, roster_ids, other_rosters, players_ctx, free_agents_ctx, weeks,
            settings.slots, settings.slot_eligibility, strength_cfg["max_trade_targets"],
        )
        partner_summary = []
        for ot in espn_teams:
            if ot.team_id == t.team_id:
                continue
            my_deficit = min(settings.positions, key=lambda pos: strength_by_team[t.team_id][pos]["vs_avg"])
            their_surplus = max(settings.positions, key=lambda pos: strength_by_team[ot.team_id][pos]["vs_avg"])
            partner_summary.append({"partner_team_id": ot.team_id, "their_surplus": their_surplus, "your_surplus": my_deficit})

        teams_out.append(
            {
                "team_id": t.team_id, "name": t.team_name, "manager": t.manager,
                "wins": t.wins, "losses": t.losses, "points_for": t.points_for,
                "lineup_total_ros": lineup_total(roster_ids, players_ctx, weeks, settings.slots, settings.slot_eligibility),
                "position_strength": strength_by_team[t.team_id],
                "slot_strength": slot_strength_by_team[t.team_id],
                "slot_labels": slot_labels,
                "depth": depth_table, "pickups": pickup_list, "trade_targets": targets,
                "partner_summary": partner_summary,
            }
        )

    # --- lineups (every remaining week, so Start/Sit can show future weeks) ---
    all_week_lineups: dict[tuple[int, int], tuple[float, dict[str, str]]] = {}
    lineups_out: dict[str, dict] = {}
    past_weeks = list(range(1, current_week))
    past_lineups = client.get_past_lineups(past_weeks) if past_weeks else {}
    espn_started_by_team: dict[int, set[str]] = {}
    # A past week's box score can name a player who's since been dropped from
    # every roster and fallen out of the current top-N free-agent pull (see
    # get_free_agents' fa_size) - they're real and resolvable (box_scores
    # gives their name/position/team same as anyone else), just missing from
    # players_out's "current universe" of rostered + top free agents. Keyed
    # separately here (not appended to players_out) so this doesn't leak a
    # mostly-blank row into Rankings/Team Strength/Trade Calculator, which
    # all iterate players_out wholesale - only Schedule's past-week view,
    # which resolves a specific known id, ever looks here.
    unrostered_stub_players: dict[str, dict] = {}
    for t in espn_teams:
        roster_ids = team_rosters[t.team_id]
        weeks_out = {}
        for w in weeks:
            # Only FUTURE weeks stream a bye-week fill-in - the current
            # week's optimal lineup stays real-roster-only, since it's
            # compared directly against your actual ESPN lineup just below
            # (changes_vs_espn) and a free-agent suggestion there isn't
            # necessarily actionable (waivers may already be locked/processed
            # for a week already underway).
            if w > current_week:
                total, assignment, streamed = optimal_lineup_for_week_with_bye_fill(
                    roster_ids, players_ctx, free_agents_ctx, w, settings.slots, settings.slot_eligibility, bye_week_by_player,
                )
            else:
                total, assignment = optimal_lineup_for_week(roster_ids, players_ctx, w, settings.slots, settings.slot_eligibility)
                streamed = set()
            all_week_lineups[(t.team_id, w)] = (total, assignment)
            started = set(assignment.values())
            bench = sorted(
                (pid for pid in roster_ids if pid not in started),
                key=lambda pid: players_by_id[pid]["espn_projected_week"] or 0, reverse=True,
            )
            weeks_out[str(w)] = {"total": total, "slots": assignment, "bench": bench, "streamed": sorted(streamed)}

        # "Changes vs. your ESPN lineup" only makes sense for the current week
        # - ESPN's actual lineup submission is a live, current-state snapshot,
        # not something set for future weeks. Only true bench<->start swaps
        # count: which specific starting slot a player occupies (RB vs the
        # flex slot) is not a "change" by itself - engine.lineup's tie-break
        # picks a natural-looking slot among equally-valuable options, but
        # it's still just a display preference, not a real lineup difference.
        # Pair each newly-started player with a newly-benched
        # player of the SAME position where possible, so a swap always tells a
        # coherent single-position story; only fall back across positions if
        # there's no same-position counterpart.
        cur_total, cur_assignment = all_week_lineups[(t.team_id, current_week)]
        our_started = set(cur_assignment.values())
        espn_started = {pid for pid in roster_ids if espn_lineup_slot.get(pid, "BE") not in ("BE", "IR")}
        espn_started_by_team[t.team_id] = espn_started
        added = sorted(our_started - espn_started, key=lambda pid: players_by_id[pid]["this_week"], reverse=True)
        removed = list(espn_started - our_started)

        changes = []
        for optimal_pid in added:
            pos = players_by_id[optimal_pid]["position"]
            same_pos = [pid for pid in removed if players_by_id[pid]["position"] == pos]
            pool = same_pos or removed
            if not pool:
                break
            espn_pid = min(pool, key=lambda pid: players_by_id[pid]["this_week"])
            removed.remove(espn_pid)
            changes.append(
                {
                    "optimal": optimal_pid, "espn": espn_pid,
                    "delta": players_by_id[optimal_pid]["this_week"] - players_by_id[espn_pid]["this_week"],
                }
            )

        # Already-played weeks get ESPN's own real, submitted lineup (via
        # box_scores) instead of our computed-optimal one, so the Schedule
        # page's per-week detail reflects what actually happened rather than
        # what would have been best in hindsight.
        for w in past_weeks:
            entries = past_lineups.get((t.team_id, w))
            if not entries:
                continue
            by_base: dict[str, list[str]] = {}
            bench_ids: list[str] = []
            points_by_pid: dict[str, float] = {}
            total = 0.0
            for e in entries:
                res = id_map.resolve(espn_id=e.espn_id, name=e.name, pos=e.position, team=e.nfl_team)
                if res.id not in players_by_id:
                    unrostered_stub_players[res.id] = {
                        "id": res.id, "name": e.name, "position": e.position, "nfl_team": e.nfl_team,
                    }
                points_by_pid[res.id] = e.points
                if e.lineup_slot in _PAST_LINEUP_BENCH_SLOTS:
                    bench_ids.append(res.id)
                    continue
                base = _PAST_LINEUP_SLOT_BASE.get(e.lineup_slot, e.lineup_slot)
                by_base.setdefault(base, []).append(res.id)
                total += e.points
            slots_assignment = {}
            for base, pids in by_base.items():
                if len(pids) == 1:
                    slots_assignment[base] = pids[0]
                else:
                    for i, pid in enumerate(pids, start=1):
                        slots_assignment[f"{base}{i}"] = pid
            weeks_out[str(w)] = {
                "total": total, "slots": slots_assignment, "bench": bench_ids,
                "streamed": [], "actual": True, "points": points_by_pid,
            }

        lineups_out[str(t.team_id)] = {"weeks": weeks_out, "changes_vs_espn": changes}

    # Not a team - see unrostered_stub_players above. Every real key above is
    # a str(team_id), so this can't collide with one.
    lineups_out["_unrostered_players"] = unrostered_stub_players

    # --- matchups.json (points-allowed index table) ---
    matchups_out = {
        pos: {
            team: {
                "index": matchup_index.index[pos][team],
                "rank": matchup_index.rank[pos][team],
                "allowed_ppg": matchup_index.allowed_ppg[pos][team],
                "l5_allowed_ppg": matchup_index.l5_allowed_ppg[pos][team],
                "adjusted_allowed_ppg": matchup_index.adjusted_allowed_ppg.get(pos, {}).get(team, matchup_index.allowed_ppg[pos][team]),
                "pa_factor": matchup_index.pa_factor.get(pos, {}).get(team, 1.0),
            }
            for team in matchup_index.index[pos]
        }
        for pos in matchup_positions
    }

    # --- standings ---
    espn_matchups = client.get_matchups()
    remaining = [
        RemainingMatchup(week=m.week, home_team_id=m.home_team_id, away_team_id=m.away_team_id)
        for m in espn_matchups if not m.played and m.week <= reg_season_count
    ]

    # Per-team, per-week projected total mean/SD - used below as the win-
    # probability input for every not-yet-current, not-yet-played matchup
    # (the current week gets a more precise LIVE version instead, see
    # team_live_mean_sd just below), and again further down as
    # simulate_playoffs' own per-week input - computed once, shared by both.
    team_week_mean: dict[tuple[int, int], float] = {}
    team_week_sd: dict[tuple[int, int], float] = {}
    for t in espn_teams:
        for w in weeks:
            wtotal, wassign = all_week_lineups[(t.team_id, w)]
            team_week_mean[(t.team_id, w)] = wtotal
            # sum of starters' weekly variance -> sqrt for the team's weekly SD
            sd_sq = sum(
                (next((wp["sd"] for wp in players_by_id[pid]["weekly"] if wp["week"] == w), 0.0)) ** 2
                for pid in wassign.values()
            )
            team_week_sd[(t.team_id, w)] = sd_sq ** 0.5

    # --- live win probability (current week only) ---
    # Each starter's (mean, sd) BLENDS toward (actual points, 0) smoothly as
    # their real NFL game progresses, rather than snapping straight from
    # "fully uncertain" to "fully final" the instant ESPN's fantasy API
    # marks their game done - live-verified against the ESPN app's own
    # displayed in-game projection for a real player (Christian Watson,
    # week 1 2026): actual_so_far + remaining_fraction * pregame_projection
    # matched it exactly at halftime (remaining_fraction=0.5). SD scales by
    # sqrt(remaining_fraction), not remaining_fraction itself - variance
    # (not SD) is the additive quantity for a process accumulating roughly
    # uniformly over game-clock time (the same reasoning a random walk's
    # variance grows linearly with elapsed time while its SD only grows
    # with elapsed time's square root), so a full-game SD represents all 60
    # minutes of variance and the REMAINING portion is pregame_sd *
    # sqrt(remaining_fraction).
    #
    # remaining_fraction comes from ESPN's PUBLIC scoreboard (real
    # quarter/clock state - the fantasy API never exposes this, only a
    # crude done/not-done flag) via ingest.espn_scoreboard, keyed by team;
    # a team missing from it (that fetch failed entirely, or - degenerate -
    # a team not found on the live scoreboard for some other reason) falls
    # back to the OLD binary rule instead of guessing every team is at some
    # arbitrary default, so a scoreboard outage degrades to today's
    # already-safe behavior rather than corrupting every matchup's win%.
    #
    # Summed per team into one Normal(mean, sd), then
    # engine.standings.matchup_win_probability turns the two teams'
    # distributions into a single win% for the matchup - accurate as of the
    # last site build, not truly real-time in-game (the pipeline itself
    # only runs on its own schedule). Every OTHER not-yet-played week (no
    # live status to blend in yet regardless) uses the plain projected
    # team_week_mean/team_week_sd instead - see _win_pcts below.
    live_status_by_espn_id = client.get_live_week_player_status(current_week)
    remaining_frac_by_team = fetch_remaining_game_fraction()
    team_live_mean_sd: dict[int, tuple[float, float]] = {}
    for team_id, starters in espn_started_by_team.items():
        mean_total = 0.0
        var_total = 0.0
        for pid in starters:
            p = players_by_id[pid]
            wp = next((w for w in p["weekly"] if w["week"] == current_week), None)
            pregame_mean, pregame_sd = p["this_week"] or 0.0, wp["sd"] if wp else 0.0
            live = live_status_by_espn_id.get(p["espn_id"])
            points_so_far = live[0] if live is not None else 0.0
            frac = remaining_frac_by_team.get(p["nfl_team"])
            if frac is None:
                frac = 0.0 if (live is not None and live[1]) else 1.0
            mean_total += points_so_far + frac * pregame_mean
            var_total += (pregame_sd * (frac**0.5)) ** 2
        team_live_mean_sd[team_id] = (mean_total, var_total**0.5)

    def _win_pcts(m: Matchup) -> tuple[float | None, float | None]:
        # Decided games don't need a win% (the real score already says who
        # won). The current week gets the more precise LIVE version (real
        # points for finished player-games, sd collapsed to 0 for those) -
        # every other not-yet-played week falls back to the plain pre-game
        # projected mean/SD (team_week_mean/team_week_sd, above) as its best
        # available estimate, rather than showing nothing at all just
        # because the game hasn't started yet.
        if m.played:
            return None, None
        if m.week == current_week and m.home_team_id in team_live_mean_sd and m.away_team_id in team_live_mean_sd:
            home_mean_sd, away_mean_sd = team_live_mean_sd[m.home_team_id], team_live_mean_sd[m.away_team_id]
        elif (m.home_team_id, m.week) in team_week_mean and (m.away_team_id, m.week) in team_week_mean:
            home_mean_sd = (team_week_mean[(m.home_team_id, m.week)], team_week_sd[(m.home_team_id, m.week)])
            away_mean_sd = (team_week_mean[(m.away_team_id, m.week)], team_week_sd[(m.away_team_id, m.week)])
        else:
            return None, None
        home_win_pct = matchup_win_probability(*home_mean_sd, *away_mean_sd)
        return home_win_pct, 1.0 - home_win_pct

    schedule_out = []
    for m in espn_matchups:
        home_win_pct, away_win_pct = _win_pcts(m)
        schedule_out.append(
            {
                "week": m.week, "home_team_id": m.home_team_id, "away_team_id": m.away_team_id,
                "home_score": m.home_score, "away_score": m.away_score, "played": m.played,
                "home_win_pct": home_win_pct, "away_win_pct": away_win_pct,
            }
        )

    team_states = [
        TeamState(team_id=t.team_id, division_id=t.division_id, wins=t.wins, losses=t.losses, ties=t.ties, points_for=t.points_for)
        for t in espn_teams
    ]
    sim_results = simulate_playoffs(
        team_states, remaining, team_week_mean, team_week_sd,
        iterations=sim_cfg["iterations"], seed=sim_cfg["seed"],
        playoff_team_count=settings.playoff_team_count, division_winners_first=sim_cfg["division_winners_first"],
    )
    # --- current seed (real, not simulated) ---
    # Uses configurable per-seed tiebreakers (see docs/js/settings.js and
    # README's "Settings sheet" section) rather than simulate_playoffs' fixed
    # wins->PF sim tiebreak, which stays as-is (a reasonable approximation
    # for a 10000-iteration Monte Carlo forecast, not the real current
    # standings). Falls back to today's behavior - division_winners_first +
    # wins->PF for every seed - until seeding is actually configured in the
    # settings sheet, so unconfigured leagues don't silently change.
    division_count = len(set(settings.divisions.keys()) & {t.division_id for t in espn_teams})
    division_tiebreak_order, seed_configs = parse_seeding_config(sim_cfg.get("seeding_raw", {}))
    if not division_tiebreak_order:
        division_tiebreak_order = ["wins", "points_for"]
    if not seed_configs:
        seed_configs = {
            n: SeedConfig(division_priority=sim_cfg["division_winners_first"] and n <= division_count, tiebreak_order=["wins", "points_for"])
            for n in range(1, settings.playoff_team_count + 1)
        }
    seed_team_states = [
        SeedTeamState(
            team_id=t.team_id, division_id=t.division_id, wins=t.wins, losses=t.losses, ties=t.ties,
            points_for=t.points_for, points_against=t.points_against,
        )
        for t in espn_teams
    ]
    played_matchups = [
        PlayedMatchup(home_team_id=m.home_team_id, away_team_id=m.away_team_id, home_score=m.home_score, away_score=m.away_score)
        for m in espn_matchups
        if m.played and m.home_score is not None and m.away_score is not None
    ]
    current_seeds = compute_current_seeds(
        seed_team_states, played_matchups, settings.playoff_team_count, division_tiebreak_order, seed_configs,
    )

    standings_out = []
    for t in espn_teams:
        s = sim_results[t.team_id]
        standings_out.append(
            {
                "team_id": t.team_id, "wins": t.wins, "losses": t.losses, "ties": t.ties, "points_for": t.points_for,
                "points_against": t.points_against, "seed": current_seeds.get(t.team_id),
                "division": settings.divisions.get(t.division_id, str(t.division_id)),
                "expected_wins": s["expected_wins"], "playoff_odds": s["playoff_odds"], "bye_odds": s["bye_odds"],
                "division_win_odds": s["division_win_odds"], "seed_probs": s["seed_probs"], "iterations": s["iterations"],
            }
        )

    # --- meta ---
    for pid, entries in players_by_id.items():
        # ros_total is only ever computed over the PROJECTED weeks (see
        # project_player's weeks = range(current_week, final_week+1)) - a
        # played week's entry in "weekly" (see above) has projected=None by
        # design, nothing to include in this sum.
        weekly_sum = sum(w["projected"] for w in entries["weekly"] if w["projected"] is not None)
        if abs(weekly_sum - entries["ros_total"]) > 0.01:
            warnings.append(f"player {pid} weekly sum {weekly_sum:.2f} != ros_total {entries['ros_total']:.2f}")
    if unmapped:
        warnings.append(f"{len(unmapped)} players unmapped to nflverse ids")

    meta = {
        "slug": slug,
        # The real ESPN league id behind this site league - lets client JS
        # pick "my league" out of cross-league pooled data (e.g. the FAAB
        # bid_distribution dot-plot's source_league_id per bid) without
        # hardcoding any one league, generalizing beyond just The O League.
        "league_id": cfg["league_id"],
        "season": season, "current_week": current_week, "weeks_played": weeks_played, "final_week": final_week,
        "reg_season_count": reg_season_count, "generated_at": datetime.now(timezone.utc).isoformat(),
        "rankings_source": rankings_source, "curve_seasons": val_cfg["curve_seasons"],
        "positions": settings.positions,
        "slots": settings.slots, "slot_eligibility": {k: sorted(v) for k, v in settings.slot_eligibility.items()},
        "playoff_team_count": settings.playoff_team_count,
        "division_count": division_count,
        "teams": [
            {"team_id": t.team_id, "name": t.team_name, "manager": t.manager, "abbrev": t.abbrev, "division": settings.divisions.get(t.division_id, str(t.division_id))}
            for t in espn_teams
        ],
        "warnings": warnings,
        "settings": {
            "matchup_dampening": val_cfg["matchup_dampening"],
            "pa_basis": val_cfg["pa_basis"],
            "pa_l5_weight": val_cfg["pa_l5_weight"],
            "pa_prior_season_weeks": val_cfg["pa_prior_season_weeks"],
            "division_winners_first": sim_cfg["division_winners_first"],
            "seeding_raw": sim_cfg.get("seeding_raw", {}),
        },
        "settings_sheet_id": settings_sheet_id,
        "settings_overrides_applied": settings_overrides,
    }

    # Gated on the week's actual KICKOFF instant, not just whether today's
    # calendar date has reached that week's first game day (see
    # ingest/nfl_data.py's week_for_kickoff vs. week_for_date) - an earlier
    # date-only version compared UTC's "today" against nflverse's Eastern-
    # dated gameday, which rolled over up to ~5 hours early every evening
    # (UTC crosses midnight at 7-8pm Eastern) and could fire
    # _faab_week_override before that day's game had even kicked off, let
    # alone the calendar day it's on. See the conversation this was built
    # from (a real prod case: FAAB showed week 3 while it was still
    # Wednesday evening Central, a full day before week 2's Thursday
    # opener).
    faab_week_started = nd.week_for_kickoff(datetime.now(timezone.utc), schedules_current, season)
    faab_current_week = _faab_week_override(current_week, faab_week_started)
    try:
        faab_estimates_out = _compute_faab_estimates(
            cfg, client, players_out, season, faab_current_week, team_rosters, fa_values_out,
            snap_pct_index, gsis_to_pfr, stats_index, team_rb_carries,
        )
    except Exception as exc:
        logger.warning("faab_model: estimate computation failed, writing empty faab_estimates.json: %s", exc)
        faab_estimates_out = {}

    return {
        "meta.json": meta,
        "players.json": players_out,
        "teams.json": teams_out,
        "lineups.json": lineups_out,
        "matchups.json": matchups_out,
        "standings.json": standings_out,
        "recent_results.json": recent_results,
        "points_against.json": points_against,
        "schedule.json": schedule_out,
        "fa_values.json": fa_values_out,
        "fa_values_detail.json": fa_values_detail_out,
        "game_log_plays.json": game_log_plays_out,
        "unmapped.json": unmapped,
        "faab_estimates.json": faab_estimates_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default=None, help="league slug; omit to run every configured league")
    parser.add_argument("--out", default="docs/data")
    args = parser.parse_args()

    out_root = Path(args.out)
    configs = load_all_league_configs()
    if args.league:
        configs = [c for c in configs if c["slug"] == args.league]

    leagues_index = []
    any_failed = False
    for cfg in configs:
        try:
            files = run_league(cfg)
        except Exception:
            logger.exception("league %s failed", cfg["slug"])
            any_failed = True
            continue

        out_dir = out_root / cfg["slug"]
        out_dir.mkdir(parents=True, exist_ok=True)
        for filename, data in files.items():
            with open(out_dir / filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        leagues_index.append(
            {
                "slug": cfg["slug"], "name": cfg["name"], "season": cfg["season"],
                "scoring_format": cfg["scoring_format"], "positions": files["meta.json"]["positions"],
            }
        )
        logger.info("wrote %s", out_dir)

    out_root.mkdir(parents=True, exist_ok=True)
    with open(out_root / "leagues.json", "w", encoding="utf-8") as f:
        json.dump(leagues_index, f, ensure_ascii=False)

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
