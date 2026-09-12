"""Step 2: for each settings-compatible candidate from vet_candidates.py,
check whether its past seasons' FAAB transaction history (mTransactions2) is
readable without ESPN login credentials - the same view pull_o_league_bids.py
needs to actually pull bids.

Being public + FAAB + settings-compatible today says nothing about whether
past seasons are open - The O League itself proves that: 2026 needs no
credentials but 2019-2025 do (discovered live 2026-09-10). Only checks
scoringPeriodId=1 of each year as a cheap probe, not a full pull - once a
league/year shows up here as "accessible", pull_o_league_bids.py's fetch_season
logic (or a copy of it, pointed at this league) is what actually pulls all weeks.

Run after vet_candidates.py has produced some compatible candidates:
    python check_candidate_history.py
Resumable - skips league ids already in history_availability.json.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

from league_profile import HEADERS

VETTED_PATH = Path(__file__).parent / "vetted_candidates.json"
OUT_PATH = Path(__file__).parent / "history_availability.json"
TRANSACTIONS_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}"
DELAY_RANGE = (0.6, 1.8)


def check_year(league_id: int, year: int) -> str:
    filters = {"transactions": {"filterType": {"value": ["FREEAGENT", "WAIVER", "WAIVER_ERROR"]}}}
    headers = {**HEADERS, "x-fantasy-filter": json.dumps(filters)}
    try:
        resp = requests.get(
            TRANSACTIONS_URL.format(year=year, league_id=league_id),
            params={"view": "mTransactions2", "scoringPeriodId": 1},
            headers=headers,
            timeout=10,
        )
    except requests.RequestException as exc:
        return f"error({exc.__class__.__name__})"
    if resp.status_code == 404:
        return "does_not_exist(404)"
    if resp.status_code == 401:
        return "private(401)"
    if resp.status_code != 200:
        return f"http_{resp.status_code}"
    return "accessible" if "transactions" in resp.json() else "ok_but_unexpected_shape"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2019, help="matches pull_o_league_bids.py's default - FAAB auctions started 2019 for our league; adjust if a candidate is older/younger")
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()

    if not VETTED_PATH.exists():
        sys.exit(f"{VETTED_PATH} not found - run vet_candidates.py first")
    compatible = [r for r in json.loads(VETTED_PATH.read_text()) if r.get("compatible")]
    if not compatible:
        sys.exit("no settings-compatible candidates in vetted_candidates.json yet")

    results = []
    if OUT_PATH.exists():
        results = json.loads(OUT_PATH.read_text())
    done_ids = {r["league_id"] for r in results}

    usable_count = sum(1 for r in results if r.get("accessible_years"))
    for cand in compatible:
        lid = cand["league_id"]
        if lid in done_ids:
            continue
        year_status = {}
        for year in range(args.start_year, args.end_year + 1):
            status = check_year(lid, year)
            year_status[year] = status
            print(f"{lid} ({cand['name']!r}) {year}: {status}", file=sys.stderr)
            time.sleep(random.uniform(*DELAY_RANGE))
        accessible_years = [y for y, s in year_status.items() if s == "accessible"]
        if accessible_years:
            usable_count += 1
        results.append({
            "league_id": lid,
            "name": cand["name"],
            "year_status": year_status,
            "accessible_years": accessible_years,
        })
        print(
            f"{lid}: {len(accessible_years)}/{len(year_status)} years accessible without credentials "
            f"[{usable_count} leagues with usable history so far]\n",
            file=sys.stderr,
        )
        OUT_PATH.write_text(json.dumps(results, indent=2))

    print(f"\n{len(results)} compatible candidates checked, {usable_count} have at least one openly accessible historical season", file=sys.stderr)


if __name__ == "__main__":
    main()
