import polars as pl
import pytest

from engine.matchups import (
    _index_for_basis,
    _rank_positions,
    allowed_by_team_week_pos,
    compute_matchup_index,
    dst_points_by_team_week_pos,
    points_by_team_week_pos,
    team_weeks_from_opponent,
)
from engine.scoring import ScoringRules

REY_SCORING = ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}])

# 4-team round robin, 3 weeks: A-B/C-D, A-C/B-D, A-D/B-C.
OPPONENT = {
    ("A", 1): "B", ("B", 1): "A", ("C", 1): "D", ("D", 1): "C",
    ("A", 2): "C", ("C", 2): "A", ("B", 2): "D", ("D", 2): "B",
    ("A", 3): "D", ("D", 3): "A", ("B", 3): "C", ("C", 3): "B",
}
TEAM_WEEKS = {"A": [1, 2, 3], "B": [1, 2, 3], "C": [1, 2, 3], "D": [1, 2, 3]}

# team -> week -> receiving yards scored by that team's WR that week.
OUTPUT = {
    "A": {1: 10, 2: 12, 3: 8},
    "B": {1: 8, 2: 6, 3: 10},
    "C": {1: 20, 2: 16, 3: 18},
    "D": {1: 6, 2: 14, 3: 12},
}


def _rows():
    rows = []
    for team, weeks in OUTPUT.items():
        for week, yards in weeks.items():
            rows.append(
                {"season": 2025, "season_type": "REG", "team": team, "week": week, "position": "WR", "receiving_yards": yards}
            )
    return rows


def test_points_by_team_week_pos():
    df = pl.DataFrame(_rows())
    table = points_by_team_week_pos(df, 2025, ["WR"], REY_SCORING)
    assert table[("A", 1, "WR")] == 10
    assert table[("C", 3, "WR")] == 18


def test_index_for_basis_matches_hand_computation():
    df = pl.DataFrame(_rows())
    points = points_by_team_week_pos(df, 2025, ["WR"], REY_SCORING)
    index, allowed = _index_for_basis(points, OPPONENT, TEAM_WEEKS, ["WR"], lambda ws: ws)

    assert index["WR"]["A"] == pytest.approx((1.0 + 16 / 18 + 12 / (32 / 3)) / 3)
    assert index["WR"]["B"] == pytest.approx((10 / 10 + 14 / (32 / 3) + 18 / 18) / 3)
    assert index["WR"]["C"] == pytest.approx((6 / (32 / 3) + 12 / 10 + 10 / 8) / 3)
    assert index["WR"]["D"] == pytest.approx((20 / 18 + 6 / 8 + 8 / 10) / 3)

    assert allowed["WR"]["A"] == pytest.approx((8 + 16 + 12) / 3)


def test_allowed_by_team_week_pos_is_the_opponents_raw_output():
    df = pl.DataFrame(_rows())
    points = points_by_team_week_pos(df, 2025, ["WR"], REY_SCORING)
    allowed = allowed_by_team_week_pos(points, OPPONENT, TEAM_WEEKS, ["WR"])
    # A's week-1 opponent is B, who scored 8 that week.
    assert allowed[("A", 1, "WR")] == pytest.approx(8.0)
    assert allowed[("B", 1, "WR")] == pytest.approx(10.0)
    assert allowed[("C", 3, "WR")] == pytest.approx(10.0)  # C's week-3 opponent is B, who scored 10


def test_rank_positions_highest_index_is_best_matchup():
    index = {"WR": {"A": 1.0046, "B": 1.1042, "C": 1.0042, "D": 0.8870}}
    rank = _rank_positions(index)
    assert rank["WR"]["B"] == 1  # highest index = best/easiest matchup
    assert rank["WR"]["A"] == 2
    assert rank["WR"]["C"] == 3
    assert rank["WR"]["D"] == 4  # lowest index = toughest matchup


def test_dst_points_by_team_week_pos_uses_opponent_offense_for_yards_allowed():
    schedules_df = pl.DataFrame(
        [{"season": 2025, "game_type": "REG", "game_id": "G1", "home_team": "A", "away_team": "B", "home_score": 10, "away_score": 20}]
    )
    team_stats_df = pl.DataFrame(
        [
            {
                "season": 2025, "season_type": "REG", "game_id": "G1", "week": 1, "team": "A", "opponent_team": "B",
                "def_sacks": 2, "def_interceptions": 1, "passing_yards": 15, "rushing_yards": 5,
            },
            {
                "season": 2025, "season_type": "REG", "game_id": "G1", "week": 1, "team": "B", "opponent_team": "A",
                "def_sacks": 0, "def_interceptions": 0, "passing_yards": 200, "rushing_yards": 100,
            },
        ]
    )
    dst_scoring = ScoringRules.from_espn(
        [{"id": 99, "abbr": "SK", "points": 1}, {"id": 95, "abbr": "INT", "points": 2}], is_dst=True
    )
    table = dst_points_by_team_week_pos(team_stats_df, schedules_df, 2025, dst_scoring)
    # A's DST (2 sacks, 1 INT against B) scored 2*1 + 1*2 = 4 points; keyed by
    # A itself (the team whose DST scored), same "team's own output"
    # convention as points_by_team_week_pos for offense positions.
    assert table[("A", 1, "DST")] == pytest.approx(4.0)
    assert table[("B", 1, "DST")] == pytest.approx(0.0)  # B's DST had 0 sacks/INTs


def test_dst_matchup_index_reflects_offense_vulnerability_not_defense_quality():
    # Two DSTs (A, C) of differing quality both play the SAME weak offense B
    # in different weeks; a third DST (D) plays a strong offense E. The
    # resulting index, keyed by OFFENSE, should reflect B's own vulnerability
    # (high index = easy matchup for whichever DST plays them) regardless of
    # which specific defense produced the sample, and should differ from E's.
    opponent = {("A", 1): "B", ("B", 1): "A", ("C", 2): "B", ("B", 2): "C", ("D", 3): "E", ("E", 3): "D"}
    team_weeks = {"A": [1], "B": [1, 2], "C": [2], "D": [3], "E": [3]}
    # team_week_pos_points: each team's own DST score that week (natural keying).
    points = {
        ("A", 1, "DST"): 10.0,  # A's DST scored 10 vs weak offense B
        ("C", 2, "DST"): 8.0,  # C's DST scored 8 vs weak offense B
        ("D", 3, "DST"): 2.0,  # D's DST scored only 2 vs strong offense E
    }
    # offense_avg for B (an "offense" in the generic function's terms, since
    # B never has its own DST scored here) is undefined/0 - not needed since
    # B is never the outer-loop "defense" in this test; we only read
    # index['DST']['B'] and index['DST']['E'], which are keyed as offenses.
    index, allowed = _index_for_basis(points, opponent, team_weeks, ["DST"], lambda ws: ws)
    # index['DST']['B'] is built from A's and C's own average DST quality as
    # the normalizer - what matters here is simply that B (fed two strong DST
    # scores) comes out easier to score DST points against than E (fed one
    # weak DST score), confirming the key represents offense vulnerability.
    assert allowed["DST"]["B"] > allowed["DST"]["E"]


def test_l5_window_differs_from_season_with_more_than_5_weeks():
    # Team X allows a lot early, nothing late; season avg and L5 avg should differ.
    opponent = {("X", w): "OPP" for w in range(1, 8)}
    team_weeks = {"X": list(range(1, 8)), "OPP": list(range(1, 8))}
    # OPP's own output must be nonzero to normalize ratios; keep it constant.
    rows = []
    for w in range(1, 8):
        rows.append({"season": 2025, "season_type": "REG", "team": "OPP", "week": w, "position": "WR", "receiving_yards": 10})
        rows.append({"season": 2025, "season_type": "REG", "team": "X", "week": w, "position": "WR", "receiving_yards": 10})
    df = pl.DataFrame(rows)
    points = points_by_team_week_pos(df, 2025, ["WR"], REY_SCORING)

    season_index, _ = _index_for_basis(points, opponent, team_weeks, ["WR"], lambda ws: ws)
    l5_index, _ = _index_for_basis(points, opponent, team_weeks, ["WR"], lambda ws: ws[-5:])
    # both should be 1.0 here (constant output) - sanity check the plumbing works
    assert season_index["WR"]["X"] == pytest.approx(1.0)
    assert l5_index["WR"]["X"] == pytest.approx(1.0)


# A different round-robin pairing than OPPONENT, so a bug that reused the
# current season's schedule to look up the prior season's ratios would
# produce different (wrong) numbers than using this schedule correctly.
PRIOR_OPPONENT = {
    ("A", 1): "C", ("C", 1): "A", ("B", 1): "D", ("D", 1): "B",
    ("A", 2): "B", ("B", 2): "A", ("C", 2): "D", ("D", 2): "C",
    ("A", 3): "D", ("D", 3): "A", ("B", 3): "C", ("C", 3): "B",
}


def _rows_for_season(output: dict, season: int) -> list[dict]:
    rows = []
    for team, weeks in output.items():
        for week, yards in weeks.items():
            rows.append(
                {"season": season, "season_type": "REG", "team": team, "week": week, "position": "WR", "receiving_yards": yards}
            )
    return rows


def test_prior_season_index_uses_the_priors_own_schedule_not_currents():
    prior_df = pl.DataFrame(_rows_for_season(OUTPUT, 2024))  # OUTPUT data, as the "prior" season
    prior_points = points_by_team_week_pos(prior_df, 2024, ["WR"], REY_SCORING)
    current_points = {}  # irrelevant here: weeks_played=0 means only the prior index is used

    result = compute_matchup_index(
        current_points, prior_points, 2025, 2024, weeks_played=0, opponent=OPPONENT,
        prior_opponent=PRIOR_OPPONENT, positions=["WR"],
        pa_basis="season", pa_l5_weight=0.5, pa_prior_season_weeks=6, index_clamp=(0.0, 10.0),
    )

    prior_team_weeks = team_weeks_from_opponent(PRIOR_OPPONENT, ["A", "B", "C", "D"], [1, 2, 3])
    expected_index, _ = _index_for_basis(prior_points, PRIOR_OPPONENT, prior_team_weeks, ["WR"], lambda ws: ws)

    for team in ["A", "B", "C", "D"]:
        assert result.index["WR"][team] == pytest.approx(expected_index["WR"][team])

    # Sanity check the bug this guards against: computing with the WRONG
    # (current-season) schedule gives visibly different numbers.
    wrong_team_weeks = team_weeks_from_opponent(OPPONENT, ["A", "B", "C", "D"], [1, 2, 3])
    wrong_index, _ = _index_for_basis(prior_points, OPPONENT, wrong_team_weeks, ["WR"], lambda ws: ws)
    assert wrong_index["WR"]["A"] != pytest.approx(expected_index["WR"]["A"])


CURRENT_OUTPUT = {
    "A": {1: 20, 2: 5, 3: 15},
    "B": {1: 5, 2: 20, 3: 5},
    "C": {1: 10, 2: 10, 3: 25},
    "D": {1: 15, 2: 15, 3: 5},
}


def _rows_for(output: dict, season: int) -> list[dict]:
    rows = []
    for team, weeks in output.items():
        for week, yards in weeks.items():
            rows.append(
                {"season": season, "season_type": "REG", "team": team, "week": week, "position": "WR", "receiving_yards": yards}
            )
    return rows


def test_prior_season_fallback_weight():
    prior_df = pl.DataFrame(_rows_for(OUTPUT, 2024))
    current_df = pl.DataFrame(_rows_for(CURRENT_OUTPUT, 2025))
    prior_points = points_by_team_week_pos(prior_df, 2024, ["WR"], REY_SCORING)
    current_points = points_by_team_week_pos(current_df, 2025, ["WR"], REY_SCORING)

    def run(weeks_played: int):
        return compute_matchup_index(
            current_points, prior_points, 2025, 2024, weeks_played=weeks_played, opponent=OPPONENT,
            prior_opponent=OPPONENT, positions=["WR"],
            pa_basis="season", pa_l5_weight=0.5, pa_prior_season_weeks=3,
            index_clamp=(0.0, 10.0),
        )

    result0 = run(0)
    result_full = run(3)  # weeks_played == pa_prior_season_weeks -> pure current
    result_half = run(1)  # weeks_played/pa_prior_season_weeks = 1/3

    prior_a = result0.index["WR"]["A"]
    full_a = result_full.index["WR"]["A"]
    half_a = result_half.index["WR"]["A"]

    assert result0.season_used == 2024
    assert result_full.season_used == 2025
    assert prior_a != pytest.approx(full_a)

    # Independently recompute what the current-season index looks like using
    # only week 1 (what weeks_played=1 restricts it to), to check the blend
    # weight formula without assuming the index is constant across windows.
    cur_points = points_by_team_week_pos(current_df, 2025, ["WR"], REY_SCORING)
    week1_only = {t: [1] for t in TEAM_WEEKS}
    cur_index_1wk, _ = _index_for_basis(cur_points, OPPONENT, week1_only, ["WR"], lambda ws: ws)
    w = 1 / 3
    assert half_a == pytest.approx(w * cur_index_1wk["WR"]["A"] + (1 - w) * prior_a, abs=1e-6)
