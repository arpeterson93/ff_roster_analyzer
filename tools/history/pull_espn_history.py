"""Pull every season of a league's ESPN history into data/history/<slug>/.

Writes games.json (team-game rows: score, opponent, result, playoff flag)
and lineups.json (every rostered player's points per team-week, bench
included - what MER and the position views are computed from).

espn_api can only fetch per-player box scores from 2019 on, so seasons
before that get games.json rows only (lineups_available=false in the
season's metadata) - MER / position views simply skip them.

Credentials: env vars named by config/leagues/<slug>.yml's `credentials:`,
or --espn-s2-env / --swid-env. Any ESPN account that is a member of the
league works (e.g. the AGS cookie pair, if that account is also in this
league).

    python -m tools.history.pull_espn_history --league o-league --seasons 2014-2025 \
        --espn-s2-env ESPN_S2_OLEAGUE --swid-env SWID_OLEAGUE
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import yaml
from espn_api.football import League

REPO = Path(__file__).resolve().parents[2]
PACE_SEC = 0.4
_POS = {"D/ST": "DST", "DEF": "DST"}
_NON_STARTING = {"BE", "IR"}


def owner_name(team) -> str:
    owners = getattr(team, "owners", None) or []
    if owners:
        o = owners[0]
        if isinstance(o, dict):
            full = f"{o.get('firstName', '').strip()} {o.get('lastName', '').strip()}".strip()
            return full or o.get("displayName") or str(o.get("id"))
        return str(o)
    return team.team_name


def _result(pf: float, pa: float) -> str:
    return "W" if pf > pa else "L" if pf < pa else "T"


def pull_season(league_id: int, season: int, espn_s2: str | None, swid: str | None, with_lineups: bool = True, completed_nfl_weeks: int | None = None):
    lg = League(league_id=league_id, year=season, espn_s2=espn_s2, swid=swid)
    s = lg.settings
    managers = {t.team_id: owner_name(t) for t in lg.teams}
    reg_weeks = s.reg_season_count
    final_week = max(int(k) for k in s.matchup_periods) if s.matchup_periods else reg_weeks
    # A finished season pulls through final_week; the in-progress one only
    # through the last NFL week whose every game is final (completed_nfl_weeks).
    last_week = final_week if completed_nfl_weeks is None else min(final_week, completed_nfl_weeks)

    games = []
    for week in range(1, last_week + 1):
        time.sleep(PACE_SEC)
        for m in lg.scoreboard(week=week):
            home, away = getattr(m, "home_team", None), getattr(m, "away_team", None)
            if home is None or away is None:
                continue  # bye in an odd-sized playoff bracket
            hs, as_ = float(m.home_score), float(m.away_score)
            if hs == 0 and as_ == 0:
                continue  # unplayed
            playoff = week > reg_weeks
            for me, opp, pf, pa in ((home, away, hs, as_), (away, home, as_, hs)):
                games.append({
                    "season": season, "week": week, "manager": managers[me.team_id], "team": me.team_name,
                    "team_id": me.team_id, "pf": pf, "opp": managers[opp.team_id], "pa": pa,
                    "result": _result(pf, pa), "playoff": playoff,
                })

    lineups = []
    lineups_ok = with_lineups and season >= 2019
    if lineups_ok:
        cache: dict[int, int] = {}
        for week in range(1, last_week + 1):
            time.sleep(PACE_SEC)
            try:
                boxes = lg.box_scores(week=week, player_team_cache=cache)
            except (KeyError, IndexError) as exc:
                # ESPN returns matchups with no roster for unplayed/odd weeks; the
                # score rows above still stand, only that week's lineups are skipped.
                print(f"  {season} week {week}: no lineup data ({type(exc).__name__}: {exc}), skipped")
                continue
            for box in boxes:
                for team, lineup in ((box.home_team, box.home_lineup), (box.away_team, box.away_lineup)):
                    if team is None or not hasattr(team, "team_id"):
                        continue
                    lineups.append({
                        "season": season, "week": week, "manager": managers[team.team_id],
                        "entries": [
                            {"id": bp.playerId, "name": bp.name, "pos": _POS.get(bp.position, bp.position),
                             "slot": _POS.get(bp.slot_position, bp.slot_position), "pts": float(bp.points or 0.0)}
                            for bp in lineup
                        ],
                    })

    slots = {k: v for k, v in s.position_slot_counts.items() if k not in _NON_STARTING and v > 0}
    # Lineup structure + scoring settings fingerprint: engine/history.py starts
    # a new scoring era whenever this changes between consecutive seasons.
    scoring = sorted((i["id"], i["points"]) for i in getattr(s, "scoring_format", []) if i.get("points"))
    meta = {
        "signature": {"slots": dict(sorted(slots.items())), "scoring": scoring},
        "team_count": len(lg.teams), "reg_weeks": reg_weeks, "final_week": final_week, "source": "espn",
        "lineups_available": bool(lineups_ok), "slots": slots,
    }
    return meta, games, lineups


def parse_seasons(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="o-league")
    ap.add_argument("--seasons", help="e.g. 2014-2025 or 2019,2021")
    ap.add_argument("--current", action="store_true", help="just the config's current season (what CI runs on every build)")
    ap.add_argument("--espn-s2-env")
    ap.add_argument("--swid-env")
    ap.add_argument("--no-lineups", action="store_true")
    args = ap.parse_args()

    cfg = yaml.safe_load((REPO / "config" / "leagues" / f"{args.league}.yml").read_text(encoding="utf-8"))
    creds = cfg.get("credentials") or {}
    s2_env = args.espn_s2_env or creds.get("espn_s2")
    swid_env = args.swid_env or creds.get("swid")
    espn_s2 = os.environ.get(s2_env) if s2_env else None
    swid = os.environ.get(swid_env) if swid_env else None
    if not (espn_s2 and swid):
        raise SystemExit("Set the ESPN s2 + SWID env vars first (--espn-s2-env / --swid-env).")

    d = REPO / "data" / "history" / args.league
    games_path, lineups_path = d / "games.json", d / "lineups.json"
    raw = json.loads(games_path.read_text(encoding="utf-8")) if games_path.exists() else {"league": args.league, "seasons": {}, "team_games": []}
    lineups = json.loads(lineups_path.read_text(encoding="utf-8")) if lineups_path.exists() else []

    if not (args.seasons or args.current):
        raise SystemExit("pass --seasons or --current")
    for season in ([cfg["season"]] if args.current else parse_seasons(args.seasons)):
        print(f"season {season} ...", flush=True)
        completed = None
        if season == cfg["season"]:  # in progress: only weeks whose NFL games are all final
            from ingest import nfl_data as nd
            completed = nd.weeks_complete(nd.schedules(season, current_season=season), season)
        meta, games, lus = pull_season(cfg["league_id"], season, espn_s2, swid, not args.no_lineups, completed)
        raw["seasons"][str(season)] = meta
        raw["team_games"] = [g for g in raw["team_games"] if g["season"] != season] + games
        lineups = [l for l in lineups if l["season"] != season] + lus
        print(f"  {len(games)} team-games, {len(lus)} lineups")

    raw["seasons"] = dict(sorted(raw["seasons"].items()))
    raw["team_games"].sort(key=lambda g: (g["season"], g["week"], g["manager"]))
    d.mkdir(parents=True, exist_ok=True)
    games_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    lineups_path.write_text(json.dumps(lineups, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
