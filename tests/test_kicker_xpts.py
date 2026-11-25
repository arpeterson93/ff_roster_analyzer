import polars as pl

from engine.kicker_xpts import attempt_points, expected_attempt_points, fg_bracket, kicker_weekly_xpts
from engine.scoring import ScoringRules


def _rules(items):
    return ScoringRules.from_espn([{"id": i, "abbr": a, "points": v} for i, (a, v) in enumerate(items)], is_dst=False)


RULES = _rules([("PAT", 1.0), ("PATM", -1.0), ("FG0", 3.0), ("FG40", 4.0), ("FG50", 5.0), ("FGM", -1.0)])


def test_fg_brackets():
    assert [fg_bracket(d) for d in (18, 19, 20, 39, 40, 49, 50, 59, 60, 67)] == ["0_19", "0_19", "20_29", "30_39", "40_49", "40_49", "50_59", "50_59", "60_", "60_"]


def test_attempt_points_follow_the_leagues_distance_scoring_and_miss_penalty():
    assert attempt_points(RULES, "fg", 33, True) == 3.0
    assert attempt_points(RULES, "fg", 45, True) == 4.0
    assert attempt_points(RULES, "fg", 55, True) == 5.0
    assert attempt_points(RULES, "fg", 33, False) == -1.0
    assert attempt_points(RULES, "xp", None, True) == 1.0
    assert attempt_points(RULES, "xp", None, False) == -1.0


def test_extra_point_example_from_the_spec():
    # 95% x +1 on the make side, 5% x -1 on the miss side in a league that penalises a missed PAT
    assert round(expected_attempt_points(RULES, "xp", None, 0.95), 6) == 0.9
    no_penalty = _rules([("PAT", 1.0), ("FG0", 3.0)])
    assert round(expected_attempt_points(no_penalty, "xp", None, 0.95), 6) == 0.95


def test_weekly_sum_over_attempts():
    pbp = pl.DataFrame({
        "field_goal_attempt": [1, 1, 0, 0],
        "extra_point_attempt": [0, 0, 1, 1],
        "kicker_player_id": ["K1", "K1", "K1", "K2"],
        "week": [3, 3, 3, 3],
        "kick_distance": [33.0, 52.0, None, None],
        "fg_prob": [0.9, 0.6, None, None],
        "extra_point_prob": [None, None, 0.95, 0.9],
    })
    out = kicker_weekly_xpts(pbp, RULES)
    assert round(out[("K1", 3)], 6) == round((0.9 * 3 - 0.1) + (0.6 * 5 - 0.4) + 0.9, 6)
    assert round(out[("K2", 3)], 6) == round(0.9 * 1 - 0.1, 6)


def test_kicks_without_a_probability_or_distance_are_skipped():
    pbp = pl.DataFrame({
        "field_goal_attempt": [1, 1], "extra_point_attempt": [0, 0], "kicker_player_id": ["K1", "K1"], "week": [1, 1],
        "kick_distance": [None, 40.0], "fg_prob": [0.5, None], "extra_point_prob": [None, None],
    })
    assert kicker_weekly_xpts(pbp, RULES) == {}


def test_team_weekly_keys_by_kicking_team_and_regular_season():
    from engine.kicker_xpts import team_weekly_kicker_xpts
    pbp = pl.DataFrame({
        "field_goal_attempt": [0, 0], "extra_point_attempt": [1, 1], "kicker_player_id": ["K1", "K2"],
        "week": [4, 4], "kick_distance": [None, None], "fg_prob": [None, None], "extra_point_prob": [0.95, 0.95],
        "posteam": ["BUF", "MIA"], "season_type": ["REG", "POST"],
    })
    out = team_weekly_kicker_xpts(pbp, RULES)
    assert list(out) == [("BUF", 4, "K")] and round(out[("BUF", 4, "K")], 6) == round(0.95 - 0.05, 6)


def test_empty_pbp_gives_nothing():
    assert kicker_weekly_xpts(pl.DataFrame(), RULES) == {}
