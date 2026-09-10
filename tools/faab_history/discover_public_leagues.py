"""Scans a range of ESPN league ids looking for candidates to extend the
FAAB training dataset beyond The O League: leagues that are (a) public
RIGHT NOW (so a cheap, unauthenticated check works at all - see isPublic in
raw mSettings, discovered live 2026-09-10), and (b) currently configured for
real FAAB bidding (acquisitionSettings.isUsingAcquisitionBudget).

Being public today does NOT mean a league's past seasons are public too -
The O League itself is a case in point (its own 2019-2025 seasons require
credentials even though 2026 doesn't), so this is only step 1: it narrows
a huge id space down to a short list worth then checking year-by-year for
actual pullable history (see check_league_history() / a future step 2
script). Paced with a small delay between requests - this hits a real
public API belonging to a third party, so it goes slowly and stops
cleanly rather than hammering it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE_URL = "https://fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}"
OUT_PATH = Path(__file__).parent / "discovered_leagues.json"
DELAY_SECONDS = 0.25


def check_league(league_id: int, year: int) -> dict | None:
    """None if not a public, fetchable league this year. Otherwise a dict of
    what we'd need to decide if it's worth pulling FAAB history from."""
    try:
        resp = requests.get(BASE_URL.format(year=year, league_id=league_id), params={"view": "mSettings"}, timeout=10)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    settings = data.get("settings", {})
    if not settings.get("isPublic"):
        return None
    acq = settings.get("acquisitionSettings", {})
    return {
        "league_id": league_id,
        "name": settings.get("name"),
        "size": settings.get("size"),
        "uses_faab": bool(acq.get("isUsingAcquisitionBudget")),
        "acquisition_budget": acq.get("acquisitionBudget"),
        "scoring_periods": len((data.get("status") or {}).get("waiverLastExecutionDate", "") or "") or None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--year", type=int, default=2026, help="current season - cheapest check, see module docstring")
    args = parser.parse_args()

    found = []
    if OUT_PATH.exists():
        found = json.loads(OUT_PATH.read_text())
    seen_ids = {r["league_id"] for r in found}

    checked = 0
    for lid in range(args.start, args.end + 1):
        if lid in seen_ids:
            continue
        result = check_league(lid, args.year)
        checked += 1
        if result:
            found.append(result)
            tag = "FAAB" if result["uses_faab"] else "non-FAAB"
            print(f"  {lid}: PUBLIC ({tag}) - {result['name']!r}, {result['size']} teams, budget={result['acquisition_budget']}", file=sys.stderr)
        if checked % 50 == 0:
            print(f"...{checked} checked, {len(found)} public so far (through id {lid})", file=sys.stderr)
            OUT_PATH.write_text(json.dumps(found, indent=2))
        time.sleep(DELAY_SECONDS)

    OUT_PATH.write_text(json.dumps(found, indent=2))
    faab_count = sum(1 for r in found if r["uses_faab"])
    print(f"\nscanned {args.start}-{args.end} ({checked} new checks): {len(found)} public leagues, {faab_count} use FAAB", file=sys.stderr)


if __name__ == "__main__":
    main()
