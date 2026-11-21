"""Pull every season's draft and transactions (adds / drops / trades) for one
ESPN league straight from ESPN's raw API, 2014 onwards - enough to work out who
owned which player and when. Weekly rosters are opt-in (--rosters).

tools/history/pull_espn_history.py (via the espn_api package) only reaches
games and lineups; this fills in the rest of how players moved on and off each
roster. Raw responses are saved per season as
data/history/<slug>/player_history/<season>.json so a later script can build
player archives from them offline.

Credentials: seasons before the current one need the league member's ESPN
cookies. Put them in a gitignored .env file (or the environment):

    ESPN_S2_OLEAGUE=<espn_s2 cookie>
    SWID_OLEAGUE=<SWID cookie, braces included>

The current season is public and works without them. Usage:

    python -m tools.history.pull_player_history --league o-league
    python -m tools.history.pull_player_history --league o-league --seasons 2014-2018
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml

try:  # python-dotenv is in requirements.txt; optional so the script still runs without it
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

HOST = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
MAX_PERIOD = 18  # regular season + playoffs upper bound; empty periods are cheap


def season_url(league_id: int, season: int, current_season: int) -> tuple[str, dict]:
    """ESPN serves seasons since 2018 under /seasons/<year>/segments/0/leagues/<id>
    and older ones under /leagueHistory/<id>?seasonId=<year> (a one-element list)."""
    if season >= 2018:
        return f"{HOST}/seasons/{season}/segments/0/leagues/{league_id}", {}
    return f"{HOST}/leagueHistory/{league_id}", {"seasonId": season}


def fetch(session: requests.Session, url: str, base_params: dict, views: list[str], extra: dict | None = None):
    """One ESPN GET. Returns (payload or None, status_code). The pre-2018 endpoint
    wraps the league in a list - unwrapped here so callers see one shape."""
    params = list(base_params.items()) + [("view", v) for v in views] + list((extra or {}).items())
    for attempt in range(3):
        try:
            r = session.get(url, params=params, timeout=45)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                data = data[0] if data else None
            return data, 200
        if r.status_code in (401, 403, 404):
            return None, r.status_code
        time.sleep(1.5 * (attempt + 1))
    return None, -1


def pull_season(session, league_id: int, season: int, current_season: int, with_rosters: bool) -> dict:
    url, base = season_url(league_id, season, current_season)
    out: dict = {"season": season, "status": "ok"}

    teams, code = fetch(session, url, base, ["mTeam", "mDraftDetail"])
    if teams is None:
        return {"season": season, "status": f"unavailable (HTTP {code})"}
    out["teams"] = [
        {"id": t["id"], "name": ((t.get("location") or "") + " " + (t.get("nickname") or "")).strip() or t.get("name"), "owners": t.get("owners")}
        for t in teams.get("teams", [])
    ]
    out["members"] = [{"id": m.get("id"), "name": f"{m.get('firstName', '')} {m.get('lastName', '')}".strip(), "displayName": m.get("displayName")} for m in teams.get("members", [])]
    dd = teams.get("draftDetail") or {}
    out["draft"] = {"drafted": dd.get("drafted"), "picks": dd.get("picks", [])}

    # Transactions: the default call returns only recent ones, so walk every scoring period and de-dupe by id.
    tx_by_id: dict[str, dict] = {}
    for period in range(0, MAX_PERIOD + 1):
        extra = {"scoringPeriodId": period} if period else {}
        data, code = fetch(session, url, base, ["mTransactions2"], extra)
        for t in (data or {}).get("transactions", []):
            tx_by_id[t["id"]] = t
    out["transactions"] = list(tx_by_id.values())

    if with_rosters:
        rosters: dict[str, list] = {}
        for period in range(1, MAX_PERIOD + 1):
            data, code = fetch(session, url, base, ["mRoster"], {"scoringPeriodId": period})
            teams_r = (data or {}).get("teams", [])
            week = []
            for t in teams_r:
                for e in (t.get("roster") or {}).get("entries", []):
                    week.append([t["id"], e.get("playerId"), e.get("lineupSlotId")])
            if week:
                rosters[str(period)] = week
        out["rosters"] = rosters
    return out


def parse_seasons(spec: str | None, first: int, last: int) -> list[int]:
    if not spec:
        return list(range(first, last + 1))
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="o-league", help="slug of config/leagues/<slug>.yml")
    ap.add_argument("--seasons", help="e.g. 2014-2018 or 2014,2016 (default: 2014..current)")
    ap.add_argument("--first", type=int, default=2014)
    ap.add_argument("--rosters", action="store_true", help="also pull weekly mRoster snapshots (slow; ownership is already derivable from draft + transactions)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(f"config/leagues/{args.league}.yml").read_text(encoding="utf-8"))
    league_id, current = cfg["league_id"], cfg["season"]
    creds = cfg.get("credentials") or {}
    s2 = os.environ.get(creds.get("espn_s2", ""))
    swid = os.environ.get(creds.get("swid", ""))
    session = requests.Session()
    if s2 and swid:
        session.cookies.set("espn_s2", s2, domain=".espn.com")
        session.cookies.set("SWID", swid, domain=".espn.com")
    else:
        print("No ESPN cookies in the environment/.env - only the current season will be reachable.", file=sys.stderr)

    out_dir = Path("data/history") / args.league / "player_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    for season in parse_seasons(args.seasons, args.first, current):
        res = pull_season(session, league_id, season, current, args.rosters)
        (out_dir / f"{season}.json").write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
        if res["status"] != "ok":
            print(f"{season}: {res['status']}")
            continue
        tx = res["transactions"]
        kinds = {}
        for t in tx:
            kinds[t["type"]] = kinds.get(t["type"], 0) + 1
        print(f"{season}: {len(res['draft']['picks'])} draft picks, {len(tx)} transactions {kinds}, {len(res.get('rosters', {}))} roster weeks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
