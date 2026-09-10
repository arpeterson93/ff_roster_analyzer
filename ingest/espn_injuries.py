"""Scrapes ESPN's public NFL injuries page (espn.com/nfl/injuries - NOT the
fantasy API) for each player's ESPN-estimated return date. Used by the
pipeline to know how many *future* weeks a fantasy-rostered IR player should
stay projected at zero before engine/valuation.py's rebalancing step spreads
their ROS total across the weeks they're actually expected to play (see
project_player's ir_return_week param).

ESPN's fantasy player id and the numeric id in this page's player URLs
(espn.com/nfl/player/_/id/<id>/...) are the same underlying athlete id -
verified live 2026-09-10 (Zach Charbonnet: 4426385 on both).

Best-effort only: this page embeds its data as a `window['__espnfitt__']`
JSON blob whose shape is undocumented and can change without notice, so any
fetch/parse failure here returns {} rather than raising - a missing
return-week estimate just falls back to the existing current-week-only zero
(see engine/valuation.py's zero_this_week_statuses)."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime

import polars as pl
import requests

from ingest.nfl_data import week_for_date

logger = logging.getLogger(__name__)

_INJURIES_URL = "https://www.espn.com/nfl/injuries"
_FITT_RE = re.compile(r"window\['__espnfitt__'\]\s*=\s*(\{.*?\});", re.DOTALL)
_ID_RE = re.compile(r"/id/(\d+)/")


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


def fetch_ir_return_weeks(season: int, schedules_df: pl.DataFrame) -> dict[int, int] | None:
    """{espn_id: first week they're expected to play again}. None on any
    fetch/parse failure (caller can surface that as a warning); an entry
    whose date can't itself be resolved is just skipped, not a failure."""
    try:
        resp = requests.get(_INJURIES_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        match = _FITT_RE.search(resp.text)
        if not match:
            logger.warning("espn_injuries: __espnfitt__ blob not found on the page")
            return None
        teams = json.loads(match.group(1))["page"]["content"]["injuries"]
    except Exception:
        logger.warning("espn_injuries: fetch/parse failed", exc_info=True)
        return None

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
