from unittest.mock import patch

from ingest.weather import fetch_game_weather


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_POINTS_PAYLOAD = {"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/CIN/1,1/forecast/hourly"}}


def _hourly_payload(periods):
    return {"properties": {"periods": periods}}


def test_fetch_game_weather_picks_the_hour_containing_kickoff():
    periods = [
        {
            "startTime": "2026-09-13T12:00:00-04:00", "endTime": "2026-09-13T13:00:00-04:00",
            "temperature": 70, "windSpeed": "5 mph", "windDirection": "W",
            "probabilityOfPrecipitation": {"value": 10}, "shortForecast": "Sunny",
        },
        {
            "startTime": "2026-09-13T13:00:00-04:00", "endTime": "2026-09-13T14:00:00-04:00",
            "temperature": 82, "windSpeed": "7 mph", "windDirection": "NW",
            "probabilityOfPrecipitation": {"value": 0}, "shortForecast": "Partly sunny",
        },
    ]
    with patch("ingest.weather.requests.get") as mock_get:
        mock_get.side_effect = [_FakeResponse(_POINTS_PAYLOAD), _FakeResponse(_hourly_payload(periods))]
        result = fetch_game_weather("CIN00", "outdoors", "2026-09-13T17:00:00Z")  # 13:00 EDT
    assert result == {
        "temperature_f": 82, "wind": "7 mph", "wind_direction": "NW",
        "precip_pct": 0, "short_forecast": "Partly sunny",
    }


def test_fetch_game_weather_returns_none_for_a_dome():
    with patch("ingest.weather.requests.get") as mock_get:
        assert fetch_game_weather("DET00", "dome", "2026-09-13T17:00:00Z") is None
    mock_get.assert_not_called()


def test_fetch_game_weather_returns_none_for_an_unmapped_stadium():
    with patch("ingest.weather.requests.get") as mock_get:
        assert fetch_game_weather("ZZZ99", "outdoors", "2026-09-13T17:00:00Z") is None
    mock_get.assert_not_called()


def test_fetch_game_weather_returns_none_when_kickoff_is_beyond_the_forecast_horizon():
    periods = [
        {
            "startTime": "2026-09-13T12:00:00-04:00", "endTime": "2026-09-13T13:00:00-04:00",
            "temperature": 70, "windSpeed": "5 mph", "windDirection": "W",
            "probabilityOfPrecipitation": {"value": 10}, "shortForecast": "Sunny",
        },
    ]
    with patch("ingest.weather.requests.get") as mock_get:
        mock_get.side_effect = [_FakeResponse(_POINTS_PAYLOAD), _FakeResponse(_hourly_payload(periods))]
        result = fetch_game_weather("CIN00", "outdoors", "2026-09-20T17:00:00Z")  # a week later
    assert result is None


def test_fetch_game_weather_returns_none_on_a_fetch_failure_rather_than_raising():
    with patch("ingest.weather.requests.get", side_effect=RuntimeError("boom")):
        assert fetch_game_weather("CIN00", "outdoors", "2026-09-13T17:00:00Z") is None


def test_fetch_game_weather_attempts_a_retractable_roof_with_unknown_state():
    with patch("ingest.weather.requests.get") as mock_get:
        mock_get.side_effect = [_FakeResponse(_POINTS_PAYLOAD), _FakeResponse(_hourly_payload([]))]
        fetch_game_weather("HOU00", None, "2026-09-13T17:00:00Z")
    assert mock_get.call_count == 2
