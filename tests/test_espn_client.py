import time
from types import SimpleNamespace
from unittest.mock import patch

from ingest.espn_client import EspnClient


def _client_with_fake_league(box_scores_fn, current_week=None):
    """EspnClient.__init__ constructs a real espn_api League (a live network
    call) - bypass it entirely via __new__ and stand in a fake `_league`
    with just the one method get_live_week_player_status actually calls."""
    client = EspnClient.__new__(EspnClient)
    client._league = SimpleNamespace(box_scores=box_scores_fn, current_week=current_week)
    return client


def test_get_past_lineups_keys_by_team_id_not_team_object():
    # espn_api's own Team class has no __eq__/__hash__ override (object
    # identity only) - a real box_scores() call hands back Team OBJECTS as
    # box.home_team/away_team (League.box_scores replaces the raw int with
    # the actual Team instance before returning), not plain ints. This stand-
    # in mirrors that exactly: same "no custom equality" behavior as the real
    # thing, so this test would have caught keying the result dict by the
    # object itself instead of team.team_id (confirmed live - that bug made
    # every past-week lineup lookup miss, not just early-season ones).
    home_team = SimpleNamespace(team_id=10)
    away_team = SimpleNamespace(team_id=20)
    box = SimpleNamespace(
        home_team=home_team,
        home_lineup=[SimpleNamespace(playerId=1, name="A", position="RB", proTeam="KC", slot_position="RB", points=12.5)],
        away_team=away_team,
        away_lineup=[SimpleNamespace(playerId=2, name="B", position="WR", proTeam="SF", slot_position="BE", points=3.0)],
    )
    client = _client_with_fake_league(lambda week, player_team_cache: [box], current_week=2)
    result = client.get_past_lineups([1])
    assert set(result.keys()) == {(10, 1), (20, 1)}
    assert result[(10, 1)][0].espn_id == 1
    assert result[(20, 1)][0].espn_id == 2


def test_get_live_week_player_status_happy_path():
    box = SimpleNamespace(
        home_lineup=[SimpleNamespace(playerId=1, points=12.5, game_played=100)],
        away_lineup=[SimpleNamespace(playerId=2, points=0.0, game_played=0)],
    )
    client = _client_with_fake_league(lambda week: [box])
    result = client.get_live_week_player_status(1)
    assert result == {1: (12.5, True), 2: (0.0, False)}


def test_get_live_week_player_status_returns_empty_on_exception():
    def raises(week):
        raise RuntimeError("WAF challenge")

    client = _client_with_fake_league(raises)
    assert client.get_live_week_player_status(1) == {}


def test_get_live_week_player_status_returns_empty_on_timeout():
    def hangs(week):
        time.sleep(5)
        return []

    client = _client_with_fake_league(hangs)
    with patch("ingest.espn_client._LIVE_STATUS_TIMEOUT_SEC", 0.05):
        result = client.get_live_week_player_status(1)
    assert result == {}
