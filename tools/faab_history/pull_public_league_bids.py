"""Historical FAAB bid puller for the OTHER public leagues found by
discover_public_leagues.py / vet_candidates.py / check_candidate_history.py -
the multi-league sibling of pull_o_league_bids.py.

No ESPN credentials needed here, unlike pull_o_league_bids.py: every
(league, year) this pulls was already confirmed accessible without auth by
check_candidate_history.py (lm-api-reads.fantasy.espn.com works fine
unauthenticated for genuinely public leagues/years - verified live
2026-09-11). Reuses pull_o_league_bids.py's fetch_season/
collapse_contingent_bids/classify unchanged - same transaction shape, same
competitive-signal classification - just looped over many leagues instead
of one, and with no credential requirement.

Every row is tagged source_league_id so build_training_table.py can apply
that league's OWN scoring rules and keep provenance for eval/backtesting -
see the conversation this was built from (a bid reflects what that league's
bidders saw under THEIR scoring, not ours).

Two output files:
  - other-leagues-bids-raw.json   every fetched transaction row, unprocessed -
                                   the resumability source of truth (skips
                                   (league_id, season) pairs already in here)
  - other-leagues-bids.json       collapse_contingent_bids + classify run
                                   over ALL raw rows so far - rewritten after
                                   EVERY league-season, so this file is
                                   always immediately usable by
                                   build_training_table.py even if the run
                                   is interrupted partway through.

Paced like discover_public_leagues.py (randomized delay + periodic longer
break) - this is a much bigger request volume (potentially 200+ league-
seasons x up to 17 weeks each), all unauthenticated, so the same courtesy
applies.

Run after check_candidate_history.py has produced some accessible years:
    python pull_public_league_bids.py
Expect this to take a while - let it run in your own terminal like the
discovery scan.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from espn_api.football import League

from pull_o_league_bids import WEEKS, classify, collapse_contingent_bids, fetch_season

HISTORY_PATH = Path(__file__).parent / "history_availability.json"
RAW_OUT_PATH = Path(__file__).parent / "other-leagues-bids-raw.json"
OUT_PATH = Path(__file__).parent / "other-leagues-bids.json"
DELAY_RANGE = (0.6, 1.8)
LONG_PAUSE_EVERY = 200  # roughly every this-many weekly requests
LONG_PAUSE_RANGE = (15.0, 45.0)


def write_classified(raw_rows: list[dict]) -> list[dict]:
    deduped = collapse_contingent_bids(raw_rows)
    # 0.0, not pull_o_league_bids.py's own $2 default - that's The O League's
    # private house rule for uncontested pickups, not a platform behavior we
    # have any reason to assume these other leagues share. See classify()'s
    # docstring.
    classified = classify(deduped, freeagent_flat_cost_dollars=0.0)
    classified.sort(key=lambda r: (r["source_league_id"], r["season"], r["week"], r["team_id"]))
    OUT_PATH.write_text(json.dumps(classified, indent=2))
    return classified


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", type=int, default=None, help="only pull this one league (default: every league in history_availability.json with an accessible year)")
    args = parser.parse_args()

    if not HISTORY_PATH.exists():
        sys.exit(f"{HISTORY_PATH} not found - run check_candidate_history.py first")
    candidates = json.loads(HISTORY_PATH.read_text())
    if args.league_id is not None:
        candidates = [c for c in candidates if c["league_id"] == args.league_id]
    candidates = [c for c in candidates if c.get("accessible_years")]
    if not candidates:
        sys.exit("no candidates with accessible years to pull")

    raw_rows: list[dict] = []
    if RAW_OUT_PATH.exists():
        raw_rows = json.loads(RAW_OUT_PATH.read_text())
    done_league_seasons = {(r["source_league_id"], r["season"]) for r in raw_rows}

    weeks_requested = 0
    for cand in candidates:
        lid = cand["league_id"]
        for year in cand["accessible_years"]:
            if (lid, year) in done_league_seasons:
                continue
            print(f"league {lid} ({cand['name']!r}) season {year}...", file=sys.stderr)
            try:
                league = League(league_id=lid, year=year)
            except Exception as exc:
                print(f"  couldn't open league: {exc}, skipping", file=sys.stderr)
                continue

            # cents_scale=False - see fetch_season's docstring. The O
            # League's own bidAmount really is in cents; these other
            # leagues' isn't, confirmed live 2026-09 (a $200-budget league's
            # top real bid had raw bidAmount exactly 200, an all-in bid, not
            # $2.00) - keep their nominal amount, don't touch it.
            season_rows = fetch_season(league, year, cents_scale=False)
            for r in season_rows:
                r["source_league_id"] = lid
            raw_rows.extend(season_rows)
            print(f"  {len(season_rows)} raw transaction rows", file=sys.stderr)

            RAW_OUT_PATH.write_text(json.dumps(raw_rows, indent=2))
            classified = write_classified(raw_rows)
            leagues_done = len({r["source_league_id"] for r in classified})
            print(f"  [{len(classified)} bids across {leagues_done} leagues written so far]", file=sys.stderr)

            weeks_requested += len(WEEKS)
            if weeks_requested % LONG_PAUSE_EVERY < len(WEEKS):
                pause = random.uniform(*LONG_PAUSE_RANGE)
                print(f"  taking a longer break ({pause:.0f}s)", file=sys.stderr)
                time.sleep(pause)
            else:
                time.sleep(random.uniform(*DELAY_RANGE))

    print(f"\ndone - see {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
