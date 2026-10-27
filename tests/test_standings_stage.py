import types

import pytest

from engine.standings import matchup_win_probability
from engine.standings_stage import live_team_mean_sd, standings_outputs
from ingest.base import Matchup


def _settings(reg_season_count=3, playoff_team_count=2, divisions=None):
    return types.SimpleNamespace(
        reg_season_count=reg_season_count,
        playoff_team_count=playoff_team_count,
        divisions=divisions or {0: "League"},
    )


class _Team:
    def __init__(self, team_id, wins=0, losses=0, ties=0, points_for=0.0, points_against=0.0, division_id=0):
        self.team_id = team_id
        self.division_id = division_id
        self.wins = wins
        self.losses = losses
        self.ties = ties
        self.points_for = points_for
        self.points_against = points_against


SIM_CFG = {"iterations": 200, "seed": 1, "division_winners_first": False}


def test_standings_outputs_matches_hand_computed_win_pct_with_no_live_data():
    # 3 teams, 2 remaining weeks, no live blend at all (week_started=False) -
    # win% must come straight from the plain pregame team_week_mean/sd, and
    # no matchup should be marked live.
    teams = [_Team(1, wins=1), _Team(2), _Team(3)]
    matchups = [
        Matchup(week=2, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False),
        Matchup(week=3, home_team_id=1, away_team_id=3, home_score=None, away_score=None, played=False),
    ]
    team_week_mean = {(1, 2): 100.0, (2, 2): 90.0, (1, 3): 100.0, (3, 3): 95.0}
    team_week_sd = {(1, 2): 10.0, (2, 2): 10.0, (1, 3): 10.0, (3, 3): 10.0}

    schedule_out, standings_out = standings_outputs(
        teams, matchups, _settings(), SIM_CFG, current_week=2,
        team_week_mean=team_week_mean, team_week_sd=team_week_sd,
        team_live_mean_sd={}, live_points_by_team={}, week_started=False,
    )

    week2_row = next(r for r in schedule_out if r["week"] == 2)
    assert week2_row["home_win_pct"] == pytest.approx(matchup_win_probability(100.0, 10.0, 90.0, 10.0))
    assert week2_row["live"] is False
    assert week2_row["home_score"] is None  # not played, not live -> ESPN's own (absent) value passes through

    assert {s["team_id"] for s in standings_out} == {1, 2, 3}


def test_live_override_moves_win_pct_and_expected_wins_and_marks_the_row_live():
    teams = [_Team(1), _Team(2)]
    matchups = [Matchup(week=2, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False)]
    team_week_mean = {(1, 2): 100.0, (2, 2): 100.0}
    team_week_sd = {(1, 2): 10.0, (2, 2): 10.0}
    settings = _settings(playoff_team_count=1)

    baseline_schedule, baseline_standings = standings_outputs(
        teams, matchups, settings, SIM_CFG, current_week=2,
        team_week_mean=team_week_mean, team_week_sd=team_week_sd,
        team_live_mean_sd={}, live_points_by_team={}, week_started=False,
    )
    baseline_row = baseline_schedule[0]
    assert baseline_row["home_win_pct"] == pytest.approx(0.5, abs=0.01)
    assert baseline_row["live"] is False

    # Team 1 is blowing team 2 out live.
    team_live_mean_sd = {1: (150.0, 5.0), 2: (50.0, 5.0)}
    live_points_by_team = {1: {"p1": 80.0, "p2": 70.0}, 2: {"p3": 30.0, "p4": 20.0}}
    live_schedule, live_standings = standings_outputs(
        teams, matchups, settings, SIM_CFG, current_week=2,
        team_week_mean=team_week_mean, team_week_sd=team_week_sd,
        team_live_mean_sd=team_live_mean_sd, live_points_by_team=live_points_by_team, week_started=True,
    )
    live_row = live_schedule[0]
    assert live_row["live"] is True
    assert live_row["home_score"] == pytest.approx(150.0)  # sum of team 1's live per-player points
    assert live_row["away_score"] == pytest.approx(50.0)
    assert live_row["home_win_pct"] > baseline_row["home_win_pct"]

    baseline_ew = {s["team_id"]: s["expected_wins"] for s in baseline_standings}
    live_ew = {s["team_id"]: s["expected_wins"] for s in live_standings}
    assert live_ew[1] > baseline_ew[1]  # the sim's own current-week input moved too (D8)


def test_week_started_false_never_marks_a_matchup_live_even_with_data_present():
    # A nightly run before kickoff must not show a spurious 0-0 "(live)" row
    # even if team_live_mean_sd/live_points_by_team happen to be populated.
    teams = [_Team(1), _Team(2)]
    matchups = [Matchup(week=2, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False)]
    team_week_mean = {(1, 2): 100.0, (2, 2): 100.0}
    team_week_sd = {(1, 2): 10.0, (2, 2): 10.0}
    schedule_out, _ = standings_outputs(
        teams, matchups, _settings(playoff_team_count=1), SIM_CFG, current_week=2,
        team_week_mean=team_week_mean, team_week_sd=team_week_sd,
        team_live_mean_sd={1: (100.0, 5.0), 2: (100.0, 5.0)},
        live_points_by_team={1: {"p1": 0.0}, 2: {"p2": 0.0}}, week_started=False,
    )
    assert schedule_out[0]["live"] is False
    assert schedule_out[0]["home_win_pct"] == pytest.approx(0.5, abs=0.01)


def test_live_team_mean_sd_blends_points_so_far_with_remaining_projection():
    players_by_id = {
        "p1": {"position": "WR", "nfl_team": "KC", "espn_id": 1, "this_week": 20.0, "weekly": [{"week": 2, "sd": 6.0}]},
    }
    started_by_team = {10: {"p1"}}
    live_status = {1: (12.0, False)}  # 12 pts so far, game not yet marked complete
    remaining_frac = {"KC": 0.5}

    team_live_mean_sd, live_points, live_projected = live_team_mean_sd(
        players_by_id, started_by_team, current_week=2, live_status=live_status, remaining_frac=remaining_frac,
    )
    # mean = points_so_far + frac * pregame_mean = 12 + 0.5*20 = 22
    assert team_live_mean_sd[10][0] == pytest.approx(22.0)
    # sd = pregame_sd * sqrt(frac) = 6 * sqrt(0.5)
    assert team_live_mean_sd[10][1] == pytest.approx(6.0 * (0.5**0.5))
    # "points" is the REAL points-so-far only, not the blended mean.
    assert live_points[10]["p1"] == pytest.approx(12.0)
    # "projected" is that same player's blended points-so-far + remaining share.
    assert live_projected[10]["p1"] == pytest.approx(22.0)


def test_live_team_mean_sd_falls_back_to_position_sd_when_no_weekly_entry():
    # A live-tier starter stubbed in from ESPN's live roster (not in the
    # nightly players.json baseline) has no weekly[] at all.
    players_by_id = {
        "stub1": {"position": "RB", "nfl_team": "SF", "espn_id": 99, "this_week": 10.0, "weekly": []},
    }
    started_by_team = {20: {"stub1"}}
    team_live_mean_sd, live_points, live_projected = live_team_mean_sd(
        players_by_id, started_by_team, current_week=2, live_status={}, remaining_frac={},
        fallback_sd_by_pos={"RB": 4.0},
    )
    # No live status and no remaining_frac entry -> frac defaults to 1.0 (pregame).
    assert team_live_mean_sd[20][0] == pytest.approx(10.0)
    assert team_live_mean_sd[20][1] == pytest.approx(4.0)
    assert live_points[20]["stub1"] == pytest.approx(0.0)
    assert live_projected[20]["stub1"] == pytest.approx(10.0)


def test_thru_week_plays_out_the_current_week_at_projected_scores_and_reseeds():
    # Team 2 is ahead now (1-0 vs 0-1), but team 1 is projected to beat it this
    # week, so one week out team 1 is seed 1 with a 1-1 record that includes
    # the projected points; team 3 (bye) keeps its record.
    teams = [_Team(1, wins=0, losses=1, points_for=90.0, points_against=100.0),
             _Team(2, wins=1, losses=0, points_for=100.0, points_against=90.0),
             _Team(3, wins=0, losses=0)]
    matchups = [
        Matchup(week=1, home_team_id=1, away_team_id=2, home_score=90.0, away_score=100.0, played=True),
        Matchup(week=2, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False),
    ]
    team_week_mean = {(1, 2): 110.0, (2, 2): 95.0}
    team_week_sd = {(1, 2): 10.0, (2, 2): 10.0}

    _, standings_out = standings_outputs(
        teams, matchups, _settings(reg_season_count=3, playoff_team_count=1), SIM_CFG, current_week=2,
        team_week_mean=team_week_mean, team_week_sd=team_week_sd,
        team_live_mean_sd={}, live_points_by_team={}, week_started=False,
    )
    by_id = {s["team_id"]: s for s in standings_out}
    t1, t2, t3 = (by_id[i]["thru"] for i in (1, 2, 3))

    assert (t1["wins"], t1["losses"], t1["result"], t1["week"]) == (1, 1, "W", 2)
    assert t1["points_for"] == pytest.approx(200.0) and t1["points_against"] == pytest.approx(195.0)
    assert (t2["wins"], t2["losses"], t2["result"]) == (1, 1, "L")
    assert t3["result"] is None and (t3["wins"], t3["losses"]) == (0, 0)
    # 1-1 vs 1-1: team 1 has more points for (200 vs 195) -> seed 1 one week out, while team 2 is seed 1 today
    assert by_id[2]["seed"] == 1 and by_id[1]["seed"] != 1
    assert t1["seed"] == 1 and t2["seed"] == 2


def test_thru_week_uses_the_live_blend_once_the_week_has_started():
    teams = [_Team(1), _Team(2)]
    matchups = [Matchup(week=2, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False)]
    team_week_mean = {(1, 2): 100.0, (2, 2): 100.0}
    team_week_sd = {(1, 2): 10.0, (2, 2): 10.0}
    # Pregame it's a coin flip (home wins ties only by >), but live team 2 is far ahead.
    _, standings_out = standings_outputs(
        teams, matchups, _settings(playoff_team_count=1), SIM_CFG, current_week=2,
        team_week_mean=team_week_mean, team_week_sd=team_week_sd,
        team_live_mean_sd={1: (80.0, 5.0), 2: (120.0, 5.0)}, live_points_by_team={1: {}, 2: {}}, week_started=True,
    )
    by_id = {s["team_id"]: s for s in standings_out}
    assert by_id[2]["thru"]["result"] == "W" and by_id[2]["thru"]["projected_score"] == pytest.approx(120.0)
    assert by_id[1]["thru"]["result"] == "L"


def test_thru_week_is_absent_once_the_regular_season_is_over():
    teams = [_Team(1), _Team(2)]
    matchups = [Matchup(week=4, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False)]
    _, standings_out = standings_outputs(
        teams, matchups, _settings(reg_season_count=3), SIM_CFG, current_week=4,
        team_week_mean={}, team_week_sd={}, team_live_mean_sd={}, live_points_by_team={}, week_started=False,
    )
    assert all(s["thru"] is None for s in standings_out)
