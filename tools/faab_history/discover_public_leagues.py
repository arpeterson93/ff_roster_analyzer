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
import random
import sys
import time
from pathlib import Path

import requests

# fantasy.espn.com/apis/v3/... now returns an empty, unusable 202 for API calls
# (discovered live 2026-09-10) - reads have moved to this host, which returns
# real 200 JSON for the same paths.
BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}"
OUT_PATH = Path(__file__).parent / "discovered_leagues.json"
DELAY_RANGE = (0.6, 1.8)  # randomized per-request pause, not a fixed bot-like interval
LONG_PAUSE_EVERY = 200  # take a longer break periodically, like a human clicking around
LONG_PAUSE_RANGE = (15.0, 45.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://fantasy.espn.com/",
    "Origin": "https://fantasy.espn.com",
}


def check_league(league_id: int, year: int) -> tuple[str, dict | None]:
    """Returns (status_label, info). info is only set when status is "public"."""
    try:
        resp = requests.get(
            BASE_URL.format(year=year, league_id=league_id),
            params={"view": "mSettings"},
            headers=HEADERS,
            timeout=10,
        )
    except requests.RequestException as exc:
        return f"error({exc.__class__.__name__})", None
    # 404 = no league was ever created at this id. 401 = a league exists here
    # but isn't public - "not authorized", not "not found". Both are useful
    # signal for id density even though neither is a hit.
    if resp.status_code == 404:
        return "does_not_exist(404)", None
    if resp.status_code == 401:
        return "exists_but_private(401)", None
    if resp.status_code != 200:
        return f"http_{resp.status_code}", None
    data = resp.json()
    settings = data.get("settings", {})
    if not settings.get("isPublic"):
        return "exists_but_private(200,isPublic=false)", None
    acq = settings.get("acquisitionSettings", {})
    info = {
        "league_id": league_id,
        "name": settings.get("name"),
        "size": settings.get("size"),
        "uses_faab": bool(acq.get("isUsingAcquisitionBudget")),
        "acquisition_budget": acq.get("acquisitionBudget"),
        "matchup_period_count": settings.get("scheduleSettings", {}).get("matchupPeriodCount"),
    }
    return "public", info


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
    exists_count = 0  # leagues that exist but aren't public (401, or 200+isPublic=false) - id density signal
    faab_count = sum(1 for r in found if r["uses_faab"])
    for lid in range(args.start, args.end + 1):
        if lid in seen_ids:
            continue
        status, result = check_league(lid, args.year)
        checked += 1
        if result:
            found.append(result)
            if result["uses_faab"]:
                faab_count += 1
            tag = "FAAB" if result["uses_faab"] else "non-FAAB"
            print(
                f"{lid}: PUBLIC ({tag}) - {result['name']!r}, {result['size']} teams, "
                f"budget={result['acquisition_budget']} "
                f"[{len(found)} public found so far, {faab_count} of those use FAAB]",
                file=sys.stderr,
            )
            OUT_PATH.write_text(json.dumps(found, indent=2))
        else:
            if status.startswith("exists_but_private"):
                exists_count += 1
            print(f"{lid}: {status}", file=sys.stderr)
        if checked % 20 == 0:
            print(
                f"...{checked} checked, {len(found)} public so far ({faab_count} FAAB), "
                f"{exists_count} real-but-private leagues seen (through id {lid})",
                file=sys.stderr,
            )
            OUT_PATH.write_text(json.dumps(found, indent=2))
        if checked % LONG_PAUSE_EVERY == 0:
            pause = random.uniform(*LONG_PAUSE_RANGE)
            print(f"...taking a longer break ({pause:.0f}s)", file=sys.stderr)
            time.sleep(pause)
        else:
            time.sleep(random.uniform(*DELAY_RANGE))

    OUT_PATH.write_text(json.dumps(found, indent=2))
    faab_count = sum(1 for r in found if r["uses_faab"])
    print(f"\nscanned {args.start}-{args.end} ({checked} new checks): {len(found)} public leagues, {faab_count} use FAAB", file=sys.stderr)


if __name__ == "__main__":
    main()
