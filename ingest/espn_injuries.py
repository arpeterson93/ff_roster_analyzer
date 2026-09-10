"""Scrapes ESPN's public NFL injuries page (espn.com/nfl/injuries - NOT the
fantasy API) for each player's ESPN-estimated return date. Used by the
pipeline to know how many *future* weeks an injured player - any player with
a listed return estimate, regardless of fantasy roster/IR-slot status -
should stay projected at zero before engine/valuation.py's rebalancing step
spreads their ROS total across the weeks they're actually expected to play
(see project_player's ir_return_week param).

ESPN's fantasy player id and the numeric id in this page's player URLs
(espn.com/nfl/player/_/id/<id>/...) are the same underlying athlete id -
verified live 2026-09-10 (Zach Charbonnet: 4426385 on both).

Best-effort only: this page embeds its data as a `window['__espnfitt__']`
JSON blob whose shape is undocumented and can change without notice.
Retries (same pattern/backoff as ingest/rankings.py's FantasyPros scrape)
cover a transient blip; a real failure raises EspnInjuriesFetchError with
the underlying cause, which the pipeline surfaces in its warning banner
(cfg/pipeline.py) - a missing return-week estimate just falls back to the
existing current-week-only zero (see engine/valuation.py's
zero_this_week_statuses)."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime

import polars as pl
import requests

from ingest.nfl_data import week_for_date

logger = logging.getLogger(__name__)

_INJURIES_URL = "https://www.espn.com/nfl/injuries"
_FITT_RE = re.compile(r"window\['__espnfitt__'\]\s*=\s*(\{.*?\});", re.DOTALL)
_ID_RE = re.compile(r"/id/(\d+)/")
# A bare User-Agent with nothing else is an easy automated-traffic
# fingerprint - a real browser always sends Accept/Accept-Language too.
# Cheap insurance alongside the request pacing in ingest/espn_client.py;
# not guaranteed to matter, but costs nothing to include.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 15
_RETRIES = 3


class EspnInjuriesFetchError(Exception):
    pass


def _parse_return_date(date_str: str, season: int) -> date | None:
    """"Oct 11" -> a real date. The page gives no year - Aug-Dec reads as
    the season's own year, Jan-Jul as the following one (an NFL season's
    playoffs/offseason run into the next calendar year)."""
    try:
        parsed = datetime.strptime(date_str.strip(), "%b %d")
    except ValueError:
        return None
    year = season if parsed.month >= 8 else season + 1
    try:
        return date(year, parsed.month, parsed.day)
    except ValueError:
        return None


def _fetch_injuries_blob() -> list[dict]:
    """The page's raw {team: [...items]} list. Retries transient failures
    up to _RETRIES times before giving up - that now explicitly includes a
    "successful" but non-200 status (e.g. a live 2026-09-10 run got a 202
    with an empty body: requests.raise_for_status() only raises on 4xx/5xx,
    so a 202 sails through as if it were the real page and would otherwise
    hit the blob-missing case below - that reads like a soft rate-limit/
    challenge response from whatever sits in front of espn.com, not a
    structural page change, so it's worth another attempt after a pause).
    A blob genuinely missing from a real 200 page (or one that doesn't
    match the expected JSON shape) raises immediately instead, since
    retrying an actually-unchanged page can't fix that."""
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = requests.get(_INJURIES_URL, timeout=_TIMEOUT, headers=_HEADERS)
            resp.raise_for_status()
            if resp.status_code != 200:
                raise RuntimeError(f"non-200 status {resp.status_code} ({len(resp.text)} bytes) - likely a soft block/challenge, not the real page")
            match = _FITT_RE.search(resp.text)
            if not match:
                raise EspnInjuriesFetchError(f"__espnfitt__ blob not found on the page (status {resp.status_code}, {len(resp.text)} bytes)")
            try:
                return json.loads(match.group(1))["page"]["content"]["injuries"]
            except (json.JSONDecodeError, KeyError) as exc:
                raise EspnInjuriesFetchError(f"__espnfitt__ blob found but didn't match the expected shape: {exc}") from exc
        except EspnInjuriesFetchError:
            raise  # structural failure on a genuine 200 page - no point retrying
        except Exception as exc:  # noqa: BLE001 - retry loop, re-raised below
            last_exc = exc
            logger.warning("espn_injuries: attempt %s/%s failed: %s", attempt + 1, _RETRIES, exc)
            if attempt < _RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise EspnInjuriesFetchError(f"failed after {_RETRIES} attempts: {last_exc}") from last_exc


def fetch_ir_return_weeks(season: int, schedules_df: pl.DataFrame) -> dict[int, int]:
    """{espn_id: first week they're expected to play again}. Raises
    EspnInjuriesFetchError on any fetch/parse failure (caller surfaces that
    in its warning banner); an entry whose date can't itself be resolved is
    just skipped, not a failure."""
    teams = _fetch_injuries_blob()

    result: dict[int, int] = {}
    for team in teams:
        for item in team.get("items", []):
            href = (item.get("athlete") or {}).get("href") or ""
            id_match = _ID_RE.search(href)
            date_str = item.get("date")
            if not id_match or not date_str:
                continue
            return_date = _parse_return_date(date_str, season)
            if return_date is None:
                continue
            week = week_for_date(return_date, schedules_df, season)
            if week is None:
                continue
            result[int(id_match.group(1))] = week
    return result
