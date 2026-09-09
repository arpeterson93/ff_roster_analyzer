"""Orchestrates ingestion + engine computation per configured league and
writes docs/data/<slug>/*.json plus the top-level docs/data/leagues.json."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from engine.curve import Curve, blend_current, build_curve, build_dst_curve
from engine.matchups import (
    allowed_by_team_week_pos,
    compute_matchup_index,
    dst_points_by_team_week_pos,
    points_by_team_week_pos,
    team_weeks_from_opponent,
)
from engine.points_against import dst_points_against_detail, points_against_detail
from engine.scoring import ScoringRules
from engine.standings import RemainingMatchup, TeamState, simulate_playoffs
from engine.team_strength import (
    PlayerCtx,
    depth_values_by_week,
    fa_values,
    lineup_total,
    optimal_lineup_for_week,
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
from ingest.espn_client import EspnClient
from ingest.settings_sheet import SettingsSheetError, apply_remote_settings, fetch_remote_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_IR_SLOTS = {"IR"}

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


def _actual_stats_row(row: dict, fields: list[str]) -> dict:
    stats = {f: row.get(f) for f in fields}
    stats["two_pt_conversions"] = (
        (row.get("passing_2pt_conversions") or 0)
        + (row.get("rushing_2pt_conversions") or 0)
        + (row.get("receiving_2pt_conversions") or 0)
    )
    return stats


def _id_lookup(ranks_df, id_map: ids_mod.IdMap) -> dict[str, dict]:
    """canonical id -> FantasyPros ranking row, for either the ROS or weekly frame."""
    lookup = {}
    for row in ranks_df.iter_rows(named=True):
        res = id_map.resolve(fp_id=row.get("fp_id"), name=row["name"], pos=row["pos"], team=row.get("team"))
        lookup[res.id] = row
    return lookup


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
    current_week = settings.current_week
    final_week = settings.final_week
    reg_season_count = settings.reg_season_count
    prior_season = season - 1
    weeks = list(range(current_week, final_week + 1))

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
    for row in current_stats.iter_rows(named=True):
        pid = id_map.resolve(gsis_id=row["player_id"], name=row["player_display_name"], pos=row["position"], team=row["team"]).id
        actual_offense_by_id_week[(pid, row["week"])] = row
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

    current_allowed = allowed_by_team_week_pos(current_points, opponent, current_team_weeks, matchup_positions)
    prior_allowed = allowed_by_team_week_pos(prior_points, prior_opponent, prior_team_weeks, matchup_positions)

    recent_results = {"current_season": season, "prior_season": prior_season, "by_position": {}}
    for pos in matchup_positions:
        recent_results["by_position"][pos] = {
            team: {
                "current": {str(w): current_allowed.get((team, w, pos), 0.0) for w in current_team_weeks.get(team, [])},
                "prior": {str(w): prior_allowed.get((team, w, pos), 0.0) for w in prior_team_weeks.get(team, [])},
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

    def _actual_weekly_stats(position, nfl_team, pid, week, weeks_played_, offense_lookup, dst_lookup):
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
        if not row:
            return None
        return {"stats": _actual_stats_row(row, _OFFENSE_STAT_FIELDS), "points": player_rules.points_for_row(row)}

    def _register(p, fantasy_team_id: int | None):
        res = id_map.resolve(espn_id=p.espn_id, name=p.name, pos=p.position, team=p.nfl_team)
        if res.source == "unmapped":
            unmapped.append({"name": p.name, "pos": p.position, "team": p.nfl_team, "source": "espn"})

        ros_row = ros_lookup.get(res.id)
        weekly_row = weekly_lookup.get(res.id)
        ros_pos_rank = ros_row["ros_pos_rank"] if ros_row else None
        week_pos_rank = weekly_row["week_pos_rank"] if weekly_row else None

        proj = project_player(
            position=p.position, nfl_team=p.nfl_team, ros_pos_rank=ros_pos_rank,
            injury_status=p.injury_status, week_pos_rank=week_pos_rank,
            curve=curve, matchup_index=matchup_index, current_week=current_week, final_week=final_week,
            reg_season_count=reg_season_count, opponent=opponent, cfg=val_cfg,
            espn_week_projection=p.espn_projected_week,
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
                "value_delta": None, "value_delta_ww": None,
                "position_avg_ratio": (proj.baseline_ppg / position_avg) if position_avg else None,
                "weekly": [
                    {
                        "week": wp.week, "opponent": wp.opponent, "home": is_home.get((p.nfl_team, wp.week)),
                        "index": wp.index, "rank": wp.rank, "projected": wp.projected, "sd": wp.sd,
                        "actual": _actual_weekly_stats(p.position, p.nfl_team, res.id, wp.week, weeks_played, actual_offense_by_id_week, actual_dst_by_team_week),
                    }
                    for wp in proj.weekly
                ],
                "espn_projected_total": p.espn_projected_total, "espn_projected_week": p.espn_projected_week,
                "percent_owned": p.percent_owned, "percent_owned_delta": p.percent_owned_delta,
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
    else:
        ranked = sorted((p for p in players_out if p["ros_pos_rank"] is not None), key=lambda p: p["ros_total"], reverse=True)
        for i, p in enumerate(ranked, start=1):
            p["ros_overall_rank"] = i
        for p in players_out:
            p.setdefault("ros_overall_rank", None)

    players_by_id = {p["id"]: p for p in players_out}

    # --- team strength ---

    team_ppw: dict[int, dict] = {}
    team_slot_ppw: dict[int, dict] = {}
    slot_labels: list[str] = []
    for t in espn_teams:
        roster_ids = team_rosters[t.team_id]
        team_ppw[t.team_id] = position_strength(roster_ids, players_ctx, weeks, settings.slots, settings.slot_eligibility, settings.positions)
        team_slot_ppw[t.team_id], slot_labels = slot_strength(roster_ids, players_ctx, weeks, settings.slots, settings.slot_eligibility)
    strength_by_team = rank_and_compare(team_ppw, settings.positions)
    slot_strength_by_team = rank_and_compare(team_slot_ppw, slot_labels)

    teams_out = []
    fa_values_out: dict[str, dict[str, float]] = {}
    for t in espn_teams:
        roster_ids = team_rosters[t.team_id]
        depth_by_week = depth_values_by_week(roster_ids, players_ctx, free_agents_ctx, weeks, settings.slots, settings.slot_eligibility)
        for pid, vals in depth_by_week.items():
            players_by_id[pid]["value_delta"] = sum(vals["value_delta"].values())
            players_by_id[pid]["value_delta_ww"] = sum(vals["value_delta_ww"].values())

        depth_table: dict[str, list[dict]] = {pos: [] for pos in settings.positions}
        for pid in roster_ids:
            pos = players_by_id[pid]["position"]
            vals = depth_by_week.get(pid, {"value_delta": {}, "value_delta_ww": {}})
            depth_table.setdefault(pos, []).append(
                {
                    "id": pid,
                    "value_delta": sum(vals["value_delta"].values()),
                    "value_delta_ww": sum(vals["value_delta_ww"].values()),
                    "weekly": [
                        {"week": w, "value_delta": vals["value_delta"].get(w, 0.0), "value_delta_ww": vals["value_delta_ww"].get(w, 0.0)}
                        for w in weeks
                    ],
                }
            )
        for pos_list in depth_table.values():
            pos_list.sort(key=lambda x: x["value_delta_ww"], reverse=True)

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
    for t in espn_teams:
        roster_ids = team_rosters[t.team_id]
        weeks_out = {}
        for w in weeks:
            total, assignment = optimal_lineup_for_week(roster_ids, players_ctx, w, settings.slots, settings.slot_eligibility)
            all_week_lineups[(t.team_id, w)] = (total, assignment)
            started = set(assignment.values())
            bench = sorted(
                (pid for pid in roster_ids if pid not in started),
                key=lambda pid: players_by_id[pid]["espn_projected_week"] or 0, reverse=True,
            )
            weeks_out[str(w)] = {"total": total, "slots": assignment, "bench": bench}

        # "Changes vs. your ESPN lineup" only makes sense for the current week
        # - ESPN's actual lineup submission is a live, current-state snapshot,
        # not something set for future weeks. Only true bench<->start swaps
        # count: which specific starting slot a player occupies (RB vs the
        # flex slot) is not a "change" by itself - the Hungarian solver has
        # no preference between equally-eligible slots, so the same two
        # starters can land in either slot from one run to the next with no
        # real difference. Pair each newly-started player with a newly-benched
        # player of the SAME position where possible, so a swap always tells a
        # coherent single-position story; only fall back across positions if
        # there's no same-position counterpart.
        cur_total, cur_assignment = all_week_lineups[(t.team_id, current_week)]
        our_started = set(cur_assignment.values())
        espn_started = {pid for pid in roster_ids if espn_lineup_slot.get(pid, "BE") not in ("BE", "IR")}
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

        lineups_out[str(t.team_id)] = {"weeks": weeks_out, "changes_vs_espn": changes}

    # --- matchups.json (points-allowed index table) ---
    matchups_out = {
        pos: {
            team: {
                "index": matchup_index.index[pos][team],
                "rank": matchup_index.rank[pos][team],
                "allowed_ppg": matchup_index.allowed_ppg[pos][team],
                "l5_allowed_ppg": matchup_index.l5_allowed_ppg[pos][team],
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
    schedule_out = [
        {
            "week": m.week, "home_team_id": m.home_team_id, "away_team_id": m.away_team_id,
            "home_score": m.home_score, "away_score": m.away_score, "played": m.played,
        }
        for m in espn_matchups
    ]
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

    team_states = [
        TeamState(team_id=t.team_id, division_id=t.division_id, wins=t.wins, losses=t.losses, ties=t.ties, points_for=t.points_for)
        for t in espn_teams
    ]
    sim_results = simulate_playoffs(
        team_states, remaining, team_week_mean, team_week_sd,
        iterations=sim_cfg["iterations"], seed=sim_cfg["seed"],
        playoff_team_count=settings.playoff_team_count, division_winners_first=sim_cfg["division_winners_first"],
    )
    standings_out = []
    for t in espn_teams:
        s = sim_results[t.team_id]
        standings_out.append(
            {
                "team_id": t.team_id, "wins": t.wins, "losses": t.losses, "ties": t.ties, "points_for": t.points_for,
                "division": settings.divisions.get(t.division_id, str(t.division_id)),
                "expected_wins": s["expected_wins"], "playoff_odds": s["playoff_odds"], "bye_odds": s["bye_odds"],
                "division_win_odds": s["division_win_odds"], "seed_probs": s["seed_probs"], "iterations": s["iterations"],
            }
        )

    # --- meta ---
    for pid, entries in players_by_id.items():
        weekly_sum = sum(w["projected"] for w in entries["weekly"])
        if abs(weekly_sum - entries["ros_total"]) > 0.01:
            warnings.append(f"player {pid} weekly sum {weekly_sum:.2f} != ros_total {entries['ros_total']:.2f}")
    if unmapped:
        warnings.append(f"{len(unmapped)} players unmapped to nflverse ids")

    meta = {
        "slug": slug,
        "season": season, "current_week": current_week, "weeks_played": weeks_played, "final_week": final_week,
        "reg_season_count": reg_season_count, "generated_at": datetime.now(timezone.utc).isoformat(),
        "rankings_source": rankings_source, "curve_seasons": val_cfg["curve_seasons"],
        "positions": settings.positions,
        "slots": settings.slots, "slot_eligibility": {k: sorted(v) for k, v in settings.slot_eligibility.items()},
        "teams": [
            {"team_id": t.team_id, "name": t.team_name, "manager": t.manager, "abbrev": t.abbrev, "division": settings.divisions.get(t.division_id, str(t.division_id))}
            for t in espn_teams
        ],
        "warnings": warnings,
        "settings": {
            "matchup_dampening": val_cfg["matchup_dampening"],
            "pa_basis": val_cfg["pa_basis"],
            "pa_l5_weight": val_cfg["pa_l5_weight"],
            "division_winners_first": sim_cfg["division_winners_first"],
        },
        "settings_sheet_id": settings_sheet_id,
        "settings_overrides_applied": settings_overrides,
    }

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
        "unmapped.json": unmapped,
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
