import pytest

from engine.faab_estimate import (
    build_event_won_rows_index,
    consolidate_cross_league_events,
    price_confidence_samples,
    target_pct,
    weighted_percentile,
)


def test_target_pct_is_pct_of_effective_starting_budget():
    # bid / effective_starting_budget - a FIXED number known before the
    # season starts, not the bidder's own shrinking remaining balance and
    # not a season-end average (see the conversation this was built from).
    row = {"effective_cost_dollars": 8.0, "effective_starting_budget": 40.0}
    assert target_pct(row) == pytest.approx(0.2)


def test_target_pct_zero_without_effective_starting_budget():
    # A row from an older table generation (pre-dating this field) or one
    # where the budget genuinely couldn't be determined - degrades to $0
    # rather than raising.
    row = {"effective_cost_dollars": 8.0}
    assert target_pct(row) == 0.0


def test_target_pct_zero_for_no_bid_and_outbid():
    no_bid = {"effective_cost_dollars": 0.0, "effective_starting_budget": 40.0}
    outbid = {"effective_cost_dollars": 0.0, "effective_starting_budget": 40.0}
    assert target_pct(no_bid) == 0.0
    assert target_pct(outbid) == 0.0  # a losing bid never actually drew down the budget


def test_target_pct_uses_consolidated_value_when_present():
    # consolidate_cross_league_events stores an already-averaged value here -
    # target_pct must prefer it over recomputing from a single league's
    # effective_cost_dollars.
    row = {"effective_cost_dollars": 999.0, "effective_starting_budget": 40.0, "consolidated_target_pct": 0.15}
    assert target_pct(row) == pytest.approx(0.15)


def test_target_pct_uses_each_leagues_own_effective_budget_not_a_shared_one():
    # Two different leagues' bids in the same season must not share one
    # budget - each league's own effective_starting_budget applies to its
    # own rows only (this field is set per-row by build_training_table.py,
    # already keyed by (league, season) there).
    row_league_1 = {"effective_cost_dollars": 8.0, "effective_starting_budget": 40.0}
    row_league_2 = {"effective_cost_dollars": 8.0, "effective_starting_budget": 100.0}
    assert target_pct(row_league_1) == pytest.approx(0.2)
    assert target_pct(row_league_2) == pytest.approx(0.08)


def _bid_row(league_id, **overrides):
    row = {
        "source_league_id": league_id, "season": 2022, "week": 5, "add_player_id": 42, "add_player_name": "Test Player",
        "signal": "won", "type": "WAIVER", "position": "RB",
        "prior_week_actual_points": 10.0, "trailing_2_3_avg_points": 8.0, "season_avg_points": 9.0,
        "snap_pct_prior_week": 0.5, "own_injury_status": None, "teammate_position_injury_flag": False,
        "prior_week_had_stat_row": True, "week": 5,
        "bid_amount_dollars": 5.0, "effective_cost_dollars": 5.0, "effective_starting_budget": 40.0,
    }
    row.update(overrides)
    return row


def test_consolidate_cross_league_events_passes_through_single_league_events():
    rows = [_bid_row(1), _bid_row(2, week=6)]  # different events (different weeks) - no collapsing
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 2


def test_consolidate_cross_league_events_averages_price_across_leagues():
    # Same real event (season, week, player) won in two different leagues,
    # at different dollar amounts and different effective budgets - must
    # collapse to ONE row whose consolidated_target_pct is the average of
    # each league's own target_pct, not either league's raw dollars.
    rows = [
        _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0),  # target_pct = 10/40 = 0.25
        _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0),  # target_pct = 6/20 = 0.30
    ]
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 1
    assert target_pct(out[0]) == pytest.approx((0.25 + 0.30) / 2)
    assert out[0]["consolidated_from_leagues"] == [1, 2]


def test_consolidate_cross_league_events_averages_points_features():
    rows = [
        _bid_row(1, prior_week_actual_points=10.0, trailing_2_3_avg_points=8.0, season_avg_points=9.0),
        _bid_row(2, prior_week_actual_points=14.0, trailing_2_3_avg_points=10.0, season_avg_points=11.0),
    ]
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 1
    assert out[0]["prior_week_actual_points"] == pytest.approx(12.0)
    assert out[0]["trailing_2_3_avg_points"] == pytest.approx(9.0)
    assert out[0]["season_avg_points"] == pytest.approx(10.0)


def test_consolidate_cross_league_events_interest_prefers_won_over_no_bid():
    # One league saw real interest, the other saw none - the broader market
    # DID show interest, so the consolidated row must not read as no_bid.
    rows = [
        _bid_row(1, signal="won"),
        _bid_row(2, signal="no_bid", type="NONE", team_id=None, bid_amount_dollars=0.0, effective_cost_dollars=0.0),
    ]
    out = consolidate_cross_league_events(rows, is_price=False)
    assert len(out) == 1
    assert out[0]["signal"] == "won"


def test_consolidate_cross_league_events_attaches_o_league_detail():
    O_LEAGUE_ID = 355398
    rows = [
        _bid_row(O_LEAGUE_ID, signal="won", bid_amount_dollars=8.5),
        _bid_row(2, signal="won", bid_amount_dollars=3.0),
    ]
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 1
    assert out[0]["o_league_detail"] == {"signal": "won", "bid_dollars": 8.5}


def test_consolidate_cross_league_events_no_o_league_detail_when_o_league_not_involved():
    rows = [_bid_row(2, signal="won"), _bid_row(3, signal="won")]
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 1
    assert out[0]["o_league_detail"] is None


def test_consolidate_cross_league_events_no_o_league_detail_for_single_league_event():
    # Not cross-league at all - the row already IS a direct O-League record,
    # nothing extra to attach.
    O_LEAGUE_ID = 355398
    out = consolidate_cross_league_events([_bid_row(O_LEAGUE_ID)], is_price=True)
    assert len(out) == 1
    assert "o_league_detail" not in out[0]


def test_weighted_percentile_basic():
    samples = [(1.0, 1.0), (2.0, 1.0), (3.0, 1.0), (4.0, 1.0)]
    assert weighted_percentile(samples, 0) == 1.0
    assert weighted_percentile(samples, 100) == 4.0
    # 50th percentile with 4 equally-weighted points and this function's
    # "first value whose cumulative weight reaches the target" convention
    # (not numpy's own interpolation scheme) lands on the 2nd point.
    assert weighted_percentile(samples, 50) == 2.0


def test_weighted_percentile_empty_is_zero():
    assert weighted_percentile([], 50) == 0.0


def test_price_confidence_samples_converts_losing_bids_to_pct_of_budget():
    # One auction: won for $10 of a $40 budget (target_pct=0.25); a real
    # rival bid $6 (never a winner, but a real data point) - 6/40 = 0.15.
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner_team")
    index = {(1, 2022, 5, 42): [{"team_id": "rival_team", "bid_dollars": 6.0}]}
    samples = price_confidence_samples([row], [2.0], index)
    values = sorted(v for v, _ in samples)
    assert values == pytest.approx([0.15, 0.25])


def test_price_confidence_samples_one_auction_one_vote():
    # Two auctions with the SAME k-NN weight - one contested (winner + 3
    # real losing bids), one uncontested (winner only). Per the "one real
    # event, one vote" principle this file already applies elsewhere
    # (dedupe_events_for_interest/consolidate_cross_league_events), each
    # auction's TOTAL weight in the pool must be equal (2.0 each) even
    # though the contested one contributes 4 rows to the uncontested one's 1.
    contested = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner_1")
    uncontested = _bid_row(2, add_player_id=99, effective_cost_dollars=8.0, effective_starting_budget=40.0, team_id="winner_2")
    index = {
        (1, 2022, 5, 42): [
            {"team_id": "rival_a", "bid_dollars": 9.0},
            {"team_id": "rival_b", "bid_dollars": 7.0},
            {"team_id": "rival_c", "bid_dollars": 5.0},
        ],
    }
    samples = price_confidence_samples([contested, uncontested], [2.0, 2.0], index)
    contested_total_weight = sum(w for v, w in samples if v != pytest.approx(8.0 / 40.0))
    uncontested_total_weight = sum(w for v, w in samples if v == pytest.approx(8.0 / 40.0))
    assert contested_total_weight == pytest.approx(2.0)
    assert uncontested_total_weight == pytest.approx(2.0)
    assert len(samples) == 5  # 1 winner + 3 rivals from the contested auction, 1 winner from the other


def test_build_event_won_rows_index_groups_across_leagues():
    # Same real event (season, week, player), won in 3 different leagues -
    # must all land under one key, regardless of source_league_id.
    rows = [_bid_row(1, effective_cost_dollars=5.0), _bid_row(2, effective_cost_dollars=8.0), _bid_row(3, effective_cost_dollars=3.0)]
    index = build_event_won_rows_index(rows)
    assert len(index[(2022, 5, 42)]) == 3


def test_price_confidence_samples_expands_consolidated_event_across_leagues():
    # The k-NN search only ever sees ONE representative row for a
    # cross-league consolidated event (see consolidate_cross_league_events)
    # - but the confidence distribution should still see EVERY league's own
    # real outcome for that same event, via event_won_rows_index, each with
    # its own budget and its own real rivals.
    league_a = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a_winner")  # 0.25
    league_b = _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0, team_id="b_winner")  # 0.30
    representative = dict(league_a)  # what consolidate_cross_league_events would hand the k-NN search
    event_won_rows_index = {(2022, 5, 42): [league_a, league_b]}
    competing_bids_index = {(2, 2022, 5, 42): [{"team_id": "b_rival", "bid_dollars": 4.0}]}  # 4/20 = 0.20, league B only

    samples = price_confidence_samples([representative], [3.0], competing_bids_index, event_won_rows_index)
    values = sorted(round(v, 4) for v, _ in samples)
    assert values == [0.2, 0.25, 0.3]  # league B's rival, league A's win, league B's win
    # One real event, one vote: all 3 expanded samples still share the
    # representative's single k-NN weight (3.0), split evenly.
    assert sum(w for _, w in samples) == pytest.approx(3.0)
    assert all(w == pytest.approx(1.0) for _, w in samples)


def test_price_confidence_samples_falls_back_without_event_won_rows_index():
    # Omitting event_won_rows_index (e.g. an older caller) must reproduce
    # the original single-league behavior exactly, not silently drop data.
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    samples = price_confidence_samples([row], [2.0], None)
    assert samples == [(0.25, 2.0)]
