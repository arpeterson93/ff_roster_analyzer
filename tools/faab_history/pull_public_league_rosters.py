"""Weekly rostered-player-id puller for the OTHER public leagues - the
multi-league sibling of pull_weekly_rosters.py, same relationship
pull_public_league_bids.py has to pull_o_league_bids.py.

No ESPN credentials needed, same reasoning as pull_public_league_bids.py:
every (league, year) pulled here already passed check_candidate_history.py's
unauthenticated accessibility check. Reuses pull_weekly_rosters.py's
rostered_ids_for_week (espn_api's box_scores(week) - ESPN's own ground
truth for who was actually on a roster that week, not something
reconstructed by replaying transactions).

Lets build_training_table.py's build_no_bid_rows() construct real "nobody
bid on this guy" negative training examples for these other leagues too,
not just The O League - see the conversation this was built from: without
this, the INTEREST stage (P(anyone bids at all)) stays stuck at whatever
The O League alone provides, even after pooling every other league's real
bid data into the PRICE stage.

Output shape: {league_id_str: {season_str: {week_str: [rostered_player_ids]}}} -
one level deeper than pull_weekly_rosters.py's own output (which has no
league level, since it only ever covers The O League), so the two files
stay separate and build_training_table.py loads each independently.

Paced like pull_public_league_bids.py - one request per week is a lot of
volume across ~200+ league-seasons, all unauthenticated.

Run after pull_public_league_bids.py (or independently - both read the same
history_availability.json worklist):
    python pull_public_league_rosters.py
Resumable - skips (league_id, season) pairs already in the output file.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from espn_api.football import League

from pull_weekly_rosters import WEEKS, rostered_ids_for_week

HISTORY_PATH = Path(__file__).parent / "history_availability.json"
OUT_PATH = Path(__file__).parent / "other-leagues-rostered-by-week.json"
DELAY_RANGE = (0.6, 1.8)
LONG_PAUSE_EVERY = 200  # roughly every this-many weekly requests
LONG_PAUSE_RANGE = (15.0, 45.0)


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

    result: dict[str, dict[str, dict[str, list[int]]]] = {}
    if OUT_PATH.exists():
        result = json.loads(OUT_PATH.read_text())
    done_league_seasons = {(lid, int(season)) for lid, seasons in result.items() for season in seasons}

    weeks_requested = 0
    for cand in candidates:
        lid = cand["league_id"]
        lid_str = str(lid)
        for year in cand["accessible_years"]:
            if (lid_str, year) in done_league_seasons:
                continue
            print(f"league {lid} ({cand['name']!r}) season {year}...", file=sys.stderr)
            try:
                league = League(league_id=lid, year=year)
            except Exception as exc:
                print(f"  couldn't open league: {exc}, skipping", file=sys.stderr)
                continue

            result.setdefault(lid_str, {})[str(year)] = {}
            for week in WEEKS:
                try:
                    ids = rostered_ids_for_week(league, week)
                except Exception as exc:
                    print(f"  week {week}: failed ({exc}), skipping", file=sys.stderr)
                    continue
                if ids:
                    result[lid_str][str(year)][str(week)] = ids

                weeks_requested += 1
                if weeks_requested % LONG_PAUSE_EVERY == 0:
                    pause = random.uniform(*LONG_PAUSE_RANGE)
                    print(f"  taking a longer break ({pause:.0f}s)", file=sys.stderr)
                    time.sleep(pause)
                else:
                    time.sleep(random.uniform(*DELAY_RANGE))

            weeks_done = len(result[lid_str][str(year)])
            print(f"  {weeks_done} weeks with rosters written", file=sys.stderr)
            OUT_PATH.write_text(json.dumps(result, indent=2))

    leagues_done = len(result)
    print(f"\ndone - {leagues_done} leagues written to {OUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
