"""How many pooled leagues had each bid event's player genuinely available that week.

Why this exists: engine/faab_estimate.py's interest comps report `leagues_with_bid / leagues_eligible`, where
"eligible" was only the leagues that have SOME training row for that (season, week, player). A synthetic no_bid
row is only written when the player clears `is_faab_relevant` (usage or ROS rank), so a player who fails that gate
but was still bid on somewhere (e.g. a QB ranked 58th) had no no_bid rows at all: the only leagues counted were
the ones that bid, and the bid rate read 100% by construction. The real denominator is every pooled league with a
weekly roster snapshot where the player was on no roster that week.

Output (tools/faab_history/event-availability.json, small enough to commit):
    {"snapshot_leagues": {"2023": {"11": 387}},        # leagues counted per (season, week)
     "available": {"2023-11-4367178": 353, ...}}       # leagues where that player was unrostered, bid events only

Inputs: combined-training-table.parquet (for the bid events and which league-seasons are active) plus the two
weekly-roster snapshot files. Run after fetch_release_data.py --all:

    python -m tools.faab_history.build_event_availability
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import polars as pl

HERE = Path(__file__).resolve().parent
TABLE = HERE / "combined-training-table.parquet"
OTHER_ROSTERS = HERE / "other-leagues-rostered-by-week.json"
O_LEAGUE_ROSTERS = HERE / "o-league-rostered-by-week.json"
OUT = HERE / "event-availability.json"
O_LEAGUE_ID = 355398


def main() -> None:
    table = pl.read_parquet(TABLE, columns=["season", "week", "add_player_id", "signal", "source_league_id"])
    # A league-season only counts if it has at least one training row that season - the same spirit as
    # build_training_table's "season with real activity" gate, so a folded league doesn't inflate the denominator.
    active = {(int(l), int(s)) for l, s in table.select("source_league_id", "season").unique().iter_rows()}
    events = table.filter(pl.col("signal") != "no_bid").select("season", "week", "add_player_id").unique()

    rosters: dict[int, dict] = {int(k): v for k, v in json.loads(OTHER_ROSTERS.read_text()).items()}
    if O_LEAGUE_ROSTERS.exists():
        rosters[O_LEAGUE_ID] = json.loads(O_LEAGUE_ROSTERS.read_text())

    snapshot_leagues: dict[tuple[int, int], int] = Counter()
    rostered_count: dict[tuple[int, int], Counter] = defaultdict(Counter)
    for lid, seasons in rosters.items():
        for season_str, weeks in seasons.items():
            season = int(season_str)
            if (lid, season) not in active:
                continue
            for week_str, ids in weeks.items():
                key = (season, int(week_str))
                snapshot_leagues[key] += 1
                rostered_count[key].update(set(ids))

    available: dict[str, int] = {}
    for season, week, player in events.iter_rows():
        key = (int(season), int(week))
        n = snapshot_leagues.get(key, 0)
        if n:
            available[f"{season}-{week}-{player}"] = n - rostered_count[key].get(int(player), 0)

    out = {
        "snapshot_leagues": {
            str(s): {str(w): n for (ss, w), n in sorted(snapshot_leagues.items()) if ss == s}
            for s in sorted({s for s, _ in snapshot_leagues})
        },
        "available": available,
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {len(available):,} bid events over {len(snapshot_leagues)} season-weeks to {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
