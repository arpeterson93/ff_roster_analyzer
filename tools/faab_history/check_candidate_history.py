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

"Accessible" alone is NOT enough to call a season usable, though: vet_candidates.py's
own uses_faab check is only ever against the CURRENT (--year, default 2026) season's
settings (see discover_public_leagues.py's docstring) - it says nothing about whether
THIS specific historical season was actually running FAAB. A league can switch onto
FAAB partway through its ESPN history (e.g. plain waiver-priority claims for a few
early seasons, FAAB from some year on); an accessible-but-pre-FAAB season's WAIVER
transactions would otherwise get pulled in and misread as real $0 bids rather than
excluded. So every accessible year also gets its OWN mSettings re-checked here
(check_faab_enabled) and only counts toward accessible_years if that season's own
acquisitionSettings.isUsingAcquisitionBudget was true.

Checks a FIXED --start-year..--end-year window by default (2019-2025, matching
The O League's own FAAB history). --extend-earlier additionally walks backward
year by year past --start-year, for leagues whose history goes back further -
see that flag's own help text for exactly what it does and why it's opt-in.

Run after vet_candidates.py has produced some compatible candidates, from
the repo root (needed for the league_profile import below to resolve):
    python -m tools.faab_history.check_candidate_history
    python -m tools.faab_history.check_candidate_history --extend-earlier
    python -m tools.faab_history.check_candidate_history --reverify
Resumable - skips league ids already in history_availability.json unless --reverify is passed
(needed once for every league checked before check_faab_enabled existed - see that flag's help text).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

from tools.faab_history.atomic_json import write_json
from tools.faab_history.league_profile import HEADERS, fetch_settings, profile_settings

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


def check_faab_enabled(league_id: int, year: int) -> bool | None:
    """Whether THIS specific season's own acquisitionSettings.isUsingAcquisitionBudget
    was true - not just whatever the league's CURRENT settings say (see module
    docstring). Only meaningful to call once check_year has already confirmed the
    season's transactions are readable; still makes its own independent mSettings
    request since a season's transactions and its settings aren't guaranteed to share
    the same access requirements.

    Returns None (not False) when mSettings itself couldn't be fetched for this year -
    e.g. it needs the same login some of this league's own historical seasons need for
    transactions (The O League's 2019-2025 needs credentials for both views, confirmed
    live 2026-09-16) - kept distinct from a confirmed non-FAAB season so callers never
    silently treat "couldn't check" as "confirmed clean."
    """
    status, settings = fetch_settings(league_id, year)
    if status != "ok" or settings is None:
        return None
    return profile_settings(settings)["uses_faab"]


def _usable(transactions_status: str | None, uses_faab: bool | None) -> bool:
    return transactions_status == "accessible" and uses_faab is True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2019, help="matches pull_o_league_bids.py's default - FAAB auctions started 2019 for our league; adjust if a candidate is older/younger. With --extend-earlier, this is just where the FIXED forward range starts, not a hard floor - see that flag.")
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--extend-earlier", action="store_true",
        help="after checking --start-year..--end-year, keep walking backward one year at a time "
        "(--start-year - 1, - 2, ...) for as long as each earlier season stays accessible without "
        "credentials AND confirmed FAAB (see check_faab_enabled), stopping at the first season that "
        "comes back private(401) (a real season that exists but needs login), does_not_exist(404) "
        "(before the league's own first ESPN season), or accessible-but-not-actually-FAAB (a real "
        "season this league ran under plain waiver priority instead) - whichever comes first. Off by "
        "default: --start-year=2019 already matches when The O League "
        "itself started FAAB bidding, and this adds real unbounded-per-league request volume to a "
        "rate-limited public API, worth opting into deliberately rather than on every routine "
        "--reverify-style run. A candidate whose real FAAB history goes back further than The O "
        "League's own 2019 start will end up contributing MORE weeks of history than The O League "
        "itself once this is on - real, usable signal, just another way per-league history depth in "
        "the pooled training set ends up uneven (see the conversation this was built from).",
    )
    parser.add_argument(
        "--reverify", action="store_true",
        help="re-check EVERY compatible candidate's years, not just ones not yet in history_availability.json. "
        "Needed once after check_faab_enabled was added (2026-09-16): every league already in "
        "history_availability.json from before then was only ever checked for transaction accessibility, "
        "never for whether each of those accessible seasons was actually running FAAB - a normal resumable "
        "run would never revisit them and their accessible_years could still include real pre-FAAB seasons.",
    )
    args = parser.parse_args()

    if not VETTED_PATH.exists():
        sys.exit(f"{VETTED_PATH} not found - run vet_candidates.py first")
    compatible = [r for r in json.loads(VETTED_PATH.read_text()) if r.get("compatible")]
    if not compatible:
        sys.exit("no settings-compatible candidates in vetted_candidates.json yet")

    results = []
    if OUT_PATH.exists():
        results = json.loads(OUT_PATH.read_text())
    by_id = {r["league_id"]: r for r in results}

    usable_count = sum(1 for r in results if r.get("accessible_years"))
    total = len(compatible)
    for i, cand in enumerate(compatible, start=1):
        lid = cand["league_id"]
        if lid in by_id and not args.reverify:
            continue
        was_usable = bool(by_id.get(lid, {}).get("accessible_years"))
        year_status = {}
        year_uses_faab = {}
        for year in range(args.start_year, args.end_year + 1):
            status = check_year(lid, year)
            year_status[year] = status
            faab = check_faab_enabled(lid, year) if status == "accessible" else None
            year_uses_faab[year] = faab
            suffix = "" if faab is None else f", uses_faab={faab}"
            print(f"[{i}/{total}] {lid} ({cand['name']!r}) {year}: {status}{suffix}", file=sys.stderr)
            time.sleep(random.uniform(*DELAY_RANGE))
        # See --extend-earlier's own help text - only worth trying if
        # --start-year itself was actually open AND actually FAAB; a league
        # already private, nonexistent, or confirmed non-FAAB there is
        # extremely unlikely to somehow reopen (or switch back onto FAAB)
        # one year earlier.
        if args.extend_earlier and _usable(year_status.get(args.start_year), year_uses_faab.get(args.start_year)):
            year = args.start_year - 1
            while True:
                status = check_year(lid, year)
                year_status[year] = status
                faab = check_faab_enabled(lid, year) if status == "accessible" else None
                year_uses_faab[year] = faab
                suffix = "" if faab is None else f", uses_faab={faab}"
                print(f"[{i}/{total}] {lid} ({cand['name']!r}) {year}: {status}{suffix} (extended backward)", file=sys.stderr)
                time.sleep(random.uniform(*DELAY_RANGE))
                if not _usable(status, faab):
                    break
                year -= 1
        accessible_years = [y for y, s in year_status.items() if _usable(s, year_uses_faab.get(y))]
        is_usable = bool(accessible_years)
        if is_usable and not was_usable:
            usable_count += 1
        elif was_usable and not is_usable:
            usable_count -= 1
        by_id[lid] = {
            "league_id": lid,
            "name": cand["name"],
            "year_status": year_status,
            "year_uses_faab": year_uses_faab,
            "accessible_years": accessible_years,
        }
        print(
            f"[{i}/{total}] {lid}: {len(accessible_years)}/{len(year_status)} years accessible without credentials "
            f"AND confirmed FAAB [{usable_count} leagues with usable history so far]\n",
            file=sys.stderr,
        )
        write_json(OUT_PATH, list(by_id.values()), indent=2)

    print(f"\n{len(by_id)} compatible candidates checked, {usable_count} have at least one openly accessible, confirmed-FAAB historical season", file=sys.stderr)


if __name__ == "__main__":
    main()
