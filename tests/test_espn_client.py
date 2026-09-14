import time
from types import SimpleNamespace
from unittest.mock import patch

from ingest.espn_client import EspnClient


def _client_with_fake_league(box_scores_fn):
    """EspnClient.__init__ constructs a real espn_api League (a live network
    call) - bypass it entirely via __new__ and stand in a fake `_league`
    with just the one method get_live_week_player_status actually calls."""
    client = EspnClient.__new__(EspnClient)
    client._league = SimpleNamespace(box_scores=box_scores_fn)
    return client


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
