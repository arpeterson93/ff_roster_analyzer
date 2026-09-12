"""FAAB bid estimator for currently-unrostered (ESPN "WAIVERS" status) free
agents. Trained on tools/faab_history/o-league-training-table.json - see
that directory's scripts (pull_o_league_bids.py, pull_weekly_rosters.py,
build_training_table.py) for how that frozen 2019-2025 historical dataset
was built and cleaned.

TWO-STAGE ("hurdle") design. A waiver outcome is really two different
questions bolted together - "does anyone bid at all" (true ~10% of the
time) and, conditional on yes, "how much does it take to win" - and one
model can't answer both well from one undifferentiated pool of mostly-$0
rows. So every method below is actually a pair:
  - INTEREST stage: P(anyone bids), fit on the full won+outbid+no_bid pool
    (interest_rows) - needs outbid rows as real "yes, this drew a bid"
    examples and no_bid rows as "no" examples, which is the ONLY way any
    of this can learn to say "probably $0" for an uninteresting player.
  - PRICE stage: the dollar amount IF a bid happens, fit on WON rows ONLY
    (price_rows) - real market-clearing prices, nothing else. Deliberately
    excludes outbid rows here (a losing bid's amount is a CENSORED
    observation - we know the true price was higher, by an unknown
    margin, so training on it as if it were the real price systematically
    drags every estimate down) and no_bid rows (irrelevant to "what does
    it cost to win," by definition never won anything).
  - The headline number a caller should show is price-if-contested
    (conditional_price) alone, for each method - NOT P(bid) x price. That
    product is an ex-ante expected cost (it averages in the worlds where
    nobody bids and this costs $0 too) - a different question from "what
    should I bid if I've decided I want this player," which is what
    conditional_price answers directly. Once you're actually bidding you're
    conditioning on a contest existing; a real rival prices off their own
    conditional valuation, not off the odds a contest happens at all.
    bid_probability is real, useful context ("but you may not even need to
    bid") shown alongside conditional_price, never multiplied into it. (This
    file used to compute and expose that product as "estimate"/comp_based/
    simple_baseline/regression - removed; see the conversation this was
    built from for the concrete example - Chris Boswell, 36% interest, a
    real $38.92 conditional price, and a misleading $13.86 blended number -
    that motivated dropping it.)

Backtested three alternatives to this before landing here (all measured
under the SAME event-grouped holdout split - see
tools/faab_history/evaluate_model.py's stratified_split, which groups by
(season, week, add_player_id) rather than by row, closing a leak where one
real auction's sibling bids could land on both sides of a row-level
split):
  - Single pool, outbid target = its own losing bid amount instead of $0:
    worse everywhere (overall MAE $7.33->$8.37, RB $19.69->$23.29) - the
    censoring problem above, confirmed empirically.
  - Drop outbid rows from training entirely, keep no_bid rows, single
    pool: also worse (overall $7.33->$8.01, outbid-row MAE $43->$58) -
    outbid rows are real, densely-clustered near-$0 neighbors that sit
    close (in feature space) to genuinely contested, high-value
    situations; removing them thinned that local density and made the
    single pool's read on "is this contested" noisier.
  - Won-only training pool (no interest stage at all): catastrophic -
    overall MAE $7.33->$53.89, no_bid-row MAE $2.68->$53.97. Every
    training target becomes a positive dollar amount, so the model loses
    any way to output "not worth a bid" - exactly the failure the no_bid
    rows exist to prevent. This is why the interest stage has to see
    no_bid rows even though the price stage never does.

Three deliberately-explainable methods (this is meant to be shown
transparently in the Rankings player modal - comps, inputs, and how each
number was reached, not a black box), each producing its OWN P(bid) and
its OWN conditional price:
  1. comp_based_estimate()    - two k-NN searches, standardized feature
     distance: the K nearest same-position INTEREST comps (won+outbid+
     no_bid) vote on P(bid); the K nearest same-position PRICE comps
     (won only) weighted-average into the conditional price. The comps
     themselves ARE the explanation.
  2. simple_baseline_estimate() - same split, but bucketed by usage decile
     (recent points + snap share percentile) instead of k-NN - one
     sentence to explain each half.
  3. regression_estimate()    - a fitted logistic regression (plain
     coefficient table) for P(bid), x a fitted OLS-on-log1p regression
     (also a plain coefficient table) for conditional price, trained on
     interest_rows and price_rows respectively.

Conditional price is a PERCENTAGE of that (league, season)'s EFFECTIVE
STARTING budget (target_pct) - not a raw dollar amount, and not a fraction
of the bidding team's own remaining budget at the time either (considered
and rejected - dividing by a denominator that trends toward zero late in
the season blows up into noise; see the conversation this was built from).
Budget scarcity as the season progresses is a real, separate signal -
it belongs on bidder_pct_budget_remaining_at_bid / league_avg_pct_budget_
remaining_at_bid as an input feature, not baked into the target.

num_competing_bidders is deliberately NOT a feature, despite being the
strongest predictor in an early pass - it's an OUTCOME of a bidding round,
unknowable before one happens, so training on it would leak information a
live, prospective estimate can never actually have.

Losing bids' own dollar amounts are still real, visible data - just never
a training target (see PRICE stage above). They're surfaced as
competing_bids context on each price comp (see build_competing_bids_index)
- "how contested was this specific historical situation," independent of
what actually set the price.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

TRAINING_TABLE_PATH = Path(__file__).resolve().parent.parent / "tools" / "faab_history" / "o-league-training-table.json"
# The O-League-only table stays FaabModel's own default and evaluate_model.py's
# default --table (deliberately NOT changed here) - that's the established
# "plain run" baseline the pooling-comparison backtests (--table
# combined-training-table.json --holdout-league-id 355398) are measured
# against; silently flipping what "the default" means would quietly break
# that comparison's meaning. engine/pipeline.py (the live site) passes this
# path explicitly instead, now that pooling is confirmed to help (see the
# conversation this was built from) and other leagues on the site have no
# FAAB history of their own to train on regardless.
POOLED_TRAINING_TABLE_PATH = Path(__file__).resolve().parent.parent / "tools" / "faab_history" / "combined-training-table.json"
O_LEAGUE_ID = 355398  # duplicated from tools/faab_history/build_training_table.py's own constant - engine/ doesn't import from tools/, wrong dependency direction
TRAINABLE_SIGNALS = {"won", "outbid", "no_bid", "other_failure"}  # "other_failure" (roster-limit/contingency logistics, e.g. a manager's OTHER simultaneous claim consumed the roster slot - see pull_o_league_bids.py's classify()) is real "someone placed a bid" evidence for the INTEREST stage even though, like outbid, it never carries price signal - see dedupe_events_for_interest and module docstring
POSITIONS = ["QB", "RB", "WR", "TE", "K"]  # QB is the OLS reference category (no dummy)
FEATURE_NAMES = [
    "week",
    "prior_week_actual_points",
    "prior_week_had_stat_row",
    "own_injury_flag",
    "teammate_position_injury_flag",
    "snap_pct_prior_week",
    "trailing_2_3_avg_points",
    "season_avg_points",
    "weekly_rank",
    "had_weekly_rank",
    "ros_rank",
    "had_ros_rank",
    "best_position_competitor_ros_rank",
    "had_position_competitor_rank",
]

# Missing-rank sentinel (see feature_vector) - a real FantasyPros ECR rank
# NUMBER (see tools/faab_history/build_training_table.py's forward_rank_
# features for why NOT a percentile: the ranked population depth varies up
# to ~3x across real snapshots). 150 reads as "replacement-level/deep
# bench" without the model ever training on synthetic 0 or negative values
# it would need to unlearn - paired with had_weekly_rank/had_ros_rank so
# the model can tell "genuinely replacement-level" from "we don't know"
# (before 2020, or a player FantasyPros never ranked that week).
MISSING_RANK_SENTINEL = 150.0

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
    """(source_league_id, season, week, add_player_id) -> that auction's
    losing bids, sorted highest-first. Built from the FULL row set
    (all_rows), not the trainable subset - "outbid" rows are excluded from
    the price target/comp pool (see module docstring) but kept here purely
    as "how contested was this specific historical comp" context to attach
    to a won/no_bid comp.

    Keyed by source_league_id too, not just (season, week, add_player_id) -
    ESPN player ids are global across every league on the platform, so two
    different leagues bidding on the same real player in the same week are
    not "competing" with each other and must not be merged into one
    auction."""
    index: dict[tuple, list[dict]] = defaultdict(list)
    for r in all_rows:
        if r["signal"] != "outbid":
            continue
        key = (r.get("source_league_id"), r["season"], r["week"], r["add_player_id"])
        index[key].append({"team_id": r["team_id"], "bid_dollars": r["bid_amount_dollars"]})
    for bids in index.values():
        bids.sort(key=lambda b: -b["bid_dollars"])
    return dict(index)


def build_event_won_rows_index(trainable_rows: list[dict]) -> dict[tuple, list[dict]]:
    """(season, week, add_player_id) -> every league's own WON row for that
    SAME real-world event - the cross-league analog of
    build_competing_bids_index (which is scoped to one league's own losing
    bids). Built from trainable_rows (already excludes FREEAGENT
    uncontested pickups and known-bad fat-fingered bids - see
    load_trainable_rows) so this never needs to duplicate those exclusions.

    Used by price_confidence_samples to expand a k-NN neighbor that
    consolidate_cross_league_events collapsed into one representative row
    back out into every individual league's own real outcome - e.g. a real
    RB injury that triggered a waiver run in 40 different pooled leagues
    is ONE k-NN neighbor (so it can't hog 40 neighbor slots or skew the
    point estimate/regression), but for the confidence distribution
    specifically, that neighbor's "how much did it actually take to win"
    answer should reflect all 40 real outcomes, not the single
    consolidated_target_pct average consolidate_cross_league_events
    computed for the point estimate. Not keyed by source_league_id (unlike
    build_competing_bids_index) - the whole point is gathering EVERY
    league's own row for the same real event."""
    index: dict[tuple, list[dict]] = defaultdict(list)
    for r in trainable_rows:
        if r["signal"] == "won":
            index[(r["season"], r["week"], r["add_player_id"])].append(r)
    return dict(index)


def annotate_position_competition(all_rows: list[dict]) -> None:
    """Mutates every row in place, attaching best_position_competitor_ros_rank
    / had_position_competitor_rank - a cheap first cut at a "hot commodity"
    / crowding-out feature (see the conversation this was built from): the
    more elite OTHER free agents are sitting on the wire at the same
    position that week, the more a decent-but-not-elite player's own
    interest/price should get squeezed, since FAAB attention (and budget)
    is zero-sum within a week. Deliberately starting from ROS rank ALONE
    rather than a weighted combination of prior-week points/weekly rank/ROS
    rank - ROS rank is already position-specific and league-scoring-format-
    agnostic (a real FantasyPros consensus rank, not derived from any one
    league's own point values), so no cross-feature combination or
    cross-position normalization is needed at all to make it comparable.
    Per the conversation, this is intentionally the simplest possible
    version to validate the underlying idea before reaching for anything
    more complex (e.g. a weighted blend of features, or literally running
    the interest model on every competitor).

    Grouped by (source_league_id, season, week, position) - the same
    grouping build_training_table.py uses to generate no_bid rows for
    every free agent at that spot, so "other" here genuinely means every
    other player who was on the wire, not just other rows that happen to
    have drawn a bid. Uses the row's OWN pre-substitution ros_rank (None
    when FantasyPros never ranked that player-week), not the
    MISSING_RANK_SENTINEL-filled feature_vector value, so a real "nobody
    notable is ranked" read isn't quietly diluted by treating every
    unranked competitor as tied at the sentinel.

    Must run on the FULL unfiltered row set (before load_trainable_rows
    filters to trainable signals) - a no_bid row IS another free agent for
    this purpose, and dropping them would silently shrink "other players
    available" down to just whoever happened to draw a bid, which is
    exactly backwards for a competition-for-attention signal."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in all_rows:
        groups[(r.get("source_league_id"), r["season"], r["week"], r["position"])].append(r)
    for group in groups.values():
        for r in group:
            other_ranks = [
                g["ros_rank"] for g in group
                if g["add_player_id"] != r["add_player_id"] and g.get("ros_rank") is not None
            ]
            r["best_position_competitor_ros_rank"] = min(other_ranks) if other_ranks else None
            r["had_position_competitor_rank"] = bool(other_ranks)


def load_trainable_rows(rows: list[dict]) -> list[dict]:
    """Takes an already-parsed, already-annotated row list (see
    annotate_position_competition - must run on the FULL row set BEFORE
    this filters it down, so it has every free agent to compare against,
    not just the ones that happen to be trainable) rather than a path, so
    a caller that also needs the unfiltered all_rows (both current callers
    do) can parse+annotate the file once and share the same row objects,
    instead of two independent reads silently drifting into two unrelated
    copies of "the same" data.

    Excludes type == "FREEAGENT" - once a player clears the waiver
    period unclaimed, ANY team can add him instantly for free, first-come-
    first-served, with no bid involved at all. A "won" FREEAGENT row (The O
    League's own $2 house fee, or $0 for every other league - see
    tools/faab_history/pull_o_league_bids.py's classify()) never went
    through any competitive pricing process, so it's not a market data
    point a bid-amount model should learn from, and it's not a real
    "nobody wanted him" interest signal either (someone did want him, badly
    enough to grab him - he just didn't have to pay for the privilege).
    Still present in the full training-table JSON (this function's caller
    reads a FILTERED view) so tools/faab_history/build_training_table.py's
    build_effective_budgets, which reads the unfiltered bids directly,
    still accounts for real house-fee dollars that came out of the season
    budget when estimating The O League's effective starting budget per
    season.

    Also excludes KNOWN_BAD_BID_TRANSACTION_IDS - real bids confirmed to be
    data-entry errors, not real spend (duplicated from tools/faab_history/
    build_training_table.py's own constant of the same name - engine/
    doesn't import from tools/, wrong dependency direction, see O_LEAGUE_ID
    above). Left in at face value, a $1003.25 bid against a ~$40 effective
    budget (see build_effective_budgets) would train a target_pct near
    2500% - a single row that would badly distort both the price
    regression fit and any k-NN search that happened to draw it as a
    neighbor."""
    KNOWN_BAD_BID_TRANSACTION_IDS = {"d598e6ff-563e-4e03-b512-91216518fa59"}
    return [
        r for r in rows
        if r["signal"] in TRAINABLE_SIGNALS and r["position"] in POSITIONS and r["type"] != "FREEAGENT"
        and r.get("transaction_id") not in KNOWN_BAD_BID_TRANSACTION_IDS
    ]


def dedupe_events_for_interest(rows: list[dict]) -> list[dict]:
    """Collapses a real auction's several team-rows - one per bidder, see
    tools/faab_history/pull_o_league_bids.py - to ONE representative row per
    (source_league_id, season, week, add_player_id) event, for INTEREST-stage
    training only. Keyed by league too - ESPN player ids are global across
    every league on the platform, so two different leagues' bids on the same
    real player in the same week are separate events, not one.
    Without this, a heavily-contested auction (one won row + N outbid rows,
    all sharing the SAME feature vector since features describe the player,
    not the bidder) counts as N+1 "yes, someone bid" examples while a
    lightly-contested one counts as 1 - silently smuggling
    num_competing_bidders' signal back in through row count, despite it
    being deliberately excluded as a feature (see module docstring).

    Prefers won, then outbid, then other_failure (each a weaker but still
    real "yes, someone placed a bid" signal than the last), in that order -
    checked against the actual historical data: of 361 multi-row events
    that include an other_failure row, 203 have NO won or outbid row at
    all (the manager's OWN other contingent claim consumed the roster slot
    - see pull_o_league_bids.py - not a rival's competing bid), so without
    this fallback chain those 203 real "someone tried to bid" events would
    be invisible to the interest stage entirely. Every multi-row event that
    DOES include a won row already has one, so preferring won never loses
    information, it just avoids over-weighting one contested event by its
    row count."""
    by_event: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_event[(r.get("source_league_id"), r["season"], r["week"], r["add_player_id"])].append(r)
    signal_priority = {"won": 0, "outbid": 1, "other_failure": 2, "no_bid": 3}
    out = [min(group, key=lambda r: signal_priority.get(r["signal"], 99)) for group in by_event.values()]
    return out


_CROSS_LEAGUE_POINTS_FEATURES = ("prior_week_actual_points", "trailing_2_3_avg_points", "season_avg_points")


def consolidate_cross_league_events(rows: list[dict], *, is_price: bool) -> list[dict]:
    """Collapses rows describing the SAME real-world event (season, week,
    add_player_id) across DIFFERENT leagues into one representative row -
    the cross-league analog of dedupe_events_for_interest above, which
    already does this WITHIN one league's own multi-bidder auction. Without
    this, a popular real-world trigger (one specific player's one specific
    week) pooled from N leagues would occupy up to N of a k-NN search's K
    neighbor slots and contribute N correlated observations to a regression
    fit - not because that situation is N times more relevant or
    trustworthy, but purely because it happened to be visible in N pooled
    leagues' own rosters that week. See the conversation this was built
    from - confirmed real and non-trivial even with just 2 pooled leagues
    (6.3% of real bid events already overlapped).

    week / prior_week_had_stat_row / own_injury_flag / teammate_position_
    injury_flag / snap_pct_prior_week all come from nflverse/injury data -
    literally identical across leagues for the same real player-week, so
    whichever row is picked as the base carries the right values already.
    The three points-based features differ by each league's own scoring
    rules and are averaged across every league involved.

    is_price=False (interest pool): a deliberately simple "did the broader
    market show interest at ALL" read - if any involved league's row isn't
    no_bid, keep one such row (same won > outbid > other_failure priority
    as dedupe_events_for_interest); otherwise keep a no_bid row. This does
    NOT weight by how MANY of the involved leagues actually bid (a
    documented simplification, not a claim that 1-of-20 leagues bidding is
    as meaningful as 15-of-20) - a fractional-vote version would be a
    reasonable future enhancement.

    is_price=True (price pool): every row here is already a real "won" row
    (see build_price_rows) - one per league that won this event. Averages
    each league's own target_pct (already normalized to that league's own
    season-average spend) across the leagues that won it, and stores the
    result on the representative row as consolidated_target_pct, which
    target_pct() reads in preference to recomputing from a single league's
    effective_cost_dollars.

    Also attaches o_league_detail whenever a group of 2+ leagues includes
    The O League specifically - {"signal", "bid_dollars"} (bid_dollars only
    set when signal == "won") straight from The O League's own row in the
    group, so a UI showing a comp sourced from an unfamiliar pooled league
    can still say "The O League saw this too, and here's what happened in
    OUR league" - real specificity from the one league a reader actually
    knows, on top of the cross-league statistical read. None when The O
    League wasn't part of this event (most groups) or when the event
    wasn't cross-league at all (group of 1 - that row already IS whichever
    single league's own record, nothing extra to attach)."""
    by_event: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        by_event[(r["season"], r["week"], r["add_player_id"])].append(r)

    signal_priority = {"won": 0, "outbid": 1, "other_failure": 2, "no_bid": 3}
    out = []
    for group in by_event.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        representative = dict(min(group, key=lambda r: signal_priority.get(r["signal"], 99)))
        for f in _CROSS_LEAGUE_POINTS_FEATURES:
            vals = [r[f] for r in group if r.get(f) is not None]
            representative[f] = (sum(vals) / len(vals)) if vals else None
        representative["consolidated_from_leagues"] = sorted({r["source_league_id"] for r in group})
        if is_price:
            representative["consolidated_target_pct"] = sum(target_pct(r) for r in group) / len(group)
        o_league_row = next((r for r in group if r["source_league_id"] == O_LEAGUE_ID), None)
        representative["o_league_detail"] = (
            {"signal": o_league_row["signal"], "bid_dollars": o_league_row["bid_amount_dollars"] if o_league_row["signal"] == "won" else None}
            if o_league_row is not None
            else None
        )
        out.append(representative)
    return out


def build_price_rows(trainable_rows: list[dict]) -> list[dict]:
    """The PRICE-stage training pool: every real won row, plus one
    synthetic won-equivalent per event that has real bidding activity but
    NO won or outbid row at all (an event whose only rows are
    "other_failure" - see dedupe_events_for_interest) - that event's
    HIGHEST other_failure bid_amount_dollars, treated as its
    effective_cost_dollars (target_pct reads effective_cost_dollars, not
    bid_amount_dollars, so this is what actually makes it price like a won
    row). It never won, but it also never lost to a rival bid (that's
    "outbid", excluded from price for the censoring reason in the module
    docstring) - it's the closest thing this event has to "what someone was
    willing to pay," so on balance it's a better price observation than
    dropping the event's price signal entirely.

    Backtested against the alternative (drop these events from price
    entirely, same as an outbid row) under the same event-grouped holdout:
    a clear net win - overall MAE $7.63->$6.79, outbid-row MAE $50.62->
    $31.60, RB MAE $19.09->$14.75, no_bid MAE $2.84->$2.50 - with won-row
    MAE essentially flat ($44.46->$44.85, <1% - noise, not a real cost)."""
    won = [r for r in trainable_rows if r["signal"] == "won"]

    by_event: dict[tuple, list[dict]] = defaultdict(list)
    for r in trainable_rows:
        by_event[(r.get("source_league_id"), r["season"], r["week"], r["add_player_id"])].append(r)

    synthetic = []
    for group in by_event.values():
        if any(r["signal"] in ("won", "outbid") for r in group):
            continue
        other_failure_rows = [r for r in group if r["signal"] == "other_failure"]
        if not other_failure_rows:
            continue
        best = max(other_failure_rows, key=lambda r: r["bid_amount_dollars"])
        synthetic.append({**best, "signal": "won", "effective_cost_dollars": best["bid_amount_dollars"]})

    return won + synthetic


def feature_vector(r: dict) -> dict[str, float]:
    return {
        "week": r["week"],
        "prior_week_actual_points": r["prior_week_actual_points"] if r.get("prior_week_actual_points") is not None else 0.0,
        "prior_week_had_stat_row": 1.0 if r.get("prior_week_had_stat_row") else 0.0,
        "own_injury_flag": 1.0 if r.get("own_injury_status") else 0.0,
        "teammate_position_injury_flag": 1.0 if r.get("teammate_position_injury_flag") else 0.0,
        "snap_pct_prior_week": r["snap_pct_prior_week"] if r.get("snap_pct_prior_week") is not None else 0.0,
        "trailing_2_3_avg_points": r["trailing_2_3_avg_points"] if r.get("trailing_2_3_avg_points") is not None else 0.0,
        "season_avg_points": r["season_avg_points"] if r.get("season_avg_points") is not None else 0.0,
        "weekly_rank": r["weekly_rank"] if r.get("weekly_rank") is not None else MISSING_RANK_SENTINEL,
        "had_weekly_rank": 1.0 if r.get("weekly_rank") is not None else 0.0,
        "ros_rank": r["ros_rank"] if r.get("ros_rank") is not None else MISSING_RANK_SENTINEL,
        "had_ros_rank": 1.0 if r.get("ros_rank") is not None else 0.0,
        "best_position_competitor_ros_rank": r["best_position_competitor_ros_rank"] if r.get("best_position_competitor_ros_rank") is not None else MISSING_RANK_SENTINEL,
        "had_position_competitor_rank": 1.0 if r.get("had_position_competitor_rank") else 0.0,
    }


WEEK_BUCKET_DUMMY_NAMES = ["week_decline_flag", "week_championship_flag"]


def week_bucket_dummies(week: int) -> list[float]:
    """Two dummies (baseline = weeks 1-12, "normal season") encoding a
    3-cluster season-phase structure - INTEREST-stage regression only (see
    fit_interest_regression/regression_estimate); week itself stays a
    single continuous feature everywhere else (k-NN's distance metric
    already handles graduated week-to-week closeness fine, and a linear
    regression term literally cannot fit a non-monotonic break the way a
    dummy can).

    Found via a weighted 1D changepoint analysis (optimal contiguous-
    segment partition minimizing within-segment variance, a.k.a. Jenks
    natural breaks) of real bid RATE by week across the full pooled
    dataset, not guessed: SSE dropped sharply going from 2 clusters to 3
    (65.0->25.1) but barely at all from 3 to 4 (25.1->17.5) - the elbow is
    at 3. Those 3 clusters are weeks 1-12 (bid rate ~14%, flat), 13-15
    (~11%, declining), 16-17 (~5.7%, craters - only teams still alive for
    a title keep bidding). Conditional PRICE showed no comparable break in
    the same analysis - once someone does bid late, they still pay a
    normal price - so this is deliberately interest-only, not applied to
    the price-stage regression. week==1 is excluded from ever appearing in
    interest TRAINING (see FaabModel.__init__ / evaluate_model.py's own
    exclusion) because the pooled dataset has zero real no_bid rows for
    week 1 at all - so it can't inform where the weeks 1-12 boundary
    actually falls - but a live week-1 query still needs a bucket
    assignment for prediction, and week<=12 ("normal") is the sane
    default."""
    is_decline = 1.0 if 13 <= week <= 15 else 0.0
    is_championship = 1.0 if week >= 16 else 0.0
    return [is_decline, is_championship]


def target_pct(r: dict) -> float:
    """This bid as a fraction of that (league, season)'s EFFECTIVE STARTING
    budget (see build_effective_budgets) - not a fraction of the bidding
    team's own remaining budget at the time (considered and rejected - see
    the conversation this was built from: dividing by remaining budget
    explodes into noise late in the season as that denominator shrinks
    toward zero, e.g. a throwaway $3 bid with $5 left reads as "60% of
    remaining" despite being a trivial, low-stakes bid - a real statistical
    instability, not just a style preference), and not a fraction of that
    (league, season)'s average total END-of-season spend (the older
    definition before that - needed the whole season to already be over
    just to compute). Budget scarcity as the season progresses is still a
    real, worthwhile signal - it belongs on bidder_pct_budget_remaining_at_
    bid / league_avg_pct_budget_remaining_at_bid as an input FEATURE the
    model can learn a relationship from, not baked into the target's own
    denominator.

    Reads effective_starting_budget, set by tools/faab_history/
    build_training_table.py's build_effective_budgets/annotate_budget_
    remaining - a FIXED number per (league, season), known before the
    season even starts, so this never needs end-of-season information and
    never divides by something that trends toward zero. Returns 0.0 when
    that field is missing/zero (no_bid/outbid rows always have
    effective_cost_dollars == 0 anyway, so the exact denominator never
    matters for them) rather than raising, so a row from an older table
    generation (pre-dating this field) degrades to $0 instead of crashing.

    Checks consolidated_target_pct first - set by
    consolidate_cross_league_events on a row representing the SAME real
    event won in more than one pooled league, where the right answer is
    already an average across those leagues' own target_pct values.

    Uses effective_cost_dollars, NOT bid_amount_dollars - they only differ
    for a won "FREEAGENT" (uncontested) pickup, where ESPN records the raw
    bid as $0 (there was no auction) but The O League's real house rule
    still charges the flat $2 fee (see FREEAGENT_FLAT_COST_DOLLARS in
    pull_o_league_bids.py) - other leagues get $0 here, see that file's
    classify(). Reading bid_amount_dollars here would silently train all
    521 of The O League's real, successful pickups as worth exactly
    nothing - indistinguishable from a true no_bid row - which is wrong:
    "won uncontested for the minimum fee" is real information, just weaker
    than "someone had to outbid a rival for this". (Moot in practice for
    this specific field either way, since load_trainable_rows excludes
    FREEAGENT rows from ever reaching a model - see that function - but
    this function is still correct to call on a FREEAGENT row directly,
    e.g. from a diagnostic script.)"""
    if "consolidated_target_pct" in r:
        return r["consolidated_target_pct"]
    budget = r.get("effective_starting_budget")
    return (r["effective_cost_dollars"] / budget) if budget else 0.0


# ---------------------------------------------------------------------------
# Shared: standardized-distance helpers
# ---------------------------------------------------------------------------
def _feature_stats(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """mean/std per feature, POOLED across positions - only used to scale
    distances so no one feature dominates just from bigger raw units. Fit
    on interest_rows (the larger, full pool) so both stages' k-NN searches
    share one standardized space, even though the price search draws its
    actual neighbors from the smaller won-only pool."""
    stats = {}
    for f in FEATURE_NAMES:
        vals = np.array([feature_vector(r)[f] for r in rows])
        stats[f] = (vals.mean(), vals.std() or 1.0)
    return stats


def _knn(query: dict, pool: list[dict], stats: dict[str, tuple[float, float]], k: int) -> tuple[list[dict], list[float]]:
    """The k nearest same-position rows in pool, by standardized Euclidean
    distance, plus their 1/(dist+0.05) weights - the one distance routine
    both the interest and price k-NN searches share."""
    same_pos = [r for r in pool if r["position"] == query["position"]]

    def vec(r):
        fv = feature_vector(r)
        return np.array([(fv[f] - stats[f][0]) / stats[f][1] for f in FEATURE_NAMES])

    qv = vec(query)
    scored = sorted(same_pos, key=lambda r: np.linalg.norm(vec(r) - qv))[:k]
    weights = [1.0 / (np.linalg.norm(vec(r) - qv) + 0.05) for r in scored]
    return scored, weights


def _comp_rank_and_injury_fields(r: dict) -> dict:
    """weekly_rank/ros_rank (None, not MISSING_RANK_SENTINEL, when
    FantasyPros never ranked this player-week - a UI showing this comp
    should say "unranked", not a misleadingly literal "150") plus the two
    injury flags and the two recency-average features - the same fields
    tools/faab_history's example_lib.py (built for the artifact this UI is
    modeled on) already exposes per comp, folded into the production comp
    dicts so the live site can show the same detail instead of a strictly
    poorer subset of it."""
    fv = feature_vector(r)
    return {
        "trailing_2_3_avg_points": fv["trailing_2_3_avg_points"],
        "season_avg_points": fv["season_avg_points"],
        "weekly_rank": None if fv["weekly_rank"] == MISSING_RANK_SENTINEL else fv["weekly_rank"],
        "ros_rank": None if fv["ros_rank"] == MISSING_RANK_SENTINEL else fv["ros_rank"],
        "own_injury_flag": fv["own_injury_flag"] > 0,
        "teammate_position_injury_flag": fv["teammate_position_injury_flag"] > 0,
    }


def _price_comp_dicts(scored: list[dict], competing_bids_index: dict[tuple, list[dict]] | None) -> list[dict]:
    return [
        {
            "season": r["season"], "week": r["week"], "name": r["add_player_name"],
            "signal": r["signal"], "bid_dollars": r["bid_amount_dollars"],
            # Same pct-of-remaining-budget transform behind the headline
            # conditional_price, per comp - this is what a "distribution"
            # actually means here: not a modeled confidence interval, but
            # the real spread of what the K nearest WINNING comps actually
            # cost their own bidder, as a % of what that bidder had left,
            # in the same units as the point estimate (which is just this
            # array's weighted average).
            "pct_of_remaining_budget": target_pct(r),
            "prior_week_actual_points": r.get("prior_week_actual_points"),
            "snap_pct_prior_week": r.get("snap_pct_prior_week"),
            **_comp_rank_and_injury_fields(r),
            # Other real bids that lost this SAME historical auction -
            # context on how contested it was, not part of the price
            # estimate itself (see module docstring).
            "competing_bids": [
                b for b in (competing_bids_index or {}).get((r.get("source_league_id"), r["season"], r["week"], r["add_player_id"]), [])
                if b["team_id"] != r["team_id"]
            ],
            # See consolidate_cross_league_events - real specificity from
            # The O League itself when this comp is actually a cross-league
            # consolidated event that included it.
            "o_league_detail": r.get("o_league_detail"),
        }
        for r in scored
    ]


def _interest_comp_dicts(scored: list[dict]) -> list[dict]:
    return [
        {
            "season": r["season"], "week": r["week"], "name": r["add_player_name"],
            "signal": r["signal"],
            "prior_week_actual_points": r.get("prior_week_actual_points"),
            "snap_pct_prior_week": r.get("snap_pct_prior_week"),
            **_comp_rank_and_injury_fields(r),
            "o_league_detail": r.get("o_league_detail"),
        }
        for r in scored
    ]


# ---------------------------------------------------------------------------
# 1. Comp-based nearest-neighbor
# ---------------------------------------------------------------------------
def price_confidence_samples(
    scored_price_comps: list[dict],
    price_weights: list[float],
    competing_bids_index: dict[tuple, list[dict]] | None,
    event_won_rows_index: dict[tuple, list[dict]] | None = None,
) -> list[tuple[float, float]]:
    """(value, weight) pairs - the raw material for a "bid $X for an
    N% historical win rate" confidence slider. Two poolings happen here,
    nested:

    1. ACROSS LEAGUES: a k-NN neighbor that consolidate_cross_league_events
       collapsed into one representative row (because the same real-world
       trigger - e.g. a real RB injury - was visible and bid on across many
       pooled leagues) gets expanded back out via event_won_rows_index into
       every one of those leagues' own real winning price, not just the
       single averaged consolidated_target_pct the point estimate uses. If
       a real event happened to be visible in 40 pooled leagues, this
       neighbor contributes up to 40 real per-league outcomes, not 1.
    2. WITHIN each of those per-league outcomes: that SAME league's own
       real LOSING bids (see build_competing_bids_index) get pooled in too
       - a losing bid is still a genuine data point about what a
       comparable bidder was actually willing to pay.

    Either pooling degrades gracefully to the plain single-row behavior
    when event_won_rows_index is omitted, or when an event was never
    actually cross-league (event_won_rows_index[key] == [r] - just itself).

    Each NEIGHBOR's total weight is its own k-NN weight, split EVENLY
    across every real bid it expands into across BOTH poolings combined -
    not one full k-NN weight per bid, and not one full share per league
    either. Without this, a real trigger visible in 100 leagues (or one
    single-league auction with 5 real bidders) would get proportionally
    more influence over the resulting distribution purely by virtue of
    being pooled from more leagues or drawing more bids, not because it's
    more relevant to the query - the exact same "one real event should be
    one vote" failure mode dedupe_events_for_interest/
    consolidate_cross_league_events already guard against elsewhere in
    this file for the point estimate, just showing up in a new place for
    the distribution.

    Every dollar amount is converted to %-of-budget units using ITS OWN
    row's effective_starting_budget - a real per-(league, season) constant
    every team in that league-season shares (see build_effective_budgets),
    so this is a safe, meaningful conversion even across many different
    leagues' own budgets at once."""
    samples: list[tuple[float, float]] = []
    for r, w in zip(scored_price_comps, price_weights):
        key = (r["season"], r["week"], r["add_player_id"])
        per_league_wins = (event_won_rows_index or {}).get(key) or [r]
        values: list[float] = []
        for lr in per_league_wins:
            budget = lr.get("effective_starting_budget")
            if not budget:
                continue
            values.append(lr["effective_cost_dollars"] / budget)
            rivals = [
                b for b in (competing_bids_index or {}).get((lr.get("source_league_id"), lr["season"], lr["week"], lr["add_player_id"]), [])
                if b["team_id"] != lr["team_id"]
            ]
            values.extend(b["bid_dollars"] / budget for b in rivals)
        if not values:
            continue
        share = w / len(values)
        samples.extend((v, share) for v in values)
    return samples


def weighted_percentile(samples: list[tuple[float, float]], pct: float) -> float:
    """pct in [0, 100]. The value at which `pct`% of the total WEIGHT (not
    row count) falls at or below it - same idea as numpy.percentile, just
    weight-aware, since price_confidence_samples' rows aren't all equally
    trustworthy. Returns 0.0 for an empty pool rather than raising - a
    position/situation with no real price comps at all has no confidence
    curve to show, not an error."""
    if not samples:
        return 0.0
    ordered = sorted(samples, key=lambda sw: sw[0])
    total = sum(w for _, w in ordered)
    if total <= 0:
        return ordered[-1][0]
    target = (pct / 100.0) * total
    cum = 0.0
    for v, w in ordered:
        cum += w
        if cum >= target:
            return v
    return ordered[-1][0]


def comp_based_estimate(
    query: dict,
    interest_rows: list[dict],
    price_rows: list[dict],
    competing_bids_index: dict[tuple, list[dict]] | None = None,
    k: int = 10,
    price_k: int | None = None,
    event_won_rows_index: dict[tuple, list[dict]] | None = None,
) -> dict:
    """Two k-NN searches, one standardized distance space (fit on
    interest_rows - see module docstring for why the interest and price
    pools differ): the K nearest same-position INTEREST comps vote (by the
    same 1/(dist+0.05) weighting used for price) on P(anyone bids); the K
    nearest same-position PRICE comps (won rows only) weighted-average into
    the conditional price - now a % OF THE WINNING BIDDER'S OWN REMAINING
    BUDGET at the time (see target_pct), not a $-scaled-to-$1000 amount.

    Deliberately does NOT also return P(bid) x conditional price as a single
    blended number - that product is an ex-ante expected cost (averaged
    across the worlds where nobody bids and this costs $0 too), not "what
    should I bid if I've decided I want this player" - once you're actually
    bidding you're conditioning on a contest existing, and a real competitor
    prices off their own conditional valuation, not off the odds a contest
    happens at all. See the conversation this was built from. The headline
    number a caller should show is conditional_price; bid_probability is
    real, useful context ("but you may not even need to bid"), shown
    separately, not multiplied in.

    price_k (defaults to k when omitted) can widen the PRICE search
    specifically, independent of the interest search's own k - see
    price_confidence_samples: estimating a tail percentile for the
    confidence-slider feature wants a bigger pool than a plain weighted
    average needs to already be stable, and the two searches have no reason
    to share one k just because they happened to default to the same
    number historically."""
    stats = _feature_stats(interest_rows)

    interest_scored, interest_weights = _knn(query, interest_rows, stats, k)
    bid_probability = sum(w * (1.0 if r["signal"] != "no_bid" else 0.0) for w, r in zip(interest_weights, interest_scored)) / sum(interest_weights)

    price_scored, price_weights = _knn(query, price_rows, stats, price_k or k)
    conditional_pct = sum(w * target_pct(r) for w, r in zip(price_weights, price_scored)) / sum(price_weights) if price_scored else 0.0

    return {
        "bid_probability": bid_probability,
        "conditional_price": conditional_pct,
        "comps": _price_comp_dicts(price_scored, competing_bids_index),
        "interest_comps": _interest_comp_dicts(interest_scored),
        "price_confidence_samples": price_confidence_samples(price_scored, price_weights, competing_bids_index, event_won_rows_index),
    }


# ---------------------------------------------------------------------------
# 2. Simple baseline: same-position, same-usage-decile historical average
# ---------------------------------------------------------------------------
def _combined_usage_percentile(r: dict, points_vals: list[float], snap_vals: list[float]) -> float:
    fv = feature_vector(r)

    def percentile_rank(values: list[float], value: float) -> float:
        return sum(1 for v in values if v <= value) / len(values)

    return (percentile_rank(points_vals, fv["prior_week_actual_points"]) + percentile_rank(snap_vals, fv["snap_pct_prior_week"])) / 2.0


def _usage_bucket(query: dict, pool: list[dict]) -> list[dict]:
    """Same-position rows within +/-0.05 combined usage percentile of
    query - falls back to the whole pool if that's empty."""
    same_pos = [r for r in pool if r["position"] == query["position"]]
    if not same_pos:
        return []
    points_vals = [feature_vector(r)["prior_week_actual_points"] for r in same_pos]
    snap_vals = [feature_vector(r)["snap_pct_prior_week"] for r in same_pos]
    query_pct = _combined_usage_percentile(query, points_vals, snap_vals)
    bucket = [r for r in same_pos if abs(_combined_usage_percentile(r, points_vals, snap_vals) - query_pct) <= 0.05]
    return bucket or same_pos


def simple_baseline_estimate(query: dict, interest_rows: list[dict], price_rows: list[dict]) -> dict:
    """Deliberately NOT position_avg x a bounded multiplier - with ~90% of
    interest_rows being no-bids, a multiplier capped anywhere near 1x-3x
    can never reach what the top usage tier actually commands. Bucketing by
    decile lets the top bucket's own (much higher) average speak for
    itself. Same two-stage split as comp_based_estimate: P(bid) from the
    interest-pool bucket, conditional price from the (won-only) price-pool
    bucket - a % of remaining budget (see target_pct), not a $ amount."""
    interest_bucket = _usage_bucket(query, interest_rows)
    bid_probability = (sum(1.0 if r["signal"] != "no_bid" else 0.0 for r in interest_bucket) / len(interest_bucket)) if interest_bucket else 0.0

    price_bucket = _usage_bucket(query, price_rows)
    conditional_pct = (sum(target_pct(r) for r in price_bucket) / len(price_bucket)) if price_bucket else 0.0

    return {
        "bid_probability": bid_probability,
        "conditional_price": conditional_pct,
    }


# ---------------------------------------------------------------------------
# 3. Regression: logistic (P(bid)) x OLS-on-log1p (conditional price),
#    both plain numpy - no new dependency
# ---------------------------------------------------------------------------
def _design_matrix(rows: list[dict]) -> np.ndarray:
    X_rows = []
    for r in rows:
        fv = feature_vector(r)
        pos_dummies = [1.0 if r["position"] == p else 0.0 for p in POSITIONS[1:]]
        X_rows.append([1.0] + [fv[f] for f in FEATURE_NAMES] + pos_dummies)
    return np.array(X_rows)


def _design_vector(query: dict) -> np.ndarray:
    fv = feature_vector(query)
    pos_dummies = [1.0 if query["position"] == p else 0.0 for p in POSITIONS[1:]]
    return np.array([1.0] + [fv[f] for f in FEATURE_NAMES] + pos_dummies)


_COEF_NAMES = ["intercept"] + FEATURE_NAMES + [f"pos_{p}" for p in POSITIONS[1:]]
# Interest-stage-only coef names - see week_bucket_dummies for why the
# interest design matrix has 2 extra columns the price design matrix
# doesn't.
_INTEREST_COEF_NAMES = ["intercept"] + FEATURE_NAMES + WEEK_BUCKET_DUMMY_NAMES + [f"pos_{p}" for p in POSITIONS[1:]]


def fit_price_regression(price_rows: list[dict]) -> dict[str, float]:
    """OLS on log1p(target_pct), fit on WON rows only - see module
    docstring for why outbid/no_bid rows never touch this fit."""
    X = _design_matrix(price_rows)
    y = np.array([math.log1p(target_pct(r)) for r in price_rows])
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    return dict(zip(_COEF_NAMES, coefs))


def fit_interest_regression(interest_rows: list[dict], max_iter: int = 25, l2: float = 1.0) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Logistic regression (Newton-Raphson / IRLS) predicting P(signal !=
    no_bid), fit on the FULL won+outbid+no_bid+other_failure pool, deduped
    one row per event (see dedupe_events_for_interest) so a heavily-
    contested auction doesn't outweigh a lightly-contested one just by row
    count.

    Standardized (same mean/std _feature_stats the k-NN searches use) and
    L2-ridge-penalized (lambda=1, intercept excluded) - plain unpenalized
    Newton-Raphson on RAW-scale features diverged here (coefficients like
    -23.87 on a single binary flag), a classic (quasi-)separation symptom
    given this pool's ~9% positive rate. Standardizing first makes one
    ridge strength meaningful across differently-scaled features; the
    ridge itself keeps the fit well-behaved without materially changing
    what it predicts. Returns (coefs in STANDARDIZED-feature units, the
    feature stats needed to standardize a query the same way at predict
    time - see regression_estimate)."""
    stats = _feature_stats(interest_rows)

    def std_row(r):
        fv = feature_vector(r)
        std_feats = [(fv[f] - stats[f][0]) / stats[f][1] for f in FEATURE_NAMES]
        pos_dummies = [1.0 if r["position"] == p else 0.0 for p in POSITIONS[1:]]
        return [1.0] + std_feats + week_bucket_dummies(r["week"]) + pos_dummies

    X = np.array([std_row(r) for r in interest_rows])
    y = np.array([0.0 if r["signal"] == "no_bid" else 1.0 for r in interest_rows])
    n, d = X.shape
    beta = np.zeros(d)
    penalty = l2 * np.eye(d)
    penalty[0, 0] = 0.0  # never shrink the intercept
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
    return dict(zip(_INTEREST_COEF_NAMES, beta)), stats


def regression_estimate(query: dict, price_coefs: dict[str, float], interest_coefs: dict[str, float], interest_stats: dict[str, tuple[float, float]]) -> dict:
    fv = feature_vector(query)
    std_feats = [(fv[f] - interest_stats[f][0]) / interest_stats[f][1] for f in FEATURE_NAMES]
    pos_dummies = [1.0 if query["position"] == p else 0.0 for p in POSITIONS[1:]]
    x_std = np.array([1.0] + std_feats + week_bucket_dummies(query["week"]) + pos_dummies)
    z = float(np.clip(np.dot(x_std, np.array(list(interest_coefs.values()))), -30, 30))
    bid_probability = 1.0 / (1.0 + math.exp(-z))

    x = _design_vector(query)
    log_pred = float(np.dot(x, np.array(list(price_coefs.values()))))
    conditional_pct = max(math.expm1(log_pred), 0.0)

    return {
        "bid_probability": bid_probability,
        "conditional_price": conditional_pct,
        "price_coefs": price_coefs,
        "interest_coefs": interest_coefs,
    }


# ---------------------------------------------------------------------------
class FaabModel:
    """Fit once per pipeline run, reused for every candidate player."""

    def __init__(self, training_table_path: Path = TRAINING_TABLE_PATH):
        all_rows = json.loads(training_table_path.read_text())
        annotate_position_competition(all_rows)
        trainable = load_trainable_rows(all_rows)
        self.competing_bids_index = build_competing_bids_index(all_rows)
        # Every league's own WON row per real event - see
        # build_event_won_rows_index/price_confidence_samples. From
        # trainable, not all_rows, so this never needs its own copy of
        # load_trainable_rows' FREEAGENT/known-bad-bid exclusions.
        self.event_won_rows_index = build_event_won_rows_index(trainable)
        # consolidate_cross_league_events collapses the SAME real event
        # (season, week, add_player_id) won/seen across MULTIPLE pooled
        # leagues into one row - without it, a popular real-world trigger
        # visible in N leagues would occupy up to N k-NN neighbor slots and
        # contribute N correlated rows to the regression fits, purely
        # because it happened to be pooled from N leagues - see that
        # function's docstring. A no-op for a single-league table (every
        # event already has group size 1).
        # week == 1 is excluded from interest TRAINING only (not price) -
        # the pooled dataset has zero real no_bid rows for week 1 across
        # every league (roster/free-agent snapshots start at week 2), so
        # every week-1 row that reaches this pool is a real bid - training
        # on that would teach the interest stage a false "week 1 always
        # draws a bid" signal instead of reflecting real appetite. See
        # week_bucket_dummies for why week 1 still needs a sane bucket
        # default at PREDICT time despite never being trained on directly.
        self.interest_rows = consolidate_cross_league_events(dedupe_events_for_interest([r for r in trainable if r["week"] != 1]), is_price=False)
        self.price_rows = consolidate_cross_league_events(build_price_rows(trainable), is_price=True)
        self.price_coefs = fit_price_regression(self.price_rows)
        self.interest_coefs, self.interest_stats = fit_interest_regression(self.interest_rows)

    def estimate(self, query: dict) -> dict:
        # price_k=25 (vs. the interest search's own default 10) - a plain
        # weighted average is already stable at 10 neighbors, but the
        # confidence-slider feature (price_confidence_samples, further
        # widened by pooling in real losing bids from each of those
        # auctions) wants a bigger base pool to estimate a tail percentile
        # from without every step being noisy.
        comp = comp_based_estimate(
            query, self.interest_rows, self.price_rows, self.competing_bids_index,
            price_k=25, event_won_rows_index=self.event_won_rows_index,
        )
        simple = simple_baseline_estimate(query, self.interest_rows, self.price_rows)
        reg = regression_estimate(query, self.price_coefs, self.interest_coefs, self.interest_stats)

        comp_values = sorted(c["pct_of_remaining_budget"] for c in comp["comps"])
        return {
            # No blended P(bid) x conditional_price product here on purpose -
            # see comp_based_estimate's docstring. conditional_price below IS
            # the headline "what to bid if you want him" number per method,
            # as a % of that (league, season)'s effective starting budget -
            # NOT a $ amount; bid_probability is separate context ("but you
            # may not need to"), never multiplied in.
            "bid_probability": {"comp_based": comp["bid_probability"], "simple_baseline": simple["bid_probability"], "regression": reg["bid_probability"]},
            "conditional_price": {"comp_based": comp["conditional_price"], "simple_baseline": simple["conditional_price"], "regression": reg["conditional_price"]},
            "comps": comp["comps"],
            "interest_comps": comp["interest_comps"],
            # Raw (value, weight) pairs behind the confidence slider (see
            # price_confidence_samples) - shipped as-is rather than
            # precomputed at a fixed set of percentiles, so the UI can
            # compute any confidence level live as a slider moves instead
            # of snapping to whatever handful of levels were baked in here.
            "price_confidence_samples": comp["price_confidence_samples"],
            # The spread of the K PRICE comps themselves, in the same
            # pct-of-budget units as conditional_price (which is just their
            # weighted average) - not a modeled confidence interval, the
            # actual range of what comparable WINNING bids actually cost
            # their own bidder.
            "distribution": {
                "min": comp_values[0], "max": comp_values[-1],
                "p25": float(np.percentile(comp_values, 25)),
                "median": float(np.percentile(comp_values, 50)),
                "p75": float(np.percentile(comp_values, 75)),
            } if comp_values else None,
            "inputs": feature_vector(query) | {"position": query["position"]},
        }
