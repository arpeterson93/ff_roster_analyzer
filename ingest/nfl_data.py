"""Thin wrappers over nflreadpy: polars frames, team-abbrev normalization, and a
local file cache so repeated local runs don't re-download the 4 history seasons.

nflreadpy's own cache is in-memory only by default and does not persist across
process invocations, so it does not risk caching a stale 404 for the current
season across pipeline runs (see CURRENT_SEASON_TTL below for the local-cache
equivalent).
"""
from __future__ import annotations

import time
from pathlib import Path

import nflreadpy as nfl
import polars as pl

CACHE_DIR = Path(".cache/nflverse")
CURRENT_SEASON_TTL = 12 * 3600  # seconds; past seasons never change, cached forever

# Canonical NFL team abbreviations (nflverse's own convention) that everything
# in engine/ can rely on. Maps ESPN, FantasyPros, and DynastyProcess variants
# (plus a couple of legacy relocations that still appear in older data) to it.
_TEAM_ALIASES = {
    # ESPN
    "LAR": "LA",
    "WSH": "WAS",
    # FantasyPros
    "JAC": "JAX",
    # DynastyProcess (load_ff_playerids)
    "LVR": "LV",
    "GBP": "GB",
    "KCC": "KC",
    "NEP": "NE",
    "NOS": "NO",
    "SFO": "SF",
    "TBB": "TB",
    # legacy relocations
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
}


def normalize_team(abbr: str | None) -> str | None:
    if abbr is None:
        return None
    return _TEAM_ALIASES.get(abbr, abbr)


def _normalize_team_columns(df: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    exprs = [pl.col(col).replace(_TEAM_ALIASES).alias(col) for col in columns if col in df.columns]
    if not exprs:
        return df
    return df.with_columns(exprs)


def _cache_path(name: str, seasons: list[int]) -> Path:
    key = "-".join(str(s) for s in seasons)
    return CACHE_DIR / f"{name}_{key}.parquet"


def _is_current_season(seasons: list[int], current_season: int | None) -> bool:
    return current_season is not None and current_season in seasons


def _cached_load(name: str, seasons: list[int], loader, *, current_season: int | None) -> pl.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(name, seasons)
    fresh_for_current = _is_current_season(seasons, current_season)
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if not fresh_for_current or age < CURRENT_SEASON_TTL:
            return pl.read_parquet(path)
    df = loader()
    df.write_parquet(path)
    return df


def schedules(seasons: int | list[int], current_season: int | None = None) -> pl.DataFrame:
    seasons_list = [seasons] if isinstance(seasons, int) else list(seasons)
    df = _cached_load(
        "schedules", seasons_list, lambda: nfl.load_schedules(seasons_list), current_season=current_season
    )
    return _normalize_team_columns(df, ["away_team", "home_team"])


def player_stats(seasons: int | list[int], current_season: int | None = None) -> pl.DataFrame:
    """Weekly per-player stats. Returns an empty frame with the expected columns
    if the season's file does not exist yet (e.g. the current season before
    week 1 completes)."""
    seasons_list = [seasons] if isinstance(seasons, int) else list(seasons)
    try:
        df = _cached_load(
            "player_stats",
            seasons_list,
            lambda: nfl.load_player_stats(seasons_list),
            current_season=current_season,
        )
    except ConnectionError:
        # Load a season with data purely to steal its schema (columns/dtypes),
        # then return an empty frame with the same shape.
        probe = _cached_load(
            "player_stats_schema_probe",
            [2023],
            lambda: nfl.load_player_stats([2023]),
            current_season=None,
        )
        return probe.clear()
    return _normalize_team_columns(df, ["team", "opponent_team"])


def team_stats(seasons: int | list[int], current_season: int | None = None) -> pl.DataFrame:
    seasons_list = [seasons] if isinstance(seasons, int) else list(seasons)
    try:
        df = _cached_load(
            "team_stats", seasons_list, lambda: nfl.load_team_stats(seasons_list), current_season=current_season
        )
    except ConnectionError:
        probe = _cached_load(
            "team_stats_schema_probe",
            [2023],
            lambda: nfl.load_team_stats([2023]),
            current_season=None,
        )
        return probe.clear()
    return _normalize_team_columns(df, ["team", "opponent_team"])


def playerids() -> pl.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "playerids.parquet"
    if path.exists() and (time.time() - path.stat().st_mtime) < CURRENT_SEASON_TTL:
        df = pl.read_parquet(path)
    else:
        df = nfl.load_ff_playerids()
        df.write_parquet(path)
    return _normalize_team_columns(df, ["team"])


def players() -> pl.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / "players.parquet"
    if path.exists() and (time.time() - path.stat().st_mtime) < CURRENT_SEASON_TTL:
        df = pl.read_parquet(path)
    else:
        df = nfl.load_players()
        df.write_parquet(path)
    return _normalize_team_columns(df, ["team"] if "team" in df.columns else [])


def game_scores(schedules_df: pl.DataFrame, season: int) -> dict[str, dict[str, float]]:
    """{game_id: {team: points_scored}} for a REG season, from load_schedules."""
    rows = schedules_df.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
    scores: dict[str, dict[str, float]] = {}
    for row in rows.iter_rows(named=True):
        if row["home_score"] is None or row["away_score"] is None:
            continue
        scores[row["game_id"]] = {row["home_team"]: row["home_score"], row["away_team"]: row["away_score"]}
    return scores


def home_away_from_schedule(schedules_df: pl.DataFrame, season: int) -> dict[tuple[str, int], bool]:
    """{(team, week): True if that team is home that week}, REG season only."""
    reg = schedules_df.filter(pl.col("season") == season, pl.col("game_type") == "REG")
    is_home: dict[tuple[str, int], bool] = {}
    for row in reg.iter_rows(named=True):
        is_home[(row["home_team"], row["week"])] = True
        is_home[(row["away_team"], row["week"])] = False
    return is_home


def nfl_week_context(
    season: int, schedules_df: pl.DataFrame
) -> tuple[int, dict[str, int], dict[tuple[str, int], str | None]]:
    """Derive (weeks_played, bye_weeks, opponent) from a REG-season schedule frame.

    weeks_played = highest week with both scores present (0 if none yet).
    bye_weeks[team] = the one week that team has no game in the REG schedule.
    opponent[(team, week)] = opposing team, or None if that team is on bye.
    """
    reg = schedules_df.filter(pl.col("season") == season, pl.col("game_type") == "REG")

    played = reg.filter(pl.col("away_score").is_not_null() & pl.col("home_score").is_not_null())
    weeks_played = int(played["week"].max()) if played.height else 0

    all_weeks = sorted(reg["week"].unique().to_list())
    teams = sorted(set(reg["away_team"].to_list()) | set(reg["home_team"].to_list()))

    opponent: dict[tuple[str, int], str | None] = {}
    team_weeks: dict[str, set[int]] = {t: set() for t in teams}
    for row in reg.iter_rows(named=True):
        opponent[(row["away_team"], row["week"])] = row["home_team"]
        opponent[(row["home_team"], row["week"])] = row["away_team"]
        team_weeks[row["away_team"]].add(row["week"])
        team_weeks[row["home_team"]].add(row["week"])

    bye_weeks: dict[str, int] = {}
    for team in teams:
        missing = [w for w in all_weeks if w not in team_weeks[team]]
        if missing:
            bye_weeks[team] = missing[0]
        for w in all_weeks:
            opponent.setdefault((team, w), None if w == bye_weeks.get(team) else opponent.get((team, w)))

    return weeks_played, bye_weeks, opponent
