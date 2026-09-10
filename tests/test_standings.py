import pytest

from engine.standings import (
    PlayedMatchup,
    RemainingMatchup,
    SeedConfig,
    SeedTeamState,
    TeamState,
    compute_current_seeds,
    matchup_win_probability,
    simulate_playoffs,
)


def test_matchup_win_probability_even_teams_is_fifty_fifty():
    assert matchup_win_probability(100.0, 10.0, 100.0, 10.0) == pytest.approx(0.5)


def test_matchup_win_probability_favors_higher_mean():
    p = matchup_win_probability(110.0, 10.0, 100.0, 10.0)
    assert 0.5 < p < 1.0


def test_matchup_win_probability_zero_sd_is_a_certainty():
    """Every starter's game already final (sd collapses to 0 for both teams)
    makes the higher score a certainty, not a coin flip on a 0/0 division."""
    assert matchup_win_probability(105.0, 0.0, 100.0, 0.0) == pytest.approx(1.0)
    assert matchup_win_probability(100.0, 0.0, 105.0, 0.0) == pytest.approx(0.0)
    assert matchup_win_probability(100.0, 0.0, 100.0, 0.0) == pytest.approx(0.5)


def test_division_priority_seeds_go_to_division_winners_ranked_by_shared_chain():
    teams = [
        SeedTeamState(team_id=1, division_id=1, wins=8, losses=2, ties=0, points_for=1000, points_against=900),
        SeedTeamState(team_id=2, division_id=1, wins=6, losses=4, ties=0, points_for=1100, points_against=950),
        SeedTeamState(team_id=3, division_id=2, wins=9, losses=1, ties=0, points_for=950, points_against=800),
        SeedTeamState(team_id=4, division_id=2, wins=5, losses=5, ties=0, points_for=900, points_against=850),
    ]
    seed_configs = {
        1: SeedConfig(division_priority=True, tiebreak_order=[]),
        2: SeedConfig(division_priority=True, tiebreak_order=[]),
        3: SeedConfig(division_priority=False, tiebreak_order=["wins", "points_for"]),
        4: SeedConfig(division_priority=False, tiebreak_order=["wins", "points_for"]),
    }
    seeds = compute_current_seeds(
        teams, [], playoff_team_count=4, division_tiebreak_order=["wins", "points_for"], seed_configs=seed_configs
    )
    # division winners: team1 (div 1, 8 wins) and team3 (div 2, 9 wins) - ranked by wins, team3 first
    assert seeds[3] == 1
    assert seeds[1] == 2
    # remaining wildcards ranked by wins: team2 (6) ahead of team4 (5)
    assert seeds[2] == 3
    assert seeds[4] == 4


def test_wildcard_seed_uses_its_own_tiebreak_order_not_wins():
    """Mirrors the O-League's real 6th-seed rule: best PF among whoever is
    left over for that seed, regardless of who has more wins."""
    teams = [
        SeedTeamState(team_id=1, division_id=1, wins=7, losses=3, ties=0, points_for=1000, points_against=900),
        SeedTeamState(team_id=2, division_id=1, wins=6, losses=4, ties=0, points_for=1200, points_against=900),
    ]
    seed_configs = {1: SeedConfig(division_priority=False, tiebreak_order=["points_for"])}
    seeds = compute_current_seeds(
        teams, [], playoff_team_count=1, division_tiebreak_order=["wins", "points_for"], seed_configs=seed_configs
    )
    assert seeds[2] == 1  # fewer wins but higher PF wins this seed


def test_head_to_head_breaks_a_wins_and_pf_tie():
    teams = [
        SeedTeamState(team_id=1, division_id=1, wins=6, losses=4, ties=0, points_for=1000, points_against=900),
        SeedTeamState(team_id=2, division_id=1, wins=6, losses=4, ties=0, points_for=1000, points_against=900),
    ]
    played = [PlayedMatchup(home_team_id=1, away_team_id=2, home_score=120.0, away_score=100.0)]
    seed_configs = {1: SeedConfig(division_priority=False, tiebreak_order=["wins", "points_for", "head_to_head"])}
    seeds = compute_current_seeds(
        teams, played, playoff_team_count=1, division_tiebreak_order=["wins"], seed_configs=seed_configs
    )
    assert seeds[1] == 1  # team 1 beat team 2 head-to-head


def test_division_priority_falls_back_to_wildcard_pool_when_winners_exhausted():
    """More division-priority seeds configured than divisions shouldn't
    crash - once the division-winner queue runs dry, those seeds just fall
    back to ranking the remaining pool by that seed's own tiebreak order."""
    teams = [
        SeedTeamState(team_id=1, division_id=1, wins=9, losses=1, ties=0, points_for=1000, points_against=900),
        SeedTeamState(team_id=2, division_id=1, wins=6, losses=4, ties=0, points_for=1100, points_against=950),
    ]
    seed_configs = {
        1: SeedConfig(division_priority=True, tiebreak_order=[]),
        2: SeedConfig(division_priority=True, tiebreak_order=["wins"]),
    }
    seeds = compute_current_seeds(
        teams, [], playoff_team_count=2, division_tiebreak_order=["wins"], seed_configs=seed_configs
    )
    assert seeds == {1: 1, 2: 2}


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
