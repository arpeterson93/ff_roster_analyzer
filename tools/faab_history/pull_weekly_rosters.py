"""Pulls the ACTUAL fantasy-rostered player set for every week of every FAAB
history season, via espn_api's box_scores(week) - which returns each
matchup's full roster (rosterForCurrentScoringPeriod: starters AND bench)
straight from ESPN for that specific historical week. This is ESPN's own
ground truth, not something reconstructed by replaying transactions
ourselves.

Used by build_training_table.py to know who was genuinely UNOWNED (a real
free agent, not just "didn't get bid on this particular week while sitting
on someone's bench") in a given week - the required ingredient for the
"nobody bid on this guy" negative training examples.

Requires ESPN_S2_OLEAGUE / SWID_OLEAGUE env vars, same as pull_o_league_bids.py.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from espn_api.football import League

LEAGUE_ID = 355398
WEEKS = range(1, 18)
OUT_PATH = Path(__file__).parent / "o-league-rostered-by-week.json"


def rostered_ids_for_week(league: League, week: int) -> list[int]:
    ids: set[int] = set()
    for box in league.box_scores(week=week):
        for p in (box.home_lineup or []) + (box.away_lineup or []):
            ids.add(p.playerId)
    return sorted(ids)


def main():
    espn_s2 = os.environ.get("ESPN_S2_OLEAGUE")
    swid = os.environ.get("SWID_OLEAGUE")
    if not espn_s2 or not swid:
        sys.exit("Set ESPN_S2_OLEAGUE and SWID_OLEAGUE env vars first.")

    bids = json.loads((Path(__file__).parent / "o-league-bids.json").read_text())
    seasons = sorted({r["season"] for r in bids})

    result: dict[str, dict[str, list[int]]] = {}
    for season in seasons:
        print(f"season {season}...", file=sys.stderr)
        league = League(league_id=LEAGUE_ID, year=season, espn_s2=espn_s2, swid=swid)
        result[str(season)] = {}
        for week in WEEKS:
            try:
                ids = rostered_ids_for_week(league, week)
            except Exception as exc:
                print(f"  week {week}: failed ({exc}), skipping", file=sys.stderr)
                continue
            if not ids:
                continue
            result[str(season)][str(week)] = ids
            print(f"  week {week}: {len(ids)} rostered players", file=sys.stderr)

    OUT_PATH.write_text(json.dumps(result, indent=2))
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
