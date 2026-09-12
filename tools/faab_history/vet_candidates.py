"""Step 1.5, between discover_public_leagues.py and pulling any real history:
for each FAAB candidate discovered so far, pull its full settings and compare
against The O League's own settings to decide whether its FAAB bids would be
a sane addition to the training set.

Being public + FAAB today isn't enough on its own - a 6-team IDP league
scored entirely off return yardage would only add noise to a 12-team
standard-scoring model. Hard filters: team count within [--min-size,
--max-size] of ours, same scoring_type (points vs categories/roto), not an
IDP league, and has recognizable standard offensive TD scoring. PPR format
is reported but never disqualifying - useful context, not a hard requirement.

Run after discover_public_leagues.py has found some FAAB candidates:
    python vet_candidates.py
Resumable like the discovery scan - skips league ids already in vetted_candidates.json.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from league_profile import compare_to_baseline, fetch_settings, load_scoring_ledger, profile_settings, scoring_format_items

DISCOVERED_PATH = Path(__file__).parent / "discovered_leagues.json"
OUT_PATH = Path(__file__).parent / "vetted_candidates.json"
BASELINE_LEAGUE_ID = 355398  # The O League
DELAY_RANGE = (0.6, 1.8)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, default=2026, help="current season - the one every candidate is guaranteed public in")
    parser.add_argument("--min-size", type=int, default=10)
    parser.add_argument("--max-size", type=int, default=16)
    parser.add_argument(
        "--reverify", action="store_true",
        help="re-fetch settings and re-run compare_to_baseline (including the scoring ledger check) for EVERY "
        "candidate, not just ones not yet in vetted_candidates.json - needed once after scoring_ledger.json is "
        "created or edited, since compatible=true/false was already decided for existing rows before the ledger "
        "existed and a normal run would never revisit them.",
    )
    args = parser.parse_args()

    if not DISCOVERED_PATH.exists():
        sys.exit(f"{DISCOVERED_PATH} not found - run discover_public_leagues.py first")
    candidates = [r for r in json.loads(DISCOVERED_PATH.read_text()) if r.get("uses_faab")]
    if not candidates:
        sys.exit("no FAAB candidates in discovered_leagues.json yet - let discover_public_leagues.py run longer")

    baseline_status, baseline_raw = fetch_settings(BASELINE_LEAGUE_ID, args.year)
    if baseline_status != "ok":
        sys.exit(f"couldn't fetch our own league's settings as a baseline: {baseline_status}")
    baseline = profile_settings(baseline_raw)
    print(
        f"baseline (The O League): {baseline['size']} teams, {baseline['ppr_label']}, "
        f"scoring_type={baseline['scoring_type']}",
        file=sys.stderr,
    )

    ledger = load_scoring_ledger()
    print(
        f"scoring ledger: {len(ledger)} reviewed categories" if ledger else "scoring ledger: none found - not gating on it yet",
        file=sys.stderr,
    )

    results = []
    if OUT_PATH.exists():
        results = json.loads(OUT_PATH.read_text())
    by_id = {r["league_id"]: r for r in results}

    compatible_count = sum(1 for r in results if r.get("compatible"))
    for cand in candidates:
        lid = cand["league_id"]
        if lid in by_id and not args.reverify:
            continue
        status, raw = fetch_settings(lid, args.year)
        if status != "ok":
            print(f"{lid} ({cand['name']!r}): couldn't re-fetch settings ({status}), skipping", file=sys.stderr)
            time.sleep(random.uniform(*DELAY_RANGE))
            continue
        profile = profile_settings(raw)
        scoring_items = scoring_format_items(raw)
        verdict = compare_to_baseline(profile, baseline, scoring_items=scoring_items, ledger=ledger, min_size=args.min_size, max_size=args.max_size)
        was_compatible = by_id.get(lid, {}).get("compatible")
        row = {"league_id": lid, **profile, **verdict}
        by_id[lid] = row
        if verdict["compatible"]:
            if was_compatible is not True:
                compatible_count += 1
            print(
                f"{lid} ({profile['name']!r}): COMPATIBLE - {profile['size']} teams, {profile['ppr_label']} "
                f"[{compatible_count} compatible so far]",
                file=sys.stderr,
            )
        else:
            if was_compatible is True:
                compatible_count -= 1
                print(f"{lid} ({profile['name']!r}): now REJECTED (was compatible) - {'; '.join(verdict['reasons'])}", file=sys.stderr)
            else:
                print(f"{lid} ({profile['name']!r}): rejected - {'; '.join(verdict['reasons'])}", file=sys.stderr)
        results = list(by_id.values())
        OUT_PATH.write_text(json.dumps(results, indent=2))
        time.sleep(random.uniform(*DELAY_RANGE))

    print(f"\n{len(results)} candidates vetted, {compatible_count} compatible with baseline settings", file=sys.stderr)


if __name__ == "__main__":
    main()
