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
     no_bid) weighted-average their own real bid RATE (leagues_with_bid /
     leagues_eligible - see _shrunk_interest_fractions, not a binary "did
     any pooled league bid"), each rate first shrunk toward this same
     neighborhood's own leave-one-out pooled rate so a low-backing comp's
     noisy rate can't swing P(bid) on its own, into P(bid). The K nearest
     same-position PRICE comps (won only) produce TWO parallel conditional-
     price reads off the SAME selected comps: conditional_price_mean, the
     original credibility-weighted average (a single very-well-backed comp
     can still dominate this - working as designed, but a real problem when
     that comp's own price is an outlier relative to its neighbors), and
     conditional_price_median, a weighted median over the same comps after
     capping any one comp's weight relative to the rest (see _cap_weights/
     _weighted_median) - more robust to exactly that case. The comps
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
a training target (see PRICE stage above), and, per the conversation this
was built from, no longer surfaced in the UI's per-comp bid-distribution
view either: a losing bid is a CENSORED observation (the true clearing
price was higher, by an unknown margin - see PRICE stage above), so a
dot/bin for one on the same axis as real winning prices reads as "what it
cost" when it's really "less than what it cost," which is misleading in a
view whose whole point is showing what a comp actually took to win.
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
    # Whether the qualifying teammate's OWN injury/reserve status is new
    # this week (he wasn't ALSO flagged the week before) vs. an ongoing,
    # already-priced-in absence - see build_injury_indices/enrich_player_
    # week. Anecdotally, FAAB bids on a newly-relevant backup spike hardest
    # the very first week a starter goes down and cool off once the market
    # has had a week to price the backup in - see the conversation this was
    # built from. Always 0.0 when teammate_position_injury_flag itself is
    # false - there's no "new" reading without a real flagged teammate to
    # begin with.
    "teammate_position_injury_is_new",
    "snap_pct_prior_week",
    # had_X flags for the three fields above that don't already have one via
    # a dedicated stat-row check (prior_week_had_stat_row covers prior_week_
    # actual_points already) - see the conversation this was built from.
    # Without these, "no real data because he didn't play" and "played and
    # scored/snapped exactly 0" were both silently mapped to the same 0.0,
    # indistinguishable to both the k-NN distance and the regression fit -
    # a real conflation for exactly the population this model cares about
    # (a rank-rescued player who's been out for weeks looking identical,
    # feature-wise, to one on a genuine 0-point/0-snap streak).
    "had_snap_pct_prior_week",
    "trailing_2_3_avg_points",
    "had_trailing_2_3_avg_points",
    "season_avg_points",
    "had_season_avg_points",
    "weekly_rank",
    "had_weekly_rank",
    "ros_rank",
    "had_ros_rank",
    # RB-position-scoped carry share and team-wide (any position) target
    # share - see recent_carry_share/recent_target_share. Only ever a real
    # signal for an RB's own carry share or a WR/TE's own target share (the
    # k-NN search already only ever compares same-position rows, so a
    # QB/K's own near-meaningless carry share never competes against an
    # RB's real one) - see the conversation this was built from: meant to
    # help distinguish a real featured back/receiver from a blocking
    # fullback or in-line TE who racked up a high snap share without much
    # actual on-ball involvement.
    "carry_share_prior_week",
    "had_carry_share_prior_week",
    "target_share_prior_week",
    "had_target_share_prior_week",
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

# A third, independent path to relevance alongside the usage bars above: a
# player with NO recent usage at all (a bye, a benching, a return from
# injury with zero touches yet) can still be a real, well-known name the
# market would clearly bid on - the usage-only gate was silently zeroing
# these out too, indistinguishable from a genuinely irrelevant player (see
# the conversation this was built from - Tank Bigsby, correctly caught by
# the usage gate alone, vs. a startable player just having a quiet/inactive
# week, which the usage gate alone can't tell apart from Bigsby).
# Deliberately ROS rank only, never weekly rank: weekly rank has a real,
# season-varying blackout window early in every season (FantasyPros' weekly
# pages don't start publishing until well after week 1 - confirmed 2020-2026,
# worst in 2024 at ~4 weeks) during which EVERY player, stars included, has
# no weekly rank at all - gating on it would zero out the entire league
# during that window, not just genuinely irrelevant players. ROS rank stays
# reliably populated even then. Calibrated from the pooled table: the 70th
# percentile of ros_rank among real historical bids on players who had NO
# stat row at all that week (the "quiet, not genuinely absent" population) -
# see the conversation this was built from.
NO_BID_MAX_ROS_RANK = {"QB": 25, "RB": 73, "WR": 73, "TE": 25, "K": 18}


def is_faab_relevant(prior_points: float | None, snap_pct: float | None, ros_rank: float | None, position: str) -> bool:
    """Whether a player's own prior-week usage OR ROS rank clears the bar to
    plausibly be worth a real FAAB bid at all - the ONE shared gate both
    tools/faab_history/build_training_table.py (which players are even
    eligible to become a synthetic no_bid training row) and
    engine/pipeline.py (which live WAIVERS candidates the model actually
    runs a search for) call, so a live query and the historical no_bid
    population it's trained against apply an identical bar rather than two
    separately-maintained copies that could quietly drift apart. See the
    conversation this was built from.

    Deliberately ROS rank only, never weekly rank, for the third path - see
    NO_BID_MAX_ROS_RANK's own docstring for why.

    rank_ok fires regardless of whether the player actually had a stat row
    last week - a deliberate call, not an oversight (see the conversation
    this was built from: Tank Bigsby, 1 carry, 0.3 points, a real if weak
    stat row, ROS rank 48, well under RB's ceiling of 73). Real historical
    data shows a player who DID play and looked bad skews meaningfully
    worse-ranked, on average, than one who was genuinely quiet that week
    (the population NO_BID_MAX_ROS_RANK was actually calibrated against) -
    so this does knowingly accept some "played weak but still rank-rescued"
    cases into the modeled population, in exchange for never missing a real
    speculative name purely because he had a token, near-empty stat line
    rather than a blank week. A version gating this on "no stat row at all"
    was tried and rejected for being too narrow."""
    ros_ceiling = NO_BID_MAX_ROS_RANK.get(position)
    usage_ok = (prior_points is not None and prior_points >= NO_BID_MIN_PRIOR_POINTS) or (
        snap_pct is not None and snap_pct >= NO_BID_MIN_SNAP_PCT
    )
    rank_ok = ros_rank is not None and ros_ceiling is not None and ros_rank <= ros_ceiling
    return usage_ok or rank_ok


# ESPN's own injury_status vocabulary (ACTIVE/QUESTIONABLE/DOUBTFUL/OUT/
# INJURY_RESERVE/SUSPENSION/DAY_TO_DAY - see docs/js/colors.js's
# INJURY_BADGE). Only near-certain absences count - QUESTIONABLE/DAY_TO_DAY
# are excluded since most players tagged with those still suit up. Used for
# BOTH the TEAMMATE flag and the bid target's own own_injury_flag (mirrors
# the historical data's Out/Doubtful/RESERVE-only filter - see
# INJURY_FLAG_STATUSES) - see the conversation this was built from.
TEAMMATE_INJURY_FLAG_STATUSES = {"OUT", "DOUBTFUL", "INJURY_RESERVE", "SUSPENSION"}

# nflverse's own report_status vocabulary (Questionable/Doubtful/Out from the
# weekly PRACTICE REPORT) - a completely different vocabulary/data source
# from TEAMMATE_INJURY_FLAG_STATUSES above (ESPN's live roster status), used
# wherever a caller (historical or live) is reading nflverse's own injury/
# roster data via build_injury_indices below, not ESPN's. "RESERVE" is not a
# real nflverse value - see build_injury_indices for why it's synthesized.
INJURY_FLAG_STATUSES = {"Out", "Doubtful", "RESERVE"}
RESERVE_ROSTER_STATUS = "RES"
SYNTHETIC_RESERVE_REPORT_STATUS = "RESERVE"


def build_injury_indices(
    injuries_df, rosters_weekly_df
) -> tuple[dict[tuple[str, int], str], dict[tuple[str, int, str], list[dict]]]:
    """({(gsis_id, week): report_status}, {(team, week, position): rows}) -
    the former for a player's own injury status, the latter for scanning his
    teammates at the same position that week. Shared by tools/faab_history/
    build_training_table.py (historical) and engine/pipeline.py (live) so
    both read nflverse's own injury/roster data the same way.

    Built from injuries_df (the weekly PRACTICE-REPORT data) first, THEN
    overlaid with rosters_weekly_df's own real roster status wherever it
    shows RES (Reserve/Injured, PUP, NFI, etc.) for a (player, week) the
    practice report never recorded at all - see the conversation this was
    built from: a real ~10-week Isiah Pacheco 2024 IR stint (confirmed live
    2026-09-16 via load_rosters_weekly's own week-by-week status, RES from
    week 3 through week 12) had ZERO injuries_df rows for 8 of those 10
    weeks - the practice report only tracks day-to-day game-status
    uncertainty leading up to a game, not "already on injured reserve, not
    practicing at all" the way a real roster-status transaction does. Only
    fills a gap, never overwrites a real practice-report entry - a genuine
    Questionable/Doubtful/Out designation is real, more granular information
    the roster status alone doesn't carry, so it's never downgraded to the
    synthetic RESERVE tag."""
    own: dict[tuple[str, int], str] = {}
    by_team_week_pos: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for row in injuries_df.to_dicts():
        own[(row["gsis_id"], row["week"])] = row.get("report_status")
        by_team_week_pos[(row["team"], row["week"], row["position"])].append(row)

    for row in rosters_weekly_df.to_dicts():
        if row.get("status") != RESERVE_ROSTER_STATUS or not row.get("gsis_id"):
            continue
        key = (row["gsis_id"], row["week"])
        if own.get(key) is not None:
            continue  # a real practice-report entry already exists - don't overwrite it
        own[key] = SYNTHETIC_RESERVE_REPORT_STATUS
        by_team_week_pos[(row["team"], row["week"], row["position"])].append(
            {"gsis_id": row["gsis_id"], "report_status": SYNTHETIC_RESERVE_REPORT_STATUS}
        )
    return own, dict(by_team_week_pos)


def build_gsis_to_pfr_map(playerids_df) -> dict[str, str]:
    """gsis_id -> pfr_id, straight from the raw ESPN<->nflverse crosswalk
    frame (ingest.nfl_data.playerids()) - ingest.ids.IdMap's records don't
    carry pfr_id through, see tools/faab_history/build_training_table.py."""
    result = {}
    for row in playerids_df.iter_rows(named=True):
        if row.get("gsis_id") and row.get("pfr_id"):
            result[row["gsis_id"]] = row["pfr_id"]
    return result


def build_snap_pct_index(snaps_df) -> dict[str, dict[int, float]]:
    """{pfr_player_id: {week: offense_pct}} - built ONCE per season so
    recent_snap_pct can do an O(1) dict lookup instead of a fresh polars
    .filter() call every time it's asked about a player-week. See the
    conversation this was built from: profiling a real enrich_player_week
    call showed ~80% of its time was pure polars query-collection overhead
    (not any actual work) from calling .filter() on this same season-long
    DataFrame repeatedly - with a training table in the hundreds of
    thousands of rows, that overhead alone ran into hours. A season's worth
    of snap_counts rows is small enough that converting all of it to a dict
    once is negligible next to the per-row lookups it replaces."""
    index: dict[str, dict[int, float]] = defaultdict(dict)
    for row in snaps_df.to_dicts():
        index[row["pfr_player_id"]][row["week"]] = row.get("offense_pct")
    return dict(index)


def build_snap_counts_index(snaps_df) -> dict[str, dict[int, float]]:
    """{pfr_player_id: {week: offense_snaps}} - the RAW count twin of
    build_snap_pct_index's own offense_pct, for callers that need to
    aggregate snap share across multiple weeks themselves (e.g. a season-
    long split, which has to be sum(offense_snaps)/sum(team offense_snaps)
    across the weeks actually played, not an average of each week's own
    already-divided percentage - averaging percentages would let a week
    with a handful of snaps count exactly as much as a full game and skew
    the season number away from the player's real overall share). See
    engine/pipeline.py's per-player `weekly[].offense_snaps` - the frontend
    pairs this with `team_offense_snaps` (team_snap_totals below) to derive
    both a single-week and a season Snap % itself, the same way it already
    derives Szn Avg/3wk/2wk/1wk from raw per-week points rather than a
    pre-averaged number."""
    index: dict[str, dict[int, float]] = defaultdict(dict)
    for row in snaps_df.to_dicts():
        index[row["pfr_player_id"]][row["week"]] = row.get("offense_snaps")
    return dict(index)


def team_snap_totals(snaps_df) -> dict[tuple[str, int], float]:
    """{(team, week): total offense_snaps by every player on that team that
    week} - the Snap % denominator, same "team_position_totals but for
    snap_counts instead of player_stats, and no position filter (a team's
    total offensive snaps, not one position's)" shape. snap_counts carries
    a row per player REGARDLESS of position, including defense/special
    teams snaps on separate columns - summing offense_snaps here already
    only touches the offensive side, so no position filter is needed the
    way team_position_totals' RB-only carries total needs one."""
    grouped = snaps_df.group_by(["team", "week"]).agg(pl.col("offense_snaps").sum().alias("total"))
    return {(r["team"], r["week"]): r["total"] for r in grouped.to_dicts()}


def recent_snap_pct(pfr_id: str | None, week: int, snap_pct_index: dict[str, dict[int, float]]) -> float | None:
    """This player's offense_pct in the most recent of week-1/week-2 that
    has a row. snap_pct_index is build_snap_pct_index's own output - see
    that function for why this takes a pre-built index rather than the raw
    snaps DataFrame."""
    if not pfr_id:
        return None
    weeks = snap_pct_index.get(pfr_id)
    if not weeks:
        return None
    for wk in (week - 1, week - 2):
        if wk < 1:
            continue
        pct = weeks.get(wk)
        if pct is not None:
            return pct
    return None


def build_stats_index(stats_df) -> dict[str, dict[int, dict]]:
    """{player_id: {week: row}} - the full nflverse player_stats row per
    player-week, built ONCE per season for the same reason build_snap_pct_
    index exists (see that function's docstring) - recent_carry_share/
    recent_target_share do O(1) lookups into this instead of a fresh
    .filter() per call."""
    index: dict[str, dict[int, dict]] = defaultdict(dict)
    for row in stats_df.to_dicts():
        index[row["player_id"]][row["week"]] = row
    return dict(index)


def team_position_totals(stats_df, position: str | None, stat_col: str) -> dict[tuple[str, int], float]:
    """{(team, week): total `stat_col` by every player AT `position` on that
    team, that week} - the denominator for a position-scoped usage share
    (e.g. an RB's own share of his team's RB-position carries specifically,
    not every position's carries combined - unlike nflverse's own
    target_share, which is already computed team-wide across every
    position and needs no recomputation here - see recent_target_share).
    Computed once per (stats_df, position, stat_col) and reused across every
    player's own lookup, rather than re-aggregating per player. Already a
    single polars group_by (not called per-row), so unlike recent_snap_pct/
    recent_carry_share/recent_target_share this was never the bottleneck -
    kept taking the raw DataFrame rather than build_stats_index's output.

    position=None skips the position filter entirely - the team-WIDE total
    across every position, e.g. team_targets for a Tgt % denominator (any
    position can be targeted, unlike RB-only carries where mixing in a
    receiving back's occasional carries against a non-RB-only pool would be
    the wrong comparison)."""
    rows = stats_df if position is None else stats_df.filter(pl.col("position") == position)
    grouped = rows.group_by(["team", "week"]).agg(pl.col(stat_col).sum().alias("total"))
    return {(r["team"], r["week"]): r["total"] for r in grouped.to_dicts()}


def recent_carry_share(gsis_id: str | None, week: int, stats_index: dict[str, dict[int, dict]], team_rb_carries: dict[tuple[str, int], float]) -> float | None:
    """This RB's own share of his TEAM's total RB-position carries (not
    every position's carries combined - see team_position_totals), for the
    most recent of week-1/week-2 that has a real stat row - same lookback
    convention as recent_snap_pct, so the SNAP/ATT/TGT usage columns all
    read as of the same reference point. Meaningful for an RB specifically
    (a WR's own occasional carry share against an RB-only denominator isn't
    a real signal); callers displaying it should scope that to RB, same as
    recent_target_share's own callers scope to WR/TE. None (not 0.0) when
    the team had zero RB carries that week at all - a real 0/0, not a real
    0% share. stats_index is build_stats_index's own output."""
    if not gsis_id:
        return None
    weeks = stats_index.get(gsis_id)
    if not weeks:
        return None
    for wk in (week - 1, week - 2):
        if wk < 1:
            continue
        row = weeks.get(wk)
        if row is not None:
            total = team_rb_carries.get((row.get("team"), wk))
            return (row.get("carries") or 0) / total if total else None
    return None


def recent_target_share(gsis_id: str | None, week: int, stats_index: dict[str, dict[int, dict]]) -> float | None:
    """This player's own target_share - nflverse's own stat, already
    computed team-wide ACROSS EVERY POSITION (his targets / his team's
    total targets, any position), used as-is - unlike carry share, no
    position-scoped recomputation needed here (see team_position_totals).
    Most recent of week-1/week-2 that has a real stat row - same lookback
    convention as recent_snap_pct. stats_index is build_stats_index's own
    output."""
    if not gsis_id:
        return None
    weeks = stats_index.get(gsis_id)
    if not weeks:
        return None
    for wk in (week - 1, week - 2):
        if wk < 1:
            continue
        row = weeks.get(wk)
        if row is not None:
            return row.get("target_share")
    return None


def build_event_won_rows_index(trainable_rows: list[dict]) -> dict[tuple, list[dict]]:
    """(season, week, add_player_id) -> every league's own WON row for that
    SAME real-world event. Callers should pass trainable_rows AFTER add_synthetic_price_wins
    has already run on it (FaabModel.__init__ does this once, upstream, for
    every trainable_rows consumer - see that function) - this filters to
    signal == "won", which by then includes both real wins and each
    league's own synthetic won-equivalent (the highest real bid in a league
    where every real attempt on this event failed for a non-competitive
    reason, e.g. contingency/roster-limit logistics - see
    add_synthetic_price_wins' docstring). Without that upstream step, a
    league whose own claim never executed but was still a real,
    meaningfully-priced attempt would have its bid silently dropped from
    this event's expansion any time some OTHER pooled league happened to
    have a genuine win for the same event (the `or [r]` single-row fallback
    in _price_comp_bid_distribution/price_confidence_samples only kicks in
    when NO league is in this index at all) - real evidence lost precisely
    when there was other league data to compare it against, not just when
    there wasn't.

    Used by price_confidence_samples to expand a k-NN neighbor that
    consolidate_cross_league_events collapsed into one representative row
    back out into every individual league's own real outcome - e.g. a real
    RB injury that triggered a waiver run in 40 different pooled leagues
    is ONE k-NN neighbor (so it can't hog 40 neighbor slots or skew the
    point estimate/regression), but for the confidence distribution
    specifically, that neighbor's "how much did it actually take to win"
    answer should reflect all 40 real outcomes, not the single
    consolidated_target_pct average consolidate_cross_league_events
    computed for the point estimate. Not keyed by source_league_id - the
    whole point is gathering EVERY league's own row for the same real
    event."""
    index: dict[tuple, list[dict]] = defaultdict(list)
    for r in trainable_rows:
        if r["signal"] == "won":
            index[(r["season"], r["week"], r["add_player_id"])].append(r)
    return dict(index)


def load_trainable_rows(rows: list[dict]) -> list[dict]:
    """Takes an already-parsed row list rather than a path, so a caller that
    also needs the unfiltered all_rows (both current callers do) can parse
    the file once and share the same row objects, instead of two
    independent reads silently drifting into two unrelated copies of "the
    same" data.

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

    is_price=False (interest pool): the representative row's own signal is
    still a deliberately simple "did the broader market show interest at
    ALL" read - if any involved league's row isn't no_bid, keep one such
    row (same won > outbid > other_failure priority as
    dedupe_events_for_interest); otherwise keep a no_bid row. The fractional
    picture IS captured separately though, via leagues_with_bid (count of
    distinct leagues in the group whose own row isn't no_bid) and
    leagues_eligible (len(group) - every league we have real data for this
    event from, i.e. a real bid OR roster data confirming he was a free
    agent there that week - see build_no_bid_rows). 1-of-20 leagues bidding
    and 15-of-20 leagues bidding both still collapse to the same "won"/
    "outbid" representative signal, but a UI can tell them apart via these
    two counts instead of only the single collapsed signal.

    is_price=True (price pool): every row here is already a real "won" row
    (see build_price_rows) - one per league that won this event. Takes the
    MEDIAN (not the mean) of each league's own target_pct (already
    normalized to that league's own season-average spend) across the
    leagues that won it, and stores the result on the representative row as
    consolidated_target_pct, which target_pct() reads in preference to
    recomputing from a single league's effective_cost_dollars.

    Median, deliberately, NOT a plain mean - see the conversation this was
    built from. Real within-group spread across leagues for the SAME event
    is large (median max/min ratio 5.5x, p90 30x, in the pooled table), so
    one league paying wildly more or less than everyone else for the same
    player-week (different rosters/needs/remaining budgets) can drag a mean
    a long way from what most leagues actually paid - and roughly 39% of
    ALL price rows are exact $0 wins (a real, common "won it uncontested"
    outcome in FAAB leagues, not noise), so a group mixing a $0 win in one
    league with a real price in another is common, not a rare edge case.
    Backtested against a held-out raw (never-averaged) single-league actual
    cost, median beat mean everywhere: overall MAE 0.03894 vs 0.04179, and
    the gap widens specifically as more leagues go into a group (comps
    averaging 3+ leagues of backing: 0.04736 vs 0.05157) - exactly where a
    plain mean has the most outlier exposure. (A geometric mean was also
    considered and rejected: it dampens high-side outliers the same way,
    but is comparably fragile on the LOW side - a value near zero pulls it
    down disproportionately - which is a bad match for a dataset that's
    ~39% exact zeros, and would need an arbitrary floor substitution for
    those zeros that ends up deciding a large share of the results itself.)

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
            row = group[0]
            if not is_price:
                row = dict(row)
                row["leagues_with_bid"] = 0 if row["signal"] == "no_bid" else 1
                row["leagues_eligible"] = 1
            out.append(row)
            continue
        representative = dict(min(group, key=lambda r: signal_priority.get(r["signal"], 99)))
        for f in _CROSS_LEAGUE_POINTS_FEATURES:
            vals = [r[f] for r in group if r.get(f) is not None]
            representative[f] = (sum(vals) / len(vals)) if vals else None
        representative["consolidated_from_leagues"] = sorted({r["source_league_id"] for r in group})
        if is_price:
            representative["consolidated_target_pct"] = float(np.median([target_pct(r) for r in group]))
        else:
            representative["leagues_with_bid"] = len({r["source_league_id"] for r in group if r["signal"] != "no_bid"})
            representative["leagues_eligible"] = len(representative["consolidated_from_leagues"])
        o_league_row = next((r for r in group if r["source_league_id"] == O_LEAGUE_ID), None)
        representative["o_league_detail"] = (
            {"signal": o_league_row["signal"], "bid_dollars": o_league_row["bid_amount_dollars"] if o_league_row["signal"] == "won" else None}
            if o_league_row is not None
            else None
        )
        out.append(representative)
    return out


def add_synthetic_price_wins(trainable_rows: list[dict]) -> list[dict]:
    """trainable_rows PLUS one synthetic won-equivalent per (league, event)
    that had real bidding activity in that league but NO won or outbid row
    there at all (that league's own rows for this event are ALL
    "other_failure" - see dedupe_events_for_interest) - that league's
    HIGHEST other_failure bid_amount_dollars for this event, treated as its
    effective_cost_dollars (target_pct reads effective_cost_dollars, not
    bid_amount_dollars, so this is what actually makes it price like a won
    row). It never won, but it also never lost to a rival bid (that's
    "outbid", excluded from price for the censoring reason in the module
    docstring) - it's the closest thing this league had to "what someone
    was willing to pay" for this event, so on balance it's a better price
    observation than dropping this league's price signal for the event
    entirely. Exactly one synthetic row per (source_league_id, season,
    week, add_player_id) - never more, and never for a league that already
    has a real won or outbid row for that same event.

    A pure ADDITION to trainable_rows, not a filter - callers needing just
    the price-stage pool (every real+synthetic won row) should further
    filter the result to signal == "won" (see build_price_rows). Doing the
    promotion here, upstream of that filter, means every consumer of
    trainable_rows sees these rows consistently - the interest stage
    (dedupe_events_for_interest/consolidate_cross_league_events, is_price=
    False) already treated the ORIGINAL other_failure row as real "someone
    bid" interest evidence identically to a synthetic won row (both count
    as signal != no_bid - see TRAINABLE_SIGNALS), so promoting it here
    changes nothing for interest, but it DOES fix real cross-league price
    provenance: build_event_won_rows_index built from trainable_rows
    BEFORE this step ran would never see a league's own synthetic win at
    all (see that function's docstring on the resulting data loss).

    Backtested (as the price-stage won + synthetic split this produces via
    build_price_rows) against the alternative (drop these events from
    price entirely, same as an outbid row) under the same event-grouped
    holdout: a clear net win - overall MAE $7.63->$6.79, outbid-row MAE
    $50.62->$31.60, RB MAE $19.09->$14.75, no_bid MAE $2.84->$2.50 - with
    won-row MAE essentially flat ($44.46->$44.85, <1% - noise, not a real
    cost)."""
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

    return trainable_rows + synthetic


def build_price_rows(trainable_rows: list[dict]) -> list[dict]:
    """The PRICE-stage training pool: every won row - real AND synthetic
    (see add_synthetic_price_wins, which should already have run on
    trainable_rows by the time this is called - this is just the filter,
    not the promotion)."""
    return [r for r in trainable_rows if r["signal"] == "won"]


def feature_vector(r: dict) -> dict[str, float]:
    return {
        "week": r["week"],
        "prior_week_actual_points": r["prior_week_actual_points"] if r.get("prior_week_actual_points") is not None else 0.0,
        "prior_week_had_stat_row": 1.0 if r.get("prior_week_had_stat_row") else 0.0,
        "own_injury_flag": 1.0 if r.get("own_injury_status") else 0.0,
        "teammate_position_injury_flag": 1.0 if r.get("teammate_position_injury_flag") else 0.0,
        "teammate_position_injury_is_new": 1.0 if r.get("teammate_position_injury_is_new") else 0.0,
        "snap_pct_prior_week": r["snap_pct_prior_week"] if r.get("snap_pct_prior_week") is not None else 0.0,
        "had_snap_pct_prior_week": 1.0 if r.get("snap_pct_prior_week") is not None else 0.0,
        "trailing_2_3_avg_points": r["trailing_2_3_avg_points"] if r.get("trailing_2_3_avg_points") is not None else 0.0,
        "had_trailing_2_3_avg_points": 1.0 if r.get("trailing_2_3_avg_points") is not None else 0.0,
        "season_avg_points": r["season_avg_points"] if r.get("season_avg_points") is not None else 0.0,
        "had_season_avg_points": 1.0 if r.get("season_avg_points") is not None else 0.0,
        "weekly_rank": r["weekly_rank"] if r.get("weekly_rank") is not None else MISSING_RANK_SENTINEL,
        "had_weekly_rank": 1.0 if r.get("weekly_rank") is not None else 0.0,
        "ros_rank": r["ros_rank"] if r.get("ros_rank") is not None else MISSING_RANK_SENTINEL,
        "had_ros_rank": 1.0 if r.get("ros_rank") is not None else 0.0,
        "carry_share_prior_week": r["carry_share_prior_week"] if r.get("carry_share_prior_week") is not None else 0.0,
        "had_carry_share_prior_week": 1.0 if r.get("carry_share_prior_week") is not None else 0.0,
        "target_share_prior_week": r["target_share_prior_week"] if r.get("target_share_prior_week") is not None else 0.0,
        "had_target_share_prior_week": 1.0 if r.get("target_share_prior_week") is not None else 0.0,
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


def _knn(
    query: dict, pool: list[dict], stats: dict[str, tuple[float, float]], k: int, max_distance: float | None = None
) -> tuple[list[dict], list[float]]:
    """The k nearest same-position rows in pool, by standardized Euclidean
    distance, plus their raw 1/(dist+0.05) weights - the one distance
    routine both the interest and price k-NN searches share. scored is
    already sorted nearest-first (== highest-weight-first), so a caller
    displaying comps in this same order needs no separate re-sort. These
    are the SAME (unnormalized) weights callers use for the weighted-
    average bid_probability/conditional_price - see _normalized_weights for
    the display-friendly, sums-to-1 version _price_comp_dicts/
    _interest_comp_dicts expose per comp.

    max_distance, when given, drops any candidate beyond that standardized
    distance BEFORE the top-k cut - see the conversation this was built
    from (a 2026 wk2 Kirk Cousins query whose 4th-nearest comp, a 2023
    Brock Purdy row, sat meaningfully farther away than the other 9 but
    still won a large slice of the vote purely because Bühlmann credibility
    - a SEPARATE axis, see _credibility - rewarded its unusually large
    backing count; distance-based selection has to be capable of rejecting
    a comp outright, not just down-weighting it, since credibility can and
    does overrule a plain distance-decay weight on its own). Always returns
    at least one neighbor when the pool has any same-position rows at all -
    falling back to the single nearest one even when it's beyond
    max_distance - rather than an empty comp set silently making
    bid_probability/conditional_price both read as 0, which would look
    exactly like a confident "nobody would bid" instead of "no comparable
    situation was actually found." Callers that care can tell the two
    apart: a real thin-but-in-range set is len(scored) between 1 and k, a
    forced single fallback neighbor is len(scored) == 1 with its own
    distance still visible on request."""
    same_pos = [r for r in pool if r["position"] == query["position"]]

    def vec(r):
        fv = feature_vector(r)
        return np.array([(fv[f] - stats[f][0]) / stats[f][1] for f in FEATURE_NAMES])

    qv = vec(query)
    all_dists = [float(np.linalg.norm(vec(r) - qv)) for r in same_pos]
    order = sorted(range(len(same_pos)), key=lambda i: all_dists[i])
    if max_distance is not None:
        within_range = [i for i in order if all_dists[i] <= max_distance]
        order = within_range if within_range else order[:1]
    order = order[:k]
    scored = [same_pos[i] for i in order]
    weights = [1.0 / (all_dists[i] + 0.05) for i in order]
    return scored, weights


DISTANCE_CUTOFF_PERCENTILE = 70.0  # see _position_distance_cutoffs


def _position_distance_cutoffs(
    pool: list[dict], stats: dict[str, tuple[float, float]], k: int = 10, pct: float = DISTANCE_CUTOFF_PERCENTILE,
    max_sample: int = 3000, seed: int = 0,
) -> dict[str, float]:
    """Per-position max_distance for _knn's cutoff (see that function) - the
    `pct`th percentile, across a sample of this pool's own same-position
    rows, of each sampled row's OWN k-th-nearest same-position-neighbor
    distance (leave-one-out, excluding itself). Recomputed fresh from
    whatever training table FaabModel loads (see its __init__), rather than
    a hardcoded distance number, so the cutoff tracks the real, current
    density of comps instead of quietly going stale as more real bids
    accumulate week over week - the exact same reasoning _feature_stats
    already applies to the mean/std it fits fresh each time.

    max_sample caps how many of a position's own rows are used as LOO
    queries here - a full O(n^2) pass over the whole WR interest pool
    (~10k rows) is unnecessary precision for a single percentile estimate,
    and this only needs to run once per FaabModel fit (see class docstring:
    "fit once per pipeline run"), not once per live query the way _knn
    itself does.

    pct=70 chosen empirically, NOT guessed - see the conversation this was
    built from (a live 2026 wk2 Kirk Cousins query whose 4th-nearest comp, a
    2023 Brock Purdy row, took an outsized vote share purely from a large
    backing count inflating its credibility - see _credibility - despite
    sitting meaningfully farther away than the other 9 comps). Backtested
    by rebuilding this exact calculation against tools/faab_history/
    combined-training-table.json specifically - the pooled table this
    actually runs against in production (see POOLED_TRAINING_TABLE_PATH) -
    since the single-league table can't even reproduce the effect: every
    one of its rows shares leagues_eligible == 1, so credibility never
    varies there at all, and a first calibration pass against it found
    ZERO "credibility overrode distance" cases before pooling was tried.
    Against the pooled table, thousands of real holdout cases showed the
    same shape as Cousins/Purdy (a distance-rank 3-10 comp taking the
    largest single share of the vote) in both the PRICE and INTEREST pools.
    p70 is where held-out won-row conditional-price MAE (the PRICE pool -
    literally "% of budget", the number Cousins/Purdy was distorting) was
    minimized: 0.03745 vs 0.03761 uncapped, degrading again by p60/p50 -
    a real, if modest, U-shaped improvement, not just "more cutoff is
    better." The INTEREST pool's own bid_probability error kept improving
    monotonically all the way down to much more aggressive cutoffs (p5) in
    the same backtest, but that reads less like "the credibility-override
    bug is fixed" and more like the well-known artifact of a k-NN average
    creeping toward a 1-NN classifier on a rare-event (~10-14% positive)
    binary target - not a trend to chase blindly by picking whatever
    percentile minimizes that number. p70 is used for both pools
    deliberately: it's the directly-supported answer for price, and a
    comparably conservative, non-extreme choice for interest that still
    recovers a real chunk of that stage's own improvement (MAE
    0.20305->0.19761 in the same backtest) without wandering into 1-NN
    territory."""
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for r in pool:
        by_pos[r["position"]].append(r)

    def vec(r):
        fv = feature_vector(r)
        return np.array([(fv[f] - stats[f][0]) / stats[f][1] for f in FEATURE_NAMES])

    rng = np.random.RandomState(seed)
    cutoffs = {}
    for pos, rows in by_pos.items():
        if len(rows) <= k:
            continue
        vecs = np.array([vec(r) for r in rows])
        n = len(vecs)
        idxs = range(n) if n <= max_sample else rng.choice(n, size=max_sample, replace=False)
        kth_dists = []
        for i in idxs:
            d = np.linalg.norm(vecs - vecs[i], axis=1)
            d = np.delete(d, i)
            kth_dists.append(np.partition(d, k - 1)[k - 1])
        cutoffs[pos] = float(np.percentile(kth_dists, pct))
    return cutoffs


BUHLMANN_K = 15.0  # the "half-credibility" point - see _credibility


def _credibility(n: int, k: float = BUHLMANN_K) -> float:
    """Bühlmann credibility: n/(n+k), the standard actuarial shrinkage
    formula for how much a single estimate should count given how many
    independent real observations back it - see the conversation this was
    built from. Asymptotically approaches full credibility as n grows but
    never literally reaches 1.0 (unlike a hard cap at some fixed n) - even
    a comp backed by 100 leagues is still, honestly, not INFINITELY
    trustworthy. k=15 means a comp backed by exactly 15 leagues counts for
    half of what one with unlimited backing would.

    This is a SEPARATE axis from the existing distance-based k-NN weight
    (see _knn) - relevance (how similar is this situation) and credibility
    (how much real evidence backs THIS row's own reading of it) aren't the
    same thing. A comp identical in distance to two different queries
    should count the same either way; a comp backed by 1 league's single
    data point and one backed by 40 leagues' real outcomes should NOT,
    even at identical distance. Applied multiplicatively on top of the
    existing weight, never replacing it, and never used to change WHICH
    comps get selected (that stays purely distance-based, via _knn) - only
    how much each selected comp counts once chosen.

    Only ever applied to the PRICE weight in comp_based_estimate, not
    interest - see _shrunk_interest_fractions, whose own value already
    divides by this same backing count, so multiplying by n/(n+15) on top
    of it would double-count it rather than adding a genuinely separate
    axis."""
    return n / (n + k) if n > 0 else 0.0


def _backing_count(r: dict) -> int:
    """How many distinct real leagues' own data went into this row - the
    credibility signal _credibility uses. Works for both interest rows
    (leagues_eligible, set unconditionally by consolidate_cross_league_events
    for every interest-pool row - see that function) and price rows
    (consolidated_from_leagues, only set there for an actual multi-league
    group; a single-league price row never gets that field at all, so it
    defaults to 1 - just its own one league backing it)."""
    if "leagues_eligible" in r:
        return r["leagues_eligible"]
    from_leagues = r.get("consolidated_from_leagues")
    return len(from_leagues) if from_leagues else 1


def _credibility_weighted(scored: list[dict], weights: list[float]) -> list[float]:
    """weights, each multiplied by its own row's Bühlmann credibility (see
    _credibility/_backing_count) - the one place every comp_based_estimate
    caller applies this, so bid_probability/conditional_price, the
    displayed per-comp "weight" column, and price_confidence_samples (which
    is handed these same weights) all stay consistent with each other
    automatically rather than needing separate credibility logic each."""
    return [w * _credibility(_backing_count(r)) for w, r in zip(weights, scored)]


def _normalized_weights(weights: list[float]) -> list[float]:
    """weights rescaled to sum to 1 - a weighted-average result is
    invariant to this (only the relative ratios between weights matter), so
    this is purely a display transform for _price_comp_dicts/
    _interest_comp_dicts: "this comp was 18% of the vote" reads a lot more
    directly than the raw, unbounded 1/(dist+0.05) value it's derived
    from."""
    total = sum(weights)
    return [w / total for w in weights] if total else weights


INTEREST_SHRINKAGE_K = 15.0  # same magnitude as BUHLMANN_K by default - independently tunable, see _shrunk_interest_fractions


def _interest_counts(r: dict) -> tuple[float, float]:
    """(leagues_with_bid, leagues_eligible) for one interest comp - see
    consolidate_cross_league_events, which sets both unconditionally on
    every real interest_rows row, single-league or cross-league. Falls
    back to the collapsed won/outbid/no_bid signal only when
    leagues_eligible is missing (a row built directly, e.g. in a test,
    without going through that function)."""
    eligible = r.get("leagues_eligible")
    if eligible:
        return r.get("leagues_with_bid", 0), eligible
    return (1.0, 1.0) if r["signal"] != "no_bid" else (0.0, 1.0)


def _shrunk_interest_fractions(scored: list[dict]) -> list[float]:
    """This comp's own real bid RATE across every pooled league that had a
    real chance to bid on it - leagues_with_bid / leagues_eligible (see
    _interest_counts) - NOT the collapsed won/outbid/no_bid signal's binary
    "did ANY of them bid at all." A cross-league event where only 4 of 50
    pooled leagues actually bid should contribute 8% interest, not the same
    full 100% "yes" a 40-of-50 event would contribute purely because at
    least one league in each case happened to bid - see the conversation
    this was built from (a 2026 wk2 AJ Barner query whose interest comps
    table was full of single-digit bid rates, yet the aggregate
    bid_probability still came out at 66%, because every nonzero bid rate
    was being treated as a full yes vote regardless of how small it
    actually was).

    That raw rate is then Bühlmann-shrunk toward a LOCAL prior, pooled from
    every OTHER comp in this same k-NN neighborhood - not a fixed global
    constant, and not run back through _credibility_weighted (unlike
    price): that would double-count a comp's own backing count, since it's
    already priced into this rate's own denominator (see comp_based_
    estimate's own docstring for the exact math of why that mistake was
    made and reverted). A local prior fixes the real remaining problem
    without reintroducing that one: a 2-of-2 (100%) comp sitting among a
    neighborhood of mostly ~15% comps was swinging P(bid) far more than its
    tiny backing count justifies - shrinking it toward a single GLOBAL
    average would also risk the opposite mistake in a neighborhood that's
    genuinely a "usually gets bid on" region of feature space (a global
    average has no way to know that).

    LEAVE-ONE-OUT: a comp's own value never contributes to its own prior -
    otherwise an extreme rate would slightly inflate the very prior meant
    to correct it, diluting the correction exactly where it matters most.

    The prior POOLS raw counts, not a mean of ratios - sum(with_bid) /
    sum(eligible) across the other comps, so a comp backed by 50 leagues
    naturally counts 50x as much toward "what's typical here" as one backed
    by 2 (the same reasoning _backing_count/_credibility already apply on
    the price side) - a plain mean of ratios would let a handful of noisy
    small-n neighbors define the very prior meant to correct small-n noise.

    Falls back to a 0% prior only when there's no other comp in this
    neighborhood to pool at all (k==1 - _knn's own last-resort single-
    neighbor fallback for a query sitting outside interest_max_distance of
    everything else) - a real query this far from every known situation is
    itself a signal this is an unusually obscure candidate, and 0% is the
    safe assumption with zero real local evidence to lean on."""
    counts = [_interest_counts(r) for r in scored]
    total_with_bid = sum(c[0] for c in counts)
    total_eligible = sum(c[1] for c in counts)
    fractions = []
    for with_bid, eligible in counts:
        other_eligible = total_eligible - eligible
        prior = (total_with_bid - with_bid) / other_eligible if other_eligible > 0 else 0.0
        fractions.append((with_bid + INTEREST_SHRINKAGE_K * prior) / (eligible + INTEREST_SHRINKAGE_K))
    return fractions


DEFAULT_WEIGHT_CAP_RATIO = 5.0  # deliberately looser than a strict 2x - see _cap_weights


def _cap_weights(weights: list[float], max_ratio: float = DEFAULT_WEIGHT_CAP_RATIO) -> list[float]:
    """weights, clamped so the FULL RANGE never exceeds max_ratio - no comp
    ends up with more than max_ratio times ANY other comp's weight, not just
    the one immediately below it. 5x by default rather than a stricter 2x -
    real distance-based k-NN weights already span a legitimate range across
    k genuinely-different-distance neighbors on their own, before
    credibility ever enters into it; a tight 2x ceiling risks flattening
    that real distance differentiation too, not just reining in a
    credibility-driven outlier. 5x leaves normal distance spread alone while
    still catching a real Carson Steele-shaped case, where credibility alone
    (not distance) was pushing one comp far past a 5x share of the rest.
    Directly targets a single very-well-backed
    comp dominating the PRICE weighted average purely because of its own
    outsized credibility weight, even when its distance rank doesn't justify
    that much influence (see the conversation this was built from - a real
    Carson Steele-shaped case: high credibility from real backing, but that
    comp's own price is an outlier relative to its neighbors, and a plain
    weighted mean converges toward it anyway). Credibility weighting
    elsewhere in this file is doing exactly what it's designed to do; this
    is a separate, deliberately blunt guard against the specific case where
    "well-measured" and "representative of what this query should expect"
    diverge.

    An earlier version capped only the top weight against the SECOND-
    highest, then redistributed the excess proportionally across the rest -
    that let [100, 50, 5, 5] sail through untouched (100 <= 2*50), even
    though 100 is 20x the smallest weight in the set, not the "no comp over
    2x ANY other" guarantee this is actually supposed to provide. Clamping
    every weight down to min(weights) * max_ratio fixes that in one pass:
    the untouched minimum anchors the ceiling, so max/min <= max_ratio holds
    by construction - no iteration, no redistribution needed. Redistributing
    the clipped excess elsewhere isn't needed either: this is only ever fed
    into _weighted_median (scale- and shift-invariant to how much total
    weight there nominally is, as long as relative proportions among the
    comps are right) and _normalized_weights (which rescales to sum to 1 for
    display regardless of what the pre-normalization total happens to be) -
    neither cares about preserving the original sum.

    Only ever consulted by conditional_price_median - the original
    conditional_price_mean is intentionally left on the uncapped weights,
    since capping is a real behavior change some callers may not want
    applied silently to the incumbent number."""
    if len(weights) < 2:
        return list(weights)
    ceiling = min(weights) * max_ratio
    return [min(w, ceiling) for w in weights]


def _weighted_median(values: list[float], weights: list[float]) -> float:
    """The value where cumulative weight (sorted by value, ascending) first
    reaches half of the total weight - half the weighted evidence sits at
    or below it, half at or above. Far more resistant to a single
    high-weight outlier than a weighted mean: an outlier needs to command
    MORE THAN HALF the total weight on its own to move the median away from
    where the bulk of the other comps sit, whereas even a modest-weight
    outlier can drag a mean noticeably just by sitting far from the rest in
    value (see _cap_weights' own docstring for the case this is paired
    with). 0.0 for an empty input, matching comp_based_estimate's existing
    fallback when there are no price comps at all."""
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[len(pairs) // 2][0]
    half = total / 2
    cumulative = 0.0
    for value, w in pairs:
        cumulative += w
        if cumulative >= half:
            return value
    return pairs[-1][0]


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


def _price_comp_bid_distribution(r: dict, event_won_rows_index: dict[tuple, list[dict]] | None) -> list[dict]:
    """{value, source_league_id} for every pooled league's own REAL WINNING
    price tied to this comp's same real-world event (season, week,
    add_player_id) - via event_won_rows_index, the same cross-league
    expansion price_confidence_samples uses for the aggregate confidence
    slider. WINNERS ONLY, deliberately - see the module docstring: a losing
    bid is a censored observation (the true clearing price was higher, by
    an unknown margin), so plotting one on the same axis as real winning
    prices would read as "what it cost" when it's really "less than what it
    cost."

    Degrades to just this row's own win when the event was never actually
    cross-league (dollar amounts from different leagues' own budgets were
    never comparable on their own - see target_pct - this is what makes a
    real cross-league distribution meaningful at all). This row's own
    pct_of_remaining_budget is always one of the values returned, since its
    own league's win is always part of its own event. source_league_id lets
    a caller pick out "what happened in MY league" for any league, not just
    a hardcoded one - see oLeagueTag in playermodal.js for the one case
    (o_league_detail) this module still hardcodes, kept only because
    o_league_detail additionally carries the raw un-normalized bid_dollars.
    Powers the price comp row's click-to-expand view."""
    key = (r["season"], r["week"], r["add_player_id"])
    per_league_wins = (event_won_rows_index or {}).get(key) or [r]
    values: list[dict] = []
    for lr in per_league_wins:
        budget = lr.get("effective_starting_budget")
        if not budget:
            continue
        values.append({"value": lr["effective_cost_dollars"] / budget, "source_league_id": lr.get("source_league_id")})
    return sorted(values, key=lambda v: v["value"])


def _price_comp_dicts(
    scored: list[dict],
    weights: list[float],
    event_won_rows_index: dict[tuple, list[dict]] | None,
    weights_capped: list[float] | None = None,
) -> list[dict]:
    """weights is the ORIGINAL (uncapped) credibility weight behind
    conditional_price_mean; weights_capped (optional - omitted by callers
    that don't need the second method, e.g. existing tests) is the
    _cap_weights output behind conditional_price_median, exposed as its own
    "weight_capped" field only when provided, so a reader can see exactly
    how much a comp's influence changed between the two methods - most
    visible on the one comp _cap_weights actually clipped."""
    return [
        {
            "season": r["season"], "week": r["week"], "name": r["add_player_name"],
            # Same pct-of-remaining-budget transform behind the headline
            # conditional_price, per comp - this is what "% of budget" means
            # here: not a modeled confidence interval, but the real cost
            # this comp's own bidder(s) actually paid, as a % of what they
            # had to spend. Deliberately no raw $ amount alongside it - a
            # dollar figure from an unfamiliar pooled league's own budget
            # scale isn't meaningfully comparable to anything a reader
            # already knows, unlike this normalized %.
            "pct_of_remaining_budget": target_pct(r),
            "bid_distribution": _price_comp_bid_distribution(r, event_won_rows_index),
            "prior_week_actual_points": r.get("prior_week_actual_points"),
            "snap_pct_prior_week": r.get("snap_pct_prior_week"),
            # RB-position-scoped carry share and team-wide (any position)
            # target share, both as of the same prior-week reference point
            # as snap_pct_prior_week above - see recent_carry_share/
            # recent_target_share. A UI displaying these should scope carry
            # share to RB and target share to WR/TE, same as those
            # functions' own callers do; None for a comp built before this
            # field existed, or for a position where it was never computed.
            "carry_share_prior_week": r.get("carry_share_prior_week"),
            "target_share_prior_week": r.get("target_share_prior_week"),
            **_comp_rank_and_injury_fields(r),
            # See consolidate_cross_league_events - real specificity from
            # The O League itself when this comp is actually a cross-league
            # consolidated event that included it.
            "o_league_detail": r.get("o_league_detail"),
            # This comp's share (0-1, sums to 1 across every comp in this
            # list) of the total weight behind conditional_price_mean - see
            # _normalized_weights. scored is already sorted nearest-first
            # (== highest-weight-first by the UNCAPPED weight).
            "weight": w,
            **({"weight_capped": weights_capped[i]} if weights_capped is not None else {}),
        }
        for i, (r, w) in enumerate(zip(scored, weights))
    ]


def _interest_comp_dicts(scored: list[dict], weights: list[float], shrunk_fractions: list[float] | None = None) -> list[dict]:
    """shrunk_fractions (optional - omitted by callers that don't need it,
    e.g. existing tests) is _shrunk_interest_fractions' own output for these
    same comps, exposed as "shrunk_bid_fraction" only when provided - lets a
    reader see the corrected rate actually used in bid_probability
    alongside the raw leagues_with_bid/leagues_eligible counts below, most
    useful on a low-backing comp whose raw rate and shrunk rate diverge."""
    return [
        {
            "season": r["season"], "week": r["week"], "name": r["add_player_name"],
            "prior_week_actual_points": r.get("prior_week_actual_points"),
            "snap_pct_prior_week": r.get("snap_pct_prior_week"),
            # RB-position-scoped carry share and team-wide (any position)
            # target share, both as of the same prior-week reference point
            # as snap_pct_prior_week above - see recent_carry_share/
            # recent_target_share. A UI displaying these should scope carry
            # share to RB and target share to WR/TE, same as those
            # functions' own callers do; None for a comp built before this
            # field existed, or for a position where it was never computed.
            "carry_share_prior_week": r.get("carry_share_prior_week"),
            "target_share_prior_week": r.get("target_share_prior_week"),
            **_comp_rank_and_injury_fields(r),
            "o_league_detail": r.get("o_league_detail"),
            # See _price_comp_dicts's identical field - same normalized weight.
            "weight": w,
            # See consolidate_cross_league_events - of the leagues we have
            # real data for this exact (season, week, player) event (either
            # a real bid, or roster data confirming he was a free agent
            # there), how many actually saw a bid. Always present on a real
            # interest_rows row (consolidate_cross_league_events sets both
            # unconditionally); .get() only guards direct callers in tests.
            "leagues_with_bid": r.get("leagues_with_bid"),
            "leagues_eligible": r.get("leagues_eligible"),
            **({"shrunk_bid_fraction": shrunk_fractions[i]} if shrunk_fractions is not None else {}),
        }
        for i, (r, w) in enumerate(zip(scored, weights))
    ]


# ---------------------------------------------------------------------------
# 1. Comp-based nearest-neighbor
# ---------------------------------------------------------------------------
def price_confidence_samples(
    scored_price_comps: list[dict],
    price_weights: list[float],
    event_won_rows_index: dict[tuple, list[dict]] | None = None,
) -> list[tuple[float, float]]:
    """(value, weight) pairs - the raw material for a "bid $X would have
    won about N% of comparable historical auctions" confidence slider.
    Reuses _price_comp_bid_distribution's own per-comp expansion (every
    pooled league's real winning price for that comp's event - a k-NN
    neighbor that consolidate_cross_league_events collapsed into one
    representative row gets expanded back out via event_won_rows_index into
    every one of those leagues' own real winning price, not just the single
    averaged consolidated_target_pct the point estimate uses) - WINNERS
    ONLY by construction (see that function - a losing bid is never the
    real win/lose threshold for a candidate bid: the actual winner's price
    is always >= any rival's in the same auction, so a rival's amount can
    only ever UNDERSTATE what a bid needed to clear).

    Each NEIGHBOR's total weight is its own k-NN weight, split EVENLY
    across every real per-league WIN it expands into - not one full k-NN
    weight per league: a heavily-contested auction's real winning price
    shouldn't count for LESS in this pool just because more people happened
    to lose it. Same "one real event should be one vote" principle
    dedupe_events_for_interest/consolidate_cross_league_events already
    enforce elsewhere in this file for the point estimate.

    Every dollar amount is converted to %-of-budget units using ITS OWN
    row's effective_starting_budget - a real per-(league, season) constant
    every team in that league-season shares (see build_effective_budgets),
    so this is a safe, meaningful conversion even across many different
    leagues' own budgets at once."""
    samples: list[tuple[float, float]] = []
    for r, w in zip(scored_price_comps, price_weights):
        wins = [v["value"] for v in _price_comp_bid_distribution(r, event_won_rows_index)]
        if not wins:
            continue
        share = w / len(wins)
        samples.extend((v, share) for v in wins)
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
    k: int = 10,
    event_won_rows_index: dict[tuple, list[dict]] | None = None,
    interest_max_distance: float | None = None,
    price_max_distance: float | None = None,
) -> dict:
    """Two k-NN searches, one standardized distance space (fit on
    interest_rows - see module docstring for why the interest and price
    pools differ): the K nearest same-position INTEREST comps weighted-
    average their own real, Bühlmann-shrunk bid rate (by the same
    1/(dist+0.05) weighting used for price - see _shrunk_interest_fractions
    for why this is a shrunk rate, not a binary won/no_bid vote) into
    P(anyone bids); the K nearest same-position PRICE comps (won rows only)
    produce TWO parallel conditional-price reads off that SAME selected set -
    conditional_price_mean (the original credibility-weighted average) and
    conditional_price_median (a weighted median over the same comps, after
    capping any one comp's weight relative to the rest - see _cap_weights/
    _weighted_median) - both a % OF THE WINNING BIDDER'S OWN REMAINING
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

    price_confidence_samples (the confidence-slider feature) draws from
    exactly the SAME k price comps as everything else here - conditional_
    price_mean/median, the displayed comps table, all of it - one single
    k-NN search, not a wider pool computed on the side. An earlier version
    ran a separate, wider price_k search (default 25 vs this k's 10) just
    for price_confidence_samples on the theory that a tail-percentile
    estimate wants a bigger base pool than a plain weighted average needs to
    already be stable - but that meant the confidence slider's own "N real
    winning prices" count silently included real per-league wins from
    neighbors that were never actually shown as one of the displayed comps,
    which read as a real discrepancy (a live 2026 wk2 Antonio Williams query
    counted 34 there vs 20 summed from the visible comps' own expand views -
    confirmed live 2026-09-16, not a bug in the count itself, just two
    different pools quietly feeding two different UI elements). One shared
    pool means what the confidence slider counts is now exactly what a
    reader can verify by expanding every comp below - the entire point of
    this being a deliberately explainable method in the first place.

    The PRICE weight is credibility-adjusted (see _credibility_weighted)
    immediately after its k-NN call, before anything downstream ever sees
    it - selection (WHICH k rows are neighbors) stays purely distance-
    based, only how much each selected one counts changes. The INTEREST
    weight is NOT credibility-adjusted - see _shrunk_interest_fractions for
    why applying credibility on top of a rate that already divides by the
    same backing count would double-count a comp's size instead of cleanly
    complementing distance the way it does for price.

    interest_max_distance/price_max_distance (see _knn) cap how far a
    candidate may sit before it's excluded from selection entirely, ahead
    of credibility ever seeing it - separate knobs, not one shared value,
    because the price pool (won rows only) is far sparser than the interest
    pool at the same k (see the conversation this was built from: median
    10th-nearest-neighbor distance runs roughly 2-3x larger for price than
    interest at the same position), so one shared cutoff would either barely
    filter interest or gut price."""
    stats = _feature_stats(interest_rows)

    interest_scored, interest_weights = _knn(query, interest_rows, stats, k, max_distance=interest_max_distance)
    interest_fractions = _shrunk_interest_fractions(interest_scored)
    bid_probability = sum(w * f for w, f in zip(interest_weights, interest_fractions)) / sum(interest_weights)

    price_scored, price_weights = _knn(query, price_rows, stats, k, max_distance=price_max_distance)
    price_weights = _credibility_weighted(price_scored, price_weights)
    price_targets = [target_pct(r) for r in price_scored]
    conditional_pct_mean = sum(w * t for w, t in zip(price_weights, price_targets)) / sum(price_weights) if price_scored else 0.0
    price_weights_capped = _cap_weights(price_weights)
    conditional_pct_median = _weighted_median(price_targets, price_weights_capped)
    # EXPERIMENTAL, not surfaced anywhere in FaabModel.estimate()/the live UI
    # yet - see the conversation this was built from. conditional_pct_median
    # takes one median across the K comps' own already-cross-league-
    # consolidated values (each comp = one vote, however many leagues won
    # it). This is the alternative that skips that consolidation for the
    # median specifically: expand each of the SAME K comps back out to
    # every one of its own real per-league winning prices (reusing
    # price_confidence_samples' own expansion - the same "one event's
    # weight split evenly across its own real wins" the confidence slider
    # already relies on), then take ONE flat weighted median over that
    # pooled set of individual real bids instead of over K pre-collapsed
    # per-event values. Whether this is actually more accurate than
    # conditional_pct_median is an open, real empirical question - see
    # tools/faab_history/evaluate_price_median_flattening.py, which
    # backtests both against real held-out data before either one gets
    # promoted or discarded.
    conditional_pct_median_flattened = weighted_percentile(
        price_confidence_samples(price_scored, price_weights_capped, event_won_rows_index), 50
    )

    return {
        "bid_probability": bid_probability,
        "conditional_price_mean": conditional_pct_mean,
        "conditional_price_median": conditional_pct_median,
        "conditional_price_median_flattened": conditional_pct_median_flattened,
        "comps": _price_comp_dicts(
            price_scored, _normalized_weights(price_weights), event_won_rows_index, _normalized_weights(price_weights_capped)
        ),
        "interest_comps": _interest_comp_dicts(interest_scored, _normalized_weights(interest_weights), interest_fractions),
        "price_confidence_samples": price_confidence_samples(price_scored, price_weights, event_won_rows_index),
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
        # add_synthetic_price_wins runs BEFORE any other trainable_rows
        # consumer sees this data - every league's own synthetic won-
        # equivalent (the highest other_failure bid for an event with no
        # real won/outbid row in that league - see that function's
        # docstring on why this is still a real price signal, just one
        # that didn't execute) is baked in here once, so interest_rows,
        # price_rows, and event_won_rows_index below all see it
        # consistently instead of needing their own separate plumbing.
        trainable = add_synthetic_price_wins(load_trainable_rows(all_rows))
        # Every league's own WON (real or synthetic) row per real event -
        # see build_event_won_rows_index/price_confidence_samples. A league
        # whose own claim never executed (contingency, roster limit, etc.)
        # but WAS the highest real attempt still has a real bid price worth
        # keeping in this event's expansion, not silently dropped just
        # because some OTHER pooled league happened to have a genuine win
        # for the same real-world event.
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

        # Per-position max-distance cutoffs for the two comp_based_estimate
        # k-NN searches (see _position_distance_cutoffs) - computed once
        # here, from whatever training table was just loaded, rather than a
        # hardcoded number that would go stale as more real bids accumulate.
        # Uses the SAME standardized-feature space _knn itself searches in
        # (fit on interest_rows - see comp_based_estimate's own docstring on
        # why both stages share one space).
        distance_stats = _feature_stats(self.interest_rows)
        self.interest_max_distance = _position_distance_cutoffs(self.interest_rows, distance_stats)
        self.price_max_distance = _position_distance_cutoffs(self.price_rows, distance_stats)

    def estimate(self, query: dict) -> dict:
        comp = comp_based_estimate(
            query, self.interest_rows, self.price_rows,
            event_won_rows_index=self.event_won_rows_index,
            interest_max_distance=self.interest_max_distance.get(query["position"]),
            price_max_distance=self.price_max_distance.get(query["position"]),
        )
        # simple_baseline_estimate is deliberately not called here anymore -
        # dropped from the live UI (see the conversation this was built
        # from); the function itself stays for evaluate_model.py's own,
        # independent backtest machinery.
        reg = regression_estimate(query, self.price_coefs, self.interest_coefs, self.interest_stats)

        comp_values = sorted(c["pct_of_remaining_budget"] for c in comp["comps"])
        return {
            # No blended P(bid) x conditional_price product here on purpose -
            # see comp_based_estimate's docstring. conditional_price below IS
            # the headline "what to bid if you want him" number per method,
            # as a % of that (league, season)'s effective starting budget -
            # NOT a $ amount; bid_probability is separate context ("but you
            # may not need to"), never multiplied in.
            #
            # comp_based_mean/comp_based_median share the SAME bid_probability
            # value (comp["bid_probability"]) - capping/median only apply to
            # the PRICE side (see comp_based_estimate) - but each still needs
            # its own key here so the UI's per-method card lookup (keyed
            # identically into both dicts) finds a real number for both.
            "bid_probability": {
                "comp_based_mean": comp["bid_probability"], "comp_based_median": comp["bid_probability"], "regression": reg["bid_probability"],
            },
            "conditional_price": {
                "comp_based_mean": comp["conditional_price_mean"], "comp_based_median": comp["conditional_price_median"], "regression": reg["conditional_price"],
            },
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
