import pytest

from engine.faab_estimate import (
    _backing_count,
    _credibility,
    _credibility_weighted,
    _interest_comp_dicts,
    _normalized_weights,
    _price_comp_bid_distribution,
    _price_comp_dicts,
    add_synthetic_price_wins,
    build_event_won_rows_index,
    build_price_rows,
    comp_based_estimate,
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


def test_consolidate_cross_league_events_counts_leagues_with_bid_vs_eligible():
    # 1-of-3 leagues actually bid; the other 2 have real no_bid rows (i.e.
    # roster data confirms he was a genuine free agent there too) - the
    # collapsed signal alone can't tell 1-of-3 apart from 2-of-3, but these
    # two counts must.
    rows = [
        _bid_row(1, signal="won"),
        _bid_row(2, signal="no_bid", type="NONE", team_id=None, bid_amount_dollars=0.0, effective_cost_dollars=0.0),
        _bid_row(3, signal="no_bid", type="NONE", team_id=None, bid_amount_dollars=0.0, effective_cost_dollars=0.0),
    ]
    out = consolidate_cross_league_events(rows, is_price=False)
    assert len(out) == 1
    assert out[0]["leagues_with_bid"] == 1
    assert out[0]["leagues_eligible"] == 3


def test_consolidate_cross_league_events_leagues_with_bid_for_single_league_event():
    # Not cross-league at all, but the counts must still be populated - one
    # league total, and it either bid or it didn't.
    won_out = consolidate_cross_league_events([_bid_row(1, signal="won")], is_price=False)
    assert won_out[0]["leagues_with_bid"] == 1
    assert won_out[0]["leagues_eligible"] == 1

    no_bid_out = consolidate_cross_league_events(
        [_bid_row(1, signal="no_bid", type="NONE", team_id=None, bid_amount_dollars=0.0, effective_cost_dollars=0.0)], is_price=False
    )
    assert no_bid_out[0]["leagues_with_bid"] == 0
    assert no_bid_out[0]["leagues_eligible"] == 1


def test_consolidate_cross_league_events_no_leagues_with_bid_field_for_price_pool():
    # Price rows are win-only by construction (build_price_rows) - the
    # bid-vs-eligible distinction only means something for the interest pool.
    out = consolidate_cross_league_events([_bid_row(1, signal="won"), _bid_row(2, signal="won")], is_price=True)
    assert "leagues_with_bid" not in out[0]
    assert "leagues_eligible" not in out[0]


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


def test_price_confidence_samples_excludes_losing_rival_bids():
    # One auction: won for $10 of a $40 budget (target_pct=0.25); a real
    # rival bid $6 (lost, so never the real win/lose threshold - see
    # price_confidence_samples' docstring) must NOT appear in the pool.
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner_team")
    index = {(1, 2022, 5, 42): [{"team_id": "rival_team", "bid_dollars": 6.0}]}
    samples = price_confidence_samples([row], [2.0], index)
    assert samples == pytest.approx([(0.25, 2.0)])


def test_price_confidence_samples_one_auction_one_vote_regardless_of_rival_count():
    # Two auctions with the SAME k-NN weight - one contested (winner + 3
    # real losing bids), one uncontested (winner only). Since only the real
    # winning price counts now, both contribute exactly ONE sample each,
    # both carrying their neighbor's FULL weight, undiluted by how many
    # rivals happened to also bid - a heavily-contested auction's real
    # clearing price shouldn't count for less just because more people lost
    # it.
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
    assert sorted(samples) == pytest.approx([(0.2, 2.0), (0.25, 2.0)])


def test_build_event_won_rows_index_groups_across_leagues():
    # Same real event (season, week, player), won in 3 different leagues -
    # must all land under one key, regardless of source_league_id.
    rows = [_bid_row(1, effective_cost_dollars=5.0), _bid_row(2, effective_cost_dollars=8.0), _bid_row(3, effective_cost_dollars=3.0)]
    index = build_event_won_rows_index(rows)
    assert len(index[(2022, 5, 42)]) == 3


def test_build_event_won_rows_index_includes_synthetic_won_equivalents():
    # League 1 has a real win. League 2's only real attempt on the SAME
    # event was a real $7 bid that failed for a non-competitive reason
    # (contingency/roster-limit logistics, not being outbid) -
    # add_synthetic_price_wins promotes it to a synthetic "won" row. Both
    # leagues' rows must land under the same event key once trainable_rows
    # has gone through add_synthetic_price_wins (as FaabModel.__init__
    # always does before this index is built) - a caller building this
    # index from rows that skipped that step would silently drop league 2's
    # real $7 bid the moment any other league (here, league 1) has a
    # genuine win for the same event.
    real_win = _bid_row(1, effective_cost_dollars=5.0, effective_starting_budget=40.0, team_id="winner")
    failed_attempt = _bid_row(2, signal="other_failure", bid_amount_dollars=7.0, effective_cost_dollars=0.0, team_id="blocked")
    trainable = add_synthetic_price_wins([real_win, failed_attempt])
    index = build_event_won_rows_index(trainable)
    leagues_in_index = {r["source_league_id"] for r in index[(2022, 5, 42)]}
    assert leagues_in_index == {1, 2}
    synthetic = next(r for r in index[(2022, 5, 42)] if r["source_league_id"] == 2)
    # The real $7 bid price is retained as effective_cost_dollars, not the
    # $0 it actually cost (nothing was ever charged - the claim never
    # executed).
    assert synthetic["effective_cost_dollars"] == pytest.approx(7.0)


def test_add_synthetic_price_wins_is_additive_not_a_filter():
    # trainable_rows come back UNCHANGED, plus the synthetic row appended -
    # the original other_failure row must still be present (interest-stage
    # consumers of trainable_rows still need it), not replaced by the
    # synthetic promotion.
    failed_attempt = _bid_row(1, signal="other_failure", bid_amount_dollars=7.0, effective_cost_dollars=0.0, team_id="blocked")
    out = add_synthetic_price_wins([failed_attempt])
    assert failed_attempt in out
    assert len(out) == 2
    synthetic = next(r for r in out if r is not failed_attempt)
    assert synthetic["signal"] == "won"
    assert synthetic["effective_cost_dollars"] == pytest.approx(7.0)


def test_add_synthetic_price_wins_skips_leagues_with_a_real_won_or_outbid_row():
    # A league with a real won (or outbid) row for this event already has a
    # real price/rival signal - no synthetic row should be manufactured
    # alongside it.
    won_and_failure = [
        _bid_row(1, signal="won", team_id="winner"),
        _bid_row(1, signal="other_failure", bid_amount_dollars=99.0, effective_cost_dollars=0.0, team_id="other_claim"),
    ]
    out = add_synthetic_price_wins(won_and_failure)
    assert len(out) == 2  # no synthetic row added
    assert sum(1 for r in out if r["signal"] == "won") == 1


def test_add_synthetic_price_wins_one_per_league_uses_highest_bid():
    # Two other_failure attempts in the SAME league for the SAME event -
    # exactly one synthetic row, using the HIGHER of the two bids.
    attempts = [
        _bid_row(1, signal="other_failure", bid_amount_dollars=7.0, effective_cost_dollars=0.0, team_id="a"),
        _bid_row(1, signal="other_failure", bid_amount_dollars=15.0, effective_cost_dollars=0.0, team_id="b"),
    ]
    out = add_synthetic_price_wins(attempts)
    synthetics = [r for r in out if r["signal"] == "won"]
    assert len(synthetics) == 1
    assert synthetics[0]["effective_cost_dollars"] == pytest.approx(15.0)


def test_build_price_rows_is_a_plain_won_filter():
    # build_price_rows no longer promotes anything itself - that's
    # add_synthetic_price_wins' job now. Given a row already carrying
    # signal="won" (however it got there), this just filters.
    rows = [_bid_row(1, signal="won"), _bid_row(2, signal="other_failure")]
    assert build_price_rows(rows) == [rows[0]]


def test_price_confidence_samples_expands_consolidated_event_across_leagues():
    # The k-NN search only ever sees ONE representative row for a
    # cross-league consolidated event (see consolidate_cross_league_events)
    # - but the confidence distribution should still see EVERY league's own
    # real WINNING price for that same event, via event_won_rows_index.
    # League B's rival (a real losing bid) must NOT appear - only the two
    # real wins count as win/lose thresholds.
    league_a = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a_winner")  # 0.25
    league_b = _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0, team_id="b_winner")  # 0.30
    representative = dict(league_a)  # what consolidate_cross_league_events would hand the k-NN search
    event_won_rows_index = {(2022, 5, 42): [league_a, league_b]}
    competing_bids_index = {(2, 2022, 5, 42): [{"team_id": "b_rival", "bid_dollars": 4.0}]}  # 4/20 = 0.20, league B only - excluded

    samples = price_confidence_samples([representative], [3.0], competing_bids_index, event_won_rows_index)
    values = sorted(round(v, 4) for v, _ in samples)
    assert values == [0.25, 0.3]  # league A's win, league B's win - no rival
    # One real event, one vote: both expanded samples still share the
    # representative's single k-NN weight (3.0), split evenly across the 2
    # real wins (not diluted by league B's rival).
    assert sum(w for _, w in samples) == pytest.approx(3.0)
    assert all(w == pytest.approx(1.5) for _, w in samples)


def test_price_confidence_samples_falls_back_without_event_won_rows_index():
    # Omitting event_won_rows_index (e.g. an older caller) must reproduce
    # the original single-league behavior exactly, not silently drop data.
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    samples = price_confidence_samples([row], [2.0], None)
    assert samples == [(0.25, 2.0)]


def test_price_comp_bid_distribution_expands_across_leagues_and_rivals():
    # Same cross-league setup as the price_confidence_samples test above -
    # this is the same underlying data, just returned as a sorted list of
    # {value, source_league_id, is_winner} dicts (so a UI can pick out
    # which league - and which of those are real wins vs rival losing
    # bids) for one comp row's own click-to-expand view, instead of k-NN
    # samples.
    league_a = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a_winner")  # 0.25
    league_b = _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0, team_id="b_winner")  # 0.30
    representative = dict(league_a)
    event_won_rows_index = {(2022, 5, 42): [league_a, league_b]}
    competing_bids_index = {(2, 2022, 5, 42): [{"team_id": "b_rival", "bid_dollars": 4.0}]}  # 4/20 = 0.20

    values = _price_comp_bid_distribution(representative, competing_bids_index, event_won_rows_index)
    assert [v["value"] for v in values] == pytest.approx([0.2, 0.25, 0.3])
    assert [v["source_league_id"] for v in values] == [2, 1, 2]
    assert [v["is_winner"] for v in values] == [False, True, True]


def test_price_comp_bid_distribution_falls_back_to_just_this_row_when_not_cross_league():
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    values = _price_comp_bid_distribution(row, None, None)
    assert [v["value"] for v in values] == pytest.approx([0.25])
    assert values[0]["source_league_id"] == 1
    assert values[0]["is_winner"] is True


def test_price_comp_dicts_no_longer_expose_raw_dollars_or_competing_bids():
    # Both dropped from the UI on purpose - a raw $ amount from an
    # unfamiliar pooled league's own budget scale isn't meaningfully
    # comparable to anything a reader already knows (see target_pct), and
    # competing_bids only ever showed one representative league's own
    # rivals - superseded by bid_distribution's full cross-league view.
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    out = _price_comp_dicts([row], [0.5], None, None)
    assert "bid_dollars" not in out[0]
    assert "competing_bids" not in out[0]
    assert [v["value"] for v in out[0]["bid_distribution"]] == pytest.approx([0.25])


def test_price_comp_dicts_expose_the_same_weight_used_in_the_weighted_average():
    rows = [_bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a"), _bid_row(1, add_player_id=99, team_id="b")]
    out = _price_comp_dicts(rows, [7.4, 1.2], None, None)
    assert [c["weight"] for c in out] == pytest.approx([7.4, 1.2])


def test_interest_comp_dicts_expose_the_same_weight_used_in_the_weighted_average():
    rows = [_bid_row(1, team_id="a"), _bid_row(1, add_player_id=99, team_id="b")]
    out = _interest_comp_dicts(rows, [7.4, 1.2])
    assert [c["weight"] for c in out] == pytest.approx([7.4, 1.2])


def test_comp_dicts_no_longer_expose_a_single_signal():
    # A comp's own signal (won/outbid/no_bid) is a collapsed single-outcome
    # read - dropped from the UI in favor of the real distributions
    # (bid_distribution for price, leagues_with_bid/leagues_eligible for
    # interest) that were always the more honest picture.
    row = _bid_row(1, team_id="a")
    assert "signal" not in _price_comp_dicts([row], [1.0], None, None)[0]
    assert "signal" not in _interest_comp_dicts([row], [1.0])[0]


def test_normalized_weights_sum_to_one():
    assert _normalized_weights([3.0, 1.0]) == pytest.approx([0.75, 0.25])
    assert sum(_normalized_weights([7.4, 1.2, 0.3])) == pytest.approx(1.0)


def test_normalized_weights_empty_list_is_safe():
    assert _normalized_weights([]) == []


def test_comp_based_estimate_price_comps_capped_at_k_but_confidence_samples_use_wider_price_k():
    # price_k widens the underlying k-NN pool for price_confidence_samples'
    # tail-percentile estimate only - the displayed comps table and the
    # weighted-average conditional_price itself should only ever see the
    # nearest k, matching the interest table's own k, not the wider pool.
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    interest_rows = [_bid_row(1, add_player_id=i, prior_week_actual_points=float(i)) for i in range(20)]
    price_rows = [
        _bid_row(1, add_player_id=i, prior_week_actual_points=float(i), effective_cost_dollars=5.0, effective_starting_budget=50.0)
        for i in range(20)
    ]
    out = comp_based_estimate(query, interest_rows, price_rows, k=3, price_k=6)
    assert len(out["comps"]) == 3
    assert len(out["price_confidence_samples"]) == 6
    # comps is the nearest-first PREFIX of the wider pool, not an unrelated
    # k=3 search - the 3 closest prior_week_actual_points to the query's
    # own 10.0 either way (ids 10, 9, 11).
    assert sorted(c["prior_week_actual_points"] for c in out["comps"]) == [9.0, 10.0, 11.0]


def test_credibility_matches_buhlmann_formula_with_k_15():
    assert _credibility(15) == pytest.approx(0.5)  # the half-credibility point, by definition
    assert _credibility(1) == pytest.approx(1 / 16)
    assert _credibility(0) == 0.0


def test_credibility_approaches_but_never_reaches_one():
    assert _credibility(1000) < 1.0
    assert _credibility(1000) > _credibility(100) > _credibility(15)


def test_backing_count_uses_leagues_eligible_for_interest_rows():
    assert _backing_count({"leagues_eligible": 7, "consolidated_from_leagues": [1, 2, 3]}) == 7


def test_backing_count_uses_consolidated_from_leagues_for_price_rows():
    assert _backing_count({"consolidated_from_leagues": [1, 2, 3, 4]}) == 4


def test_backing_count_defaults_to_one_for_a_single_league_price_row():
    # A single-league group never gets consolidated_from_leagues at all
    # (see consolidate_cross_league_events) - just its own one league.
    assert _backing_count({}) == 1


def test_credibility_weighted_multiplies_each_row_by_its_own_credibility():
    scored = [{"leagues_eligible": 15}, {"leagues_eligible": 1}]
    out = _credibility_weighted(scored, [10.0, 10.0])
    assert out[0] == pytest.approx(10.0 * 0.5)
    assert out[1] == pytest.approx(10.0 * (1 / 16))


def test_comp_based_estimate_favors_a_well_backed_comp_over_an_equally_close_thin_one():
    # Two interest comps, IDENTICAL distance to the query (same feature
    # values) - one backed by 40 real leagues, one by a single one. Pure
    # distance weighting would treat them identically; credibility must
    # make the well-backed one count for more toward bid_probability.
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    thin = _bid_row(1, add_player_id=1, prior_week_actual_points=10.0, signal="won", leagues_eligible=1, leagues_with_bid=1)
    deep = _bid_row(1, add_player_id=2, prior_week_actual_points=10.0, signal="no_bid", leagues_eligible=40, leagues_with_bid=0)
    price_rows = [_bid_row(1, add_player_id=1, prior_week_actual_points=10.0, effective_cost_dollars=5.0, effective_starting_budget=50.0)]
    out = comp_based_estimate(query, [thin, deep], price_rows, k=2)
    # thin says "won" (bid_probability=1 if it alone decided), deep says
    # "no_bid" (0 if it alone decided) - credibility-weighting toward the
    # 40-league comp should pull the blended result well below 0.5, not
    # leave it at the equal-weight midpoint.
    assert out["bid_probability"] < 0.3
