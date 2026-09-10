"""Held-out backtest of the three FAAB estimators in engine/faab_estimate.py.

Splits o-league-training-table.json 80/20, STRATIFIED BY POSITION AND SIGNAL
(won/outbid/no_bid kept in their true proportions in both halves - a naive
random split would let position or signal mix drift between train/test and
confound the results). Fits/builds each method on the 80% "pool" only, scores
every 20% holdout row against it, and writes tools/faab_history/eval_results.json
(per-row predictions + errors) for the visualization script to consume.

Evaluates ALL THREE signal types the pool contains, not just real bids:
"won"/"outbid" rows check whether the estimate is in the right neighborhood
of a real dollar amount (both actually target $0 - see
engine/faab_estimate.py's module docstring for why outbid rows still
belong in the training pool despite that); "no_bid" rows check the
FALSE-POSITIVE side - does the model correctly land near $0 for a player
nobody actually wanted, or does it hallucinate value from nearby comps.

Vectorized (numpy broadcasting) rather than calling the per-query production
functions in a loop - this evaluates ~3,600 holdout rows against pools up to
~7,000 rows each; the naive per-query Python loop was too slow to iterate on.
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from engine.faab_estimate import (
    FEATURE_NAMES,
    POSITIONS,
    TRAINING_TABLE_PATH,
    compute_season_avg_team_spend,
    feature_vector,
    load_trainable_rows,
    target_pct,
)

SEED = 20260910
TEST_FRACTION = 0.2
K = 10
OUT_PATH = Path(__file__).parent / "eval_results.json"


def stratified_split(rows: list[dict], seed: int, test_fraction: float) -> tuple[list[dict], list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["position"], r["signal"])].append(r)
    rng = random.Random(seed)
    train, test = [], []
    for group in groups.values():
        shuffled = group[:]
        rng.shuffle(shuffled)
        cut = max(1, round(len(shuffled) * test_fraction)) if len(shuffled) > 1 else 0
        test.extend(shuffled[:cut])
        train.extend(shuffled[cut:])
    return train, test


def build_position_matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(standardized feature matrix, mean, std) for one position's pool rows,
    in the same row order as `rows`."""
    raw = np.array([[feature_vector(r)[f] for f in FEATURE_NAMES] for r in rows])
    mean = raw.mean(axis=0)
    std = raw.std(axis=0)
    std[std == 0] = 1.0
    return (raw - mean) / std, mean, std


def fit_regression_np(rows: list[dict], season_avg_spend: dict[int, float]) -> dict[str, float]:
    X_rows, y_rows = [], []
    for r in rows:
        fv = feature_vector(r)
        pos_dummies = [1.0 if r["position"] == p else 0.0 for p in POSITIONS[1:]]
        X_rows.append([1.0] + [fv[f] for f in FEATURE_NAMES] + pos_dummies)
        y_rows.append(math.log1p(target_pct(r, season_avg_spend)))
    X = np.array(X_rows)
    y = np.array(y_rows)
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    names = ["intercept"] + FEATURE_NAMES + [f"pos_{p}" for p in POSITIONS[1:]]
    return dict(zip(names, coefs))


def regression_predict_one(query: dict, coefs: dict[str, float]) -> float:
    fv = feature_vector(query)
    pos_dummies = [1.0 if query["position"] == p else 0.0 for p in POSITIONS[1:]]
    x = np.array([1.0] + [fv[f] for f in FEATURE_NAMES] + pos_dummies)
    log_pred = float(np.dot(x, np.array(list(coefs.values()))))
    return max(math.expm1(log_pred), 0.0)


def main():
    all_rows = json.loads(TRAINING_TABLE_PATH.read_text())
    season_avg_spend = compute_season_avg_team_spend(all_rows)
    trainable = load_trainable_rows()
    train, test = stratified_split(trainable, SEED, TEST_FRACTION)
    print(f"train pool: {len(train)}  holdout: {len(test)}")

    coefs = fit_regression_np(train, season_avg_spend)

    # Precompute per-position matrices ONCE (not per query).
    by_pos_train: dict[str, list[dict]] = defaultdict(list)
    for r in train:
        by_pos_train[r["position"]].append(r)
    pos_matrix: dict[str, np.ndarray] = {}
    pos_mean: dict[str, np.ndarray] = {}
    pos_std: dict[str, np.ndarray] = {}
    pos_targets: dict[str, np.ndarray] = {}
    pos_points_vals: dict[str, list[float]] = {}
    pos_snap_vals: dict[str, list[float]] = {}
    pos_combined: dict[str, np.ndarray] = {}

    def pct_rank(vals, v):
        return sum(1 for x in vals if x <= v) / len(vals)

    for pos, rows in by_pos_train.items():
        mat, mean, std = build_position_matrix(rows)
        pos_matrix[pos] = mat
        pos_mean[pos] = mean
        pos_std[pos] = std
        pos_targets[pos] = np.array([target_pct(r, season_avg_spend) for r in rows])

        points_vals = [feature_vector(pr)["prior_week_actual_points"] for pr in rows]
        snap_vals = [feature_vector(pr)["snap_pct_prior_week"] for pr in rows]
        pos_points_vals[pos] = points_vals
        pos_snap_vals[pos] = snap_vals
        # One-time O(n^2) per position (not per query) to get each pool row's
        # own combined usage percentile within its position.
        pos_combined[pos] = np.array(
            [(pct_rank(points_vals, pv) + pct_rank(snap_vals, sv)) / 2.0 for pv, sv in zip(points_vals, snap_vals)]
        )

    results = []
    for r in test:
        pos = r["position"]
        pool_rows = by_pos_train[pos]
        mat, mean, std = pos_matrix[pos], pos_mean[pos], pos_std[pos]
        targets = pos_targets[pos]

        qv = (np.array([feature_vector(r)[f] for f in FEATURE_NAMES]) - mean) / std
        dists = np.linalg.norm(mat - qv, axis=1)
        nearest_idx = np.argsort(dists)[:K]
        weights = 1.0 / (dists[nearest_idx] + 0.05)
        comp_pred = float(np.sum(weights * targets[nearest_idx]) / np.sum(weights))

        # Simple baseline: same-decile (±0.05 combined percentile) average.
        q_fv = feature_vector(r)
        q_combined = (pct_rank(pos_points_vals[pos], q_fv["prior_week_actual_points"]) + pct_rank(pos_snap_vals[pos], q_fv["snap_pct_prior_week"])) / 2.0
        bucket_mask = np.abs(pos_combined[pos] - q_combined) <= 0.05
        simple_pred = float(targets[bucket_mask].mean()) if bucket_mask.any() else float(targets.mean())

        reg_pred = regression_predict_one(r, coefs)
        actual = target_pct(r, season_avg_spend)

        results.append(
            {
                "position": pos, "season": r["season"], "week": r["week"], "signal": r["signal"],
                "name": r["add_player_name"],
                "actual_dollars": actual * 1000.0,
                "comp_dollars": comp_pred * 1000.0,
                "simple_dollars": simple_pred * 1000.0,
                "regression_dollars": reg_pred * 1000.0,
                "prior_week_actual_points": q_fv["prior_week_actual_points"],
                "snap_pct_prior_week": q_fv["snap_pct_prior_week"],
            }
        )

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} holdout evaluations to {OUT_PATH}")


if __name__ == "__main__":
    main()
