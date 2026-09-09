"""Per-position, per-NFL-team points-allowed, expressed as an opponent-strength
-adjusted index around 1.0 (1.15 = allows 15% more than an average offense
would be expected to score against an average defense)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from engine.scoring import ScoringRules
from ingest.nfl_data import game_scores as _load_game_scores

OFFENSE_POSITIONS = ["QB", "RB", "WR", "TE", "K"]


@dataclass
class MatchupIndex:
    index: dict[str, dict[str, float]] = field(default_factory=dict)
    rank: dict[str, dict[str, int]] = field(default_factory=dict)
    allowed_ppg: dict[str, dict[str, float]] = field(default_factory=dict)
    l5_allowed_ppg: dict[str, dict[str, float]] = field(default_factory=dict)
    season_used: int | None = None


def points_by_team_week_pos(
    df: pl.DataFrame, season: int, positions: list[str], scoring: ScoringRules
) -> dict[tuple[str, int, str], float]:
    rows = df.filter(
        (pl.col("season") == season) & (pl.col("season_type") == "REG") & (pl.col("position").is_in(positions))
    )
    table: dict[tuple[str, int, str], float] = {}
    for row in rows.iter_rows(named=True):
        key = (row["team"], row["week"], row["position"])
        table[key] = table.get(key, 0.0) + scoring.points_for_row(row)
    return table


def dst_points_by_team_week_pos(
    team_stats_df: pl.DataFrame, schedules_df: pl.DataFrame, season: int, dst_scoring: ScoringRules
) -> dict[tuple[str, int, str], float]:
    """Keyed by (team, week, 'DST'): how many fantasy points that team's own
    DST unit scored that week - the exact same natural "team's own output"
    convention as points_by_team_week_pos for offense positions. Feeding this
    into the ordinary _index_for_basis machinery (no special-casing needed)
    naturally yields index['DST'][offense] = that offense's vulnerability to
    opposing defenses: _index_for_basis's own opponent lookup computes, for
    team X, "what X's week-w opponent's DST scored" as the value allowed BY
    X - i.e. exactly X's own vulnerability - normalized by that opposing
    defense's average scoring quality. That is precisely what project_player
    needs when it looks up index['DST'][opponent_this_week]."""
    rows = team_stats_df.filter((pl.col("season") == season) & (pl.col("season_type") == "REG"))
    if rows.height == 0:
        return {}
    scores = _load_game_scores(schedules_df, season)
    offense_by_game: dict[tuple[str, str], dict] = {
        (row["game_id"], row["team"]): row for row in rows.iter_rows(named=True)
    }
    table: dict[tuple[str, int, str], float] = {}
    for row in rows.iter_rows(named=True):
        game = scores.get(row["game_id"])
        opp_offense = offense_by_game.get((row["game_id"], row["opponent_team"]))
        if game is None or opp_offense is None or row["opponent_team"] not in game:
            continue
        points_allowed = game[row["opponent_team"]]
        yards_allowed = (opp_offense.get("passing_yards") or 0) + (opp_offense.get("rushing_yards") or 0)
        pts = dst_scoring.dst_points_for_row(row, points_allowed=points_allowed, yards_allowed=yards_allowed)
        key = (row["team"], row["week"], "DST")
        table[key] = table.get(key, 0.0) + pts
    return table


def team_weeks_from_opponent(opponent: dict[tuple[str, int], str | None], teams: list[str], all_weeks: list[int]) -> dict[str, list[int]]:
    return {t: [w for w in all_weeks if opponent.get((t, w)) is not None] for t in teams}


def allowed_by_team_week_pos(
    points_by_team_week_pos: dict[tuple[str, int, str], float],
    opponent: dict[tuple[str, int], str | None],
    team_weeks: dict[str, list[int]],
    positions: list[str],
) -> dict[tuple[str, int, str], float]:
    """The raw (not index-normalized) points-allowed grid behind the
    forward-looking index: actual points each team allowed at each position,
    per week they played. Used for a "recent results" actual-history view,
    as opposed to MatchupIndex's forward-looking projection input."""
    result: dict[tuple[str, int, str], float] = {}
    for team, weeks in team_weeks.items():
        for w in weeks:
            opp = opponent.get((team, w))
            if opp is None:
                continue
            for pos in positions:
                result[(team, w, pos)] = points_by_team_week_pos.get((opp, w, pos), 0.0)
    return result


def _index_for_basis(
    team_week_pos_points: dict[tuple[str, int, str], float],
    opponent: dict[tuple[str, int], str | None],
    team_weeks: dict[str, list[int]],
    positions: list[str],
    weeks_subset_fn,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    """Returns (index, allowed_ppg) for each position/team, using only the weeks
    weeks_subset_fn(team) selects out of that team's played weeks (season = all,
    l5 = the trailing 5)."""
    offense_avg: dict[tuple[str, str], float] = {}
    for team, weeks in team_weeks.items():
        for pos in positions:
            vals = [team_week_pos_points.get((team, w, pos), 0.0) for w in weeks]
            offense_avg[(team, pos)] = float(np.mean(vals)) if vals else 0.0

    index: dict[str, dict[str, float]] = {pos: {} for pos in positions}
    allowed_ppg: dict[str, dict[str, float]] = {pos: {} for pos in positions}
    for defense, weeks in team_weeks.items():
        subset = weeks_subset_fn(weeks)
        for pos in positions:
            ratios = []
            allowed_vals = []
            for w in subset:
                opp = opponent.get((defense, w))
                if opp is None:
                    continue
                allowed = team_week_pos_points.get((opp, w, pos), 0.0)
                allowed_vals.append(allowed)
                denom = offense_avg.get((opp, pos), 0.0)
                if denom > 0:
                    ratios.append(allowed / denom)
            index[pos][defense] = float(np.mean(ratios)) if ratios else 1.0
            allowed_ppg[pos][defense] = float(np.mean(allowed_vals)) if allowed_vals else 0.0
    return index, allowed_ppg


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def _rank_positions(index: dict[str, dict[str, float]]) -> dict[str, dict[str, int]]:
    """1 = best/easiest matchup for the offense (highest index, allows the
    most points), N = toughest. This is the "he's got a #1 matchup this
    week" convention - matches how the site colors and sorts by rank."""
    rank: dict[str, dict[str, int]] = {}
    for pos, team_index in index.items():
        ordered = sorted(team_index.items(), key=lambda kv: kv[1], reverse=True)
        rank[pos] = {team: i + 1 for i, (team, _) in enumerate(ordered)}
    return rank


def compute_matchup_index(
    current_points: dict[tuple[str, int, str], float],
    prior_points: dict[tuple[str, int, str], float],
    current_season: int,
    prior_season: int,
    weeks_played: int,
    opponent: dict[tuple[str, int], str | None],
    prior_opponent: dict[tuple[str, int], str | None],
    positions: list[str],
    *,
    pa_basis: str,
    pa_l5_weight: float,
    pa_prior_season_weeks: int,
    index_clamp: tuple[float, float],
) -> MatchupIndex:
    """Opponent-strength-adjusted points-allowed index, generic over any
    "position" whose points-by-team-week table is supplied - offense
    positions (from points_by_team_week_pos) and the DST pseudo-position
    (from dst_points_by_team_week_pos) share this exact code path; a caller
    with both should merge their tables and pass one combined `positions`
    list so DST gets ranked/blended identically to everything else.

    `opponent` and `prior_opponent` MUST be each season's own schedule - who
    a team played in a given week differs year to year, so the prior-season
    ratios have to be built from the prior season's own opponent map, not
    reused from the current season's."""
    teams = sorted({t for (t, _), o in opponent.items() if o is not None} | {o for o in opponent.values() if o})
    all_weeks = sorted({w for (_, w) in opponent.keys()})
    team_weeks = team_weeks_from_opponent(opponent, teams, all_weeks)

    prior_all_weeks = sorted({w for (_, w) in prior_opponent.keys()})
    prior_team_weeks = team_weeks_from_opponent(prior_opponent, teams, prior_all_weeks)
    prior_index, prior_allowed = _index_for_basis(prior_points, prior_opponent, prior_team_weeks, positions, lambda ws: ws)

    result = MatchupIndex()

    if weeks_played <= 0:
        for pos in positions:
            result.index[pos] = {t: _clamp(v, index_clamp) for t, v in prior_index[pos].items()}
            result.allowed_ppg[pos] = dict(prior_allowed[pos])
            result.l5_allowed_ppg[pos] = dict(prior_allowed[pos])
        result.rank = _rank_positions(result.index)
        result.season_used = prior_season
        return result

    cur_points = current_points
    played_weeks = {t: [w for w in ws if w <= weeks_played] for t, ws in team_weeks.items()}

    cur_season_index, cur_season_allowed = _index_for_basis(
        cur_points, opponent, played_weeks, positions, lambda ws: ws
    )
    cur_l5_index, cur_l5_allowed = _index_for_basis(
        cur_points, opponent, played_weeks, positions, lambda ws: ws[-5:]
    )

    if pa_basis == "season":
        cur_index = cur_season_index
    elif pa_basis == "l5":
        cur_index = cur_l5_index
    else:  # blend
        cur_index = {
            pos: {
                t: pa_l5_weight * cur_l5_index[pos].get(t, 1.0) + (1 - pa_l5_weight) * cur_season_index[pos].get(t, 1.0)
                for t in cur_season_index[pos]
            }
            for pos in positions
        }

    w = min(weeks_played / pa_prior_season_weeks, 1.0) if pa_prior_season_weeks > 0 else 1.0
    for pos in positions:
        blended = {}
        for t in teams:
            cur_v = cur_index[pos].get(t, 1.0)
            prior_v = prior_index[pos].get(t, 1.0)
            blended[t] = _clamp(w * cur_v + (1 - w) * prior_v, index_clamp)
        result.index[pos] = blended
        result.allowed_ppg[pos] = dict(cur_season_allowed[pos])
        result.l5_allowed_ppg[pos] = dict(cur_l5_allowed[pos])

    result.rank = _rank_positions(result.index)
    result.season_used = current_season if w >= 1.0 else prior_season
    return result
