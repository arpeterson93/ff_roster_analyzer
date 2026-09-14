from engine.play_log import (
    build_game_play_index, game_durations_by_game_id, incomplete_targets_for_player,
    scoring_plays_for_player, zero_point_plays_for_player,
)
from engine.scoring import ScoringRules

_RULES = ScoringRules(
    items=[
        {"id": 1, "abbr": "RY", "points": 0.1}, {"id": 2, "abbr": "RTD", "points": 6},
        {"id": 3, "abbr": "REY", "points": 0.1}, {"id": 4, "abbr": "RETD", "points": 6},
        {"id": 5, "abbr": "REC", "points": 1},  # PPR
        {"id": 6, "abbr": "PY", "points": 0.04}, {"id": 7, "abbr": "PTD", "points": 4},
        {"id": 8, "abbr": "INTT", "points": -2}, {"id": 9, "abbr": "FUML", "points": -2},
        {"id": 10, "abbr": "FG0", "points": 3},
    ],
    is_dst=False,
)


def _row(**overrides):
    row = {
        "passer_player_id": None, "rusher_player_id": None, "receiver_player_id": None, "kicker_player_id": None,
        "game_seconds_remaining": 1800,  # halftime
    }
    row.update(overrides)
    return row


def test_rushing_touchdown_scores_yards_plus_td():
    row = _row(rusher_player_id="RB1", rushing_yards=15, rush_touchdown=True)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    assert len(plays) == 1
    assert plays[0]["points"] == 1.5 + 6  # 15*0.1 + 6
    assert "15 yd rush (TD)" == plays[0]["label"]
    assert plays[0]["is_td"] is True


def test_non_td_play_has_is_td_false():
    row = _row(rusher_player_id="RB1", rushing_yards=5, rush_touchdown=False)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    assert plays[0]["is_td"] is False


def test_ot_play_extends_past_regulation_instead_of_clamping_to_it():
    row = _row(rusher_player_id="RB1", rushing_yards=5, qtr=5, season_type="REG",
               quarter_seconds_remaining=550, game_seconds_remaining=550)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    # 50 seconds into a 10-minute (600s) regular-season OT period.
    assert plays[0]["elapsed_min"] == round(60 + 50 / 60, 2)


def test_postseason_ot_uses_a_15_minute_period():
    row = _row(rusher_player_id="RB1", rushing_yards=5, qtr=5, season_type="POST",
               quarter_seconds_remaining=750, game_seconds_remaining=750)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    # 150 seconds into a 15-minute (900s) postseason OT period.
    assert plays[0]["elapsed_min"] == round(60 + 150 / 60, 2)


def test_game_durations_by_game_id_uses_the_latest_play_in_the_whole_game():
    # The player's own last touch is early in OT, but the OTHER team wins
    # it on a field goal much later in the same OT period without this
    # player getting the ball back - the game's real duration has to come
    # from every play in the game, not just the ones a specific player was
    # involved in.
    rows = [
        _row(rusher_player_id="RB1", rushing_yards=5, game_id="2026_01_AAA_BBB",
             qtr=5, season_type="REG", quarter_seconds_remaining=540, game_seconds_remaining=540),  # 1 min into OT
        _row(kicker_player_id="K2", field_goal_attempt=True, field_goal_result="made", kick_distance=40,
             game_id="2026_01_AAA_BBB", qtr=5, season_type="REG", quarter_seconds_remaining=60, game_seconds_remaining=60),  # 9 min into OT
    ]
    durations = game_durations_by_game_id(rows)
    assert durations["2026_01_AAA_BBB"] == 69.0


def test_game_durations_by_game_id_tracks_multiple_games_independently():
    rows = [
        _row(rusher_player_id="RB1", rushing_yards=5, game_id="G1", game_seconds_remaining=1800),
        _row(rusher_player_id="RB2", rushing_yards=5, game_id="G2", game_seconds_remaining=100),
    ]
    durations = game_durations_by_game_id(rows)
    assert durations["G1"] == 30.0
    assert durations["G2"] > durations["G1"]


def test_second_ot_period_stacks_after_the_first():
    row = _row(rusher_player_id="RB1", rushing_yards=5, qtr=6, season_type="REG",
               quarter_seconds_remaining=600, game_seconds_remaining=600)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    # Start of the 2nd OT period = regulation + one full 10-minute OT period.
    assert plays[0]["elapsed_min"] == 70.0


def test_reception_with_ppr_and_yardage():
    row = _row(receiver_player_id="WR1", complete_pass=True, receiving_yards=22, pass_touchdown=False,
               passer_player_name="P.Mahomes")
    plays = scoring_plays_for_player([row], "WR1", _RULES)
    assert plays[0]["points"] == 1 + 2.2  # 1 REC + 22*0.1
    assert plays[0]["label"] == "22 yd catch from P.Mahomes"


def test_incomplete_target_is_dropped_not_a_zero_bar():
    row = _row(receiver_player_id="WR1", complete_pass=False, passer_player_name="P.Mahomes")
    plays = scoring_plays_for_player([row], "WR1", _RULES)
    assert plays == []


def test_fumble_lost_on_a_reception_still_counts_the_catch_and_yards_plus_the_penalty():
    row = _row(receiver_player_id="WR1", complete_pass=True, receiving_yards=8, fumble_lost=True,
               fumbled_1_player_id="WR1")
    plays = scoring_plays_for_player([row], "WR1", _RULES)
    # The fumble happens AFTER the catch/yards are already recorded (real
    # fantasy-scoring convention) - REC (1) + yards (8*0.1) + FUML (-2).
    assert plays[0]["points"] == -0.2  # rounded; REC (1) + yards (0.8) - FUML (2)
    assert plays[0]["label"] == "Fumble lost (reception)"


def test_interception_thrown_is_negative():
    row = _row(passer_player_id="QB1", interception=True, passing_yards=0)
    plays = scoring_plays_for_player([row], "QB1", _RULES)
    assert plays[0]["points"] == -2
    assert plays[0]["label"] == "Interception thrown"


def test_field_goal_made_buckets_by_distance():
    row = _row(kicker_player_id="K1", field_goal_attempt=True, field_goal_result="made", kick_distance=18)
    plays = scoring_plays_for_player([row], "K1", _RULES)
    assert plays[0]["points"] == 3
    assert plays[0]["label"] == "18 yd field goal"


def test_missed_field_goal_scores_nothing():
    row = _row(kicker_player_id="K1", field_goal_attempt=True, field_goal_result="missed", kick_distance=45)
    plays = scoring_plays_for_player([row], "K1", _RULES)
    assert plays == []


def test_plays_are_ordered_by_elapsed_game_time():
    late = _row(rusher_player_id="RB1", rushing_yards=5, game_seconds_remaining=100)  # near end of game
    early = _row(rusher_player_id="RB1", rushing_yards=5, game_seconds_remaining=3500)  # near start
    plays = scoring_plays_for_player([late, early], "RB1", _RULES)
    assert plays[0]["elapsed_min"] < plays[1]["elapsed_min"]


def test_elapsed_minutes_at_halftime_is_thirty():
    row = _row(rusher_player_id="RB1", rushing_yards=1, game_seconds_remaining=1800)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    assert plays[0]["elapsed_min"] == 30.0


def test_incomplete_target_is_captured_separately_from_scoring_plays():
    row = _row(receiver_player_id="WR1", complete_pass=False, passer_player_name="P.Mahomes")
    assert scoring_plays_for_player([row], "WR1", _RULES) == []
    incompletions = incomplete_targets_for_player([row], "WR1")
    assert len(incompletions) == 1
    assert incompletions[0]["label"] == "Incomplete target from P.Mahomes"
    assert incompletions[0]["elapsed_min"] == 30.0


def test_completed_reception_is_not_an_incomplete_target():
    row = _row(receiver_player_id="WR1", complete_pass=True, receiving_yards=8)
    assert incomplete_targets_for_player([row], "WR1") == []


def test_incomplete_targets_ignore_plays_where_player_is_not_the_receiver():
    row = _row(rusher_player_id="RB1", rushing_yards=5)
    assert incomplete_targets_for_player([row], "RB1") == []


def test_scoring_plays_are_tagged_with_their_role():
    rows = [
        _row(passer_player_id="QB1", passing_yards=20, receiver_player_name="X"),
        _row(rusher_player_id="RB1", rushing_yards=5),
        _row(receiver_player_id="WR1", complete_pass=True, receiving_yards=5),
    ]
    assert scoring_plays_for_player([rows[0]], "QB1", _RULES)[0]["role"] == "pass"
    assert scoring_plays_for_player([rows[1]], "RB1", _RULES)[0]["role"] == "rush"
    assert scoring_plays_for_player([rows[2]], "WR1", _RULES)[0]["role"] == "reception"


def test_play_starting_inside_the_5_is_flagged_short_field():
    row = _row(rusher_player_id="RB1", rushing_yards=3, rush_touchdown=True, yardline_100=3)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    assert plays[0]["short_field"] is True
    assert plays[0]["yardline"] == 3


def test_play_starting_outside_the_5_is_not_short_field():
    row = _row(rusher_player_id="RB1", rushing_yards=3, yardline_100=12)
    plays = scoring_plays_for_player([row], "RB1", _RULES)
    assert plays[0]["short_field"] is False
    assert plays[0]["yardline"] is None


def test_stuffed_carry_for_no_gain_is_a_zero_point_play_not_dropped():
    row = _row(rusher_player_id="RB1", rushing_yards=0, yardline_100=2)
    assert scoring_plays_for_player([row], "RB1", _RULES) == []
    zeros = zero_point_plays_for_player([row], "RB1", _RULES)
    assert len(zeros) == 1
    assert zeros[0]["role"] == "rush"
    assert zeros[0]["short_field"] is True
    assert zeros[0]["yardline"] == 2


def test_zero_yard_catch_in_non_ppr_is_a_zero_point_play():
    non_ppr = ScoringRules(
        items=[{"id": 3, "abbr": "REY", "points": 0.1}, {"id": 4, "abbr": "RETD", "points": 6}],
        is_dst=False,
    )
    row = _row(receiver_player_id="WR1", complete_pass=True, receiving_yards=0)
    assert scoring_plays_for_player([row], "WR1", non_ppr) == []
    zeros = zero_point_plays_for_player([row], "WR1", non_ppr)
    assert len(zeros) == 1
    assert zeros[0]["role"] == "reception"


def test_zero_point_plays_exclude_incomplete_targets():
    row = _row(receiver_player_id="WR1", complete_pass=False, passer_player_name="P.Mahomes")
    assert zero_point_plays_for_player([row], "WR1", _RULES) == []


def test_zero_point_plays_exclude_qbs_own_incompletions():
    # A QB's own incomplete pass thrown nets them 0 points too, but the
    # user explicitly doesn't want every incompletion cluttering a QB's
    # row with zero-point markers - only real carries/catches qualify.
    row = _row(passer_player_id="QB1", receiver_player_id="WR1", complete_pass=False, passing_yards=0)
    assert zero_point_plays_for_player([row], "QB1", _RULES) == []


def test_build_game_play_index_groups_by_every_relevant_id_column():
    rows = [
        _row(rusher_player_id="RB1", rushing_yards=5),
        _row(receiver_player_id="RB1", complete_pass=True, receiving_yards=3),  # same player, different role
        _row(rusher_player_id="RB2", rushing_yards=2),
    ]
    index = build_game_play_index(rows)
    assert len(index["RB1"]) == 2
    assert len(index["RB2"]) == 1
