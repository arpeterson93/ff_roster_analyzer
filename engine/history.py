"""League history: all-time records, win-confidence curves, per-week power
rankings, every-possible-schedule records, MER and position points.

Pure computation over the raw dataset in data/history/<slug>/ (games.json +
optional lineups.json, written by tools/history/*); the result is shipped to
the site as docs/data/<slug>/history.json and rendered by docs/js/history.js.

Conventions (kept in sync with the JS tab):
- Everything is keyed by *manager* (the owner's name), not team name, so a
  rebranded team stays one row.
- Rank 1 is the best: most points for, fewest points against.
- Win confidence (WC) of a score = how much of the league's scoring history
  (within the same scoring era) it beats; ties count half. The eras exist
  because the league changed its scoring/lineup (see config `history.eras`),
  which shifts the whole score distribution.
- Playoff games are kept in the raw data but excluded from power rankings,
  schedule swaps, MER and position views (regular season only); the
  All-Time table has a with/without-playoffs variant.
"""
from __future__ import annotations

import bisect
import json
import statistics
from collections import defaultdict
from pathlib import Path

from engine.lineup import optimal_lineup

REPO = Path(__file__).resolve().parents[1]

_ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
    "D/ST": {"DST"}, "DST": {"DST"},
    "RB/WR/TE": {"RB", "WR", "TE"}, "FLEX": {"RB", "WR", "TE"},
    "RB/WR": {"RB", "WR"}, "WR/TE": {"WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"}, "SUPERFLEX": {"QB", "RB", "WR", "TE"},
}
_BENCH = {"BE", "IR", "BN"}
_POS_ORDER = ["QB", "RB", "WR", "TE", "K", "DST"]


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _sd(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else None


def _result(g) -> str:
    """W/L/T for a team-game row - the stored sheet/ESPN result wins over
    re-deriving it from points (the workbook has an equal-points 'W')."""
    if g.get("result") in ("W", "L", "T"):
        return g["result"]
    return "W" if g["pf"] > g["pa"] else "L" if g["pf"] < g["pa"] else "T"


# ---------------------------------------------------------------- eras / WC

def era_for(season: int, eras: list[dict]) -> dict:
    for e in eras:
        if season >= e.get("from", -10**9) and season <= e.get("through", 10**9):
            return e
    return eras[-1]


class WinConfidence:
    """Empirical score CDF for one era."""

    def __init__(self, scores: list[float]):
        self.s = sorted(scores)

    def __len__(self):
        return len(self.s)

    def of(self, x: float, in_sample: bool = True) -> float:
        n = len(self.s)
        if n == 0:
            return 0.5
        lo = bisect.bisect_left(self.s, x)
        hi = bisect.bisect_right(self.s, x)
        equal = hi - lo
        if in_sample and equal:
            equal -= 1  # don't count the score against itself
            n -= 1
        if n <= 0:
            return 0.5
        return (lo + 0.5 * equal) / n

    def curve(self, max_points: int = 90) -> list[list[float]]:
        n = len(self.s)
        if not n:
            return []
        step = max(1, n // max_points)
        pts = [[_r(self.s[i], 1), _r(self.of(self.s[i]), 4)] for i in range(0, n, step)]
        if pts[-1][0] != _r(self.s[-1], 1):
            pts.append([_r(self.s[-1], 1), 1.0])
        return pts


# -------------------------------------------------------------- core builders

def _season_games(games: list[dict], season: int, regular_only: bool = True) -> list[dict]:
    return [g for g in games if g["season"] == season and (not regular_only or not g["playoff"])]


def power_table(games: list[dict]) -> dict:
    """Per manager, per week: PF/PA and their league rank (1 = best), plus the
    all-play expected wins (share of the league beaten that week)."""
    by_week: dict[int, list[dict]] = defaultdict(list)
    for g in games:
        by_week[g["week"]].append(g)
    weeks = sorted(by_week)
    out: dict[str, dict] = {}
    for w in weeks:
        rows = by_week[w]
        pfs = [g["pf"] for g in rows]
        pas = [g["pa"] for g in rows if g["pa"] is not None]
        for g in rows:
            m = out.setdefault(g["manager"], {k: {} for k in ("pf", "pa", "pf_rank", "pa_rank", "all_play")})
            m["pf"][w] = g["pf"]
            m["pf_rank"][w] = 1 + sum(1 for x in pfs if x > g["pf"])
            others = len(pfs) - 1
            m["all_play"][w] = _r((sum(1 for x in pfs if x < g["pf"]) + 0.5 * (sum(1 for x in pfs if x == g["pf"]) - 1)) / others, 4) if others else None
            if g["pa"] is not None:
                m["pa"][w] = g["pa"]
                m["pa_rank"][w] = 1 + sum(1 for x in pas if x < g["pa"])
    result = {}
    for mgr, m in out.items():
        pf_w = [m["pf"].get(w) for w in weeks]
        pa_w = [m["pa"].get(w) for w in weeks]
        result[mgr] = {
            "pf": [_r(x) for x in pf_w], "pa": [_r(x) for x in pa_w],
            "pf_rank": [m["pf_rank"].get(w) for w in weeks], "pa_rank": [m["pa_rank"].get(w) for w in weeks],
            "all_play": [m["all_play"].get(w) for w in weeks],
            "avg_pf_rank": _r(_mean([x for x in m["pf_rank"].values()]), 2),
            "avg_pa_rank": _r(_mean([x for x in m["pa_rank"].values()]), 2),
            "exp_wins": _r(sum(x for x in m["all_play"].values() if x is not None), 2),
        }
    return {"weeks": weeks, "teams": result}


def schedule_swap(games: list[dict]) -> dict | None:
    """Every-possible-schedule record for one season. For team T and schedule
    owner X: in each week T's score faces X's actual opponent that week; when
    that opponent is T itself, it faces the average score of the rest of the
    league instead (T excluded) and the week is flagged (asterisk)."""
    if not games or any(g["opp"] is None for g in games):
        return None
    by_week: dict[int, dict[str, dict]] = defaultdict(dict)
    for g in games:
        by_week[g["week"]][g["manager"]] = g
    managers = sorted({g["manager"] for g in games})
    out: dict[str, dict] = {}
    for t in managers:
        per_sched: dict[str, dict] = {}
        for x in managers:
            w_l_t = [0, 0, 0]
            subbed: list[int] = []
            weeks_detail: dict[int, str] = {}
            for w, rows in sorted(by_week.items()):
                if t not in rows or x not in rows:
                    continue
                my = rows[t]["pf"]
                if x == t:
                    opp_pts = rows[t]["pa"]
                elif rows[x]["opp"] == t:
                    rest = [r["pf"] for m, r in rows.items() if m != t]
                    opp_pts = sum(rest) / len(rest)
                    subbed.append(w)
                else:
                    opp_pts = rows[x]["pa"]
                idx = 0 if my > opp_pts else 1 if my < opp_pts else 2
                w_l_t[idx] += 1
                weeks_detail[w] = "WLT"[idx]
            per_sched[x] = {"w": w_l_t[0], "l": w_l_t[1], "t": w_l_t[2], "subbed": subbed,
                            "weeks": "".join(weeks_detail[w] for w in sorted(weeks_detail))}
        actual = per_sched[t]
        others = [v for k, v in per_sched.items() if k != t]
        wins = lambda v: v["w"] + 0.5 * v["t"]  # noqa: E731
        avg = _mean([wins(v) for v in others])
        out[t] = {
            "actual": wins(actual), "best": max(wins(v) for v in others), "worst": min(wins(v) for v in others),
            "avg": _r(avg, 3), "luck": _r(wins(actual) - avg, 3),
            "by_schedule": per_sched,
        }
    return out


def starters(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e["slot"] not in _BENCH]


def lineup_efficiency(entries: list[dict], slots: dict[str, int]) -> tuple[float, float]:
    """(actual starters' points, best possible lineup from this roster's own
    players under the season's slot structure)."""
    actual = sum(e["pts"] for e in starters(entries))
    elig = {label: _ELIGIBILITY[label] for label in slots if label in _ELIGIBILITY}
    usable = {k: v for k, v in slots.items() if k in elig}
    players = [(f"{e.get('id', i)}", e["pos"], e["pts"]) for i, e in enumerate(entries)]
    best, _ = optimal_lineup(players, usable, elig)
    return actual, max(best, actual)


def mer_and_positions(lineups: list[dict], season_meta: dict, season: int, reg_weeks: int, opp_of: dict) -> dict | None:
    rows = [l for l in lineups if l["season"] == season and l["week"] <= reg_weeks]
    if not rows or not season_meta.get("slots"):
        return None
    slots = season_meta["slots"]
    mer: dict[str, dict] = {}
    pos_for: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(dict))
    pos_against: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(dict))
    by_key = {(l["week"], l["manager"]): l for l in rows}
    weeks = sorted({l["week"] for l in rows})
    for l in rows:
        w, mgr = l["week"], l["manager"]
        actual, best = lineup_efficiency(l["entries"], slots)
        m = mer.setdefault(mgr, {"actual": {}, "optimal": {}})
        m["actual"][w], m["optimal"][w] = actual, best
        by_pos: dict[str, float] = defaultdict(float)
        for e in starters(l["entries"]):
            by_pos[e["pos"]] += e["pts"]
        for pos, pts in by_pos.items():
            pos_for[mgr][pos][w] = pts
        opp = opp_of.get((w, mgr))
        if opp and (w, opp) in by_key:
            for e in starters(by_key[(w, opp)]["entries"]):
                pos_against[mgr][e["pos"]][w] = pos_against[mgr][e["pos"]].get(w, 0.0) + e["pts"]
    positions = [p for p in _POS_ORDER if any(p in d for d in pos_for.values())]
    out_mer, out_pos = {}, {}
    for mgr, m in mer.items():
        a, o = sum(m["actual"].values()), sum(m["optimal"].values())
        out_mer[mgr] = {
            "weeks": [_r(m["actual"][w] / m["optimal"][w], 4) if m["optimal"].get(w) else None for w in weeks if w in m["actual"]],
            "actual": [_r(m["actual"].get(w)) for w in weeks], "optimal": [_r(m["optimal"].get(w)) for w in weeks],
            "total_actual": _r(a), "total_optimal": _r(o), "mer": _r(a / o, 4) if o else None,
        }
    for mgr in pos_for:
        out_pos[mgr] = {
            pos: {
                "for": [_r(pos_for[mgr][pos].get(w)) for w in weeks],
                "against": [_r(pos_against[mgr][pos].get(w)) for w in weeks],
            } for pos in positions
        }
    return {"weeks": weeks, "positions": positions, "mer": out_mer, "pos": out_pos}


# ------------------------------------------------------------------ all-time

def all_time(games: list[dict], wcs: dict[str, WinConfidence], include_playoffs: bool) -> list[dict]:
    pool = [g for g in games if include_playoffs or not g["playoff"]]
    latest = max((g["season"] for g in games), default=0)
    active = {g["manager"] for g in games if g["season"] == latest}
    by_mgr: dict[str, list[dict]] = defaultdict(list)
    for g in pool:
        by_mgr[g["manager"]].append(g)
    rows = []
    for mgr, gs in by_mgr.items():
        res = [_result(g) for g in gs]
        w, l, t = res.count("W"), res.count("L"), res.count("T")
        pf = [g["pf"] for g in gs]
        pa = [g["pa"] for g in gs if g["pa"] is not None]
        wc_pf = [g["wc_pf"] for g in gs]
        wc_pa = [g["wc_pa"] for g in gs if g["wc_pa"] is not None]
        wins_pf = [g["pf"] for g, r in zip(gs, res) if r == "W"]
        loss_pf = [g["pf"] for g, r in zip(gs, res) if r == "L"]
        def band_shares(xs):
            return [sum(1 for x in xs if lo <= x < hi or (hi == 1.0 and x == 1.0)) / len(xs) for lo, hi in ((0, .25), (.25, .5), (.5, .75), (.75, 1.0))] if xs else None

        bins = band_shares(wc_pf)
        pa_bins = band_shares(wc_pa)
        rows.append({
            "manager": mgr, "active": mgr in active, "games": len(gs), "w": w, "l": l, "t": t,
            "win_pct": _r((w + 0.5 * t) / len(gs), 4),
            "avg_wc_pf": _r(_mean(wc_pf), 4), "avg_wc_pa": _r(_mean(wc_pa), 4),
            "pf": {"avg": _r(_mean(pf)), "med": _r(statistics.median(pf)), "sd": _r(_sd(pf)),
                   "cov": _r(_sd(pf) / _mean(pf), 4) if _sd(pf) else None, "min": _r(min(pf)), "max": _r(max(pf)),
                   "lowest_win": _r(min(wins_pf)) if wins_pf else None, "highest_loss": _r(max(loss_pf)) if loss_pf else None},
            "wc_bins": [_r(b, 4) for b in bins],
            "wc_pa_bins": [_r(b, 4) for b in pa_bins] if pa_bins else None,
            "pa": ({"avg": _r(_mean(pa)), "med": _r(statistics.median(pa)), "sd": _r(_sd(pa)),
                    "cov": _r(_sd(pa) / _mean(pa), 4) if _sd(pa) else None, "min": _r(min(pa)), "max": _r(max(pa))} if pa else None),
        })
    rows.sort(key=lambda r: (not r["active"], -r["win_pct"]))
    return rows


def league_scoring(games: list[dict], eras: list[dict]) -> dict:
    reg = [g for g in games if not g["playoff"]]
    seasons = []
    for s in sorted({g["season"] for g in reg}):
        gs = [g for g in reg if g["season"] == s]
        pf = [g["pf"] for g in gs]
        weeks = sorted({g["week"] for g in gs})
        seasons.append({
            "season": s, "teams": len({g["manager"] for g in gs}), "era": era_for(s, eras)["id"],
            "mean": _r(_mean(pf)), "median": _r(statistics.median(pf)), "sd": _r(_sd(pf)),
            "min": _r(min(pf)), "max": _r(max(pf)),
            "by_week": {"weeks": weeks, "mean": [_r(_mean([g["pf"] for g in gs if g["week"] == w])) for w in weeks]},
        })
    hist = {}
    bin_w = 10
    for e in eras:
        pf = [g["pf"] for g in reg if era_for(g["season"], eras)["id"] == e["id"]]
        if not pf:
            continue
        top = int(max(pf) // bin_w) + 1
        counts = [0] * top
        for x in pf:
            counts[int(x // bin_w)] += 1
        hist[e["id"]] = {"bin_width": bin_w, "counts": counts, "n": len(pf)}
    return {"seasons": seasons, "hist": hist}


# --------------------------------------------------------------------- build

DEFAULT_ERAS = [{"id": "all", "label": "All seasons"}]


def detect_eras(seasons_meta: dict) -> list[dict]:
    """Eras from the starting-lineup structure each season was pulled with: a
    new era starts whenever the slot counts differ from the previous season's
    (e.g. D/ST dropped, FLEX added). Scoring-setting tweaks alone don't split
    an era - they're stored in the season's signature but too noisy to key
    on. Seasons without a fingerprint (the workbook backfill) join the era
    of the season before them."""
    eras: list[dict] = []
    prev = None
    for s in sorted(int(k) for k in seasons_meta):
        slots = (seasons_meta[str(s)].get("signature") or {}).get("slots")
        if not eras or (slots is not None and slots != prev):
            if eras:
                eras[-1]["through"] = s - 1
            eras.append({"id": f"era{len(eras) + 1}", "from": s, "slots": slots or {}})
        if slots is not None:
            prev = slots
    if not eras:
        return DEFAULT_ERAS
    last = max(int(k) for k in seasons_meta)
    for e in eras:
        lo, hi = e["from"], e.get("through", last)
        span = f"{lo}-{hi}" if hi != lo else f"{lo}"
        desc = " ".join(f"{k}x{v}" for k, v in e.pop("slots").items())
        e["label"] = f"{span}" + (f" ({desc})" if desc else "")
    eras[0].pop("from")
    return eras


def build_history(raw: dict, lineups: list[dict], cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    aliases = cfg.get("manager_aliases") or {}
    eras = cfg.get("eras") or detect_eras(raw.get("seasons", {}))
    games = [dict(g) for g in raw.get("team_games", [])]
    raw_counts: dict[str, list] = {}
    for g in games:
        r = raw_counts.setdefault(g["manager"], [g["season"], g["season"], 0])
        r[0], r[1], r[2] = min(r[0], g["season"]), max(r[1], g["season"]), r[2] + 1
    for g in games:
        g["manager"] = aliases.get(g["manager"], g["manager"])
        if g.get("opp"):
            g["opp"] = aliases.get(g["opp"], g["opp"])
    lineups = [{**l, "manager": aliases.get(l["manager"], l["manager"])} for l in lineups]
    seasons_meta = raw.get("seasons", {})

    # Win-confidence curves, one per era (regular-season scores only).
    era_scores: dict[str, list[float]] = defaultdict(list)
    for g in games:
        if not g["playoff"]:
            era_scores[era_for(g["season"], eras)["id"]].append(g["pf"])
    wcs = {e["id"]: WinConfidence(era_scores.get(e["id"], [])) for e in eras}
    for g in games:
        e = era_for(g["season"], eras)["id"]
        g["era"] = e
        g["wc_pf"] = wcs[e].of(g["pf"], in_sample=not g["playoff"])
        g["wc_pa"] = wcs[e].of(g["pa"], in_sample=not g["playoff"]) if g["pa"] is not None else None

    managers = sorted({g["manager"] for g in games})
    mgr_idx = {m: i for i, m in enumerate(managers)}
    seasons_out: dict[str, dict] = {}
    for s in sorted({g["season"] for g in games}):
        reg = _season_games(games, s)
        meta = seasons_meta.get(str(s), {})
        opp_of = {(g["week"], g["manager"]): g["opp"] for g in reg}
        block = {
            "team_count": meta.get("team_count"), "era": era_for(s, eras)["id"], "source": meta.get("source"),
            "power": power_table(reg), "schedules": schedule_swap(reg),
            "effic": mer_and_positions(lineups, meta, s, meta.get("reg_weeks", 99), opp_of),
        }
        seasons_out[str(s)] = block

    return {
        "meta": {
            "eras": eras, "managers": managers,
            # Every owner name as ESPN reports it (before manager_aliases), for the Settings merge table.
            "raw_managers": [{"name": n, "first": v[0], "last": v[1], "games": v[2], "maps_to": aliases.get(n, n)} for n, v in sorted(raw_counts.items())],
            "seasons": sorted(seasons_out),
            "lineup_seasons": [s for s, b in seasons_out.items() if b["effic"]],
        },
        "wc_curves": {e["id"]: {"label": e.get("label", e["id"]), "n": len(wcs[e["id"]]), "points": wcs[e["id"]].curve()} for e in eras},
        "all_time": {"reg": all_time(games, wcs, False), "all": all_time(games, wcs, True)},
        "league": league_scoring(games, eras),
        "games": [
            [g["season"], g["week"], mgr_idx[g["manager"]], g["pf"], mgr_idx.get(g["opp"], -1), g["pa"], _result(g),
             1 if g["playoff"] else 0, _r(g["wc_pf"], 4), _r(g["wc_pa"], 4)]
            for g in games
        ],
        "games_cols": ["season", "week", "manager", "pf", "opp", "pa", "result", "playoff", "wc_pf", "wc_pa"],
        "seasons": seasons_out,
    }


def load_and_build(slug: str, cfg: dict | None = None, root: Path | None = None) -> dict | None:
    """None when this league has no raw history yet (the site tab then shows
    its empty state)."""
    d = (root or REPO / "data" / "history") / slug
    games_path = d / "games.json"
    if not games_path.exists():
        return None
    raw = json.loads(games_path.read_text(encoding="utf-8"))
    lineups_path = d / "lineups.json"
    lineups = json.loads(lineups_path.read_text(encoding="utf-8")) if lineups_path.exists() else []
    return build_history(raw, lineups, cfg)


def main() -> None:
    """Build docs/data/<slug>/history.json alone (no full pipeline run) - for
    previewing locally after a tools/history/pull_espn_history.py pull."""
    import argparse

    from ingest.config import load_league_config

    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True)
    args = ap.parse_args()
    cfg = load_league_config(args.league)
    out = load_and_build(args.league, cfg.get("history"))
    if out is None:
        raise SystemExit(f"no raw history at data/history/{args.league}/games.json - run the pull first")
    path = REPO / "docs" / "data" / args.league / "history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    m = out["meta"]
    print(f"wrote {path}: seasons {m['seasons'][0]}-{m['seasons'][-1]}, {len(m['managers'])} managers, "
          f"eras {[e['label'] for e in m['eras']]}, lineup seasons {m['lineup_seasons']}")


if __name__ == "__main__":
    main()
