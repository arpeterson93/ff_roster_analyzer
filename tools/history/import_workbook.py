"""One-off backfill: import Alex's hand-kept "The O League Fantasy Football.xlsx"
into data/history/<slug>/games.json (the raw dataset engine/history.py reads).

Only the 'Win %tile - 12' / 'Win %tile - 10' tabs carry per-game rows (they
stop at 2018); later seasons come from tools/history/pull_espn_history.py.
Rows already present in games.json for a season are replaced only when that
season is in the workbook, so ESPN-sourced seasons are never clobbered
unless --force-seasons says so.

    python -m tools.history.import_workbook "The O League Fantasy Football.xlsx" --league o-league
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parents[2]
DEFAULT_REG_WEEKS = 13  # workbook-era seasons: weeks 14-16 were playoffs/consolation (12-team tab shows only 10 teams in wk 14)


def _num(v):
    return None if v is None else float(v)


def parse_workbook(path: Path, reg_weeks: int = DEFAULT_REG_WEEKS) -> tuple[dict, list[dict]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    games: list[dict] = []
    seasons: dict[str, dict] = {}

    def note(season: int, team_count: int):
        seasons.setdefault(str(season), {"team_count": team_count, "reg_weeks": reg_weeks, "source": "workbook"})

    # 12-team tab: columns A-E = team side, H-L = opponent side, one row per team-game.
    for r in wb["Win %tile - 12"].iter_rows(min_row=2, values_only=True):
        if not r[0] or r[1] is None:
            continue
        season, week = int(r[1]), int(r[2])
        note(season, 12)
        games.append({
            "season": season, "week": week, "manager": r[0], "pf": _num(r[3]), "result": r[4],
            "opp": r[8], "pa": _num(r[11]), "playoff": week > reg_weeks,
        })
    # 10-team tab: no opponent columns at all - PF + result only.
    for r in wb["Win %tile - 10"].iter_rows(min_row=2, values_only=True):
        if not r[0] or r[1] is None:
            continue
        season, week = int(r[1]), int(r[2])
        note(season, 10)
        games.append({
            "season": season, "week": week, "manager": r[0], "pf": _num(r[3]), "result": r[4],
            "opp": None, "pa": None, "playoff": week > reg_weeks,
        })
    return seasons, games


def merge_into(existing: dict, seasons: dict, games: list[dict], force: set[int]) -> dict:
    keep_seasons = {s for s, m in existing.get("seasons", {}).items() if m.get("source") != "workbook" and int(s) not in force}
    out_seasons = {s: m for s, m in existing.get("seasons", {}).items() if s in keep_seasons}
    out_games = [g for g in existing.get("team_games", []) if str(g["season"]) in keep_seasons]
    for s, m in seasons.items():
        if s in keep_seasons:
            continue
        out_seasons[s] = m
    out_games += [g for g in games if str(g["season"]) not in keep_seasons]
    out_games.sort(key=lambda g: (g["season"], g["week"], g["manager"]))
    return {**existing, "seasons": dict(sorted(out_seasons.items())), "team_games": out_games}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--league", default="o-league")
    ap.add_argument("--reg-weeks", type=int, default=DEFAULT_REG_WEEKS)
    ap.add_argument("--force-seasons", type=int, nargs="*", default=[], help="replace these seasons even if ESPN-sourced")
    args = ap.parse_args()

    out = REPO / "data" / "history" / args.league / "games.json"
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"league": args.league}
    seasons, games = parse_workbook(Path(args.workbook), args.reg_weeks)
    merged = merge_into(existing, seasons, games, set(args.force_seasons))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"wrote {out}: {len(merged['team_games'])} team-games across {len(merged['seasons'])} seasons")


if __name__ == "__main__":
    main()
