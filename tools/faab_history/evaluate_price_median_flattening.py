"""Held-out backtest: does flattening the K price comps to their own real
per-league winning bids before taking a median actually beat the current
design (one median across the K comps' own already-cross-league-
consolidated values)?

See the conversation this was built from. comp_based_estimate's
conditional_price_median currently takes one weighted median across the K
nearest price comps, where each comp is already ONE representative value
per real event (consolidate_cross_league_events collapsed however many
leagues won that event down to their own cross-league median before k-NN
ever saw it - see that function). The alternative (conditional_price_
median_flattened, computed unconditionally alongside the current field -
see comp_based_estimate) expands each of the SAME K comps back out to every
one of its own real per-league winning prices (reusing price_confidence_
samples' "one event's weight split evenly across its own real wins"
expansion) and takes ONE flat weighted median over that pooled set instead.

Whether this changes anything in practice is an empirical question, not a
theoretical one - most historical events are won in only one league at all,
in which case there's nothing to flatten and the two methods are
IDENTICAL for that comp. This script measures, on real held-out "won" rows
(the only signal class with a real, uncensored dollar amount to score
price accuracy against - see engine/faab_estimate.py's module docstring on
why outbid rows are never a price target), whether the two methods'
predictions actually diverge, and if so, which one is closer to the truth.

Reuses evaluate_model.py's own event-grouped, position/signal-stratified
holdout split (stratified_split) so a real auction's own siblings never
leak into the training comp pool at distance 0 - but, unlike that script's
vectorized numpy backtest, calls the REAL production comp_based_estimate
per held-out row (not a simplified numpy re-implementation) since the two
methods being compared here only differ in a few lines deep inside that
function. Slower per-row than evaluate_model.py's vectorized approach, but
tractable: only "won" rows are scored (~10% of the full holdout - see the
module docstring's own P(bid) numbers), not the full holdout set.

Run from the repo root:
    python -m tools.faab_history.evaluate_price_median_flattening
    python -m tools.faab_history.evaluate_price_median_flattening --table tools/faab_history/combined-training-table.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

from engine.faab_estimate import (
    TRAINING_TABLE_PATH,
    _feature_stats,
    _position_distance_cutoffs,
    add_synthetic_price_wins,
    annotate_position_competition,
    build_event_won_rows_index,
    build_price_rows,
    comp_based_estimate,
    consolidate_cross_league_events,
    dedupe_events_for_interest,
    load_trainable_rows,
    target_pct,
)
from tools.faab_history.evaluate_model import SEED, TEST_FRACTION, stratified_split

# A comp whose event was won in only one league expands to exactly one
# "flattened" sample - identical to its own single consolidated value, so
# the two median methods can only possibly disagree when at least one of
# the K comps is a real cross-league event. Tracked separately from the
# headline MAE numbers so a reader can tell "these rarely differ" apart
# from "these differ but it doesn't matter for accuracy."
DISAGREEMENT_THRESHOLD_PCT_OF_BUDGET = 0.01  # 1 percentage point of budget


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--table", type=Path, default=TRAINING_TABLE_PATH)
    parser.add_argument("--holdout-league-id", type=int, default=None)
    parser.add_argument(
        "--limit", type=int, default=None,
        help="score at most this many 'won' holdout rows (a random SEED-reproducible sample, not just the first N) - "
        "each row calls the real, non-vectorized comp_based_estimate against the full train pool, so a pooled multi-"
        "league --table can have far more 'won' holdout rows than is practical to score in full.",
    )
    args = parser.parse_args()

    all_rows = json.loads(args.table.read_text())
    annotate_position_competition(all_rows)
    trainable = add_synthetic_price_wins(load_trainable_rows(all_rows))
    train, test = stratified_split(trainable, SEED, TEST_FRACTION, args.holdout_league_id)

    event_won_rows_index = build_event_won_rows_index(train)
    interest_rows = consolidate_cross_league_events(dedupe_events_for_interest([r for r in train if r["week"] != 1]), is_price=False)
    price_rows = consolidate_cross_league_events(build_price_rows(train), is_price=True)
    distance_stats = _feature_stats(interest_rows)
    interest_max_distance = _position_distance_cutoffs(interest_rows, distance_stats)
    price_max_distance = _position_distance_cutoffs(price_rows, distance_stats)

    won_test_rows = [r for r in test if r["signal"] == "won"]
    total_won = len(won_test_rows)
    if args.limit is not None and total_won > args.limit:
        won_test_rows = random.Random(SEED).sample(won_test_rows, args.limit)
    print(
        f"{len(train)} train rows, {len(test)} test rows "
        f"({len(won_test_rows)}/{total_won} real 'won' rows to score price against)"
    )

    abs_err_mean, abs_err_median, abs_err_flattened = [], [], []
    disagreements = []  # |median - flattened| for every scored row, not just the ones over threshold
    for row in won_test_rows:
        actual = target_pct(row)
        comp = comp_based_estimate(
            row, interest_rows, price_rows, event_won_rows_index=event_won_rows_index,
            interest_max_distance=interest_max_distance.get(row["position"]),
            price_max_distance=price_max_distance.get(row["position"]),
        )
        abs_err_mean.append(abs(comp["conditional_price_mean"] - actual))
        abs_err_median.append(abs(comp["conditional_price_median"] - actual))
        abs_err_flattened.append(abs(comp["conditional_price_median_flattened"] - actual))
        disagreements.append(abs(comp["conditional_price_median"] - comp["conditional_price_median_flattened"]))

    n = len(won_test_rows)
    n_disagree = sum(1 for d in disagreements if d > DISAGREEMENT_THRESHOLD_PCT_OF_BUDGET)
    print(f"\nMAE (mean, for reference):       {statistics.mean(abs_err_mean):.5f}")
    print(f"MAE (median, current):            {statistics.mean(abs_err_median):.5f}")
    print(f"MAE (median, flattened):          {statistics.mean(abs_err_flattened):.5f}")
    print(
        f"\nrows where |median - flattened| > {DISAGREEMENT_THRESHOLD_PCT_OF_BUDGET*100:.0f}pp of budget: "
        f"{n_disagree}/{n} ({100*n_disagree/n:.1f}%)" if n else "no 'won' rows in the holdout"
    )
    if disagreements:
        print(f"median |median - flattened| across ALL scored rows: {statistics.median(disagreements):.5f}")
        print(f"max |median - flattened|: {max(disagreements):.5f}")


if __name__ == "__main__":
    main()
