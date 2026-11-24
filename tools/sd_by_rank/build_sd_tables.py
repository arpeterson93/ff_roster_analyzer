"""Outcome SD of a player's weekly fantasy points given (a) his FantasyPros weekly position rank and
(b) his FantasyPros rest-of-season (ROS) position rank, on the same player-weeks, 2020-2025.

Unlike engine/curve.py's table (one player's own week-to-week SD, averaged by rank), this pools every
ranked player-week at a rank and takes the SD of the actual points, so it also carries the uncertainty
in the rank itself. Standard (non-PPR) scoring from nflverse stats, which matches the O League's
scoring_format; kickers use 3/4/5 per FG tier + 1 per PAT. Ranked players with no stat row score 0.

Usage: python -m tools.sd_by_rank.build_sd_tables [out.json]   (default data/sd_by_rank.json)
Each table is also written smoothed with engine.curve.smooth_sd (what the sim would consume)."""
import sys, json, datetime as dt, collections
from pathlib import Path
import numpy as np, polars as pl, nflreadpy as nfl

from engine.curve import SD_MONOTONE_POSITIONS, smooth_sd

OUT = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2] / "data" / "sd_by_rank.json")
SEASONS = list(range(2020, 2026))
POS = ["QB", "RB", "WR", "TE", "K"]

stats = pl.concat([nfl.load_player_stats(s).filter(pl.col("season_type") == "REG") for s in SEASONS], how="diagonal_relaxed")
kcols = ["fg_made_0_19", "fg_made_20_29", "fg_made_30_39", "fg_made_40_49", "fg_made_50_59", "fg_made_60_", "pat_made"]
stats = stats.with_columns([pl.col(c).fill_null(0) for c in kcols if c in stats.columns])
stats = stats.with_columns(
    pl.when(pl.col("position") == "K")
    .then(3 * (pl.col("fg_made_0_19") + pl.col("fg_made_20_29") + pl.col("fg_made_30_39")) + 4 * pl.col("fg_made_40_49")
          + 5 * (pl.col("fg_made_50_59") + pl.col("fg_made_60_")) + pl.col("pat_made"))
    .otherwise(pl.col("fantasy_points")).alias("pts")
)
pts = {(r["player_id"], r["season"], r["week"]): r["pts"] for r in stats.select("player_id", "season", "week", "pts").iter_rows(named=True)}

ids = nfl.load_ff_playerids().select("fantasypros_id", "gsis_id").drop_nulls()
fp2gsis = {str(int(float(a))): b for a, b in ids.iter_rows()}

sched = nfl.load_schedules(SEASONS).filter(pl.col("game_type") == "REG")
first_day = {}
for r in sched.select("season", "week", "gameday").iter_rows():
    d = dt.date.fromisoformat(str(r[2]))
    k = (r[0], r[1])
    if k not in first_day or d < first_day[k]:
        first_day[k] = d

rk = nfl.load_ff_rankings("all").filter(pl.col("ecr_type") == "wp").filter(pl.col("page_type").is_in([f"weekly-{p.lower()}" for p in POS]))
rk2 = nfl.load_ff_rankings("all").filter(pl.col("ecr_type") == "rp").filter(pl.col("page_type").is_in([f"redraft-{p.lower()}" for p in POS]))


def week_for(d):
    best = None
    for (s, w), e in first_day.items():
        gap = (e - d).days
        if 0 <= gap <= 6 and (best is None or gap < best[0]):
            best = (gap, s, w)
    return None if best is None else (best[1], best[2])


def ranks_by_week(frame, prefix):
    """{(season, week, pos): {gsis: rank}} from the latest scrape on or before that week's first game day."""
    out = {}
    latest = {}
    for date_str in sorted(set(frame["scrape_date"].to_list())):
        d = dt.date.fromisoformat(date_str)
        sw = week_for(d)
        if sw is None:
            continue
        latest[sw] = date_str  # later dates overwrite: keep the one closest to kickoff
    for (s, w), date_str in latest.items():
        sub = frame.filter(pl.col("scrape_date") == date_str)
        for pos in POS:
            pg = sub.filter(pl.col("page_type") == f"{prefix}-{pos.lower()}").sort("ecr")
            m = {}
            for i, fid in enumerate(pg["id"].to_list(), 1):
                g = fp2gsis.get(str(fid))
                if g:
                    m[g] = i
            out[(s, w, pos)] = m
    return out


wk = ranks_by_week(rk, "weekly")
ros = ranks_by_week(rk2, "redraft")
rows = []  # (pos, weekly_rank, ros_rank, points)
for (s, w, pos), m in wk.items():
    r_ros = ros.get((s, w, pos), {})
    for g, wr in m.items():
        if g not in r_ros:
            continue
        rows.append((pos, wr, r_ros[g], pts.get((g, s, w), 0.0)))
print("player-weeks", len(rows), "weeks covered", len({(s, w) for s, w, _ in wk}), file=sys.stderr)


def table(idx, pos, cap):
    d = collections.defaultdict(list)
    for r in rows:
        if r[0] == pos and r[idx] <= cap:
            d[r[idx]].append(r[3])
    return {k: (float(np.std(v)), float(np.mean(v)), len(v)) for k, v in d.items() if len(v) >= 12}


CAP = {"QB": 32, "RB": 60, "WR": 70, "TE": 30, "K": 28}
out = {}
for pos in POS:
    w = table(1, pos, CAP[pos]); r = table(2, pos, CAP[pos])
    mono = pos in SD_MONOTONE_POSITIONS
    w_sm = smooth_sd({k: v[0] for k, v in w.items()}, mono)
    r_sm = smooth_sd({k: v[0] for k, v in r.items()}, mono)
    # rows: [rank, raw SD, mean points, player-weeks, smoothed SD]
    out[pos] = {"weekly": [[k, round(v[0], 3), round(v[1], 3), v[2], round(w_sm[k], 3)] for k, v in sorted(w.items())],
                "ros": [[k, round(v[0], 3), round(v[1], 3), v[2], round(r_sm[k], 3)] for k, v in sorted(r.items())]}
    print(pos, "weekly ranks", len(w), "ros ranks", len(r), "n(weekly rank 1)", w.get(1, (0, 0, 0))[2], file=sys.stderr)
json.dump(out, open(OUT, "w"))
