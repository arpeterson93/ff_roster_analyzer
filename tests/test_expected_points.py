from engine.expected_points import (
    expected_stat_row_from_pbp_pass, expected_stat_row_from_pbp_rush,
    expected_stat_row_from_weekly, weekly_xfp_points,
)
from engine.scoring import ScoringRules

_STANDARD = ScoringRules(
    items=[
        {"id": 1, "abbr": "PY", "points": 0.04}, {"id": 2, "abbr": "PTD", "points": 4},
        {"id": 3, "abbr": "INTT", "points": -2},
        {"id": 4, "abbr": "RY", "points": 0.1}, {"id": 5, "abbr": "RTD", "points": 6},
        {"id": 6, "abbr": "REY", "points": 0.1}, {"id": 7, "abbr": "RETD", "points": 6},
        {"id": 8, "abbr": "INC", "points": -0.5},
    ],
    is_dst=False,
)

_HALF_PPR = ScoringRules(
    items=[
        {"id": 1, "abbr": "PY", "points": 0.04}, {"id": 2, "abbr": "PTD", "points": 4},
        {"id": 6, "abbr": "REY", "points": 0.1}, {"id": 7, "abbr": "RETD", "points": 6},
        {"id": 9, "abbr": "REC", "points": 0.5},
    ],
    is_dst=False,
)


def _weekly_row(**overrides):
    row = {
        "pass_attempt": 30, "pass_completions_exp": 20.0, "pass_yards_gained_exp": 210.0,
        "pass_touchdown_exp": 1.2, "pass_interception_exp": 0.6, "pass_two_point_conv_exp": 0.0,
        "rush_attempt": 0, "rush_yards_gained_exp": 0.0, "rush_touchdown_exp": 0.0,
        "rush_two_point_conv_exp": 0.0, "rush_first_down_exp": 0.0,
        "rec_attempt": 0, "receptions_exp": 0.0, "rec_yards_gained_exp": 0.0,
        "rec_touchdown_exp": 0.0, "rec_two_point_conv_exp": 0.0, "rec_first_down_exp": 0.0,
    }
    row.update(overrides)
    return row


def test_weekly_map_uses_actual_opportunity_counts_not_exp():
    # pass_attempt/rush_attempt/rec_attempt are real counts fed straight in,
    # not derived from any _exp column - confirmed no such "_exp" variant
    # of an opportunity count exists in ffopportunity's weekly data.
    stat_row = expected_stat_row_from_weekly(_weekly_row(pass_attempt=33))
    assert stat_row["attempts"] == 33


def test_weekly_incompletions_are_not_negative():
    # Regression test for the gap caught in review: without `attempts` in
    # the map, INC (attempts - completions) would read as 0 - completions_exp,
    # a negative incompletion count that hands out bonus points instead of
    # docking them for a league scoring INC as a penalty.
    stat_row = expected_stat_row_from_weekly(_weekly_row(pass_attempt=30, pass_completions_exp=20.0))
    assert stat_row["attempts"] - stat_row["completions"] == 10.0


def _receiving_only_row(**overrides):
    # Zeroes out the fixture's default passing stats so only the
    # reception-related computation is under test - _STANDARD also scores
    # INC (attempts - completions), which the shared default pass_attempt/
    # pass_completions_exp would otherwise silently fold into the total.
    row = _weekly_row(pass_attempt=0, pass_completions_exp=0.0, pass_yards_gained_exp=0.0,
                       pass_touchdown_exp=0.0, pass_interception_exp=0.0)
    row.update(overrides)
    return row


def test_weekly_xfpts_respects_standard_scoring_no_ppr_bonus():
    row = _receiving_only_row(rec_attempt=10, receptions_exp=7.0, rec_yards_gained_exp=70.0, rec_touchdown_exp=0.5)
    points = weekly_xfp_points(row, _STANDARD)
    # No REC item in _STANDARD - only yardage/TD should count.
    assert points == 70.0 * 0.1 + 0.5 * 6


def test_weekly_xfpts_respects_half_ppr_reception_bonus():
    row = _receiving_only_row(rec_attempt=10, receptions_exp=7.0, rec_yards_gained_exp=70.0, rec_touchdown_exp=0.5)
    points = weekly_xfp_points(row, _HALF_PPR)
    assert points == 70.0 * 0.1 + 0.5 * 6 + 7.0 * 0.5


def test_weekly_xfpts_differs_between_leagues_for_the_same_player_week():
    # The whole point of D2: the same expected-stat row must score
    # differently under different leagues' real scoring rules.
    row = _receiving_only_row(rec_attempt=10, receptions_exp=7.0, rec_yards_gained_exp=70.0, rec_touchdown_exp=0.5)
    assert weekly_xfp_points(row, _STANDARD) != weekly_xfp_points(row, _HALF_PPR)


def test_weekly_milestone_bracket_evaluates_against_expected_yardage():
    rules_with_milestone = ScoringRules(
        items=[{"id": 1, "abbr": "RY100", "points": 3}], is_dst=False,
    )
    row = _weekly_row(rush_attempt=20, rush_yards_gained_exp=130.0)
    assert weekly_xfp_points(row, rules_with_milestone) == 3


def _pbp_pass_row(**overrides):
    row = {
        "pass_completion_exp": 0.7, "yards_after_catch_exp": 4.0, "air_yards": 8.0,
        "pass_touchdown_exp": 0.05, "pass_interception_exp": 0.02, "two_point_conv_exp": 0.0,
    }
    row.update(overrides)
    return row


def test_per_play_pass_yards_formula_matches_ffopportunity_source():
    # yards_gained_exp = complete_pass_exp * (yards_after_catch_exp + air_yards)
    row = _pbp_pass_row(pass_completion_exp=0.7, yards_after_catch_exp=4.0, air_yards=8.0)
    stat_row = expected_stat_row_from_pbp_pass(row, role="pass")
    assert stat_row["passing_yards"] == 0.7 * (4.0 + 8.0)


def test_per_play_pass_and_rec_share_the_same_yards_value():
    row = _pbp_pass_row()
    pass_row = expected_stat_row_from_pbp_pass(row, role="pass")
    rec_row = expected_stat_row_from_pbp_pass(row, role="rec")
    assert pass_row["passing_yards"] == rec_row["receiving_yards"]


def test_per_play_pass_null_guards_on_throwaway():
    # air_yards/yards_after_catch_exp come back null on throwaways/spikes -
    # the play should contribute 0 expected yards, not raise.
    row = _pbp_pass_row(air_yards=None, yards_after_catch_exp=None)
    stat_row = expected_stat_row_from_pbp_pass(row, role="pass")
    assert stat_row["passing_yards"] == 0.0


def test_per_play_rec_role_reception_count_is_completion_probability():
    row = _pbp_pass_row(pass_completion_exp=0.63)
    stat_row = expected_stat_row_from_pbp_pass(row, role="rec")
    assert stat_row["receptions"] == 0.63


def test_per_play_rush_uses_cleaned_columns_not_raw_model_output():
    # rushing_yards_exp (raw, still includes kneel-downs) must NOT be used -
    # only the cleaned rush_yards_exp.
    row = {
        "rushing_yards_exp": 999.0, "rush_yards_exp": 3.2,
        "rushing_td_exp": 999.0, "rush_touchdown_exp": 0.08,
        "two_point_conv_exp": 0.0,
    }
    stat_row = expected_stat_row_from_pbp_rush(row)
    assert stat_row["rushing_yards"] == 3.2
    assert stat_row["rushing_tds"] == 0.08
