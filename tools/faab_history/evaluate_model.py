"""Held-out backtest of the three FAAB estimators in engine/faab_estimate.py.

Splits o-league-training-table.json by EVENT (season, week, add_player_id),
STRATIFIED BY POSITION AND EVENT SIGNAL CLASS (won/outbid/no_bid kept in
their true proportions in both halves - see stratified_split for why event-
level, not row-level: a single real auction produces several team-rows that
share one feature vector, and splitting by row let a holdout query's own
auction siblings sit in the train pool at distance 0.0). Fits/builds each
method on the 80% "pool" only, scores every 20% holdout row against it, and
writes tools/faab_history/eval_results.json (per-row predictions + errors)
for the visualization script to consume.

TWO-STAGE, matching engine/faab_estimate.py's module docstring: each method
predicts P(anyone bids) from the full won+outbid+no_bid interest pool
(deduped one row per event - see engine.faab_estimate.dedupe_events_for_interest)
x conditional price from the won-only price pool. Evaluates ALL THREE
signal types the holdout contains, not just real bids: "won"/"outbid" rows
check whether the estimate is in the right neighborhood of a real dollar
amount (both actually target $0 for THIS bidder - outbid rows never drew
down a budget); "no_bid" rows check the FALSE-POSITIVE side - does the
model correctly land near $0 for a player nobody actually wanted.

Vectorized (numpy broadcasting) rather than calling the per-query production
functions in a loop - this evaluates ~3,600 holdout rows against pools up to
~14,000 rows; the naive per-query Python loop was too slow to iterate on.

--table/--holdout-league-id let this run against
tools/faab_history/combined-training-table.json (The O League plus every
other vetted public league) instead of The O League alone - see
build_training_table.py's module docstring for why pooling is expected to
help the PRICE stage for every league regardless (more real comps at
similar performance levels = lower-variance nearest-neighbor/decile/
regression estimates of the same underlying quantity), while the INTEREST
stage only pools whichever leagues actually have real no_bid negatives -
i.e. those pull_public_league_rosters.py has been run for (see
interest_eligible_leagues below) - since a league with only real bid rows
and no roster pull would just teach the model "someone always bids".
Compare a plain run (o-league-training-table.json) against a pooled run
(--table combined-training-table.json --holdout-league-id 355398, so the
test holdout is still only The O League's own bids) to see whether pooling
actually lowers held-out error, rather than assuming it does.
"""
from __future__ import annotations

import argparse
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
    annotate_position_competition,
    build_price_rows,
    consolidate_cross_league_events,
    dedupe_events_for_interest,
    feature_vector,
    load_trainable_rows,
    target_pct,
    week_bucket_dummies,
)

SEED = 20260910
TEST_FRACTION = 0.2
K = 10
OUT_PATH = Path(__file__).parent / "eval_results.json"


def _event_key(r: dict) -> tuple:
    """Keyed by source_league_id too - ESPN player ids are global across
    every league on the platform, so two different leagues' bids on the
    same real player in the same week are separate events, not one (see
    engine.faab_estimate's matching fix)."""
    return (r.get("source_league_id"), r["season"], r["week"], r["add_player_id"])


def _event_signal_class(group_rows: list[dict]) -> str:
    """A real WAIVER auction can produce several rows, one per bidding team
    (see tools/faab_history/pull_o_league_bids.py) - collapse them to a
    single stratification label so we bucket the whole event once. Same
    won > outbid > other_failure > no_bid priority as
    engine.faab_estimate.dedupe_events_for_interest."""
    signals = {r["signal"] for r in group_rows}
    for label in ("won", "outbid", "other_failure"):
        if label in signals:
            return label
    return "no_bid"


def stratified_split(
    rows: list[dict], seed: int, test_fraction: float, holdout_league_id: int | None = None
) -> tuple[list[dict], list[dict]]:
    """Splits by EVENT (source_league_id, season, week, add_player_id), not
    by row. A single real-world auction can produce several rows - one per
    bidding team - and every one of those rows shares an identical feature
    vector (features describe the player, not the bidder). Splitting by row
    let a holdout query's own auction siblings sit in the train comp pool at
    distance 0.0 - not a "similar historical situation" but the literal
    other half of the same event (verified concretely on 2023 wk7 Zach
    Evans and 2025 wk9 Chris Boswell). Grouping by event closes that leak;
    stratifying by (position, event signal class) still keeps position/
    outcome mix balanced between train and test.

    holdout_league_id, when given, restricts the TEST holdout to that one
    league's events only - every other league's events go straight into
    train regardless of test_fraction. This is what makes a leave-one-
    league-out-style evaluation possible on a pooled multi-league table
    (tools/faab_history/combined-training-table.json): "if I train on
    everyone (other public leagues, fully, plus 80% of The O League's own
    events), how well does that predict the 20% of The O League's OWN bids
    I held out?" - never testing against another league's own idiosyncratic
    market. None (the default) preserves the original behavior exactly -
    every league's events are equally eligible for the holdout."""
    events: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        events[_event_key(r)].append(r)

    strata: dict[tuple, list[tuple]] = defaultdict(list)
    forced_train_keys: list[tuple] = []
    for key, group_rows in events.items():
        if holdout_league_id is not None and group_rows[0]["source_league_id"] != holdout_league_id:
            forced_train_keys.append(key)
            continue
        strata[(group_rows[0]["position"], _event_signal_class(group_rows))].append(key)

    rng = random.Random(seed)
    train, test = [], []
    for key in forced_train_keys:
        train.extend(events[key])
    for event_keys in strata.values():
        shuffled = event_keys[:]
        rng.shuffle(shuffled)
        cut = max(1, round(len(shuffled) * test_fraction)) if len(shuffled) > 1 else 0
        test_keys = set(shuffled[:cut])
        for key in shuffled:
            (test if key in test_keys else train).extend(events[key])
    return train, test


def build_position_matrix(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(standardized feature matrix, mean, std) for one position's pool rows,
    in the same row order as `rows`."""
    raw = np.array([[feature_vector(r)[f] for f in FEATURE_NAMES] for r in rows])
    mean = raw.mean(axis=0)
    std = raw.std(axis=0)
    std[std == 0] = 1.0
    return (raw - mean) / std, mean, std


def _design_matrix(rows: list[dict]) -> np.ndarray:
    X_rows = []
    for r in rows:
        fv = feature_vector(r)
        pos_dummies = [1.0 if r["position"] == p else 0.0 for p in POSITIONS[1:]]
        X_rows.append([1.0] + [fv[f] for f in FEATURE_NAMES] + pos_dummies)
    return np.array(X_rows)


def fit_price_regression_np(price_rows: list[dict]) -> np.ndarray:
    X = _design_matrix(price_rows)
    y = np.array([math.log1p(target_pct(r)) for r in price_rows])
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coefs


def fit_interest_logistic_np(interest_rows: list[dict], max_iter: int = 25, l2: float = 1.0) -> tuple[np.ndarray, dict[str, tuple[float, float]]]:
    """Same standardized + L2-ridge IRLS fit as
    engine.faab_estimate.fit_interest_regression (see that docstring for
    why: plain unpenalized Newton-Raphson on raw-scale features diverged
    under this pool's ~9% positive rate) - kept as a local numpy-only copy
    since this module evaluates variants the production fit function
    doesn't need to know about. Returns (coefs in standardized-feature
    units, the feature stats needed to standardize a query the same way)."""
    means = np.array([np.mean([feature_vector(r)[f] for r in interest_rows]) for f in FEATURE_NAMES])
    stds = np.array([np.std([feature_vector(r)[f] for r in interest_rows]) or 1.0 for f in FEATURE_NAMES])
    feat_stats = {f: (means[i], stds[i]) for i, f in enumerate(FEATURE_NAMES)}

    def std_row(r):
        fv = feature_vector(r)
        std_feats = [(fv[f] - feat_stats[f][0]) / feat_stats[f][1] for f in FEATURE_NAMES]
        pos_dummies = [1.0 if r["position"] == p else 0.0 for p in POSITIONS[1:]]
        return [1.0] + std_feats + week_bucket_dummies(r["week"]) + pos_dummies

    X = np.array([std_row(r) for r in interest_rows])
    y = np.array([0.0 if r["signal"] == "no_bid" else 1.0 for r in interest_rows])
    n, d = X.shape
    beta = np.zeros(d)
    penalty = l2 * np.eye(d)
    penalty[0, 0] = 0.0
    for _ in range(max_iter):
        z = np.clip(X @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        w = np.clip(p * (1.0 - p), 1e-6, None)
        hessian = (X.T * w) @ X + penalty
        gradient = X.T @ (y - p) - penalty @ beta
        try:
            delta = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            delta = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        beta = beta + delta
        if np.max(np.abs(delta)) < 1e-8:
            break
    return beta, feat_stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", type=Path, default=TRAINING_TABLE_PATH, help="which training table to evaluate against - point at combined-training-table.json to include the other public leagues")
    parser.add_argument(
        "--holdout-league-id", type=int, default=None,
        help="only this league's events go into the test holdout; every other league's events are folded into training unconditionally. "
        "Use with --table combined-training-table.json --holdout-league-id 355398 to test whether pooling other public leagues' bids "
        "improves prediction of The O League's own held-out bids, vs a plain run against o-league-training-table.json alone.",
    )
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    all_rows = json.loads(args.table.read_text())
    annotate_position_competition(all_rows)
    trainable = load_trainable_rows(all_rows)
    train, test = stratified_split(trainable, SEED, TEST_FRACTION, holdout_league_id=args.holdout_league_id)
    print(f"train pool: {len(train)}  holdout: {len(test)}")

    # Interest stage (P(anyone bids at all)) trains only from leagues that
    # actually contributed real no_bid negatives (i.e. pull_public_league_
    # rosters.py has been run for them - see build_training_table.py's
    # module docstring), computed dynamically rather than hardcoded to The
    # O League: any OTHER league without a roster pull yet would contribute
    # real bid activity with no counterbalancing no-interest examples at
    # all, teaching the model "someone always bids".
    interest_eligible_leagues = {r["source_league_id"] for r in all_rows if r["signal"] == "no_bid"}
    # consolidate_cross_league_events collapses the SAME real event across
    # multiple pooled leagues into one row - without it, a popular real-
    # world trigger visible in N leagues would occupy up to N k-NN neighbor
    # slots / N correlated regression rows purely because it was pooled
    # from N leagues, not because it's N times more relevant - see that
    # function's docstring in engine/faab_estimate.py.
    # week == 1 excluded from interest training - see engine.faab_estimate.
    # FaabModel.__init__'s identical exclusion for why (zero real no_bid
    # rows for week 1 in the whole pooled dataset).
    interest_train = consolidate_cross_league_events(
        dedupe_events_for_interest([r for r in train if r["source_league_id"] in interest_eligible_leagues and r["week"] != 1]), is_price=False
    )
    price_train = consolidate_cross_league_events(build_price_rows(train), is_price=True)
    print(f"interest train (deduped+consolidated, leagues {interest_eligible_leagues}): {len(interest_train)}  price train (won + synthetic other_failure, consolidated): {len(price_train)}")

    price_coefs = fit_price_regression_np(price_train)
    interest_coefs, interest_stats = fit_interest_logistic_np(interest_train)

    def pct_rank(vals, v):
        return sum(1 for x in vals if x <= v) / len(vals)

    def build_pool(rows: list[dict], is_price_pool: bool):
        """Per-position matrices/targets for either pool - is_price_pool
        picks the vote/target array (interest: 1.0 unless no_bid; price:
        target_pct)."""
        by_pos: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_pos[r["position"]].append(r)
        matrix, mean, std, targets, points_vals, snap_vals, combined = {}, {}, {}, {}, {}, {}, {}
        for pos, prows in by_pos.items():
            mat, m, s = build_position_matrix(prows)
            matrix[pos], mean[pos], std[pos] = mat, m, s
            if is_price_pool:
                targets[pos] = np.array([target_pct(r) for r in prows])
            else:
                targets[pos] = np.array([0.0 if r["signal"] == "no_bid" else 1.0 for r in prows])
            pv = [feature_vector(pr)["prior_week_actual_points"] for pr in prows]
            sv = [feature_vector(pr)["snap_pct_prior_week"] for pr in prows]
            points_vals[pos], snap_vals[pos] = pv, sv
            combined[pos] = np.array([(pct_rank(pv, x) + pct_rank(sv, y)) / 2.0 for x, y in zip(pv, sv)])
        return {"matrix": matrix, "mean": mean, "std": std, "targets": targets, "points_vals": points_vals, "snap_vals": snap_vals, "combined": combined}

    interest_pool = build_pool(interest_train, is_price_pool=False)
    price_pool = build_pool(price_train, is_price_pool=True)

    def knn_predict(pool: dict, pos: str, q_fv: dict):
        mat, mean, std, targets = pool["matrix"].get(pos), pool["mean"].get(pos), pool["std"].get(pos), pool["targets"].get(pos)
        if mat is None or len(mat) == 0:
            return 0.0
        qv = (np.array([q_fv[f] for f in FEATURE_NAMES]) - mean) / std
        dists = np.linalg.norm(mat - qv, axis=1)
        nearest_idx = np.argsort(dists)[:K]
        weights = 1.0 / (dists[nearest_idx] + 0.05)
        return float(np.sum(weights * targets[nearest_idx]) / np.sum(weights))

    def bucket_predict(pool: dict, pos: str, q_fv: dict):
        targets, points_vals, snap_vals, combined = pool["targets"].get(pos), pool["points_vals"].get(pos), pool["snap_vals"].get(pos), pool["combined"].get(pos)
        if targets is None or len(targets) == 0:
            return 0.0
        q_combined = (pct_rank(points_vals, q_fv["prior_week_actual_points"]) + pct_rank(snap_vals, q_fv["snap_pct_prior_week"])) / 2.0
        bucket_mask = np.abs(combined - q_combined) <= 0.05
        return float(targets[bucket_mask].mean()) if bucket_mask.any() else float(targets.mean())

    def regression_predict(coefs: np.ndarray, q_fv: dict, pos: str):
        pos_dummies = [1.0 if pos == p else 0.0 for p in POSITIONS[1:]]
        x = np.array([1.0] + [q_fv[f] for f in FEATURE_NAMES] + pos_dummies)
        return float(np.dot(x, coefs))

    def interest_regression_predict(coefs: np.ndarray, stats: dict, q_fv: dict, pos: str):
        std_feats = [(q_fv[f] - stats[f][0]) / stats[f][1] for f in FEATURE_NAMES]
        pos_dummies = [1.0 if pos == p else 0.0 for p in POSITIONS[1:]]
        x = np.array([1.0] + std_feats + week_bucket_dummies(q_fv["week"]) + pos_dummies)
        return float(np.dot(x, coefs))

    results = []
    for r in test:
        pos = r["position"]
        q_fv = feature_vector(r)
        actual = target_pct(r)

        comp_p_bid = knn_predict(interest_pool, pos, q_fv)
        comp_price = knn_predict(price_pool, pos, q_fv)
        simple_p_bid = bucket_predict(interest_pool, pos, q_fv)
        simple_price = bucket_predict(price_pool, pos, q_fv)
        reg_z = np.clip(interest_regression_predict(interest_coefs, interest_stats, q_fv, pos), -30, 30)
        reg_p_bid = 1.0 / (1.0 + math.exp(-reg_z))
        reg_price = max(math.expm1(regression_predict(price_coefs, q_fv, pos)), 0.0)

        # All *_pct/*_conditional_price values are fractions of that
        # (league, season)'s effective starting budget - NOT dollars scaled
        # to a fictional $1000 (the old convention) - see target_pct.
        results.append(
            {
                "position": pos, "season": r["season"], "week": r["week"], "signal": r["signal"],
                "add_player_id": r["add_player_id"], "source_league_id": r.get("source_league_id"),
                "name": r["add_player_name"],
                "actual_pct": actual,
                "comp_pct": comp_p_bid * comp_price,
                "simple_pct": simple_p_bid * simple_price,
                "regression_pct": reg_p_bid * reg_price,
                "comp_bid_probability": comp_p_bid, "comp_conditional_price": comp_price,
                "simple_bid_probability": simple_p_bid, "simple_conditional_price": simple_price,
                "regression_bid_probability": reg_p_bid, "regression_conditional_price": reg_price,
                "prior_week_actual_points": q_fv["prior_week_actual_points"],
                "snap_pct_prior_week": q_fv["snap_pct_prior_week"],
            }
        )

    args.out.write_text(json.dumps(results, indent=2))
    print(f"wrote {len(results)} holdout evaluations to {args.out}")


if __name__ == "__main__":
    main()
