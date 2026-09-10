"""FAAB bid estimator for currently-unrostered (ESPN "WAIVERS" status) free
agents. Trained on tools/faab_history/o-league-training-table.json - see
that directory's scripts (pull_o_league_bids.py, pull_weekly_rosters.py,
build_training_table.py) for how that frozen 2019-2025 historical dataset
was built and cleaned.

Three deliberately-explainable methods (this is meant to be shown
transparently in the Rankings player modal - comps, inputs, and how each
number was reached, not a black box):
  1. comp_based_estimate()    - the K most similar historical situations
     (same position, nearest by standardized feature distance), weighted-
     averaged. The comps themselves ARE the explanation.
  2. simple_baseline_estimate() - the historical average for OTHER players
     at this position in the SAME usage decile (recent points + snap share
     percentile) - one sentence to explain.
  3. regression_estimate()    - an actual fitted (OLS, log1p-transformed)
     linear model with a plain coefficient table.

All three predict a bid as a PERCENTAGE of that season's average team
spend (target_pct), not a raw dollar amount scaled by one flat historical
constant - normalizing per-season absorbs year-to-year inflation/deflation
in real bidding behavior. Multiplying a prediction by FICTIONAL_BUDGET
($1000) gives the fictional-budget-equivalent dollar estimate directly.

num_competing_bidders is deliberately NOT a feature, despite being the
strongest predictor in an early pass - it's an OUTCOME of a bidding round,
unknowable before one happens, so training on it would leak information a
live, prospective estimate can never actually have.

"outbid" (losing-bid) rows ARE in the price target/comp pool (target_pct
correctly gives them $0, same as no_bid - a losing bid never draws down a
budget). Tried excluding them on the theory that a winning $8 bid and a
losing $6 bid for one event looked like two interchangeable price
observations; a backtest DISPROVED that fix - won-bid under-prediction got
WORSE (-$39.59 -> -$58.54), not better. What actually happened: outbid
rows are real, densely-clustered $0 neighbors that sit close (in feature
space) to genuinely contested, high-value situations - removing them
thinned that local density, so the k-NN search for a "hot" query pulled in
more distant, unrelated no_bid rows instead of the nearby comps that
should anchor it. Keep them in the pool. They're ALSO surfaced separately
as competing_bids context on each comp (see build_competing_bids_index) -
"how contested was this specific historical situation," independent of
whatever the pool decision is.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

TRAINING_TABLE_PATH = Path(__file__).resolve().parent.parent / "tools" / "faab_history" / "o-league-training-table.json"

FICTIONAL_BUDGET = 1000.0
TRAINABLE_SIGNALS = {"won", "outbid", "no_bid"}  # excludes "other_failure" - roster/logistics noise, not market signal (see module docstring for why outbid stays IN the pool)
POSITIONS = ["QB", "RB", "WR", "TE", "K"]  # QB is the OLS reference category (no dummy)
FEATURE_NAMES = [
    "week",
    "prior_week_actual_points",
    "prior_week_had_stat_row",
    "own_injury_flag",
    "teammate_position_injury_flag",
    "snap_pct_prior_week",
]

# A teammate only counts toward teammate_position_injury_flag if they had at
# least this much offensive snap share recently - filters out third-
# stringers/camp bodies who show up on every injury report but were never
# actually playing (see tools/faab_history/build_training_table.py, where
# this same constant/reasoning was first established for the historical
# data - kept in sync here for the live pipeline).
FANTASY_RELEVANT_SNAP_PCT = 0.15

# A live WAIVERS candidate only gets run through the model if their own
# recent usage/production clears one of these bars - the SAME thresholds
# tools/faab_history/build_training_table.py used to decide which players
# were even eligible to become a "no_bid" training row. Below both, there's
# no comparable population in the training data at all (a truly irrelevant
# player was never included as a no_bid example either - see that
# script's NO_BID_MIN_* constants), so the model would just extrapolate
# from whatever comps happen to be nearest, silently inflating an estimate
# for someone who should just be $0.
NO_BID_MIN_SNAP_PCT = 0.15
NO_BID_MIN_PRIOR_POINTS = 5.0

# ESPN's own injury_status vocabulary (ACTIVE/QUESTIONABLE/DOUBTFUL/OUT/
# INJURY_RESERVE/SUSPENSION/DAY_TO_DAY - see docs/js/colors.js's
# INJURY_BADGE). Only near-certain absences count toward a TEAMMATE's flag
# (mirrors the historical data's Out/Doubtful-only filter) - a player's OWN
# own_injury_flag feature is more lenient (any non-ACTIVE status, including
# Questionable, since that's what a manager actually sees when bidding).
TEAMMATE_INJURY_FLAG_STATUSES = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}


def build_gsis_to_pfr_map(playerids_df) -> dict[str, str]:
    """gsis_id -> pfr_id, straight from the raw ESPN<->nflverse crosswalk
    frame (ingest.nfl_data.playerids()) - ingest.ids.IdMap's records don't
    carry pfr_id through, see tools/faab_history/build_training_table.py."""
    result = {}
    for row in playerids_df.iter_rows(named=True):
        if row.get("gsis_id") and row.get("pfr_id"):
            result[row["gsis_id"]] = row["pfr_id"]
    return result


def recent_snap_pct(pfr_id: str | None, week: int, snaps_df) -> float | None:
    """This player's offense_pct in the most recent of week-1/week-2 that
    has a row."""
    if not pfr_id:
        return None
    hist = snaps_df.filter(pl.col("pfr_player_id") == pfr_id)
    for wk in (week - 1, week - 2):
        if wk < 1:
            continue
        row = hist.filter(pl.col("week") == wk)
        if row.height:
            return row.to_dicts()[0].get("offense_pct")
    return None


def build_competing_bids_index(all_rows: list[dict]) -> dict[tuple, list[dict]]:
    """(season, week, add_player_id) -> that auction's losing bids, sorted
    highest-first. Built from the FULL row set (all_rows), not the trainable
    subset - "outbid" rows are excluded from the price target/comp pool (see
    module docstring) but kept here purely as "how contested was this
    specific historical comp" context to attach to a won/no_bid comp."""
    index: dict[tuple, list[dict]] = defaultdict(list)
    for r in all_rows:
        if r["signal"] != "outbid":
            continue
        key = (r["season"], r["week"], r["add_player_id"])
        index[key].append({"team_id": r["team_id"], "bid_dollars": r["bid_amount_dollars"]})
    for bids in index.values():
        bids.sort(key=lambda b: -b["bid_dollars"])
    return dict(index)


def load_trainable_rows(path: Path = TRAINING_TABLE_PATH) -> list[dict]:
    rows = json.loads(path.read_text())
    return [r for r in rows if r["signal"] in TRAINABLE_SIGNALS and r["position"] in POSITIONS]


def feature_vector(r: dict) -> dict[str, float]:
    return {
        "week": r["week"],
        "prior_week_actual_points": r["prior_week_actual_points"] if r.get("prior_week_actual_points") is not None else 0.0,
        "prior_week_had_stat_row": 1.0 if r.get("prior_week_had_stat_row") else 0.0,
        "own_injury_flag": 1.0 if r.get("own_injury_status") else 0.0,
        "teammate_position_injury_flag": 1.0 if r.get("teammate_position_injury_flag") else 0.0,
        "snap_pct_prior_week": r["snap_pct_prior_week"] if r.get("snap_pct_prior_week") is not None else 0.0,
    }


def compute_season_avg_team_spend(all_rows: list[dict]) -> dict[int, float]:
    """{season: average team spend that season} - won bids only
    (effective_cost_dollars is 0 for everything else by construction),
    divided by 12 teams."""
    totals: dict[int, float] = {}
    for r in all_rows:
        totals[r["season"]] = totals.get(r["season"], 0.0) + r["effective_cost_dollars"]
    return {season: total / 12.0 for season, total in totals.items()}


def target_pct(r: dict, season_avg_spend: dict[int, float]) -> float:
    """This bid as a fraction of THAT SEASON's average team spend - see
    module docstring for why not a fraction of the bidding team's own
    total, and not a raw dollar amount.

    Uses effective_cost_dollars, NOT bid_amount_dollars - they only differ
    for a won "FREEAGENT" (uncontested) pickup, where ESPN records the raw
    bid as $0 (there was no auction) but the league's real house rule still
    charges the flat $2 fee (see FREEAGENT_FLAT_COST_DOLLARS in
    pull_o_league_bids.py). Reading bid_amount_dollars here would silently
    train all 521 of those real, successful pickups as worth exactly
    nothing - indistinguishable from a true no_bid row - which is wrong:
    "won uncontested for the minimum fee" is real information, just weaker
    than "someone had to outbid a rival for this"."""
    avg = season_avg_spend.get(r["season"])
    return (r["effective_cost_dollars"] / avg) if avg else 0.0


# ---------------------------------------------------------------------------
# 1. Comp-based nearest-neighbor
# ---------------------------------------------------------------------------
def _feature_stats(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """mean/std per feature, POOLED across positions - only used to scale
    distances so no one feature dominates just from bigger raw units."""
    stats = {}
    for f in FEATURE_NAMES:
        vals = np.array([feature_vector(r)[f] for r in rows])
        stats[f] = (vals.mean(), vals.std() or 1.0)
    return stats


def comp_based_estimate(
    query: dict,
    rows: list[dict],
    season_avg_spend: dict[int, float],
    competing_bids_index: dict[tuple, list[dict]] | None = None,
    k: int = 10,
) -> tuple[float, list[dict]]:
    stats = _feature_stats(rows)
    same_pos = [r for r in rows if r["position"] == query["position"]]

    def vec(r):
        fv = feature_vector(r)
        return np.array([(fv[f] - stats[f][0]) / stats[f][1] for f in FEATURE_NAMES])

    qv = vec(query)
    scored = sorted(same_pos, key=lambda r: np.linalg.norm(vec(r) - qv))[:k]

    weights = [1.0 / (np.linalg.norm(vec(r) - qv) + 0.05) for r in scored]
    total_w = sum(weights)
    estimate_pct = sum(w * target_pct(r, season_avg_spend) for w, r in zip(weights, scored)) / total_w

    comps = [
        {
            "season": r["season"], "week": r["week"], "name": r["add_player_name"],
            "signal": r["signal"], "bid_dollars": r["bid_amount_dollars"] if r["signal"] != "no_bid" else 0.0,
            # Same season-normalized x $1000 transform behind the headline
            # estimate, per comp - this is what a "distribution" actually
            # means here: not a modeled confidence interval, but the real
            # spread of what the K nearest historical situations actually
            # went for, put in the same fictional-budget units as the point
            # estimate (which is just this array's weighted average).
            "fictional_dollars": target_pct(r, season_avg_spend) * FICTIONAL_BUDGET,
            "prior_week_actual_points": r.get("prior_week_actual_points"),
            "snap_pct_prior_week": r.get("snap_pct_prior_week"),
            # Other real bids on this SAME historical player+week, that lost
            # - context on how contested this comp actually was, not part of
            # the price estimate itself (see module docstring). Excludes the
            # comp's own bid if the comp itself is one of the outbid rows
            # (each team has at most one row per player+week by this point -
            # see build_training_table.py's contingent-bid collapse - so
            # matching on team_id can't hide a genuinely different bid).
            "competing_bids": [
                b for b in (competing_bids_index or {}).get((r["season"], r["week"], r["add_player_id"]), [])
                if b["team_id"] != r["team_id"]
            ],
        }
        for r in scored
    ]
    return estimate_pct * FICTIONAL_BUDGET, comps


# ---------------------------------------------------------------------------
# 2. Simple baseline: same-position, same-usage-decile historical average
# ---------------------------------------------------------------------------
def _combined_usage_percentile(r: dict, points_vals: list[float], snap_vals: list[float]) -> float:
    fv = feature_vector(r)

    def percentile_rank(values: list[float], value: float) -> float:
        return sum(1 for v in values if v <= value) / len(values)

    return (percentile_rank(points_vals, fv["prior_week_actual_points"]) + percentile_rank(snap_vals, fv["snap_pct_prior_week"])) / 2.0


def simple_baseline_estimate(query: dict, rows: list[dict], season_avg_spend: dict[int, float]) -> float:
    """Deliberately NOT position_avg x a bounded multiplier - with ~90% of
    rows being $0 no-bids, a multiplier capped anywhere near 1x-3x can never
    reach what the top usage tier actually commands. Bucketing by decile
    lets the top bucket's own (much higher) average speak for itself."""
    same_pos = [r for r in rows if r["position"] == query["position"]]
    points_vals = [feature_vector(r)["prior_week_actual_points"] for r in same_pos]
    snap_vals = [feature_vector(r)["snap_pct_prior_week"] for r in same_pos]

    query_pct = _combined_usage_percentile(query, points_vals, snap_vals)
    bucket = [r for r in same_pos if abs(_combined_usage_percentile(r, points_vals, snap_vals) - query_pct) <= 0.05]
    if not bucket:
        bucket = same_pos

    return (sum(target_pct(r, season_avg_spend) for r in bucket) / len(bucket)) * FICTIONAL_BUDGET


# ---------------------------------------------------------------------------
# 3. Regression (OLS on log1p(target), plain numpy - no new dependency)
# ---------------------------------------------------------------------------
def fit_regression(rows: list[dict], season_avg_spend: dict[int, float]) -> dict[str, float]:
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


def regression_estimate(query: dict, coefs: dict[str, float]) -> float:
    fv = feature_vector(query)
    pos_dummies = [1.0 if query["position"] == p else 0.0 for p in POSITIONS[1:]]
    x = [1.0] + [fv[f] for f in FEATURE_NAMES] + pos_dummies
    log_pred = sum(c * v for c, v in zip(coefs.values(), x))
    return max(math.expm1(log_pred), 0.0) * FICTIONAL_BUDGET


# ---------------------------------------------------------------------------
class FaabModel:
    """Fit once per pipeline run, reused for every candidate player."""

    def __init__(self, training_table_path: Path = TRAINING_TABLE_PATH):
        self.rows = load_trainable_rows(training_table_path)
        all_rows = json.loads(training_table_path.read_text())
        self.season_avg_spend = compute_season_avg_team_spend(all_rows)
        self.competing_bids_index = build_competing_bids_index(all_rows)
        self.coefs = fit_regression(self.rows, self.season_avg_spend)

    def estimate(self, query: dict) -> dict:
        comp_est, comps = comp_based_estimate(query, self.rows, self.season_avg_spend, self.competing_bids_index)
        comp_values = sorted(c["fictional_dollars"] for c in comps)
        return {
            "comp_based": comp_est,
            "simple_baseline": simple_baseline_estimate(query, self.rows, self.season_avg_spend),
            "regression": regression_estimate(query, self.coefs),
            "comps": comps,
            # The spread of the K comps themselves, in the same fictional-$
            # units as comp_based (which is just their weighted average) -
            # not a modeled confidence interval, the actual range of what
            # comparable historical situations went for.
            "distribution": {
                "min": comp_values[0], "max": comp_values[-1],
                "p25": float(np.percentile(comp_values, 25)),
                "median": float(np.percentile(comp_values, 50)),
                "p75": float(np.percentile(comp_values, 75)),
            } if comp_values else None,
            "inputs": feature_vector(query) | {"position": query["position"]},
        }
