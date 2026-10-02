"""Per-play EXPECTED fantasy-points breakdown for an already-played game -
the expected-points twin of engine/play_log.py's actual per-play
breakdown, powering the Player Modal Game Log's second (expected)
chart/table underneath the existing actual one.

Offense only (QB/RB/WR/TE) - ffopportunity has no kicker or DST model (see
engine/expected_points.py), so neither gets an expected breakdown, same as
they get no xFPTS at all.

Unlike the actual version, every pass/rush play has SOME nonzero expected
value (a 2-yard completion probability still carries SOME chance of a
catch), so there's no real "zero_point_plays"/"incomplete_targets" split
here - every play a player touched gets one entry (see
engine/pipeline.py's wiring for the current call on that).

elapsed-time and short-field helpers are intentionally duplicated from
engine/play_log.py rather than imported - that module's are private by
convention (no cross-module private imports elsewhere in engine/), and
this module mirrors their exact logic rather than widening play_log.py's
own public surface for one caller.
"""
from __future__ import annotations

from engine.expected_points import expected_stat_row_from_pbp_pass, expected_stat_row_from_pbp_rush
from engine.scoring import ScoringRules

_REGULATION_SECONDS = 60 * 60
_OT_PERIOD_SECONDS_BY_SEASON_TYPE = {"POST": 15 * 60}
_DEFAULT_OT_PERIOD_SECONDS = 10 * 60
_SHORT_FIELD_YARDLINE = 5


def _elapsed_minutes(row: dict) -> float:
    """Same OT-aware elapsed-time calc as engine.play_log._elapsed_minutes -
    see that function's docstring for why OT needs special handling.
    `qtr` comes back as a categorical STRING ("1"/"2"/etc) from
    ff_opportunity_pbp, unlike real pbp's own numeric qtr column - coerced
    to int here (confirmed live: no non-numeric qtr value exists). Also
    unlike real pbp, ff_opportunity_pbp has no `season_type` or
    `quarter_seconds_remaining` column at all (confirmed live) - the
    `.get()` calls below already degrade gracefully for both (postseason
    OT falls back to the regular-season 10-minute period length, and OT
    elapsed time falls back to game_seconds_remaining), a cosmetic
    inaccuracy only for the rare postseason-OT case, not a crash."""
    qtr = row.get("qtr")
    qtr = int(qtr) if qtr is not None else None
    if qtr is not None and qtr > 4:
        period_seconds = _OT_PERIOD_SECONDS_BY_SEASON_TYPE.get(row.get("season_type"), _DEFAULT_OT_PERIOD_SECONDS)
        remaining = row.get("quarter_seconds_remaining")
        if remaining is None:
            remaining = row.get("game_seconds_remaining")
        remaining = max(0.0, min(period_seconds, remaining if remaining is not None else 0.0))
        ot_number = int(qtr) - 4
        elapsed = _REGULATION_SECONDS + (ot_number - 1) * period_seconds + (period_seconds - remaining)
        return round(elapsed / 60, 2)
    remaining = row.get("game_seconds_remaining")
    remaining = max(0.0, min(_REGULATION_SECONDS, remaining if remaining is not None else 0.0))
    return round((_REGULATION_SECONDS - remaining) / 60, 2)


def _short_field_yardline(row: dict) -> float | None:
    yardline = row.get("yardline_100")
    return yardline if yardline is not None and yardline <= _SHORT_FIELD_YARDLINE else None


def expected_passing_plays(pass_rows: list[dict], gsis_id: str, rules: ScoringRules) -> list[dict]:
    """[{elapsed_min, points, label, role, short_field, yardline}, ...] for
    every pass play this player attempted as the PASSER, ordered by game
    time - complete or not, every attempt gets an entry."""
    out = []
    for row in pass_rows:
        if row.get("passer_player_id") != gsis_id:
            continue
        stat_row = expected_stat_row_from_pbp_pass(row, role="pass")
        points = rules.points_for_row(stat_row)
        yardline = _short_field_yardline(row)
        receiver = row.get("receiver_full_name") or "?"
        out.append({
            "elapsed_min": _elapsed_minutes(row),
            "points": round(points, 2),
            "label": f"Pass to {receiver} ({stat_row['passing_yards']:.1f} xYds)",
            "role": "pass",
            "short_field": yardline is not None,
            "yardline": yardline,
        })
    out.sort(key=lambda p: p["elapsed_min"])
    return out


def expected_receiving_plays(pass_rows: list[dict], gsis_id: str, rules: ScoringRules) -> list[dict]:
    """Same as expected_passing_plays but from the RECEIVER's perspective
    on the same pass_rows (pbp_pass is one row per pass play, usable for
    both roles)."""
    out = []
    for row in pass_rows:
        if row.get("receiver_player_id") != gsis_id:
            continue
        stat_row = expected_stat_row_from_pbp_pass(row, role="rec")
        points = rules.points_for_row(stat_row)
        yardline = _short_field_yardline(row)
        passer = row.get("passer_full_name") or "?"
        out.append({
            "elapsed_min": _elapsed_minutes(row),
            "points": round(points, 2),
            "label": f"Target from {passer} ({stat_row['receiving_yards']:.1f} xYds, {stat_row['receptions']:.0%} xCatch)",
            "role": "reception",
            "short_field": yardline is not None,
            "yardline": yardline,
        })
    out.sort(key=lambda p: p["elapsed_min"])
    return out


def expected_rushing_plays(rush_rows: list[dict], gsis_id: str, rules: ScoringRules) -> list[dict]:
    """[{elapsed_min, points, label, role, short_field, yardline}, ...] for
    every rush play this player carried, ordered by game time."""
    out = []
    for row in rush_rows:
        if row.get("rusher_player_id") != gsis_id:
            continue
        stat_row = expected_stat_row_from_pbp_rush(row)
        points = rules.points_for_row(stat_row)
        yardline = _short_field_yardline(row)
        out.append({
            "elapsed_min": _elapsed_minutes(row),
            "points": round(points, 2),
            "label": f"Rush ({stat_row['rushing_yards']:.1f} xYds)",
            "role": "rush",
            "short_field": yardline is not None,
            "yardline": yardline,
        })
    out.sort(key=lambda p: p["elapsed_min"])
    return out


def expected_plays_for_player(
    pass_rows: list[dict], rush_rows: list[dict], gsis_id: str, rules: ScoringRules
) -> list[dict]:
    """Every expected-points play (passing + receiving + rushing) for one
    player in one week, combined and ordered by game time - the single
    list the Player Modal's second chart/table iterates over."""
    plays = (
        expected_passing_plays(pass_rows, gsis_id, rules)
        + expected_receiving_plays(pass_rows, gsis_id, rules)
        + expected_rushing_plays(rush_rows, gsis_id, rules)
    )
    plays.sort(key=lambda p: p["elapsed_min"])
    return plays
