import json
from unittest.mock import Mock, patch

from engine.live import run_gate, run_league
from ingest.base import FantasyTeam, LeagueSettings, Matchup, RosterPlayer
from ingest.ids import IdMap

CFG = {"slug": "o-league", "league_id": 355398, "season": 2026, "sim": {"iterations": 200, "seed": 1, "division_winners_first": False}}


def _id_map():
    return IdMap(by_gsis={}, by_fp={}, by_espn={}, by_name_pos={}, ambiguous_name_pos=set())


def _settings(current_week=3):
    return LeagueSettings(
        name="Test League", season=2026, current_week=current_week, reg_season_count=14, final_week=17,
        playoff_team_count=4, seed_tie_rule="", tie_rule="", divisions={0: "League"}, slots={}, slot_eligibility={},
        roster_size=16, scoring_items=[], positions=["QB", "RB", "WR", "TE", "K", "DST"],
    )


def _roster_player(espn_id, name, position, nfl_team, lineup_slot):
    return RosterPlayer(
        espn_id=espn_id, name=name, position=position, nfl_team=nfl_team, fantasy_team_id=1,
        lineup_slot=lineup_slot, espn_projected_week=15.0,
    )


def _team2():
    return FantasyTeam(
        team_id=2, team_name="B", manager="Sam", abbrev="B", division_id=0, wins=0, losses=1, ties=0,
        points_for=90.0, points_against=100.0, roster=[],
    )


def _write_baseline(out_dir, *, current_week=3, players=None, lineups=None, position_week_sd=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"current_week": current_week, "position_week_sd": position_week_sd or {"WR": 5.0, "RB": 4.0}}
    (out_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (out_dir / "players.json").write_text(json.dumps(players or []), encoding="utf-8")
    (out_dir / "lineups.json").write_text(json.dumps(lineups or {}), encoding="utf-8")
    return meta


def _lineups_for(team_id, weeks_sd_total):
    """weeks_sd_total: {week: (total, sd)}"""
    return {
        str(team_id): {
            "weeks": {str(w): {"total": total, "sd": sd, "slots": {}, "bench": []} for w, (total, sd) in weeks_sd_total.items()},
            "changes_vs_espn": [],
        }
    }


def test_run_league_skips_when_baseline_is_missing(tmp_path):
    data_root = tmp_path / "data"
    result = run_league(CFG, data_root, _id_map(), {}, force=False)
    assert result is None
    assert not (data_root / "o-league").exists()


def test_run_league_skips_and_writes_nothing_on_week_mismatch(tmp_path):
    data_root = tmp_path / "data"
    out_dir = data_root / "o-league"
    known_player = {"id": "espn:1", "position": "WR", "nfl_team": "KC", "this_week": 15.0, "weekly": [{"week": 3, "sd": 5.0}]}
    lineups = _lineups_for(1, {3: (100.0, 8.0)})
    _write_baseline(out_dir, current_week=2, players=[known_player], lineups=lineups)  # baseline says week 2

    mock_client = Mock()
    mock_client.get_settings.return_value = _settings(current_week=3)  # ESPN says week 3 - rolled over
    with patch("engine.live.EspnClient", return_value=mock_client):
        run_league(CFG, data_root, _id_map(), {}, force=False)

    mock_client.get_teams.assert_not_called()
    # Baseline files untouched, no schedule/standings/live written.
    assert not (out_dir / "schedule.json").exists()
    assert not (out_dir / "live.json").exists()


def test_run_league_force_proceeds_despite_week_mismatch(tmp_path):
    data_root = tmp_path / "data"
    out_dir = data_root / "o-league"
    known_player = {
        "id": "espn:1", "espn_id": 1, "position": "WR", "nfl_team": "KC",
        "this_week": 15.0, "weekly": [{"week": 3, "sd": 5.0}],
    }
    lineups = _lineups_for(1, {3: (100.0, 8.0)})
    _write_baseline(out_dir, current_week=2, players=[known_player], lineups=lineups)

    team = FantasyTeam(
        team_id=1, team_name="A", manager="Alex", abbrev="A", division_id=0, wins=1, losses=0, ties=0,
        points_for=100.0, points_against=90.0, roster=[_roster_player(1, "Known Guy", "WR", "KC", "WR")],
    )
    mock_client = Mock()
    mock_client.get_settings.return_value = _settings(current_week=3)
    mock_client.get_teams.return_value = [team, _team2()]
    mock_client.get_matchups.return_value = [Matchup(week=3, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False)]
    mock_client.get_live_week_player_status.return_value = {}
    with patch("engine.live.EspnClient", return_value=mock_client):
        run_league(CFG, data_root, _id_map(), {}, force=True)

    assert (out_dir / "schedule.json").exists()


def test_run_league_reconstructs_team_week_mean_sd_and_writes_outputs(tmp_path):
    data_root = tmp_path / "data"
    out_dir = data_root / "o-league"
    known_player = {
        "id": "espn:1", "espn_id": 1, "position": "WR", "nfl_team": "KC",
        "this_week": 15.0, "weekly": [{"week": 3, "sd": 5.0}],
    }
    # Two remaining weeks (3, 4) written by the last full/refresh run.
    lineups = _lineups_for(1, {3: (100.0, 8.0), 4: (95.0, 7.0)})
    _write_baseline(out_dir, current_week=3, players=[known_player], lineups=lineups)

    team1 = FantasyTeam(
        team_id=1, team_name="A", manager="Alex", abbrev="A", division_id=0, wins=1, losses=0, ties=0,
        points_for=100.0, points_against=90.0,
        roster=[_roster_player(1, "Known Guy", "WR", "KC", "WR")],
    )
    mock_client = Mock()
    mock_client.get_settings.return_value = _settings(current_week=3)
    mock_client.get_teams.return_value = [team1, _team2()]
    mock_client.get_matchups.return_value = [
        Matchup(week=3, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False),
    ]
    mock_client.get_live_week_player_status.return_value = {1: (10.0, False)}
    with patch("engine.live.EspnClient", return_value=mock_client):
        run_league(CFG, data_root, _id_map(), {"KC": 0.5}, force=False)

    schedule_out = json.loads((out_dir / "schedule.json").read_text(encoding="utf-8"))
    live_out = json.loads((out_dir / "live.json").read_text(encoding="utf-8"))

    week3_row = next(r for r in schedule_out if r["week"] == 3)
    assert week3_row["live"] is True
    # points_so_far (10.0) + remaining 0.5 * pregame projection (15.0) = 17.5
    assert live_out["teams"]["1"]["mean"] == 15.0 * 0.5 + 10.0
    assert live_out["teams"]["1"]["total"] == 10.0  # points-so-far only, not the blended mean
    assert live_out["week"] == 3
    assert live_out["players"] == {}  # the one starter WAS in players.json - no stub needed


def test_run_league_stubs_a_starter_missing_from_players_json(tmp_path):
    data_root = tmp_path / "data"
    out_dir = data_root / "o-league"
    lineups = _lineups_for(1, {3: (100.0, 8.0)})
    _write_baseline(out_dir, current_week=3, players=[], lineups=lineups, position_week_sd={"RB": 4.5})

    team1 = FantasyTeam(
        team_id=1, team_name="A", manager="Alex", abbrev="A", division_id=0, wins=1, losses=0, ties=0,
        points_for=100.0, points_against=90.0,
        roster=[_roster_player(2, "Waiver Pickup", "RB", "SF", "RB")],
    )
    mock_client = Mock()
    mock_client.get_settings.return_value = _settings(current_week=3)
    mock_client.get_teams.return_value = [team1, _team2()]
    mock_client.get_matchups.return_value = [
        Matchup(week=3, home_team_id=1, away_team_id=2, home_score=None, away_score=None, played=False),
    ]
    mock_client.get_live_week_player_status.return_value = {}
    with patch("engine.live.EspnClient", return_value=mock_client):
        run_league(CFG, data_root, _id_map(), {}, force=False)

    live_out = json.loads((out_dir / "live.json").read_text(encoding="utf-8"))
    stub_id = next(iter(live_out["players"]))
    stub = live_out["players"][stub_id]
    assert stub["name"] == "Waiver Pickup"
    assert stub["position"] == "RB"
    assert stub["nfl_team"] == "SF"
    # this_week uses espn_projected_week (15.0, from _roster_player's default);
    # sd falls back to meta.position_week_sd["RB"] (4.5) - no live status/
    # remaining_frac, so frac defaults to 1.0 (pregame): mean = 15.0.
    assert live_out["teams"]["1"]["mean"] == 15.0


def test_run_gate_true_when_scoreboard_shows_a_live_game():
    payload = {"events": [{"competitions": [{"status": {"type": {"state": "in"}}, "date": "2026-09-21T18:00:00Z"}]}]}
    with patch("engine.live.fetch_scoreboard", return_value=payload):
        assert run_gate() is True


def test_run_gate_false_when_everything_is_pregame():
    payload = {"events": [{"competitions": [{"status": {"type": {"state": "pre"}}, "date": "2026-09-25T18:00:00Z"}]}]}
    with patch("engine.live.fetch_scoreboard", return_value=payload):
        assert run_gate() is False


def test_run_gate_fails_open_on_scoreboard_failure():
    with patch("engine.live.fetch_scoreboard", return_value=None):
        assert run_gate() is True
