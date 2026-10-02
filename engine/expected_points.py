"""ffopportunity expected-stat-component -> ScoringRules stat-row
translation (xFPTS). Mirrors engine/scoring.py's relationship to
engine/play_log.py: scoring.py's contract is "real raw stat -> real
points" with fail-loud behavior on unmapped categories, so this stays a
separate module rather than extending it - expected rows are a distinct,
partial-coverage concept (fumbles and return TDs have no expected
component anywhere in ffopportunity's model, confirmed against its R
source) that shouldn't weaken scoring.py's fail-loud guarantee for actual
scoring.

Deliberately feeds ScoringRules.points_for_row() the expected STAT
COMPONENTS rather than using ffopportunity's own pre-baked
`*_fantasy_points_exp` columns - those assume one fixed generic scoring
format (confirmed from ffopportunity's own R source, ep_summarize.R: 1pt/
reception, 0.1pt/receiving-yard, 6pt rec TD, 4pt pass TD, 0.04pt/passing-
yard, 2pt two-point, -2pt INT - not present in the published pbp_pass/
pbp_rush files anyway), which would silently misscore either of this
site's two leagues (O League standard, Any Given Sunday half-PPR).
"""
from __future__ import annotations

from typing import Literal

from engine.scoring import ScoringRules

# ffopportunity weekly column -> engine.scoring.PLAYER_STAT_EXPR key.
# `pass_attempt`/`rush_attempt`/`rec_attempt` are real opportunity counts
# (the chance itself already happened), not modeled outcomes - there's no
# "_exp" version of an opportunity that's already real, so the actual
# count is used directly. Everything else is ffopportunity's modeled
# expectation of what an average player would do with that exact same
# opportunity. Fumbles and return TDs have no expected analog anywhere in
# ffopportunity (verified: no `_exp` column exists for either, and
# ffopportunity's own internal per-play fantasy-points formula doesn't
# include a fumble term either) - any league scoring those categories gets
# a 0 contribution from the expected row, which is correct given no model
# exists, not a gap to paper over.
WEEKLY_EXP_STAT_MAP: dict[str, str] = {
    "pass_attempt": "attempts",
    "pass_completions_exp": "completions",
    "pass_yards_gained_exp": "passing_yards",
    "pass_touchdown_exp": "passing_tds",
    "pass_interception_exp": "passing_interceptions",
    "pass_two_point_conv_exp": "passing_2pt_conversions",
    "rush_attempt": "carries",
    "rush_yards_gained_exp": "rushing_yards",
    "rush_touchdown_exp": "rushing_tds",
    "rush_two_point_conv_exp": "rushing_2pt_conversions",
    "rush_first_down_exp": "rushing_first_downs",
    "rec_attempt": "targets",
    "receptions_exp": "receptions",
    "rec_yards_gained_exp": "receiving_yards",
    "rec_touchdown_exp": "receiving_tds",
    "rec_two_point_conv_exp": "receiving_2pt_conversions",
    "rec_first_down_exp": "receiving_first_downs",
}


def expected_stat_row_from_weekly(ff_opp_row: dict) -> dict:
    """One ffopportunity weekly row -> a stat dict shaped exactly like
    engine.scoring.PLAYER_STAT_EXPR expects, ready for
    ScoringRules.points_for_row(). Milestone-bonus categories (P300/RY100/
    etc) need no special-casing here - PLAYER_STAT_EXPR computes those
    directly off whatever's in rushing_yards/passing_yards/receiving_yards,
    so they evaluate against expected yardage automatically once those
    keys are populated below."""
    return {
        stat_key: ff_opp_row.get(col) or 0.0
        for col, stat_key in WEEKLY_EXP_STAT_MAP.items()
    }


def weekly_xfp_points(ff_opp_row: dict, rules: ScoringRules) -> float:
    return rules.points_for_row(expected_stat_row_from_weekly(ff_opp_row))


def _expected_pass_yards(row: dict) -> float:
    """complete_pass_exp * (yards_after_catch_exp + air_yards) - confirmed
    directly from ffopportunity's own R source (ep_summarize.R). Note: the
    published pbp_pass column is `pass_completion_exp`, not
    `complete_pass_exp` (that's ep_summarize.R's own internal rename of the
    same value, only ever used inside its own un-published intermediate
    dataframe). Null-guarded for throwaways/spikes/no-plays, where
    air_yards/yards_after_catch_exp come back null and the play
    contributes 0 expected yards rather than raising."""
    completion_exp = row.get("pass_completion_exp")
    air_yards = row.get("air_yards")
    yac_exp = row.get("yards_after_catch_exp")
    if completion_exp is None or air_yards is None or yac_exp is None:
        return 0.0
    return completion_exp * (yac_exp + air_yards)


def expected_stat_row_from_pbp_pass(row: dict, role: Literal["pass", "rec"]) -> dict:
    """Per-play expected stat row for one pass play, from the passer's or
    receiver's perspective. Deliberately the same narrower shape
    engine.play_log.py's own ACTUAL per-play stat rows use (no first-down
    category, no milestone brackets) - that function already treats
    per-play milestone/first-down categories as a known, accepted gap (see
    its own module docstring), and the expected visual is meant to be a
    directly comparable twin of the actual one, not a more complete
    version of it."""
    yards = _expected_pass_yards(row)
    touchdown_exp = row.get("pass_touchdown_exp") or 0.0
    two_point_exp = row.get("two_point_conv_exp") or 0.0
    if role == "pass":
        return {
            "passing_yards": yards,
            "passing_tds": touchdown_exp,
            "passing_interceptions": row.get("pass_interception_exp") or 0.0,
            "passing_2pt_conversions": two_point_exp,
        }
    return {
        "receptions": row.get("pass_completion_exp") or 0.0,
        "receiving_yards": yards,
        "receiving_tds": touchdown_exp,
        "receiving_2pt_conversions": two_point_exp,
    }


def expected_stat_row_from_pbp_rush(row: dict) -> dict:
    """Per-play expected stat row for one rush play. Uses the *cleaned*
    columns (rush_yards_exp/rush_touchdown_exp), NOT the raw model-output
    columns (rushing_yards_exp/rushing_td_exp) - the raw versions still
    include kneel-downs (which should be a guaranteed -1 yard, not a model
    prediction) and don't zero out on 2pt-attempt plays (which are scored
    separately via two_point_conv_exp) - confirmed from ffopportunity's own
    R source (ep_predict.R). Same narrower shape as play_log.py's actual
    rush stat row (no first-down category)."""
    return {
        "rushing_yards": row.get("rush_yards_exp") or 0.0,
        "rushing_tds": row.get("rush_touchdown_exp") or 0.0,
        "rushing_2pt_conversions": row.get("two_point_conv_exp") or 0.0,
    }
