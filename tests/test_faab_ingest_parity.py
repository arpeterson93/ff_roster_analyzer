"""Parity test for engine.faab_estimate.load_pools (the polars-backed ingest
pipeline) against the OLD dict-based chain it replaces in FaabModel.__init__ -
see the conversation this was built from (combined-training-table.json grew
to 5.6M+ raw rows / 7.75GB, and json.loads(path.read_text()) on that OOM-
killed CI outright).

Every scenario below is duplicated through both:
  trainable = add_synthetic_price_wins(load_trainable_rows(all_rows))
  interest_old = consolidate_cross_league_events(dedupe_events_for_interest(...), is_price=False)
  price_old = consolidate_cross_league_events(build_price_rows(trainable), is_price=True)
and load_pools(parquet_path) (interest_new/price_new/event_won_new), then
compared on the fields that actually feed the model (signal,
leagues_with_bid/eligible, consolidated_from_leagues, consolidated_target_pct,
and every real feature - see FEATURE_NAMES).

NOT compared field-for-field: which SPECIFIC league's row becomes "the
representative" for an event where MULTIPLE leagues tie on interest/price
priority (e.g. two leagues both genuinely won the same real event) - this
was, even in the old code, an arbitrary pick among equally-valid real rows
(whichever happened to come first in accumulation order), and load_pools'
own docstring documents the one case worth preserving exactly (a real row
beats a synthetic one in that tie, matching the old code's structural
"synthetics always land at the very end of the pool" behavior) - see
test_price_rows_prefers_a_real_win_over_a_synthetic_one_on_ties below."""
import polars as pl
import pytest

from engine.faab_estimate import (
    add_synthetic_price_wins,
    build_event_won_rows_index,
    build_price_rows,
    consolidate_cross_league_events,
    dedupe_events_for_interest,
    load_pools,
    load_trainable_rows,
)

_BASE = {
    "type": "WAIVER", "status": "EXECUTED", "position": "RB", "gsis_id": "g42",
    "add_player_name": "Test Player", "team_id": 1, "team_that_week": "TM1",
    "bid_amount_raw": 5, "bid_amount_dollars": 5.0, "effective_cost_dollars": 0.0,
    "drop_player_ids": [], "date": 1700000000000, "contingent_variants_dropped": 0,
    "num_competing_bidders": 1, "effective_starting_budget": 100.0,
    "source_league_name": "League", "source_league_size": 12, "source_league_ppr_label": "Standard",
    "league_avg_pct_budget_remaining_at_bid": 1.0, "bidder_pct_budget_remaining_at_bid": 1.0,
    "pct_of_bidder_season_spend": 0.0, "pct_of_league_season_spend": 0.0,
    # nflverse/injury-derived - real data is identical across every league's
    # row for the same (add_player_id, week), see consolidate_cross_league_
    # events' own docstring.
    "prior_week_actual_points": 10.0, "prior_week_had_stat_row": True,
    "own_injury_status": None, "teammate_position_injury_flag": False, "teammate_position_injury_is_new": False,
    "snap_pct_prior_week": 0.5, "trailing_2_3_avg_points": 8.0, "season_avg_points": 9.0,
    "carry_share_prior_week": 0.3, "target_share_prior_week": None,
    "weekly_rank": 40.0, "ros_rank": 55.0,
}


def _row(tid, league_id, add_player_id, season, week, signal, **overrides):
    row = dict(_BASE)
    row.update(
        # A real no_bid row's transaction_id is always None - it's
        # synthetic (see build_no_bid_rows), not a real ESPN transaction.
        # Modeled here on purpose, not just f"tx{tid}" for every signal -
        # see test_no_bid_rows_survive_a_null_transaction_id below for why.
        transaction_id=(None if signal == "no_bid" else f"tx{tid}"),
        source_league_id=league_id, add_player_id=add_player_id,
        season=season, week=week, signal=signal,
    )
    row.update(overrides)
    return row


DECISION_FIELDS = {
    "signal", "leagues_with_bid", "leagues_eligible", "consolidated_from_leagues", "consolidated_target_pct",
    "position", "week", "season", "add_player_id",
    "prior_week_actual_points", "prior_week_had_stat_row", "own_injury_status",
    "teammate_position_injury_flag", "teammate_position_injury_is_new", "snap_pct_prior_week",
    "trailing_2_3_avg_points", "season_avg_points", "carry_share_prior_week", "target_share_prior_week",
    "weekly_rank", "ros_rank",
}


def _old_pools(rows):
    trainable = add_synthetic_price_wins(load_trainable_rows(rows))
    event_won = build_event_won_rows_index(trainable)
    interest = consolidate_cross_league_events(dedupe_events_for_interest([r for r in trainable if r["week"] != 1]), is_price=False)
    price = consolidate_cross_league_events(build_price_rows(trainable), is_price=True)
    return interest, price, event_won


def _new_pools(rows, tmp_path):
    path = tmp_path / "table.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return load_pools(path)


def _by_event(rows):
    d = {}
    for r in rows:
        d.setdefault((r["season"], r["week"], r["add_player_id"]), []).append(r)
    return d


def _assert_decision_fields_match(old_rows, new_rows, *, ignore_fields=frozenset()):
    fields = DECISION_FIELDS - ignore_fields
    old_by, new_by = _by_event(old_rows), _by_event(new_rows)
    assert set(old_by) == set(new_by)
    for key in old_by:
        project = lambda rows: sorted(tuple(sorted((k, v) for k, v in r.items() if k in fields)) for r in rows)
        assert project(old_by[key]) == project(new_by[key]), key


@pytest.fixture
def scenario_rows():
    """Covers: a same-league multi-bidder auction (dedupe_events_for_interest),
    a cross-league-pooled event won in two leagues (consolidate_cross_league_
    events' median + representative pick), a league with only other_failure
    activity (add_synthetic_price_wins' promotion), and a genuine no_bid week
    (a real free-agent snapshot with zero interest)."""
    rows = []
    # Event A: week 2, player 1 - one league (10), three bidders (won/outbid/outbid).
    rows += [
        _row(1, 10, 1001, 2024, 2, "won", team_id=1, bid_amount_dollars=8.0, effective_cost_dollars=8.0),
        _row(2, 10, 1001, 2024, 2, "outbid", team_id=2, bid_amount_dollars=5.0),
        _row(3, 10, 1001, 2024, 2, "outbid", team_id=3, bid_amount_dollars=3.0),
    ]
    # Event B: week 3, player 2 - won in TWO leagues (11 and 12) - cross-league median.
    rows += [
        _row(4, 11, 1002, 2024, 3, "won", bid_amount_dollars=10.0, effective_cost_dollars=10.0, effective_starting_budget=100.0),
        _row(5, 12, 1002, 2024, 3, "won", bid_amount_dollars=40.0, effective_cost_dollars=40.0, effective_starting_budget=100.0),
    ]
    # Event C: week 3, player 3 - league 13 has ONLY an other_failure row (no
    # won/outbid) - add_synthetic_price_wins should promote it.
    rows += [
        _row(6, 13, 1003, 2024, 3, "other_failure", bid_amount_dollars=6.0),
    ]
    # Event D: week 4, player 4 - a real no_bid (genuine free-agent snapshot).
    rows += [
        _row(7, 10, 1004, 2024, 4, "no_bid", bid_amount_dollars=0.0),
    ]
    return rows


def test_interest_rows_decision_fields_match(scenario_rows, tmp_path):
    interest_old, _, _ = _old_pools(scenario_rows)
    interest_new, _, _ = _new_pools(scenario_rows, tmp_path)
    # consolidated_from_leagues is deliberately excluded here -
    # _consolidate_interest_vectorized sets it unconditionally (even for a
    # single-league group, where the old dict-based consolidate_cross_
    # league_events never attaches it at all), verified safe because the
    # only interest-pool reader (_backing_count) checks "leagues_eligible"
    # in r FIRST, which is always set for interest rows regardless - see
    # test_interest_consolidated_from_leagues_is_always_present below for
    # the actual new-vs-old behavior this test intentionally doesn't check.
    _assert_decision_fields_match(interest_old, interest_new, ignore_fields={"consolidated_from_leagues"})


def test_interest_consolidated_from_leagues_is_always_present(scenario_rows, tmp_path):
    # Documents the one deliberate structural difference from the old
    # dict-based consolidate_cross_league_events (is_price=False): the
    # vectorized interest consolidation sets consolidated_from_leagues
    # unconditionally, even for a single-league event, where the old
    # function never attached the key at all. Confirmed harmless for
    # interest rows specifically - see _backing_count's own docstring.
    interest_new, _, _ = _new_pools(scenario_rows, tmp_path)
    assert all("consolidated_from_leagues" in r for r in interest_new)
    single_league_row = next(r for r in interest_new if r["add_player_id"] == 1001)  # Event A, one league
    assert single_league_row["consolidated_from_leagues"] == [10]


def test_price_rows_decision_fields_match(scenario_rows, tmp_path):
    _, price_old, _ = _old_pools(scenario_rows)
    _, price_new, _ = _new_pools(scenario_rows, tmp_path)
    _assert_decision_fields_match(price_old, price_new)


def test_event_won_rows_index_keys_match(scenario_rows, tmp_path):
    _, _, event_won_old = _old_pools(scenario_rows)
    _, _, event_won_new = _new_pools(scenario_rows, tmp_path)
    assert set(event_won_old.keys()) == set(event_won_new.keys())
    for key in event_won_old:
        old_amounts = sorted(r["bid_amount_dollars"] for r in event_won_old[key])
        new_amounts = sorted(r["bid_amount_dollars"] for r in event_won_new[key])
        assert old_amounts == new_amounts, key


def test_synthetic_win_promoted_from_other_failure_only_league(scenario_rows, tmp_path):
    # Event C (player 1003) has no real won/outbid row anywhere - only a
    # synthetic one, promoted from its other_failure bid.
    _, price_new, _ = _new_pools(scenario_rows, tmp_path)
    matches = [r for r in price_new if r["add_player_id"] == 1003]
    assert len(matches) == 1
    assert matches[0]["signal"] == "won"
    assert matches[0]["effective_cost_dollars"] == 6.0


def test_no_bid_rows_survive_a_null_transaction_id(scenario_rows, tmp_path):
    # Regression test: load_pools' native filter used to do
    # ~pl.col("transaction_id").is_in([...]) directly - polars' is_in()
    # returns null (not False) for a null input, so ~null is also null,
    # and .filter() drops any row whose predicate isn't exactly True. Since
    # EVERY real no_bid row has transaction_id=None (see _row above), this
    # silently dropped the ENTIRE no_bid population from interest_rows -
    # collapsing leagues_with_bid/leagues_eligible to the same number on
    # every single comp (100% "bid rate" shown for literally everything,
    # confirmed live against the real ~5.6M-row table: exactly 0 of 18,043
    # interest_rows had signal=="no_bid" before the fix, 19,699 of 37,742
    # after). Needs the fix in load_pools' filter (.fill_null(False) after
    # is_in()), not just this fixture change - see that function's own
    # comment on the exact line.
    interest_new, _, _ = _new_pools(scenario_rows, tmp_path)
    no_bid_rows = [r for r in interest_new if r["signal"] == "no_bid"]
    assert len(no_bid_rows) == 1
    assert no_bid_rows[0]["add_player_id"] == 1004


def test_price_rows_prefers_a_real_win_over_a_synthetic_one_on_ties(tmp_path):
    # Same real event, two leagues: league 20 has a REAL won row; league 21
    # has only an other_failure row (promoted to a synthetic won). Both tie
    # on signal priority for the representative pick - the old dict-based
    # pipeline's structural quirk (add_synthetic_price_wins appends every
    # synthetic row to the very END of the whole pool) means a real win
    # always wins that tie; load_pools reproduces it explicitly via
    # _IS_SYNTHETIC (see its own docstring) rather than relying on incidental
    # list order, so this must keep holding regardless of how the two
    # leagues' rows happen to be ordered in the source file.
    rows = [
        _row(1, 20, 1005, 2024, 5, "won", bid_amount_dollars=12.0, effective_cost_dollars=12.0),
        _row(2, 21, 1005, 2024, 5, "other_failure", bid_amount_dollars=7.0),
    ]
    _, price_old, _ = _old_pools(rows)
    _, price_new, _ = _new_pools(rows, tmp_path)
    assert len(price_old) == len(price_new) == 1
    assert price_old[0]["source_league_id"] == price_new[0]["source_league_id"] == 20
