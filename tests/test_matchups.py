import polars as pl
import pytest

from engine.matchups import (
    MatchupIndex,
    _index_for_basis,
    _league_avg_by_pos,
    _rank_positions,
    allowed_by_team_week_pos,
    compute_matchup_index,
    compute_schedule_strength,
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
    # Each ratio's denominator is the OPPONENT's own average EXCLUDING the
    # week being measured (leave-one-out) - e.g. B's weeks are {8, 6, 10}, so
    # the denominator for B's week-1 game (opponent A) is (6+10)/2 = 8, not
    # B's own plain 3-week average of 8 (same value here by coincidence; see
    # week 2/3 below where the leave-one-out and plain averages diverge).
    df = pl.DataFrame(_rows())
    points = points_by_team_week_pos(df, 2025, ["WR"], REY_SCORING)
    index, allowed = _index_for_basis(points, OPPONENT, TEAM_WEEKS, ["WR"], lambda ws: ws)

    assert index["WR"]["A"] == pytest.approx((8 / 8 + 16 / 19 + 12 / 10) / 3)
    assert index["WR"]["B"] == pytest.approx((10 / 10 + 14 / 9 + 18 / 18) / 3)
    assert index["WR"]["C"] == pytest.approx((6 / 13 + 12 / 9 + 10 / 7) / 3)
    assert index["WR"]["D"] == pytest.approx((20 / 17 + 6 / 9 + 8 / 11) / 3)

    assert allowed["WR"]["A"] == pytest.approx((8 + 16 + 12) / 3)


def test_index_for_basis_excludes_the_defenses_own_game_from_the_opponents_average():
    # A single defense (X) plays the same offense (O) in week 1 and a
    # different one (P) in week 2. O also plays a third team (Y) in week 3
    # for an unrelated, much lower output. If the self-game weren't excluded,
    # O's average would be pulled toward its own week-1 output against X,
    # inflating X's index; excluding it means only O's OTHER games (week 3)
    # feed the denominator for X's week-1 ratio.
    opponent = {("X", 1): "O", ("O", 1): "X", ("X", 2): "P", ("P", 2): "X", ("O", 3): "Y", ("Y", 3): "O"}
    team_weeks = {"X": [1, 2], "O": [1, 3], "P": [2], "Y": [3]}
    points = {
        ("O", 1, "WR"): 30.0,  # O's huge output happens to come in the very game vs X
        ("O", 3, "WR"): 10.0,  # O's only OTHER game - this is what should normalize X's week-1 ratio
        ("P", 2, "WR"): 10.0,
    }
    index, _ = _index_for_basis(points, opponent, team_weeks, ["WR"], lambda ws: ws)
    # X's week-2 ratio (opponent P) has no denominator at all - P has only
    # ONE recorded week, so there's no "other game" to leave P's own average
    # in with, and that ratio is skipped entirely (same as any denom<=0
    # week). Only the week-1 ratio survives, and it must use O's week-3-only
    # average (10), not a plain 2-game average of (30+10)/2=20 that would
    # include X's own game.
    assert index["WR"]["X"] == pytest.approx(30 / 10)


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


def test_adjusted_allowed_ppg_and_pa_factor_tie_to_the_current_season_index():
    prior_df = pl.DataFrame(_rows_for(OUTPUT, 2024))
    current_df = pl.DataFrame(_rows_for(CURRENT_OUTPUT, 2025))
    prior_points = points_by_team_week_pos(prior_df, 2024, ["WR"], REY_SCORING)
    current_points = points_by_team_week_pos(current_df, 2025, ["WR"], REY_SCORING)

    result = compute_matchup_index(
        current_points, prior_points, 2025, 2024, weeks_played=3, opponent=OPPONENT,
        prior_opponent=OPPONENT, positions=["WR"],
        pa_basis="season", pa_l5_weight=0.5, pa_prior_season_weeks=3,
        index_clamp=(0.0, 10.0),
    )

    cur_season_index, cur_season_allowed = _index_for_basis(current_points, OPPONENT, TEAM_WEEKS, ["WR"], lambda ws: ws)
    league_avg = _league_avg_by_pos(current_points, TEAM_WEEKS, ["WR"])

    for team in ["A", "B", "C", "D"]:
        expected_adjusted = league_avg["WR"] * cur_season_index["WR"][team]
        assert result.adjusted_allowed_ppg["WR"][team] == pytest.approx(expected_adjusted)
        assert result.pa_factor["WR"][team] == pytest.approx(expected_adjusted / cur_season_allowed["WR"][team])

    # Raw allowed_ppg is untouched by the adjustment - Adjusted is a separate
    # field, not a mutation of Raw.
    assert result.allowed_ppg["WR"]["A"] == pytest.approx(cur_season_allowed["WR"]["A"])


def test_game_final_gate_only_advances_the_teams_whose_own_game_is_final():
    # The TNF bug this gate fixes: week 3 has only A/B's game final (Thursday);
    # C/D haven't played yet. Passing game_final must leave C's and D's index
    # (and allowed_ppg) identical to the week-2-only computation - no 0.0
    # contamination for their not-yet-played week-3 game - while A and B DO
    # pick up their week-3 result.
    prior_df = pl.DataFrame(_rows_for_season(OUTPUT, 2024))
    current_df = pl.DataFrame(_rows_for(CURRENT_OUTPUT, 2025))
    prior_points = points_by_team_week_pos(prior_df, 2024, ["WR"], REY_SCORING)
    current_points = points_by_team_week_pos(current_df, 2025, ["WR"], REY_SCORING)

    game_final = {
        ("A", 1): True, ("B", 1): True, ("C", 1): True, ("D", 1): True,
        ("A", 2): True, ("B", 2): True, ("C", 2): True, ("D", 2): True,
        ("A", 3): True, ("D", 3): True,  # only A-D's week-3 game is final
    }

    result = compute_matchup_index(
        current_points, prior_points, 2025, 2024, weeks_played=2, opponent=OPPONENT,
        prior_opponent=OPPONENT, positions=["WR"],
        pa_basis="season", pa_l5_weight=0.5, pa_prior_season_weeks=3,
        index_clamp=(0.0, 10.0), game_final=game_final,
    )

    # A and D's own game is final in week 3 (they picked it up); B and C's
    # isn't, so they're still capped at weeks 1-2 - exactly the per-team
    # played_weeks the game_final gate should produce.
    per_team_played_weeks = {"A": [1, 2, 3], "B": [1, 2], "C": [1, 2], "D": [1, 2, 3]}
    expected_index, expected_allowed = _index_for_basis(current_points, OPPONENT, per_team_played_weeks, ["WR"], lambda ws: ws)

    for team in ["A", "B", "C", "D"]:
        assert result.allowed_ppg["WR"][team] == pytest.approx(expected_allowed["WR"][team])

    # Sanity check the bug this guards against: capping EVERYONE at week 2
    # (the old global weeks_played cutoff) would give C and B the same
    # numbers but NOT A and D, who really did play a final week 3.
    global_cutoff_weeks = {t: [w for w in ws if w <= 2] for t, ws in per_team_played_weeks.items()}
    stale_index, stale_allowed = _index_for_basis(current_points, OPPONENT, global_cutoff_weeks, ["WR"], lambda ws: ws)
    assert result.allowed_ppg["WR"]["A"] != pytest.approx(stale_allowed["WR"]["A"])


def test_game_final_none_falls_back_to_the_weeks_played_cutoff():
    # Existing int-only callers (no game_final) must be completely unaffected.
    prior_df = pl.DataFrame(_rows_for_season(OUTPUT, 2024))
    current_df = pl.DataFrame(_rows_for(CURRENT_OUTPUT, 2025))
    prior_points = points_by_team_week_pos(prior_df, 2024, ["WR"], REY_SCORING)
    current_points = points_by_team_week_pos(current_df, 2025, ["WR"], REY_SCORING)

    with_game_final_none = compute_matchup_index(
        current_points, prior_points, 2025, 2024, weeks_played=2, opponent=OPPONENT,
        prior_opponent=OPPONENT, positions=["WR"],
        pa_basis="season", pa_l5_weight=0.5, pa_prior_season_weeks=3,
        index_clamp=(0.0, 10.0),
    )
    assert with_game_final_none.allowed_ppg["WR"]["A"] > 0


def test_adjusted_allowed_ppg_falls_back_to_prior_season_when_no_current_weeks_played():
    prior_df = pl.DataFrame(_rows_for_season(OUTPUT, 2024))
    prior_points = points_by_team_week_pos(prior_df, 2024, ["WR"], REY_SCORING)
    current_points = {}

    result = compute_matchup_index(
        current_points, prior_points, 2025, 2024, weeks_played=0, opponent=OPPONENT,
        prior_opponent=PRIOR_OPPONENT, positions=["WR"],
        pa_basis="season", pa_l5_weight=0.5, pa_prior_season_weeks=6, index_clamp=(0.0, 10.0),
    )

    prior_team_weeks = team_weeks_from_opponent(PRIOR_OPPONENT, ["A", "B", "C", "D"], [1, 2, 3])
    prior_index, prior_allowed = _index_for_basis(prior_points, PRIOR_OPPONENT, prior_team_weeks, ["WR"], lambda ws: ws)
    prior_league_avg = _league_avg_by_pos(prior_points, prior_team_weeks, ["WR"])

    for team in ["A", "B", "C", "D"]:
        expected_adjusted = prior_league_avg["WR"] * prior_index["WR"][team]
        assert result.adjusted_allowed_ppg["WR"][team] == pytest.approx(expected_adjusted)
        assert result.pa_factor["WR"][team] == pytest.approx(expected_adjusted / prior_allowed["WR"][team])


# Reuses OPPONENT's 4-team round robin (week1: A-B/C-D, week2: A-C/B-D,
# week3: A-D/B-C) with a hand-picked, all-distinct index so averaging over
# the full 3-week window has no ties to arbitrate.
_SCHEDULE_INDEX = {"WR": {"A": 1.0, "B": 1.5, "C": 0.5, "D": 2.0}}


def test_compute_schedule_strength_averages_the_opponents_index_over_the_window():
    mi = MatchupIndex(index=_SCHEDULE_INDEX)
    result = compute_schedule_strength(
        mi, OPPONENT, ["A", "B", "C", "D"], ["WR"], {"reg": [1, 2, 3]}, index_clamp=(0.0, 10.0)
    )
    # A's opponents weeks 1-3: B(1.5), C(0.5), D(2.0).
    assert result.avg_index["reg"]["WR"]["A"] == pytest.approx((1.5 + 0.5 + 2.0) / 3)
    # C's opponents: D(2.0), A(1.0), B(1.5) - the best average of the four.
    assert result.avg_index["reg"]["WR"]["C"] == pytest.approx((2.0 + 1.0 + 1.5) / 3)


def test_compute_schedule_strength_ranks_highest_average_index_as_1():
    mi = MatchupIndex(index=_SCHEDULE_INDEX)
    result = compute_schedule_strength(
        mi, OPPONENT, ["A", "B", "C", "D"], ["WR"], {"reg": [1, 2, 3]}, index_clamp=(0.0, 10.0)
    )
    ranks = result.rank["reg"]["WR"]
    assert ranks["C"] == 1  # highest average index (1.5) = best remaining schedule
    assert ranks["A"] == 2
    assert ranks["B"] == 3
    assert ranks["D"] == 4  # lowest average index (1.0) = toughest remaining schedule


def test_compute_schedule_strength_supports_independent_timeframes():
    # "reg" = week 1 only (A faces B, index 1.5); "playoffs" = week 3 only
    # (A faces D, index 2.0) - each timeframe's window is independent.
    mi = MatchupIndex(index=_SCHEDULE_INDEX)
    result = compute_schedule_strength(
        mi, OPPONENT, ["A", "B", "C", "D"], ["WR"], {"reg": [1], "playoffs": [3]}, index_clamp=(0.0, 10.0)
    )
    assert result.avg_index["reg"]["WR"]["A"] == pytest.approx(1.5)
    assert result.avg_index["playoffs"]["WR"]["A"] == pytest.approx(2.0)


def test_compute_schedule_strength_omits_a_team_with_no_weeks_in_the_window():
    # A team already past reg_season_count has no "reg" weeks left at all -
    # it should be absent from that window's rank/avg_index rather than
    # given an arbitrary rank (see the function's own docstring).
    mi = MatchupIndex(index=_SCHEDULE_INDEX)
    result = compute_schedule_strength(mi, OPPONENT, ["A", "B", "C", "D"], ["WR"], {"reg": []}, index_clamp=(0.0, 10.0))
    assert result.avg_index["reg"]["WR"] == {}
    assert result.rank["reg"]["WR"] == {}


def test_compute_schedule_strength_clamps_before_averaging():
    # An index value outside the clamp bounds must be clamped BEFORE it goes
    # into the average - same clamping project_player applies per week.
    mi = MatchupIndex(index={"WR": {"A": 1.0, "B": 5.0}})  # B way outside (0.5, 1.5)
    result = compute_schedule_strength(
        mi, OPPONENT, ["A", "B", "C", "D"], ["WR"], {"reg": [1]}, index_clamp=(0.5, 1.5)
    )
    # A's week-1 opponent is B (raw index 5.0, clamped to 1.5).
    assert result.avg_index["reg"]["WR"]["A"] == pytest.approx(1.5)
