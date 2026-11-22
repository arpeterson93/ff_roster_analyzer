"""Recover per-week playoff-odds snapshots from the site-data branch's git
history (every published build keeps that day's standings.json with the sim's
real odds). For each league and each completed week it takes the FIRST build
that reported that week as complete and writes
data/history/<slug>/odds_history_seed.json, which engine/pipeline.py merges
into odds_history.json so the Standings Retrospective has weeks that predate
the live-recording. Run once (or again to refresh):

    git fetch origin site-data
    python -m tools.history.backfill_odds_history [--branch origin/site-data]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from engine.odds_history import merge, snapshot_from_build


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=False).stdout


def show_json(rev: str, path: str):
    out = git("show", f"{rev}:{path}")
    try:
        return json.loads(out) if out else None
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", default="origin/site-data")
    args = ap.parse_args()
    revs = [r for r in git("rev-list", "--reverse", args.branch).split() if r]
    slugs = sorted({p.split("/")[0] for p in git("ls-tree", "-r", "--name-only", args.branch).split() if "/" in p and p.endswith("/standings.json")})
    for slug in slugs:
        seen: set[tuple[int, int]] = set()
        result: dict = {"season": None, "snapshots": []}
        by_season: dict[int, dict] = {}
        for rev in revs:
            meta = show_json(rev, f"{slug}/meta.json")
            if not meta:
                continue
            key = (meta.get("season"), meta.get("weeks_complete") or 0)
            if key in seen or key[1] < 1:
                continue
            standings = show_json(rev, f"{slug}/standings.json")
            snap = snapshot_from_build(meta, standings or [])
            if snap is None:
                continue
            seen.add(key)
            by_season[key[0]] = merge(key[0], by_season.get(key[0]), None, snap)
        if not by_season:
            continue
        season = max(by_season)  # the pipeline only keeps the current season
        out = Path("data/history") / slug / "odds_history_seed.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(by_season[season], separators=(",", ":")), encoding="utf-8")
        print(slug, season, [s["week"] for s in by_season[season]["snapshots"]], "->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
