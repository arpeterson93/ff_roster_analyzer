import pytest

from engine.standings import RemainingMatchup, TeamState, simulate_playoffs


def test_identical_means_split_odds_roughly_evenly():
    teams = [
        TeamState(team_id=1, division_id=0, wins=0, losses=0, ties=0, points_for=0.0),
        TeamState(team_id=2, division_id=0, wins=0, losses=0, ties=0, points_for=0.0),
    ]
    matchups = [RemainingMatchup(week=1, home_team_id=1, away_team_id=2)]
    mean = {(1, 1): 100.0, (2, 1): 100.0}
    sd = {(1, 1): 10.0, (2, 1): 10.0}

    result = simulate_playoffs(
        teams, matchups, mean, sd, iterations=5000, seed=42, playoff_team_count=1, division_winners_first=False,
    )
    assert result[1]["playoff_odds"] == pytest.approx(0.5, abs=0.05)
    assert result[2]["playoff_odds"] == pytest.approx(0.5, abs=0.05)
    assert result[1]["playoff_odds"] + result[2]["playoff_odds"] == pytest.approx(1.0)


def test_clinched_team_has_certain_odds():
    teams = [
        TeamState(team_id=1, division_id=0, wins=10, losses=0, ties=0, points_for=1500.0),
        TeamState(team_id=2, division_id=0, wins=0, losses=10, ties=0, points_for=900.0),
    ]
    result = simulate_playoffs(
        teams, [], {}, {}, iterations=1000, seed=42, playoff_team_count=1, division_winners_first=False,
    )
    assert result[1]["playoff_odds"] == pytest.approx(1.0)
    assert result[2]["playoff_odds"] == pytest.approx(0.0)
    assert result[1]["expected_wins"] == pytest.approx(10.0)


def test_ties_count_as_half_win():
    teams = [TeamState(team_id=1, division_id=0, wins=2, losses=1, ties=2, points_for=0.0)]
    result = simulate_playoffs(
        teams, [], {}, {}, iterations=10, seed=1, playoff_team_count=1, division_winners_first=False,
    )
    assert result[1]["expected_wins"] == pytest.approx(3.0)  # 2 + 0.5*2


def test_division_winners_seeded_first():
    # Division A's only member (team 1) is guaranteed its division win despite
    # having fewer wins than everyone in division B; both playoff spots are
    # claimed by the two division winners (team 1 and team 2), seeded by
    # strength, leaving team 3 (division B runner-up) out despite more wins
    # than team 1.
    teams = [
        TeamState(team_id=1, division_id="A", wins=5, losses=5, ties=0, points_for=1000.0),
        TeamState(team_id=2, division_id="B", wins=9, losses=1, ties=0, points_for=1400.0),
        TeamState(team_id=3, division_id="B", wins=8, losses=2, ties=0, points_for=1300.0),
    ]
    result = simulate_playoffs(
        teams, [], {}, {}, iterations=10, seed=1, playoff_team_count=2, division_winners_first=True,
    )
    assert result[1]["division_win_odds"] == pytest.approx(1.0)
    assert result[2]["division_win_odds"] == pytest.approx(1.0)
    assert result[2]["seed_probs"][1] == pytest.approx(1.0)  # stronger division winner -> seed 1
    assert result[1]["seed_probs"][2] == pytest.approx(1.0)  # weaker division winner -> seed 2
    assert result[3]["playoff_odds"] == pytest.approx(0.0)  # division runner-up, no spots left
