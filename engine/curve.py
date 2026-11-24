"""Rank -> weekly fantasy points curve, built from actual historical performance
under this league's real scoring rules, blended with the current season as it
accumulates games."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl

from engine.scoring import ScoringRules, espn_points_allowed
from ingest.nfl_data import game_scores as _load_game_scores

OFFENSE_POSITIONS = ["QB", "RB", "WR", "TE", "K"]

# The per-rank SD table is an average over only a handful of players per rank per
# season, so it zigzags (a TE2 with a bigger SD than the TE1, say). Smoothed before use:
# RB/WR/TE spreads genuinely shrink as rank worsens, so they get a non-increasing
# (isotonic) fit plus a light moving average; QB/K/DST are roughly flat, so a wider
# moving average. See smooth_sd.
SD_MONOTONE_POSITIONS = frozenset({"RB", "WR", "TE"})
SD_SMOOTH_WINDOW = 9
SD_MONOTONE_WINDOW = 5


@dataclass
class Curve:
    ppg: dict[str, dict[int, float]] = field(default_factory=dict)
    sd: dict[str, dict[int, float]] = field(default_factory=dict)
    max_rank: dict[str, int] = field(default_factory=dict)
    position_avg: dict[str, float] = field(default_factory=dict)

    def ppg_at(self, pos: str, rank: int) -> float:
        table = self.ppg.get(pos)
        if not table:
            return 0.0
        r = max(1, min(rank, self.max_rank[pos]))
        return table[r]

    def sd_at(self, pos: str, rank: int) -> float:
        table = self.sd.get(pos)
        if not table:
            return 0.0
        r = max(1, min(rank, self.max_rank[pos]))
        return table[r]


def _moving_average(values: list[float], window: int) -> list[float]:
    """Centered moving average with the window truncated at the ends (still non-increasing for a non-increasing input)."""
    half = window // 2
    return [float(np.mean(values[max(0, i - half): i + half + 1])) for i in range(len(values))]


def _isotonic_non_increasing(values: list[float]) -> list[float]:
    """Pool-adjacent-violators: the closest non-increasing sequence (least squares)."""
    blocks: list[list[float]] = []  # [sum, count]
    for v in values:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s_, n_ = blocks.pop()
            blocks[-1][0] += s_
            blocks[-1][1] += n_
    out: list[float] = []
    for s_, n_ in blocks:
        out.extend([s_ / n_] * n_)
    return out


def smooth_sd(sd: dict[int, float], monotone: bool) -> dict[int, float]:
    """Smooth one position's {rank: SD} table. monotone=True (RB/WR/TE): non-increasing fit
    then a light moving average; otherwise a wider moving average."""
    if len(sd) < 3:
        return dict(sd)
    ranks = sorted(sd)
    vals = [sd[r] for r in ranks]
    if monotone:
        smoothed = _moving_average(_isotonic_non_increasing(vals), SD_MONOTONE_WINDOW)
    else:
        smoothed = _moving_average(vals, SD_SMOOTH_WINDOW)
    return dict(zip(ranks, smoothed))


def load_outcome_sd(path) -> dict[str, dict[str, dict[int, float]]] | None:
    """{"weekly": {pos: {rank: sd}}, "ros": {pos: {rank: sd}}} from tools/sd_by_rank's smoothed outcome-SD
    tables (the SD of a ranked player's actual weekly points, which includes the uncertainty in the rank
    itself), or None when the league has no such file."""
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return None
    positions = json.loads(p.read_text(encoding="utf-8")).get("positions", {})
    out: dict[str, dict[str, dict[int, float]]] = {"weekly": {}, "ros": {}}
    for pos, tables in positions.items():
        for kind in ("weekly", "ros"):
            rows = tables.get(kind) or []
            if rows:
                out[kind][pos] = {int(r[0]): float(r[4]) for r in rows}  # column 4 = smoothed SD
    return out if out["ros"] else None


def outcome_sd_at(table: dict[int, float], rank: int) -> float:
    """SD at `rank`, clamped to the table's range (deeper players take the last rank's SD); a rank missing
    from the table (a gap) takes the nearest listed rank."""
    r = int(rank)
    if r in table:
        return table[r]
    return table[min(table, key=lambda k: abs(k - r))]


def _player_season_ppg_sd(
    df: pl.DataFrame, season: int, position: str, scoring: ScoringRules, min_games: int
) -> list[tuple[float, float]]:
    """[(ppg, sd), ...] for every eligible player at `position` in `season`."""
    rows = df.filter(
        (pl.col("season") == season) & (pl.col("season_type") == "REG") & (pl.col("position") == position)
    )
    if rows.height == 0:
        return []
    by_player: dict[str, list[float]] = {}
    for row in rows.iter_rows(named=True):
        pts = scoring.points_for_row(row)
        by_player.setdefault(row["player_id"], []).append(pts)
    results = []
    for pts_list in by_player.values():
        if len(pts_list) >= min_games:
            arr = np.array(pts_list)
            results.append((float(arr.mean()), float(arr.std())))
    return results


def _dst_season_ppg_sd(
    df: pl.DataFrame, schedules_df: pl.DataFrame, season: int, scoring: ScoringRules, min_games: int
) -> list[tuple[float, float]]:
    rows = df.filter((pl.col("season") == season) & (pl.col("season_type") == "REG"))
    if rows.height == 0:
        return []
    game_scores = _load_game_scores(schedules_df, season)
    offense_by_game: dict[tuple[str, str], dict] = {
        (row["game_id"], row["team"]): row for row in rows.iter_rows(named=True)
    }
    by_team: dict[str, list[float]] = {}
    for row in rows.iter_rows(named=True):
        game = game_scores.get(row["game_id"])
        opp_offense = offense_by_game.get((row["game_id"], row["opponent_team"]))
        if game is None or opp_offense is None or row["opponent_team"] not in game:
            continue
        points_allowed = espn_points_allowed(game[row["opponent_team"]], opp_offense)
        yards_allowed = (opp_offense.get("passing_yards") or 0) + (opp_offense.get("rushing_yards") or 0)
        pts = scoring.dst_points_for_row(row, points_allowed=points_allowed, yards_allowed=yards_allowed)
        by_team.setdefault(row["team"], []).append(pts)
    results = []
    for pts_list in by_team.values():
        if len(pts_list) >= min_games:
            arr = np.array(pts_list)
            results.append((float(arr.mean()), float(arr.std())))
    return results


def _build_position_curve(
    per_season_samples: dict[int, list[tuple[float, float]]], starter_pool: int
) -> tuple[dict[int, float], dict[int, float], int]:
    """Average PPG/SD by rank across seasons, then a monotone non-increasing pass."""
    max_n = max((len(s) for s in per_season_samples.values()), default=0)
    if max_n == 0:
        return {}, {}, 0

    ppg_by_rank: dict[int, list[float]] = {r: [] for r in range(1, max_n + 1)}
    sd_by_rank: dict[int, list[float]] = {r: [] for r in range(1, max_n + 1)}
    for samples in per_season_samples.values():
        ranked = sorted(samples, key=lambda t: t[0], reverse=True)
        for i, (ppg, sd) in enumerate(ranked, start=1):
            ppg_by_rank[i].append(ppg)
            sd_by_rank[i].append(sd)

    ppg = {r: float(np.mean(vals)) for r, vals in ppg_by_rank.items() if vals}
    sd = {r: float(np.mean(vals)) for r, vals in sd_by_rank.items() if vals}

    ranks = sorted(ppg.keys())
    values = np.array([ppg[r] for r in ranks])
    monotone = np.minimum.accumulate(values)  # clip upward spikes so rank r+1 never exceeds rank r
    for r, v in zip(ranks, monotone):
        ppg[r] = float(v)

    max_rank = ranks[-1] if ranks else 0
    return ppg, sd, max_rank


def build_curve(
    player_stats_by_season: dict[int, pl.DataFrame],
    scoring: ScoringRules,
    min_games: int,
    positions: list[str],
    starter_pool: dict[str, int],
) -> Curve:
    """player_stats_by_season: {season: nflreadpy player_stats frame for that season}."""
    curve = Curve()
    for pos in positions:
        per_season = {
            season: _player_season_ppg_sd(df, season, pos, scoring, min_games)
            for season, df in player_stats_by_season.items()
        }
        ppg, sd, max_rank = _build_position_curve(per_season, starter_pool.get(pos, 32))
        if not ppg:
            continue
        curve.ppg[pos] = ppg
        curve.sd[pos] = smooth_sd(sd, pos in SD_MONOTONE_POSITIONS)
        curve.max_rank[pos] = max_rank
        n = starter_pool.get(pos, 32)
        top_n = [ppg[r] for r in range(1, min(n, max_rank) + 1)]
        curve.position_avg[pos] = float(np.mean(top_n)) if top_n else 0.0
    return curve


def build_dst_curve(
    team_stats_by_season: dict[int, pl.DataFrame],
    schedules_by_season: dict[int, pl.DataFrame],
    scoring: ScoringRules,
    min_games: int,
    starter_pool: int = 32,
) -> Curve:
    curve = Curve()
    per_season = {
        season: _dst_season_ppg_sd(df, schedules_by_season[season], season, scoring, min_games)
        for season, df in team_stats_by_season.items()
    }
    ppg, sd, max_rank = _build_position_curve(per_season, starter_pool)
    if ppg:
        curve.ppg["DST"] = ppg
        curve.sd["DST"] = smooth_sd(sd, False)
        curve.max_rank["DST"] = max_rank
        top_n = [ppg[r] for r in range(1, min(starter_pool, max_rank) + 1)]
        curve.position_avg["DST"] = float(np.mean(top_n)) if top_n else 0.0
    return curve


def blend_current(
    curve_hist: Curve,
    curve_cur: Curve,
    weeks_played: int,
    *,
    max_weight: float,
    full_weight_weeks: int,
) -> Curve:
    """Blend the current season's own curve into the historical one, weighted
    up to `max_weight` as `weeks_played` approaches `full_weight_weeks`."""
    if weeks_played <= 0:
        return curve_hist

    weight = min(weeks_played / full_weight_weeks, 1.0) * max_weight
    blended = Curve(position_avg=dict(curve_hist.position_avg))
    for pos, hist_table in curve_hist.ppg.items():
        cur_table = curve_cur.ppg.get(pos, {})
        cur_sd_table = curve_cur.sd.get(pos, {})
        hist_sd_table = curve_hist.sd.get(pos, {})
        max_rank = curve_hist.max_rank[pos]
        ppg = {}
        sd = {}
        for rank, hist_ppg in hist_table.items():
            cur_ppg = cur_table.get(rank)
            ppg[rank] = (1 - weight) * hist_ppg + weight * cur_ppg if cur_ppg is not None else hist_ppg
            hist_sd = hist_sd_table.get(rank, 0.0)
            cur_sd = cur_sd_table.get(rank)
            sd[rank] = (1 - weight) * hist_sd + weight * cur_sd if cur_sd is not None else hist_sd
        blended.ppg[pos] = ppg
        blended.sd[pos] = sd
        blended.max_rank[pos] = max_rank
    return blended
