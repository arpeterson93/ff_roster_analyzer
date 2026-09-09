"""Player-level points-allowed detail (Yahoo "points against" style): for
each defense/week, the team-total raw stats + fantasy points allowed to a
position, the opponent faced and whether the team being viewed was home,
plus every individual player's own line that week (no filtering - a single
team/week/position slice is naturally small, typically 1-4 players).

Stat columns are grouped into named blocks matching the site's grouped-header
UI (see stat_columns()); block CONTENTS are shared across QB/RB/WR/TE, but
block ORDER differs by position so each position's own strongest category
leads."""
from __future__ import annotations

import polars as pl

from engine.scoring import ScoringRules
from ingest.nfl_data import game_scores as _load_game_scores

PASSING_BLOCK = ("Passing", [("passing_yards", "Yds"), ("passing_tds", "TD"), ("passing_interceptions", "Int")])
RUSHING_BLOCK = ("Rushing", [("carries", "Att"), ("rushing_yards", "Yds"), ("rushing_tds", "TD")])
RECEIVING_BLOCK = ("Receiving", [("receptions", "Rec"), ("receiving_yards", "Yds"), ("receiving_tds", "TD"), ("targets", "Tgt")])
RET_TD_BLOCK = ("Ret", [("special_teams_tds", "TD")])
MISC_2PT_BLOCK = ("Misc", [("two_pt_conversions", "2PT")])
FUM_BLOCK = ("Fum", [("fumbles_lost_total", "Lost")])

# Block order per offense position - contents are identical, only the lead
# category (this position's own bread-and-butter stat) changes.
OFFENSE_BLOCK_ORDER = {
    "QB": [PASSING_BLOCK, RUSHING_BLOCK, RECEIVING_BLOCK, RET_TD_BLOCK, MISC_2PT_BLOCK, FUM_BLOCK],
    "RB": [RUSHING_BLOCK, RECEIVING_BLOCK, PASSING_BLOCK, RET_TD_BLOCK, MISC_2PT_BLOCK, FUM_BLOCK],
    "WR": [RECEIVING_BLOCK, RUSHING_BLOCK, PASSING_BLOCK, RET_TD_BLOCK, MISC_2PT_BLOCK, FUM_BLOCK],
}
OFFENSE_BLOCK_ORDER["TE"] = OFFENSE_BLOCK_ORDER["WR"]

KICKER_BLOCKS = [
    ("FG Made", [
        ("fg_made_0_19", "0-19"), ("fg_made_20_29", "20-29"), ("fg_made_30_39", "30-39"),
        ("fg_made_40_49", "40-49"), ("fg_made_50_59", "50-59"), ("fg_made_60_", "60+"),
    ]),
    ("PAT", [("pat_made", "Made")]),
]

# `xpr` (a defense returning a blocked/missed PAT for 2) has no nflverse
# team_stats column - always reported as None; see dst_points_against_detail.
DST_BLOCKS = [
    (None, [("points_allowed", "PA")]),
    ("Tackles", [("def_sacks", "Sack"), ("def_safeties", "Safety")]),
    ("Turnovers", [("def_interceptions", "Int"), ("fumble_recovery_opp", "Fum Rec")]),
    (None, [("def_tds", "TD")]),
    ("Misc", [("blocked_kicks", "Blk Kick")]),
    ("Ret", [("xpr", "XPR"), ("special_teams_tds", "TD")]),
]


def _blocks_for_position(position: str) -> list[tuple[str | None, list[tuple[str, str]]]]:
    if position == "K":
        return KICKER_BLOCKS
    if position == "DST":
        return DST_BLOCKS
    return OFFENSE_BLOCK_ORDER.get(position, [])


def stat_columns(position: str) -> list[dict]:
    """Flat [{key, label, group}] column list in display order, for the
    frontend to render grouped <th> headers from - `group` is None for a
    column with no overarching header (PA, the standalone DST "TD")."""
    return [
        {"key": key, "label": label, "group": group}
        for group, fields in _blocks_for_position(position)
        for key, label in fields
    ]


def _two_pt_conversions(row: dict) -> float:
    # All three conversion types are credited as the SAME "2PT" stat to
    # whichever offense they came from, matching how the league scores them.
    return (
        (row.get("passing_2pt_conversions") or 0)
        + (row.get("rushing_2pt_conversions") or 0)
        + (row.get("receiving_2pt_conversions") or 0)
    )


def points_against_detail(
    stats_df: pl.DataFrame,
    season: int,
    position: str,
    scoring: ScoringRules,
    is_home: dict[tuple[str, int], bool] | None = None,
) -> dict[str, dict[int, dict]]:
    """{defense_team: {week: {"points", "opponent" (offense faced), "home"
    (was the defense home), "stats": {field: total}, "players": [{"name",
    "points", "stats": {field: value}}, ...]}}}, players sorted by points
    desc."""
    is_home = is_home or {}
    fields = [c["key"] for c in stat_columns(position)]
    rows = stats_df.filter(
        (pl.col("season") == season) & (pl.col("season_type") == "REG") & (pl.col("position") == position)
    )
    result: dict[str, dict[int, dict]] = {}
    for row in rows.iter_rows(named=True):
        pts = scoring.points_for_row(row)
        defense, week, offense = row["opponent_team"], row["week"], row["team"]
        bucket = result.setdefault(defense, {}).setdefault(
            week,
            {
                "points": 0.0,
                "opponent": offense,
                "home": is_home.get((defense, week)),
                "stats": {f: 0 for f in fields},
                "players": [],
            },
        )
        row_stats = dict(row)
        row_stats["two_pt_conversions"] = _two_pt_conversions(row)
        bucket["points"] += pts
        for f in fields:
            bucket["stats"][f] = (bucket["stats"][f] or 0) + (row_stats.get(f) or 0)
        bucket["players"].append(
            {"name": row["player_display_name"], "points": round(pts, 1), "stats": {f: row_stats.get(f) for f in fields}}
        )

    for weeks in result.values():
        for wk in weeks.values():
            wk["points"] = round(wk["points"], 1)
            wk["players"].sort(key=lambda e: e["points"], reverse=True)
    return result


def dst_points_against_detail(
    team_stats_df: pl.DataFrame,
    schedules_df: pl.DataFrame,
    is_home: dict[tuple[str, int], bool],
    season: int,
    dst_scoring: ScoringRules,
) -> dict[str, dict[int, dict]]:
    """{offense_team: {week: {"points", "opponent" (the DEFENSE faced),
    "home", "stats"}}} - how many fantasy points the defense an offense
    played that week scored, i.e. that offense's own vulnerability to
    opposing DSTs. A DST is one unit, not many players, so there is no
    "players" breakdown (unlike points_against_detail)."""
    rows = team_stats_df.filter((pl.col("season") == season) & (pl.col("season_type") == "REG"))
    fields = [c["key"] for c in stat_columns("DST")]
    if rows.height == 0:
        return {}
    scores = _load_game_scores(schedules_df, season)
    offense_by_game: dict[tuple[str, str], dict] = {(row["game_id"], row["team"]): row for row in rows.iter_rows(named=True)}

    result: dict[str, dict[int, dict]] = {}
    for row in rows.iter_rows(named=True):
        defense, week, offense = row["team"], row["week"], row["opponent_team"]
        game = scores.get(row["game_id"])
        opp_offense_row = offense_by_game.get((row["game_id"], offense))
        if game is None or opp_offense_row is None or offense not in game:
            continue
        points_allowed = game[offense]
        yards_allowed = (opp_offense_row.get("passing_yards") or 0) + (opp_offense_row.get("rushing_yards") or 0)
        pts = dst_scoring.dst_points_for_row(row, points_allowed=points_allowed, yards_allowed=yards_allowed)
        stats = {
            "points_allowed": points_allowed,
            "def_sacks": row.get("def_sacks") or 0,
            "def_safeties": row.get("def_safeties") or 0,
            "def_interceptions": row.get("def_interceptions") or 0,
            "fumble_recovery_opp": row.get("fumble_recovery_opp") or 0,
            "def_tds": row.get("def_tds") or 0,
            "blocked_kicks": (row.get("def_punt_blocks") or 0) + (row.get("def_pat_blocks") or 0) + (row.get("def_fg_blocks") or 0),
            "xpr": None,
            "special_teams_tds": row.get("special_teams_tds") or 0,
        }
        result.setdefault(offense, {})[week] = {
            "points": round(pts, 1),
            "opponent": defense,
            "home": is_home.get((offense, week)),
            "stats": {f: stats.get(f) for f in fields},
        }
    return result
