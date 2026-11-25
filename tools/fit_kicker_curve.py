"""Fit the NFL-wide kick make-probability curves used by engine/kicker_xpts.py -> data/kicker_make_curve.json.

    python -m tools.fit_kicker_curve            # seasons 2016-latest

Field goals: logistic regression of made/missed (blocked = missed) on kick distance as a quadratic plus a linear
season trend, regular season only. A quadratic in logit space cannot wiggle, and the trend captures kickers making
more kicks at every distance than they used to (a distance-only fit under-predicts 2024-25 by about 3 points). The
curve is read at the latest fitted season rather than extrapolated forward. Chosen over higher degrees and over
recency weighting on a 2016-2023 fit scored on 2024-25 (log loss .372 vs .375 for distance-only; nflfastR's own
fg_prob, which also uses weather, scored .370 but varies between data vintages).

Extra points: one flat rate over the last three seasons (the rate stepped up in 2023).

Refit once a season (or whenever the diagnostics below drift).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import polars as pl

import ingest.nfl_data as nd

OUT = Path(__file__).resolve().parent.parent / "data" / "kicker_make_curve.json"
FIRST_SEASON = 2016
CENTER, SCALE = 40.0, 10.0
TREND_CENTER, TREND_SCALE = 2020.0, 5.0
XP_SEASONS = 3


def design(distance, season):
    x = (np.asarray(distance, float) - CENTER) / SCALE
    t = (np.asarray(season, float) - TREND_CENTER) / TREND_SCALE
    return np.column_stack([np.ones_like(x), x, x**2, t])


def irls(X, y, l2=1e-6, iters=100):
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = X @ b
        mu = 1 / (1 + np.exp(-eta))
        w = np.maximum(mu * (1 - mu), 1e-9)
        z = eta + (y - mu) / w
        new = np.linalg.solve(X.T @ (X * w[:, None]) + l2 * np.eye(X.shape[1]), X.T @ (w * z))
        if np.max(np.abs(new - b)) < 1e-10:
            return new
        b = new
    return b


def main() -> None:
    latest = datetime.now().year - 1  # the latest COMPLETED season (a season in progress would skew the trend)
    pbp = nd.play_by_play(list(range(FIRST_SEASON, latest + 1)))
    reg = pbp.filter(pl.col("season_type") == "REG")
    fg = reg.filter((pl.col("field_goal_attempt") == 1) & pl.col("kick_distance").is_not_null())
    d, s = fg["kick_distance"].to_numpy(), fg["season"].to_numpy()
    made = (fg["field_goal_result"] == "made").cast(pl.Float64).to_numpy()
    coefs = irls(design(d, s), made)
    xp = reg.filter((pl.col("extra_point_attempt") == 1) & (pl.col("season") > latest - XP_SEASONS) & pl.col("extra_point_result").is_not_null())
    xp_rate = float((xp["extra_point_result"] == "good").mean())
    out = {
        "fg": {"coefs": [float(c) for c in coefs], "center": CENTER, "scale": SCALE, "trend_center": TREND_CENTER, "trend_scale": TREND_SCALE, "season": latest},
        "xp": {"p": round(xp_rate, 4), "seasons": [latest - XP_SEASONS + 1, latest], "attempts": xp.height},
        "fit": {"fg_attempts": fg.height, "first_season": FIRST_SEASON, "mean_predicted": float((1 / (1 + np.exp(-design(d, s) @ coefs))).mean()), "mean_actual": float(made.mean())},
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"wrote {OUT}: {fg.height} field goals, XP rate {xp_rate:.4f}")


if __name__ == "__main__":
    main()
