"""Kicker expected fantasy points (xFP) from play-by-play.

ffopportunity (engine/expected_points.py) has no kicker model, so this builds one from what nflfastR already
publishes on every kick: `fg_prob` (its modelled chance the field goal is good, from distance and conditions) and
`extra_point_prob`. (A home-made smoothed distance curve was tried and dropped: it matched nflfastR on average but was
a little less accurate, especially on long kicks.) For each attempt the expected points under a league's own scoring are

    p(make) * points_if_made + (1 - p(make)) * points_if_missed

where each side is scored by pushing a one-kick stat row through ScoringRules.points_for_row - so distance brackets
(FG 0-39 / 40-49 / 50+ ...), missed-FG and missed-extra-point penalties, and any per-attempt items all follow the
league's rules instead of a fixed 3/1. A kicker's week is the sum over his attempts: it is what an average kicker
would be expected to score from the same kicks, so the gap to his real points is make-luck.

Blocked kicks count as misses (they are inside 1 - p). Two-point tries are not a kicker stat and are ignored.
"""
from __future__ import annotations

import polars as pl

from engine.scoring import ScoringRules

# (inclusive upper bound, nflverse bracket suffix) - the same brackets PLAYER_STAT_EXPR reads.
_FG_BRACKETS = [(19, "0_19"), (29, "20_29"), (39, "30_39"), (49, "40_49"), (59, "50_59")]
_FG_TOP_BRACKET = "60_"


def fg_bracket(distance: float) -> str:
    for upper, name in _FG_BRACKETS:
        if distance <= upper:
            return name
    return _FG_TOP_BRACKET


def attempt_points(rules: ScoringRules, kind: str, distance: float | None, made: bool) -> float:
    """League points for one kick, made or missed. kind is "fg" or "xp"."""
    if kind == "fg":
        bracket = fg_bracket(distance if distance is not None else 35.0)
        row = {"fg_att": 1}
        if made:
            row.update({"fg_made": 1, f"fg_made_{bracket}": 1})
        else:
            row.update({"fg_missed": 1, f"fg_missed_{bracket}": 1})
    else:
        row = {"pat_att": 1, "pat_made": 1} if made else {"pat_att": 1, "pat_missed": 1}
    return rules.points_for_row(row)


def expected_attempt_points(rules: ScoringRules, kind: str, distance: float | None, p_make: float) -> float:
    return p_make * attempt_points(rules, kind, distance, True) + (1.0 - p_make) * attempt_points(rules, kind, distance, False)


def _weekly(pbp: pl.DataFrame, rules: ScoringRules, key_cols: list[str]) -> dict[tuple, float]:
    out: dict[tuple, float] = {}
    if pbp.height == 0 or "fg_prob" not in pbp.columns:
        return out
    fgs = pbp.filter((pl.col("field_goal_attempt") == 1) & pl.col("kick_distance").is_not_null() & pl.col("fg_prob").is_not_null())
    for *key, dist, p in fgs.select(*key_cols, "kick_distance", "fg_prob").iter_rows():
        k = tuple(key)
        out[k] = out.get(k, 0.0) + expected_attempt_points(rules, "fg", dist, p)
    xps = pbp.filter((pl.col("extra_point_attempt") == 1) & pl.col("extra_point_prob").is_not_null())
    for *key, p in xps.select(*key_cols, "extra_point_prob").iter_rows():
        k = tuple(key)
        out[k] = out.get(k, 0.0) + expected_attempt_points(rules, "xp", None, p)
    return out


def kicker_weekly_xpts(pbp: pl.DataFrame, rules: ScoringRules) -> dict[tuple[str, int], float]:
    """{(kicker gsis id, week): expected fantasy points} for every kicker-week with a kick in `pbp`."""
    if pbp.height == 0:
        return {}
    pbp = pbp.filter(pl.col("kicker_player_id").is_not_null())
    return _weekly(pbp, rules, ["kicker_player_id", "week"])


def team_weekly_kicker_xpts(pbp: pl.DataFrame, rules: ScoringRules) -> dict[tuple[str, int, str], float]:
    """{(kicking team, week, "K"): expected fantasy points} - one team's kicker output per game, keyed like the
    other points-by-team tables so the Matchups page can turn it into points ALLOWED by each defense."""
    if pbp.height == 0:
        return {}
    pbp = pbp.filter(pl.col("posteam").is_not_null() & (pl.col("season_type") == "REG"))
    return {(team, week, "K"): v for (team, week), v in _weekly(pbp, rules, ["posteam", "week"]).items()}
