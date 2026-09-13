"""Pregame outdoor-stadium weather forecast at kickoff time, from
api.weather.gov (NOAA/NWS) - free, no API key. Deliberately NOT
nflweather.com or any other scrape of an unofficial page: this is a
documented public API on its own domain, so it carries none of the
WAF/soft-block risk ingest/espn_injuries.py already has to work around, and
it's a real hourly forecast rather than a same-day snapshot.

Only meaningful within NWS's own ~7-day-out hourly horizon, and only for a
stadium with a real sky above it - a dome, an unmapped stadium, or a kickoff
beyond that horizon all return None rather than a stale or wrong-window
guess. Callers gate on `roof` (see engine/pipeline.py) before ever calling
this, since a dome's "forecast" is a non-question.
"""
from __future__ import annotations

import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "ff-roster-analyzer (arpeterson93@gmail.com)", "Accept": "application/geo+json"}
_TIMEOUT = 10

# nflverse stadium_id -> (lat, lon). Only stadiums that actually need a real
# forecast are worth seeding here - a pure dome's games never reach this
# lookup (see _DOME_ROOFS below) - but domes are listed too for completeness/
# future-proofing against a stadium's roof status changing across seasons.
# Seeded from real nflreadpy schedule rows observed live (2026-09-13), not
# guessed: an id missing here just skips weather for that game rather than
# risking a wrong lat/lon silently attached to the wrong stadium. Extend by
# adding whatever id/name a live run's WARNING log below surfaces.
STADIUM_COORDS: dict[str, tuple[float, float]] = {
    "SEA00": (47.5952, -122.3316),  # Lumen Field, Seattle
    "CAR00": (35.2258, -80.8528),  # Bank of America Stadium, Charlotte
    "CIN00": (39.0955, -84.5161),  # Paycor Stadium, Cincinnati
    "DET00": (42.3400, -83.0456),  # Ford Field, Detroit (dome)
    "HOU00": (29.6847, -95.4107),  # NRG/Reliant Stadium, Houston (retractable)
    "IND00": (39.7601, -86.1639),  # Lucas Oil Stadium, Indianapolis (retractable)
    "JAX00": (30.3239, -81.6373),  # EverBank Stadium, Jacksonville
    "PIT00": (40.4468, -80.0158),  # Acrisure Stadium, Pittsburgh
    "NAS00": (36.1665, -86.7713),  # Nissan Stadium, Nashville
    "VEG00": (36.0909, -115.1833),  # Allegiant Stadium, Las Vegas (dome)
    "MIN01": (44.9738, -93.2575),  # U.S. Bank Stadium, Minneapolis (dome)
    "PHI00": (39.9008, -75.1675),  # Lincoln Financial Field, Philadelphia
    "LAX01": (33.9535, -118.3392),  # SoFi Stadium, Inglewood (dome)
    "NYC01": (40.8135, -74.0745),  # MetLife Stadium, East Rutherford
    "KAN00": (39.0489, -94.4839),  # GEHA Field at Arrowhead Stadium, Kansas City
    "MEL00": (-37.8199, 144.9834),  # Melbourne Cricket Ground (neutral site)
}

# A permanently closed roof - no forecast is ever meaningful there. A
# retractable roof's actual pregame state ("dome" vs "outdoors" per game) is
# often still unresolved (None) days out, so None/"outdoors"/"retractable"
# all still attempt a real forecast rather than guessing the roof will close.
_DOME_ROOFS = {"dome", "closed"}


def _parse_iso(ts: str) -> datetime:
    """Accepts both a bare-UTC "...Z" timestamp (this codebase's own kickoff
    format) and NWS's own explicit-offset format - datetime.fromisoformat on
    Python 3.10 doesn't accept a trailing "Z" directly."""
    return datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)


def fetch_game_weather(stadium_id: str | None, roof: str | None, kickoff_iso: str | None) -> dict | None:
    """Best-effort hourly forecast for the specific hour containing kickoff.
    Never raises - a live pipeline run's weather is a nice-to-have, not
    something a transient NWS outage should take the whole build down over."""
    if roof in _DOME_ROOFS or not stadium_id or not kickoff_iso:
        return None
    coords = STADIUM_COORDS.get(stadium_id)
    if coords is None:
        logger.warning(
            "weather: no coords for stadium_id %r - add it to ingest.weather.STADIUM_COORDS to enable forecasts there",
            stadium_id,
        )
        return None
    try:
        kickoff = _parse_iso(kickoff_iso)
        lat, lon = coords
        points = requests.get(f"https://api.weather.gov/points/{lat},{lon}", headers=_HEADERS, timeout=_TIMEOUT)
        points.raise_for_status()
        hourly_url = points.json()["properties"]["forecastHourly"]
        hourly = requests.get(hourly_url, headers=_HEADERS, timeout=_TIMEOUT)
        hourly.raise_for_status()
        for period in hourly.json()["properties"]["periods"]:
            start, end = _parse_iso(period["startTime"]), _parse_iso(period["endTime"])
            if start <= kickoff < end:
                return {
                    "temperature_f": period["temperature"],
                    "wind": period["windSpeed"],
                    "wind_direction": period["windDirection"],
                    "precip_pct": (period.get("probabilityOfPrecipitation") or {}).get("value"),
                    "short_forecast": period["shortForecast"],
                }
        return None  # kickoff is beyond the forecast's own horizon
    except Exception:
        logger.warning("weather: fetch failed for stadium_id %r", stadium_id, exc_info=True)
        return None
