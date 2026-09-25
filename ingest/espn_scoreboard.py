"""Live game-clock state from ESPN's PUBLIC scoreboard (site.api.espn.com -
NOT the private fantasy API espn_client.py/espn_api use, which never exposes
real quarter/clock state - only a crude done/not-done flag per player, see
EspnClient.get_live_week_player_status). Used by engine/pipeline.py to blend
a still-in-progress player's actual-so-far points with the remaining share
of their pregame projection, rather than snapping straight from "fully
uncertain" to "fully final" the moment their game ends (see the conversation
this was built from - live-verified against the ESPN app's own displayed
in-game projection for a real player: actual_so_far + remaining_fraction *
pregame_projection matched it exactly at halftime).

Same public-endpoint WAF/soft-block risk already documented in
ingest/espn_injuries.py applies here (a live, unauthenticated site.api.espn.com
scoreboard fetch was directly observed getting Akamai/AWS WAF challenge
responses from one specific sandbox's IP - not a documented espn.com policy
against automated access in general, since a plain curl from a normal
residential IP fetched the same URL successfully, and this codebase's own
CI already scrapes espn.com/nfl/injuries daily from GitHub Actions without
issue). Best-effort like that scrape: any failure here just means every
in-progress player falls back to their full pregame projection for this
build - the existing, safe default - rather than taking anything down.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import requests

from ingest.nfl_data import normalize_team

logger = logging.getLogger(__name__)

_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
# Same reasoning as espn_injuries.py's own header set - a bare User-Agent
# with nothing else is an easy automated-traffic fingerprint.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 15
_RETRIES = 3
_QUARTER_SECONDS = 15 * 60
_REGULATION_SECONDS = 4 * _QUARTER_SECONDS

# Overtime isn't worth modeling precisely (rare, and fantasy-relevant OT
# scoring is rarer still) - a small nonzero residual keeps a live-in-OT
# player from snapping all the way to "fully final" before their game
# actually ends, without needing real OT clock math.
_OVERTIME_REMAINING_FRACTION = 0.05


def _remaining_fraction(period: int, display_clock: str) -> float:
    try:
        mins, secs = display_clock.split(":")
        clock_seconds = int(mins) * 60 + int(secs)
    except (ValueError, AttributeError):
        clock_seconds = 0
    if period > 4:
        return _OVERTIME_REMAINING_FRACTION
    elapsed = max(0, period - 1) * _QUARTER_SECONDS + (_QUARTER_SECONDS - clock_seconds)
    return max(0.0, min(1.0, 1 - elapsed / _REGULATION_SECONDS))


def _fetch_scoreboard() -> dict | None:
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = requests.get(_SCOREBOARD_URL, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            if resp.status_code != 200 or not resp.text:
                raise RuntimeError(f"non-200 or empty response ({resp.status_code}, {len(resp.text)} bytes) - likely a soft block/challenge")
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - retry loop, logged below
            last_exc = exc
            logger.warning("espn_scoreboard: attempt %s/%s failed: %s", attempt + 1, _RETRIES, exc)
            if attempt < _RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    logger.warning("espn_scoreboard: failed after %s attempts: %s - live in-game blending disabled for this build", _RETRIES, last_exc)
    return None


def fetch_scoreboard() -> dict | None:
    """Public wrapper over _fetch_scoreboard - engine/live.py's own gate step
    (see live_game_window below) needs the raw scoreboard payload, not just
    the per-team remaining-fraction summary fetch_remaining_game_fraction
    derives from it."""
    return _fetch_scoreboard()


# How long after a game goes final its box score still counts as "worth a
# gameday tick" for the gate - long enough that the tick right after a game
# ends still runs (to catch late-arriving final stats/lineup settlement),
# short enough that the gate stops firing well before the next slate.
_RECENTLY_FINAL_WINDOW = timedelta(hours=4.5)


def live_game_window(events: list[dict], now: datetime) -> bool:
    """True if any event on the scoreboard is actually in progress, or went
    final within the last _RECENTLY_FINAL_WINDOW - the gameday live tier's
    gate rule (engine/live.py --gate): run only when there's something to
    show, and exit fast otherwise rather than spending ~25 ESPN requests on
    a dead window."""
    for event in events:
        try:
            comp = event["competitions"][0]
            state = comp["status"]["type"]["state"]
            if state == "in":
                return True
            if state == "post":
                kickoff = datetime.fromisoformat(comp["date"].replace("Z", "+00:00"))
                if now - kickoff < _RECENTLY_FINAL_WINDOW:
                    return True
        except (KeyError, IndexError, TypeError, ValueError):
            continue  # one malformed event shouldn't sink the whole gate check
    return False


def fetch_remaining_game_fraction() -> dict[str, float]:
    """{team: fraction of game-clock time still remaining} for every team
    with a game on the CURRENT scoreboard (whatever week ESPN's own default
    "current" view returns, which is always the real live/upcoming week -
    the pipeline only ever calls this for engine/pipeline.py's own current
    week, never a past/future one). 1.0 for a not-yet-kicked-off game, 0.0
    for a final one, in between for one actually in progress. Empty dict on
    any fetch failure - callers should treat that as "no live data available
    this build" and fall back to their own existing pregame/done logic,
    never as "every game is at 0% or 100% remaining."."""
    data = _fetch_scoreboard()
    if data is None:
        return {}
    out: dict[str, float] = {}
    for event in data.get("events", []):
        try:
            comp = event["competitions"][0]
            status = comp["status"]
            state = status["type"]["state"]
            if state == "pre":
                frac = 1.0
            elif state == "post":
                frac = 0.0
            else:
                frac = _remaining_fraction(status.get("period", 1), status.get("displayClock", "15:00"))
            for competitor in comp["competitors"]:
                team = normalize_team(competitor["team"]["abbreviation"])
                if team:
                    out[team] = frac
        except (KeyError, IndexError, TypeError):
            continue  # one malformed event shouldn't sink every other game's data
    return out
