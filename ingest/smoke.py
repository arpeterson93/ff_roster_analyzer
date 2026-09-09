"""Stage 1 exit check: python -m ingest.smoke --league o-league

Prints team count, roster sizes, FA counts per position, ROS rank counts per
position, weeks_played, and the number of unmapped players.
"""
from __future__ import annotations

import argparse
import sys

from ingest import nfl_data as nd
from ingest import rankings as rk
from ingest.config import load_league_config
from ingest.espn_client import EspnClient
from ingest.ids import build_id_map


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True)
    args = parser.parse_args()

    cfg = load_league_config(args.league)
    print(f"league: {cfg['name']} ({cfg['league_id']}, {cfg['season']})")

    client = EspnClient(cfg["league_id"], cfg["season"], cfg.get("credentials"))
    settings = client.get_settings()
    print(f"current_week={settings.current_week} final_week={settings.final_week} "
          f"reg_season_count={settings.reg_season_count} positions={settings.positions}")
    print(f"slots={settings.slots}")

    teams = client.get_teams()
    print(f"teams: {len(teams)}")
    roster_sizes = [len(t.roster) for t in teams]
    print(f"roster sizes: min={min(roster_sizes)} max={max(roster_sizes)}")

    fa_counts = {}
    for pos in settings.positions:
        fas = client.get_free_agents(pos)
        fa_counts[pos] = len(fas)
    print(f"free agents by position: {fa_counts}")

    schedules_df = nd.schedules(cfg["season"], current_season=cfg["season"])
    weeks_played, byes, _ = nd.nfl_week_context(cfg["season"], schedules_df)
    print(f"weeks_played={weeks_played}, bye weeks known for {len(byes)} teams")

    try:
        ros = rk.fetch_ros(cfg["scoring_format"])
        rank_counts = {pos: ros.filter(ros["pos"] == pos).height for pos in sorted(ros["pos"].unique().to_list())}
        print(f"ROS rank counts: {rank_counts}")
    except rk.RankingsFetchError as exc:
        print(f"FantasyPros ROS scrape failed ({exc}); falling back")
        ros = rk.fetch_ros_fallback()
        rank_counts = {pos: ros.filter(ros["pos"] == pos).height for pos in sorted(ros["pos"].unique().to_list())}
        print(f"ROS rank counts (fallback): {rank_counts}")

    id_map = build_id_map()
    unmapped = 0
    for row in ros.iter_rows(named=True):
        res = id_map.resolve(fp_id=row["fp_id"], name=row["name"], pos=row["pos"], team=row["team"])
        if res.source == "unmapped":
            unmapped += 1
    print(f"unmapped players in ROS ranks: {unmapped} / {ros.height}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
