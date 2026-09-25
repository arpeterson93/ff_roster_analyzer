"""Gameday live tier - refreshes schedule.json/standings.json/live.json for
the CURRENT week only, from a fresh ESPN live-status fetch plus whatever the
last full/refresh run already computed (lineups.json's per-week team
mean/SD), without redoing any projection work. See
update-scripts-implementation-plan.md Part 2.6.

    python -m engine.live [--league SLUG] [--data docs/data] [--gate] [--force]

`--gate` is a separate, minimal-dependency mode (only needs `requests`,
no ESPN client): prints/writes should_run=true|false and exits, for a CI
step that decides whether to even install the rest of this module's
dependencies. Without `--gate`, this runs the real per-league update
unconditionally (the caller - typically the `--gate` step's own decision -
is responsible for only invoking this during a live window at all).
`--force` only affects the per-league "ESPN's week has rolled over since the
last full/refresh baseline" safety check below - it does not bypass `--gate`."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from engine import standings_stage
from ingest import ids as ids_mod
from ingest.config import load_all_league_configs
from ingest.espn_client import EspnClient
from ingest.espn_scoreboard import fetch_remaining_game_fraction, fetch_scoreboard, live_game_window

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ESPN's own lineupSlot vocabulary -> the same base labels pipeline.py's past-
# week ESPN-lineup reconstruction uses (_PAST_LINEUP_SLOT_BASE), duplicated
# here (not imported) so this module's own dependency graph stays limited to
# what a live tick actually needs - importing engine.pipeline would drag in
# its full curve/FAAB/play-by-play import chain for one 5-entry dict.
_SLOT_BASE = {"D/ST": "DST", "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX", "TQB": "QB"}
_BENCH_SLOTS = {"BE", "IR"}


def _write_github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def run_gate() -> bool:
    """True if there's a live or recently-finished game worth a gameday
    tick for. Fails OPEN (True) on a scoreboard fetch failure - the real run
    already degrades safely (every live fetch falls back to the pregame
    projection), so a gate that can't see the scoreboard shouldn't be the
    thing that silently stops updates."""
    data = fetch_scoreboard()
    if data is None:
        logger.warning("live --gate: scoreboard fetch failed - failing open (should_run=true)")
        return True
    if live_game_window(data.get("events", []), datetime.now(timezone.utc)):
        return True
    logger.info("live --gate: no live or recently finished games")
    return False


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _slots_from_pids(pids_by_base: dict[str, list[str]]) -> dict[str, str]:
    slots: dict[str, str] = {}
    for base, pids in pids_by_base.items():
        if len(pids) == 1:
            slots[base] = pids[0]
        else:
            for i, pid in enumerate(pids, start=1):
                slots[f"{base}{i}"] = pid
    return slots


def run_league(cfg: dict, data_root: Path, id_map: ids_mod.IdMap, remaining_frac: dict[str, float], *, force: bool) -> None:
    slug = cfg["slug"]
    out_dir = data_root / slug
    meta_path, players_path, lineups_path = out_dir / "meta.json", out_dir / "players.json", out_dir / "lineups.json"
    if not (meta_path.exists() and players_path.exists() and lineups_path.exists()):
        logger.info("live: %s has no baseline data yet (needs a full run first) - skipping", slug)
        return

    meta = _load_json(meta_path)
    players_out = _load_json(players_path)
    lineups_data = _load_json(lineups_path)
    position_week_sd = meta.get("position_week_sd", {})

    client = EspnClient(cfg["league_id"], cfg["season"], cfg.get("credentials"))
    settings = client.get_settings()
    current_week = settings.current_week
    if current_week != meta["current_week"] and not force:
        logger.info(
            "live: %s ESPN's current_week (%s) has moved past the baseline (%s) - waiting for the next full run",
            slug, current_week, meta["current_week"],
        )
        return

    espn_teams = sorted(client.get_teams(), key=lambda t: (-t.wins, -t.points_for))
    espn_matchups = client.get_matchups()
    live_status = client.get_live_week_player_status(current_week)

    players_by_id = {p["id"]: p for p in players_out}
    started_by_team: dict[int, set[str]] = {}
    team_slots: dict[int, dict] = {}
    live_players_out: dict[str, dict] = {}
    resolved = stubbed = 0

    for t in espn_teams:
        pids_by_base: dict[str, list[str]] = {}
        bench_ids: list[str] = []
        starter_ids: set[str] = set()
        for p in t.roster:
            res = id_map.resolve(espn_id=p.espn_id, name=p.name, pos=p.position, team=p.nfl_team)
            pid = res.id
            slot = p.lineup_slot or "BE"
            if slot in _BENCH_SLOTS:
                bench_ids.append(pid)
                continue
            starter_ids.add(pid)
            pids_by_base.setdefault(_SLOT_BASE.get(slot, slot), []).append(pid)
            if pid in players_by_id:
                resolved += 1
            else:
                stubbed += 1
                players_by_id[pid] = {
                    "position": p.position, "nfl_team": p.nfl_team, "espn_id": p.espn_id,
                    "this_week": p.espn_projected_week or 0.0,
                    "weekly": [{"week": current_week, "sd": position_week_sd.get(p.position, 0.0)}],
                }
                live_players_out[pid] = {"id": pid, "name": p.name, "position": p.position, "nfl_team": p.nfl_team}
        started_by_team[t.team_id] = starter_ids
        team_slots[t.team_id] = {"slots": _slots_from_pids(pids_by_base), "bench": bench_ids}

    games_in_progress = sum(1 for frac in remaining_frac.values() if 0.0 < frac < 1.0)
    logger.info(
        "live: %s - %d teams, %d starters resolved, %d stubbed, %d NFL teams mid-game",
        slug, len(espn_teams), resolved, stubbed, games_in_progress,
    )

    # Per-team, per-week mean/SD read straight back from the last full/
    # refresh run's own lineups.json (see engine/pipeline.py's "sd" field on
    # each week entry) - no projection work redone here at all.
    team_week_mean: dict[tuple[int, int], float] = {}
    team_week_sd: dict[tuple[int, int], float] = {}
    for team_key, team_entry in lineups_data.items():
        if team_key == "_unrostered_players":
            continue
        team_id = int(team_key)
        for w_str, week_entry in team_entry.get("weeks", {}).items():
            w = int(w_str)
            if w < current_week or "sd" not in week_entry:
                continue
            team_week_mean[(team_id, w)] = week_entry["total"]
            team_week_sd[(team_id, w)] = week_entry["sd"]

    team_live_mean_sd, live_points_by_team = standings_stage.live_team_mean_sd(
        players_by_id, started_by_team, current_week, live_status, remaining_frac,
        fallback_sd_by_pos=position_week_sd,
    )

    schedule_out, standings_out = standings_stage.standings_outputs(
        espn_teams, espn_matchups, settings, cfg["sim"], current_week,
        team_week_mean, team_week_sd, team_live_mean_sd, live_points_by_team,
        week_started=True,
    )

    live_teams_out = {}
    for t in espn_teams:
        pts = live_points_by_team.get(t.team_id, {})
        mean_sd = team_live_mean_sd.get(t.team_id)
        live_teams_out[str(t.team_id)] = {
            **team_slots[t.team_id],
            "points": pts,
            "total": sum(pts.values()),
            "mean": mean_sd[0] if mean_sd else None,
            "sd": mean_sd[1] if mean_sd else None,
        }

    live_out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "week": current_week,
        "remaining_fraction": remaining_frac,
        "teams": live_teams_out,
        "players": live_players_out,
    }

    _write_json(out_dir / "schedule.json", schedule_out)
    _write_json(out_dir / "standings.json", standings_out)
    _write_json(out_dir / "live.json", live_out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default=None, help="league slug; omit to run every configured league")
    parser.add_argument("--data", default="docs/data")
    parser.add_argument("--gate", action="store_true", help="only check whether there's a live game worth a tick")
    parser.add_argument("--force", action="store_true", help="bypass the per-league week-mismatch skip check")
    args = parser.parse_args()

    if args.gate:
        should_run = run_gate()
        value = "true" if should_run else "false"
        print(f"should_run={value}")
        _write_github_output("should_run", value)
        return 0

    data_root = Path(args.data)
    configs = load_all_league_configs()
    if args.league:
        configs = [c for c in configs if c["slug"] == args.league]

    id_map = ids_mod.build_id_map()
    remaining_frac = fetch_remaining_game_fraction()

    any_failed = False
    for cfg in configs:
        try:
            run_league(cfg, data_root, id_map, remaining_frac, force=args.force)
        except Exception:
            logger.exception("live: league %s failed", cfg["slug"])
            any_failed = True

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
