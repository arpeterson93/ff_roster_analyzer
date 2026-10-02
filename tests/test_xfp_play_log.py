from engine.scoring import ScoringRules
from engine.xfp_play_log import (
    expected_passing_plays, expected_plays_for_player, expected_receiving_plays, expected_rushing_plays,
)

_RULES = ScoringRules(
    items=[
        {"id": 1, "abbr": "PY", "points": 0.04}, {"id": 2, "abbr": "PTD", "points": 4},
        {"id": 3, "abbr": "RY", "points": 0.1}, {"id": 4, "abbr": "RTD", "points": 6},
        {"id": 5, "abbr": "REY", "points": 0.1}, {"id": 6, "abbr": "RETD", "points": 6},
        {"id": 7, "abbr": "REC", "points": 1},
    ],
    is_dst=False,
)


def _pass_row(**overrides):
    row = {
        "passer_player_id": "QB1", "receiver_player_id": "WR1",
        "passer_full_name": "Q.Back", "receiver_full_name": "W.Receiver",
        "pass_completion_exp": 0.7, "yards_after_catch_exp": 4.0, "air_yards": 8.0,
        "pass_touchdown_exp": 0.05, "pass_interception_exp": 0.02, "two_point_conv_exp": 0.0,
        "game_seconds_remaining": 1800,
    }
    row.update(overrides)
    return row


def _rush_row(**overrides):
    row = {
        "rusher_player_id": "RB1", "rush_yards_exp": 4.2, "rush_touchdown_exp": 0.03,
        "two_point_conv_exp": 0.0, "game_seconds_remaining": 1800,
    }
    row.update(overrides)
    return row


def test_every_pass_attempt_gets_an_entry_complete_or_not():
    # Unlike the actual version, there's no "incomplete target dropped
    # entirely" case - every attempt has SOME nonzero expected value.
    row = _pass_row(pass_completion_exp=0.0)
    plays = expected_receiving_plays([row], "WR1", _RULES)
    assert len(plays) == 1


def test_passing_and_receiving_views_see_the_same_play_from_each_role():
    row = _pass_row()
    pass_plays = expected_passing_plays([row], "QB1", _RULES)
    rec_plays = expected_receiving_plays([row], "WR1", _RULES)
    assert len(pass_plays) == 1
    assert len(rec_plays) == 1
    assert pass_plays[0]["role"] == "pass"
    assert rec_plays[0]["role"] == "reception"


def test_rushing_plays_use_cleaned_expected_columns():
    row = _rush_row(rush_yards_exp=5.5)
    plays = expected_rushing_plays([row], "RB1", _RULES)
    assert plays[0]["points"] == round(5.5 * 0.1 + 0.03 * 6, 2)


def test_expected_plays_for_player_combines_all_three_roles_sorted_by_time():
    pass_rows = [_pass_row(game_seconds_remaining=3500)]  # early
    rush_rows = [_rush_row(rusher_player_id="WR1", game_seconds_remaining=100)]  # late, same player as receiver
    plays = expected_plays_for_player(pass_rows, rush_rows, "WR1", _RULES)
    assert len(plays) == 2
    assert plays[0]["elapsed_min"] < plays[1]["elapsed_min"]


def test_no_zero_point_filtering_even_for_near_zero_expected_value():
    # A deep-ball throwaway-ish play with very low completion probability
    # still produces a real (small, nonzero) entry, not a dropped play.
    row = _pass_row(pass_completion_exp=0.01, air_yards=50.0, yards_after_catch_exp=2.0)
    plays = expected_receiving_plays([row], "WR1", _RULES)
    assert len(plays) == 1
    assert plays[0]["points"] > 0
