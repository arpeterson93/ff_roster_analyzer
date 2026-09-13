from engine.play_log import build_game_play_index, scoring_plays_for_player
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


def test_build_game_play_index_groups_by_every_relevant_id_column():
    rows = [
        _row(rusher_player_id="RB1", rushing_yards=5),
        _row(receiver_player_id="RB1", complete_pass=True, receiving_yards=3),  # same player, different role
        _row(rusher_player_id="RB2", rushing_yards=2),
    ]
    index = build_game_play_index(rows)
    assert len(index["RB1"]) == 2
    assert len(index["RB2"]) == 1
