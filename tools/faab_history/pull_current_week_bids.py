"""Pulls THIS WEEK's real FAAB bids from every currently-FAAB-eligible
pooled public league, for the same-week cross-league signal shown in the
FAAB Lab modal tab (engine/faab_estimate.py's FaabModel.same_week_signal) -
see the conversation this was built from.

Deliberately NOT a scaled-down repeat of pull_public_league_bids.py's
historical backfill: that pulls up to 17 weeks x however many seasons, for
every league ever compatible. This pulls ONE week (the current one) for
whichever leagues are STILL FAAB-eligible right now - O(leagues) once a
week, not O(leagues x seasons x weeks). Most pooled leagues run their own
waivers Tuesday night/Wednesday morning - a day before The O League's own
Wednesday-night run - so a Wednesday-morning pull sees real, settled
outcomes (won/outbid/other_failure) for the exact real-world events The O
League is about to decide on itself.

Re-verifies FAAB eligibility live, every run, rather than trusting
vetted_candidates.json's cached uses_faab - a league's budget/scoring can
drift year to year, and last season's compatibility doesn't guarantee this
one's. The SAME settings fetch also returns the real current acquisition_
budget needed for %-of-budget normalization, so this isn't a separate cost
on top of the eligibility check.

Writes tools/faab_history/current-week-bids.json - a full overwrite every
run (no resumability/append bookkeeping, unlike the historical puller: this
is small - a few hundred to low thousands of rows - and cheap enough to
just rebuild from scratch each time, and is deliberately CURRENT-WEEK-ONLY,
not an accumulating dataset - see FaabModel's own docstring on why. Small
enough to commit straight into git rather than needing the release-asset +
gzip dance combined-training-table.parquet requires.

Usage:
    python -m tools.faab_history.pull_current_week_bids
    python -m tools.faab_history.pull_current_week_bids --week 4  # override
        auto-detection (testing, or catching up a missed run)
    python -m tools.faab_history.pull_current_week_bids --limit 5  # only the
        first N leagues - a fast dry run against real ESPN data before a
        full ~530-league run
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from espn_api.football import League as EspnLeague

from engine.faab_estimate import KNOWN_BAD_BID_TRANSACTION_IDS, load_trainable_rows
from engine.scoring import ScoringRules
from ingest import nfl_data as nd
from ingest.ids import build_id_map
from tools.faab_history.atomic_json import write_json
from tools.faab_history.build_training_table import (
    O_LEAGUE_ID,
    build_gsis_to_pfr_map,
    build_rank_index,
    build_rows_for_source,
    build_season_index,
)
from tools.faab_history.league_profile import fetch_settings, load_baseline_scoring_items, profile_settings
from tools.faab_history.pull_o_league_bids import classify, collapse_contingent_bids, fetch_week

VETTED_PATH = Path(__file__).parent / "vetted_candidates.json"
OUT_PATH = Path(__file__).parent / "current-week-bids.json"
SEASON = 2026

SETTINGS_DELAY_RANGE = (0.3, 0.9)
FETCH_DELAY_RANGE = (0.6, 1.8)
LONG_PAUSE_EVERY = 60
LONG_PAUSE_RANGE = (15.0, 45.0)


def _detect_weeks() -> tuple[int, int]:
    """Two DIFFERENT "current week" numbers, both genuinely needed - using
    just one of them (what the very first live run did) means either the
    ESPN fetch below returns nothing, or the output never matches what
    FaabModel.same_week_signal queries with. Confirmed empirically (not just
    reasoned) against The O League's own live endpoint on a Wednesday
    morning: querying mTransactions2 with scoringPeriodId=league.current_week
    (3) returned ZERO rows, while scoringPeriodId=week_for_kickoff's value
    (2) returned that morning's real Tuesday-night waiver activity.

    fetch_scoring_period: what to actually send ESPN as scoringPeriodId to
    get real transactions back. ESPN keeps logging transactions under the
    PRIOR week's scoring period until the next week's games actually kick
    off, even though league.current_week (a forward-looking, roster-
    management-UI concept) has already advanced - so this needs
    week_for_kickoff's backward-looking "which week has actually started"
    answer, not league.current_week.

    label_week: what to stamp on each output row's "week" field, so
    FaabModel.same_week_signal's exact (gsis_id, week) lookup can ever match
    - engine/pipeline.py queries it with _faab_week_override's result, so
    this replicates that same function (current_week + 1 only once
    current_week's own games have started) rather than importing all of
    engine.pipeline just for one line.
    """
    schedules_df = nd.schedules(SEASON, current_season=SEASON)
    week_started = nd.week_for_kickoff(datetime.now(timezone.utc), schedules_df, SEASON)
    if week_started is None:
        sys.exit(f"couldn't determine the current week for {SEASON} - pass --week explicitly")
    current_week = EspnLeague(league_id=O_LEAGUE_ID, year=SEASON).current_week
    label_week = current_week + 1 if week_started == current_week else current_week
    return week_started, label_week


def _eligible_leagues(limit: int | None) -> list[dict]:
    """{league_id, name} for every vetted_candidates.json league marked
    compatible at vetting time, minus The O League itself (circular - this
    pull exists to inform The O League's own upcoming bids, not include
    them) - re-verified for real 2026 FAAB eligibility by the caller, not
    trusted here. limit truncates the list (not a random sample) purely for
    a fast --limit dry run against real leagues, in the same discovered
    order every time."""
    if not VETTED_PATH.exists():
        sys.exit(f"{VETTED_PATH} not found - run vet_candidates.py first")
    candidates = [c for c in json.loads(VETTED_PATH.read_text()) if c.get("compatible") and c["league_id"] != O_LEAGUE_ID]
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def _verify_and_fetch_budgets(candidates: list[dict]) -> dict[int, dict]:
    """{league_id: {name, size, ppr_label, acquisition_budget}} for every
    candidate CONFIRMED live, right now, to still use FAAB - see module
    docstring on why this is a live re-check every run, not a trust of the
    cached vetted_candidates.json flag. A league that fails (private now,
    folded, settings shape changed) is dropped with a warning, never
    crashes the whole run."""
    result: dict[int, dict] = {}
    for i, cand in enumerate(candidates):
        lid = cand["league_id"]
        status, raw_settings = fetch_settings(lid, SEASON)
        if status != "ok":
            print(f"  [{i + 1}/{len(candidates)}] league {lid} ({cand['name']!r}): {status}, skipping", file=sys.stderr)
        else:
            profile = profile_settings(raw_settings)
            if not profile["uses_faab"]:
                print(f"  [{i + 1}/{len(candidates)}] league {lid} ({cand['name']!r}): no longer uses FAAB, skipping", file=sys.stderr)
            else:
                result[lid] = {
                    "name": profile["name"], "size": profile["size"],
                    "ppr_label": profile["ppr_label"], "acquisition_budget": profile["acquisition_budget"],
                }
        time.sleep(random.uniform(*SETTINGS_DELAY_RANGE))
    return result


def _annotate_budget_remaining(rows: list[dict], league_season_budgets: dict[tuple, float]) -> None:
    """Current-week-only twin of build_training_table.annotate_budget_
    remaining - that function's own "cumulative spend through the prior
    week, across the whole season's rows" logic needs full-season history
    this script deliberately doesn't have (see module docstring: this is
    ONE week, not an accumulating dataset), so bidder_pct_budget_remaining_
    at_bid/league_avg_pct_budget_remaining_at_bid (in-season scarcity
    signals) aren't meaningful here and are left out entirely. Only sets
    effective_starting_budget - the one field target_pct() actually needs -
    straight from the real acquisition_budget just fetched."""
    for r in rows:
        r["effective_starting_budget"] = league_season_budgets.get((r["source_league_id"], r["season"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", type=int, default=None, help="override auto-detected current week")
    parser.add_argument("--limit", type=int, default=None, help="only pull the first N eligible leagues (fast dry run)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    # --week is an explicit manual override (testing, or catching up a
    # missed run) - trusted as both the ESPN fetch param and the output
    # label, unlike the auto-detected pair below which deliberately differ
    # (see _detect_weeks).
    fetch_week_num, label_week = (args.week, args.week) if args.week is not None else _detect_weeks()
    print(f"pulling season {SEASON} scoringPeriodId {fetch_week_num}, labeling rows week {label_week}...")

    candidates = _eligible_leagues(args.limit)
    print(f"{len(candidates)} candidate leagues from vetted_candidates.json - re-verifying live FAAB eligibility...")
    budgets = _verify_and_fetch_budgets(candidates)
    print(f"{len(budgets)}/{len(candidates)} still FAAB-eligible for {SEASON}")
    if not budgets:
        write_json(OUT_PATH, [], indent=2)
        print(f"no eligible leagues - wrote empty {OUT_PATH}")
        return 0

    raw_rows: list[dict] = []
    weeks_requested = 0
    for i, (lid, provenance) in enumerate(budgets.items()):
        try:
            league = EspnLeague(league_id=lid, year=SEASON)
        except Exception as exc:
            print(f"  [{i + 1}/{len(budgets)}] league {lid} ({provenance['name']!r}): couldn't open ({exc}), skipping", file=sys.stderr)
            continue
        week_rows = fetch_week(league, SEASON, fetch_week_num, cents_scale=False)
        for r in week_rows:
            r["source_league_id"] = lid
            # fetch_week stamps "week" with the scoringPeriodId it queried
            # ESPN for - relabel to label_week (see _detect_weeks) so
            # FaabModel.same_week_signal's (gsis_id, week) lookup matches
            # what engine/pipeline.py actually queries with.
            r["week"] = label_week
        raw_rows.extend(week_rows)
        print(f"  [{i + 1}/{len(budgets)}] league {lid} ({provenance['name']!r}): {len(week_rows)} raw transaction rows")

        weeks_requested += 1
        if weeks_requested % LONG_PAUSE_EVERY == 0:
            pause = random.uniform(*LONG_PAUSE_RANGE)
            print(f"  taking a longer break ({pause:.0f}s)", file=sys.stderr)
            time.sleep(pause)
        else:
            time.sleep(random.uniform(*FETCH_DELAY_RANGE))

    print(f"\n{len(raw_rows)} total raw transaction rows across {len({r['source_league_id'] for r in raw_rows})} leagues")

    deduped = collapse_contingent_bids(raw_rows)
    classified = classify(deduped, freeagent_flat_cost_dollars=0.0)
    _annotate_budget_remaining(classified, {(lid, SEASON): p["acquisition_budget"] for lid, p in budgets.items()})

    print("building enrichment inputs (nflverse join + shared baseline scoring)...")
    idmap = build_id_map()
    gsis_to_pfr = build_gsis_to_pfr_map()
    baseline_scoring = ScoringRules.from_espn(load_baseline_scoring_items())
    # ingest.nfl_data's own cached wrappers, not nflreadpy directly (what
    # this used before) - nflreadpy's own cache is in-memory-only and gone
    # the moment this process exits, so every run re-downloaded the same
    # multi-season parquet files from GitHub's release-assets CDN from
    # scratch. That download is exactly what a transient CDN timeout took
    # down a full ~30-minute, 500+-league ESPN pull with on 2026-09-24 (see
    # the conversation this was built from) - the CDN fetch itself is slow
    # and occasionally flaky regardless, but there's no reason to pay for it
    # more than once when build.yml's own daily run already populated
    # .cache/nflverse (see that workflow's own actions/cache step, and this
    # one's matching step added alongside this change).
    season_index_by_season = {
        SEASON: build_season_index(
            nd.player_stats([SEASON], current_season=SEASON), nd.injuries([SEASON], current_season=SEASON),
            nd.snap_counts([SEASON], current_season=SEASON), nd.rosters_weekly([SEASON], current_season=SEASON),
        )
    }
    rank_index = build_rank_index([SEASON])
    bidder_counts: dict[tuple, int] = defaultdict(int)
    for r in classified:
        if r["type"] in ("WAIVER", "WAIVER_ERROR"):
            bidder_counts[(r["source_league_id"], r["season"], r["week"], r["add_player_id"])] += 1
    team_season_spend: dict[tuple, float] = defaultdict(float)
    league_season_spend: dict[tuple, float] = defaultdict(float)
    for r in classified:
        team_season_spend[(r["source_league_id"], r["season"], r["team_id"])] += r["effective_cost_dollars"]
        league_season_spend[(r["source_league_id"], r["season"])] += r["effective_cost_dollars"]

    all_out_rows: list[dict] = []
    for lid, provenance in budgets.items():
        bids_group = [r for r in classified if r["source_league_id"] == lid and r["transaction_id"] not in KNOWN_BAD_BID_TRANSACTION_IDS]
        if not bids_group:
            continue
        rows, unresolved = build_rows_for_source(
            bids=bids_group, provenance=provenance, idmap=idmap, gsis_to_pfr=gsis_to_pfr,
            scoring=baseline_scoring, season_index_by_season=season_index_by_season,
            bidder_counts=dict(bidder_counts), team_season_spend=dict(team_season_spend),
            league_season_spend=dict(league_season_spend), rank_index=rank_index,
        )
        # Same trainable-population filter the historical table applies
        # (signal in TRAINABLE_SIGNALS, position in POSITIONS, real bid not
        # an uncontested FREEAGENT add, not a known-bad transaction id) -
        # see engine/faab_estimate.py's load_trainable_rows. signal is
        # always won/outbid/other_failure here (this script never
        # synthesizes no_bid rows - no roster pull, see module docstring),
        # so that part is a no-op; the type != FREEAGENT and position
        # checks are the ones that actually matter.
        all_out_rows.extend(load_trainable_rows(rows))
        if unresolved:
            print(f"  league {lid}: {len(unresolved)} unresolved ESPN player ids", file=sys.stderr)

    write_json(OUT_PATH, all_out_rows, indent=2)
    print(f"\nwrote {len(all_out_rows)} rows to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
