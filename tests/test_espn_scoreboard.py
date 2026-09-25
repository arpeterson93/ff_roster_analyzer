from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from ingest.espn_scoreboard import _remaining_fraction, fetch_remaining_game_fraction, live_game_window


def test_remaining_fraction_full_at_kickoff():
    assert _remaining_fraction(1, "15:00") == 1.0


def test_remaining_fraction_halftime_is_exactly_half():
    # End of Q2, clock at 0:00 - live-verified against the ESPN app's own
    # displayed in-game projection matching actual_so_far + 0.5*pregame
    # exactly at halftime (see engine/pipeline.py's comment on this).
    assert _remaining_fraction(2, "0:00") == 0.5


def test_remaining_fraction_end_of_regulation_is_zero():
    assert _remaining_fraction(4, "0:00") == 0.0


def test_remaining_fraction_mid_third_quarter():
    # 2 full quarters (30 min) + 7:30 of Q3 elapsed = 37.5 min of 60 -> 22.5 remaining -> 0.375
    assert abs(_remaining_fraction(3, "7:30") - 0.375) < 1e-9


def test_remaining_fraction_overtime_is_a_small_nonzero_residual():
    frac = _remaining_fraction(5, "10:00")
    assert 0 < frac < 0.5


def test_remaining_fraction_falls_back_gracefully_on_a_bad_clock_string():
    # An unparseable clock falls back to clock_seconds=0 (this quarter's own
    # clock reads "exhausted") rather than raising - period 1 with 0
    # seconds left in it means Q1 fully elapsed, 3 quarters (0.75) remain.
    assert _remaining_fraction(1, "garbage") == 0.75


def _fake_event(state, period=1, clock="15:00", teams=("CIN", "TB")):
    return {
        "competitions": [
            {
                "status": {"type": {"state": state}, "period": period, "displayClock": clock},
                "competitors": [{"team": {"abbreviation": t}} for t in teams],
            }
        ]
    }


def test_fetch_remaining_game_fraction_maps_every_team_in_the_event():
    payload = {"events": [_fake_event("in", period=2, clock="0:00", teams=("CIN", "TB"))]}
    mock_resp = Mock(status_code=200, text="x")
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None
    with patch("ingest.espn_scoreboard.requests.get", return_value=mock_resp):
        result = fetch_remaining_game_fraction()
    assert result == {"CIN": 0.5, "TB": 0.5}


def test_fetch_remaining_game_fraction_normalizes_espn_team_aliases():
    # ESPN's own scoreboard uses "WSH" - must normalize to nflverse's "WAS"
    # to join correctly against p["nfl_team"] in engine/pipeline.py.
    payload = {"events": [_fake_event("pre", teams=("WSH", "PHI"))]}
    mock_resp = Mock(status_code=200, text="x")
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None
    with patch("ingest.espn_scoreboard.requests.get", return_value=mock_resp):
        result = fetch_remaining_game_fraction()
    assert result == {"WAS": 1.0, "PHI": 1.0}


def test_fetch_remaining_game_fraction_returns_empty_on_total_failure():
    with patch("ingest.espn_scoreboard.requests.get", side_effect=RuntimeError("boom")), \
         patch("ingest.espn_scoreboard.time.sleep"):
        result = fetch_remaining_game_fraction()
    assert result == {}


def test_fetch_remaining_game_fraction_skips_a_malformed_event_without_failing_the_rest():
    payload = {"events": [{"competitions": [{}]}, _fake_event("post", teams=("SEA", "NE"))]}
    mock_resp = Mock(status_code=200, text="x")
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status.return_value = None
    with patch("ingest.espn_scoreboard.requests.get", return_value=mock_resp):
        result = fetch_remaining_game_fraction()
    assert result == {"SEA": 0.0, "NE": 0.0}


# --- live_game_window: engine/live.py's --gate rule.

_NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)


def _event_at(state, kickoff: datetime):
    return {
        "competitions": [
            {"status": {"type": {"state": state}}, "date": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ")}
        ]
    }


def test_live_game_window_true_when_a_game_is_in_progress():
    events = [_event_at("in", _NOW - timedelta(hours=1))]
    assert live_game_window(events, _NOW) is True


def test_live_game_window_true_for_a_game_final_3_hours_ago():
    events = [_event_at("post", _NOW - timedelta(hours=3))]
    assert live_game_window(events, _NOW) is True


def test_live_game_window_false_for_a_game_final_6_hours_ago():
    events = [_event_at("post", _NOW - timedelta(hours=6))]
    assert live_game_window(events, _NOW) is False


def test_live_game_window_false_when_every_game_is_still_pregame():
    events = [_event_at("pre", _NOW + timedelta(hours=2)), _event_at("pre", _NOW + timedelta(hours=5))]
    assert live_game_window(events, _NOW) is False


def test_live_game_window_true_if_any_one_event_qualifies_among_several():
    events = [_event_at("pre", _NOW + timedelta(hours=2)), _event_at("in", _NOW - timedelta(minutes=30))]
    assert live_game_window(events, _NOW) is True


def test_live_game_window_skips_a_malformed_event_without_failing_the_rest():
    events = [{"competitions": [{}]}, _event_at("in", _NOW - timedelta(minutes=10))]
    assert live_game_window(events, _NOW) is True


def test_live_game_window_false_on_no_events():
    assert live_game_window([], _NOW) is False
