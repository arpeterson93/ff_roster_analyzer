"""Standings/schedule outputs, shared by the full/refresh pipeline
(engine/pipeline.py) and the gameday live tier (engine/live.py) - extracted
from run_league so the live tier can rebuild the exact same win%/playoff-odds
numbers from already-computed outputs (lineups.json's per-week team totals +
SD) plus a fresh ESPN live-status fetch, without re-running any projection
work."""
from __future__ import annotations

from ingest.base import Matchup
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
from ingest.settings_sheet import parse_seeding_config


def live_team_mean_sd(
    players_by_id: dict[str, dict],
    started_by_team: dict[int, set[str]],
    current_week: int,
    live_status: dict[int, tuple[float, bool]],
    remaining_frac: dict[str, float],
    *,
    fallback_sd_by_pos: dict[str, float] | None = None,
) -> tuple[dict[int, tuple[float, float]], dict[int, dict[str, float]]]:
    """Per-team (mean, sd) for the CURRENT week only, blending each starter's
    live box-score points so far with their remaining-game-fraction share of
    the pregame projection (see the extended comment this was moved from, in
    engine/pipeline.py's git history, for the exact blend derivation and its
    live-verification against a real in-game ESPN projection).

    Returns (team_live_mean_sd, live_points_by_team) - the second dict is
    each starter's REAL points-so-far only (not the blended projection), used
    both for schedule.json's current-week live home_score/away_score (sum
    over a team's starters) and live.json's per-player "points" detail.

    `players_by_id[pid]` needs `position`, `nfl_team`, `espn_id`, `this_week`
    (pregame mean) and a `weekly` list with a `{"week": current_week, "sd":
    ...}` entry - exactly players.json's own per-player shape, so a caller
    can pass that straight through. A player missing from `players_by_id`
    (e.g. a live-tier starter never in the nightly baseline) is the caller's
    job to stub in first (see engine/live.py) - this function does not
    special-case a missing id.

    `fallback_sd_by_pos`: used only when a player has no `weekly` entry for
    `current_week` (or that entry's `sd` is None) - the live tier's own
    stubbed-in starters use this (meta.json's `position_week_sd`) rather than
    a hard 0.0, since a real player has real week-to-week variance even
    without his own projection on file."""
    fallback_sd_by_pos = fallback_sd_by_pos or {}
    team_live_mean_sd: dict[int, tuple[float, float]] = {}
    live_points_by_team: dict[int, dict[str, float]] = {}
    for team_id, starters in started_by_team.items():
        mean_total = 0.0
        var_total = 0.0
        player_points: dict[str, float] = {}
        for pid in starters:
            p = players_by_id[pid]
            wp = next((w for w in p.get("weekly", []) if w["week"] == current_week), None)
            pregame_mean = p.get("this_week") or 0.0
            pregame_sd = wp["sd"] if wp and wp.get("sd") is not None else fallback_sd_by_pos.get(p["position"], 0.0)
            live = live_status.get(p["espn_id"])
            points_so_far = live[0] if live is not None else 0.0
            frac = remaining_frac.get(p["nfl_team"])
            if frac is None:
                frac = 0.0 if (live is not None and live[1]) else 1.0
            player_points[pid] = points_so_far
            mean_total += points_so_far + frac * pregame_mean
            var_total += (pregame_sd * (frac**0.5)) ** 2
        team_live_mean_sd[team_id] = (mean_total, var_total**0.5)
        live_points_by_team[team_id] = player_points
    return team_live_mean_sd, live_points_by_team


def standings_outputs(
    espn_teams: list,
    espn_matchups: list[Matchup],
    settings,
    sim_cfg: dict,
    current_week: int,
    team_week_mean: dict[tuple[int, int], float],
    team_week_sd: dict[tuple[int, int], float],
    team_live_mean_sd: dict[int, tuple[float, float]],
    live_points_by_team: dict[int, dict[str, float]],
    *,
    week_started: bool,
) -> tuple[list[dict], list[dict]]:
    """(schedule_out, standings_out) - real records/seeds plus simulated
    playoff odds. `week_started` gates the current week's matchup rows'
    `live` flag (and live home_score/away_score) on whether the week has
    actually kicked off - true whenever engine/live.py calls this at all
    (its own gate already confirmed a live/recently-finished game), computed
    by the full/refresh caller from ingest.nfl_data.week_for_kickoff so a
    nightly run before kickoff doesn't show a spurious 0-0 "(live)" score."""
    remaining = [
        RemainingMatchup(week=m.week, home_team_id=m.home_team_id, away_team_id=m.away_team_id)
        for m in espn_matchups if not m.played and m.week <= settings.reg_season_count
    ]

    # D8: simulate_playoffs' own per-week input for the CURRENT week is
    # overridden with the live in-progress distribution (rather than the
    # plain pregame projection) so playoff odds reflect games actually
    # underway, not just today's date.
    sim_team_week_mean = dict(team_week_mean)
    sim_team_week_sd = dict(team_week_sd)
    if week_started:
        for team_id, (mean, sd) in team_live_mean_sd.items():
            sim_team_week_mean[(team_id, current_week)] = mean
            sim_team_week_sd[(team_id, current_week)] = sd

    def _win_pcts(m: Matchup) -> tuple[float | None, float | None]:
        if m.played:
            return None, None
        if (
            week_started and m.week == current_week
            and m.home_team_id in team_live_mean_sd and m.away_team_id in team_live_mean_sd
        ):
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
        is_live = (
            week_started and not m.played and m.week == current_week
            and m.home_team_id in live_points_by_team and m.away_team_id in live_points_by_team
        )
        row = {
            "week": m.week, "home_team_id": m.home_team_id, "away_team_id": m.away_team_id,
            "home_score": m.home_score, "away_score": m.away_score, "played": m.played,
            "home_win_pct": home_win_pct, "away_win_pct": away_win_pct, "live": is_live,
        }
        if is_live:
            row["home_score"] = sum(live_points_by_team[m.home_team_id].values())
            row["away_score"] = sum(live_points_by_team[m.away_team_id].values())
        schedule_out.append(row)

    team_states = [
        TeamState(team_id=t.team_id, division_id=t.division_id, wins=t.wins, losses=t.losses, ties=t.ties, points_for=t.points_for)
        for t in espn_teams
    ]
    sim_results = simulate_playoffs(
        team_states, remaining, sim_team_week_mean, sim_team_week_sd,
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

    return schedule_out, standings_out
