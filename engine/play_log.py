"""Per-play fantasy-scoring breakdown for an already-played game - powers
the player modal's expandable Game Log row: a bar-per-play chart (x-axis =
elapsed game time, y-axis = points scored on that specific play) to show
WHEN scoring happened - e.g. whether it was garbage-time accumulation -
not just the game total, plus a short table of the biggest individual
plays (see the conversation this was built from).

Offense/kicker only (QB/RB/WR/TE/K) - a defense's scoring isn't
attributable to individual plays the same clean way (points-allowed
tiers, aggregate sacks/turnovers over the whole game), so DST keeps its
existing game-log table with no per-play breakdown.

Deliberately does NOT touch nflverse's own free-text `desc` column (which
includes tackler names, formation notes, etc. - more detail than wanted
here) - each play's label is built fresh from the same structured columns
used to compute its points (yards, TD flag, who threw/caught it), so it's
short, consistent, and mobile-friendly by construction rather than
needing text scrubbing.

Known, accepted gap: a handful of ScoringRules categories are GAME-LEVEL
milestones (P300/RY100/etc, "every N yards" bonuses) that don't attribute
to one single play - they're simply not computed here, so a player using
one of those categories will show per-play points summing to slightly
less than their real game total. Not worth chasing for a "when did the
scoring happen" visualization.
"""
from __future__ import annotations

from engine.scoring import ScoringRules

_REGULATION_SECONDS = 60 * 60

# nflverse's game_seconds_remaining resets to the OT period's own length
# once a game reaches overtime (verified against real OT games - it does
# NOT keep counting down from 3600), so OT elapsed time has to be computed
# separately from quarter_seconds_remaining and stacked after regulation
# rather than read straight off game_seconds_remaining like every other
# quarter. Regular-season OT is a single untimed-in-practice 10-minute
# period (since the 2017 rule change); the playoffs use a full 15-minute
# period and can go to a second one.
_OT_PERIOD_SECONDS_BY_SEASON_TYPE = {"POST": 15 * 60}
_DEFAULT_OT_PERIOD_SECONDS = 10 * 60

_FG_BUCKET_BY_DISTANCE = [
    (20, "fg_made_0_19"), (30, "fg_made_20_29"), (40, "fg_made_30_39"),
    (50, "fg_made_40_49"), (60, "fg_made_50_59"),
]


def _elapsed_minutes(row: dict) -> float:
    """Minutes since kickoff for this play - extended past the 60-minute
    regulation mark for overtime (one OT period's worth of minutes stacked
    on top of the last, via `qtr` - 5 is the 1st OT, 6 the 2nd, etc.)
    instead of clamping every OT play onto the Q4 boundary."""
    qtr = row.get("qtr")
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


def _short_name(name: str | None) -> str:
    return name or "?"


def _fg_bucket(distance: float) -> str:
    for ceiling, bucket in _FG_BUCKET_BY_DISTANCE:
        if distance < ceiling:
            return bucket
    return "fg_made_60_"


def game_durations_by_game_id(week_pbp_rows: list[dict]) -> dict[str, float]:
    """{game_id: minutes} - the real length of each game this week,
    including any overtime, computed across EVERY play in the game (not
    just one player's own plays). A player's own last touch can land well
    before the game actually ended (e.g. the other team wins it on a late
    OT field goal without this player getting the ball back) - sizing the
    chart's x-axis off only their own plays would size the OT segment to
    wherever their involvement happened to stop, not the game's real end."""
    durations: dict[str, float] = {}
    for row in week_pbp_rows:
        game_id = row.get("game_id")
        if not game_id:
            continue
        elapsed = _elapsed_minutes(row)
        if elapsed > durations.get(game_id, 0.0):
            durations[game_id] = elapsed
    return durations


def build_game_play_index(week_pbp_rows: list[dict]) -> dict[str, list[dict]]:
    """{gsis_id: [raw pbp row, ...]} - every play a player passed, rushed,
    received, or kicked on, for one week's worth of plays across every
    game. One pass over the week's plays regardless of how many players
    need a breakdown, rather than re-filtering per player."""
    index: dict[str, list[dict]] = {}
    for row in week_pbp_rows:
        for col in ("passer_player_id", "rusher_player_id", "receiver_player_id", "kicker_player_id"):
            pid = row.get(col)
            if pid:
                index.setdefault(pid, []).append(row)
    return index


def _play_stat_row_and_label(row: dict, gsis_id: str) -> tuple[dict, str, bool] | None:
    """(incremental stat row for ScoringRules.points_for_row, short clean
    label, is_td) for the ONE role this player had on this specific play -
    a play only ever earns them points for whichever role (passer/rusher/
    receiver/kicker) their id matches, never more than one on the same
    play. is_td powers the chart's gold TD-bar highlight, kept as an
    explicit flag rather than re-parsed from the label."""
    two_pt = row.get("two_point_conv_result") == "success"

    if row.get("passer_player_id") == gsis_id:
        yards = row.get("passing_yards") or 0
        is_td = bool(row.get("pass_touchdown"))
        is_int = bool(row.get("interception"))
        stat_row = {
            "passing_yards": yards, "passing_tds": 1 if is_td else 0,
            "passing_interceptions": 1 if is_int else 0, "passing_2pt_conversions": 1 if two_pt else 0,
        }
        if two_pt:
            label = "2pt conversion pass"
        elif is_int:
            label = "Interception thrown"
        else:
            label = f"{yards:.0f} yd pass to {_short_name(row.get('receiver_player_name'))}" + (" (TD)" if is_td else "")
        return stat_row, label, is_td

    if row.get("rusher_player_id") == gsis_id:
        yards = row.get("rushing_yards") or 0
        is_td = bool(row.get("rush_touchdown"))
        fumbled = bool(row.get("fumble_lost")) and row.get("fumbled_1_player_id") == gsis_id
        stat_row = {
            "rushing_yards": yards, "rushing_tds": 1 if is_td else 0,
            "fumbles_lost_total": 1 if fumbled else 0, "rushing_2pt_conversions": 1 if two_pt else 0,
        }
        if fumbled:
            label = "Fumble lost (rush)"
        elif two_pt:
            label = "2pt conversion rush"
        else:
            label = f"{yards:.0f} yd rush" + (" (TD)" if is_td else "")
        return stat_row, label, is_td

    if row.get("receiver_player_id") == gsis_id:
        is_catch = bool(row.get("complete_pass"))
        yards = (row.get("receiving_yards") or 0) if is_catch else 0
        is_td = is_catch and bool(row.get("pass_touchdown"))
        fumbled = is_catch and bool(row.get("fumble_lost")) and row.get("fumbled_1_player_id") == gsis_id
        stat_row = {
            "receptions": 1 if is_catch else 0, "receiving_yards": yards,
            "receiving_tds": 1 if is_td else 0, "fumbles_lost_total": 1 if fumbled else 0,
            "receiving_2pt_conversions": 1 if (two_pt and is_catch) else 0,
        }
        if fumbled:
            label = "Fumble lost (reception)"
        elif not is_catch:
            label = f"Incomplete target from {_short_name(row.get('passer_player_name'))}"
        elif two_pt:
            label = "2pt conversion catch"
        else:
            label = f"{yards:.0f} yd catch from {_short_name(row.get('passer_player_name'))}" + (" (TD)" if is_td else "")
        return stat_row, label, is_td

    if row.get("kicker_player_id") == gsis_id:
        if row.get("field_goal_attempt") and row.get("field_goal_result") == "made":
            dist = row.get("kick_distance") or 0
            return {_fg_bucket(dist): 1}, f"{dist:.0f} yd field goal", False
        if row.get("extra_point_attempt") and row.get("extra_point_result") == "good":
            return {"pat_made": 1}, "Extra point", False
    return None


def incomplete_targets_for_player(plays: list[dict], gsis_id: str) -> list[dict]:
    """[{elapsed_min, label}, ...] for every target this player was NOT
    credited a completion on - always 0 fantasy points, so never a bar in
    scoring_plays_for_player, but still a real, time-stamped event worth
    marking on the same axis (a string of drops/incompletions is exactly
    the kind of "how did they actually get their points" context the chart
    exists for)."""
    out = []
    for row in plays:
        if row.get("receiver_player_id") != gsis_id or row.get("complete_pass"):
            continue
        out.append({
            "elapsed_min": _elapsed_minutes(row),
            "label": f"Incomplete target from {_short_name(row.get('passer_player_name'))}",
        })
    out.sort(key=lambda p: p["elapsed_min"])
    return out


def scoring_plays_for_player(plays: list[dict], gsis_id: str, rules: ScoringRules) -> list[dict]:
    """[{elapsed_min, points, label, is_td}, ...] ordered by game time, for
    every play in `plays` (this player's own subset from
    build_game_play_index) where they earned a nonzero fantasy-point
    contribution - a routine incomplete target/non-scoring carry
    contributes 0 and is dropped, not shown as a zero-height bar."""
    out = []
    for row in plays:
        result = _play_stat_row_and_label(row, gsis_id)
        if result is None:
            continue
        stat_row, label, is_td = result
        points = rules.points_for_row(stat_row)
        if points == 0:
            continue
        out.append({
            "elapsed_min": _elapsed_minutes(row),
            "points": round(points, 2),
            "label": label,
            "is_td": is_td,
        })
    out.sort(key=lambda p: p["elapsed_min"])
    return out
