import pytest

from engine.faab_estimate import (
    NO_BID_MAX_ROS_RANK,
    NO_BID_MIN_PRIOR_POINTS,
    NO_BID_MIN_SNAP_PCT,
    _backing_count,
    _cap_weights,
    _credibility,
    _credibility_weighted,
    _feature_stats,
    _interest_comp_dicts,
    _knn,
    _normalized_weights,
    _position_distance_cutoffs,
    _price_comp_bid_distribution,
    _price_comp_dicts,
    _shrunk_interest_fractions,
    _weighted_median,
    add_synthetic_price_wins,
    build_event_won_rows_index,
    build_price_rows,
    comp_based_estimate,
    consolidate_cross_league_events,
    is_faab_relevant,
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


def test_consolidate_cross_league_events_medians_price_across_leagues():
    # Same real event (season, week, player) won in two different leagues,
    # at different dollar amounts and different effective budgets - must
    # collapse to ONE row whose consolidated_target_pct is the MEDIAN of
    # each league's own target_pct, not either league's raw dollars. At n=2
    # the median IS the mean of the two values - see the next test for a
    # case where they diverge.
    rows = [
        _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0),  # target_pct = 10/40 = 0.25
        _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0),  # target_pct = 6/20 = 0.30
    ]
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 1
    assert target_pct(out[0]) == pytest.approx((0.25 + 0.30) / 2)
    assert out[0]["consolidated_from_leagues"] == [1, 2]


def test_consolidate_cross_league_events_price_median_resists_a_single_outlier_league():
    # Three leagues won the same real event: two paid a similar, modest
    # price and one paid far more (a real, common pattern - see the
    # conversation this was built from: median max/min ratio within a
    # group is 5.5x at the pooled-table median, 30x at p90). A mean would
    # get dragged a long way toward the outlier; the median should land on
    # the middle (unaffected) value instead.
    rows = [
        _bid_row(1, effective_cost_dollars=2.0, effective_starting_budget=40.0),  # target_pct = 0.05
        _bid_row(2, effective_cost_dollars=3.0, effective_starting_budget=40.0),  # target_pct = 0.075
        _bid_row(3, effective_cost_dollars=36.0, effective_starting_budget=40.0),  # target_pct = 0.90 - the outlier
    ]
    out = consolidate_cross_league_events(rows, is_price=True)
    assert len(out) == 1
    assert target_pct(out[0]) == pytest.approx(0.075)  # the middle value, not the mean (~0.34)


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


def test_price_confidence_samples_only_ever_sees_winning_prices():
    # A losing bid has no path into this pool at all - price_confidence_samples
    # reads _price_comp_bid_distribution, which is winners-only by
    # construction (see the module docstring: a losing bid is a censored
    # observation, never the real win/lose threshold).
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner_team")
    samples = price_confidence_samples([row], [2.0])
    assert samples == pytest.approx([(0.25, 2.0)])


def test_price_confidence_samples_one_auction_one_vote():
    # Two independent single-league auctions with the SAME k-NN weight each
    # contribute exactly ONE sample, carrying their neighbor's FULL weight.
    contested = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner_1")
    uncontested = _bid_row(2, add_player_id=99, effective_cost_dollars=8.0, effective_starting_budget=40.0, team_id="winner_2")
    samples = price_confidence_samples([contested, uncontested], [2.0, 2.0])
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
    league_a = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a_winner")  # 0.25
    league_b = _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0, team_id="b_winner")  # 0.30
    representative = dict(league_a)  # what consolidate_cross_league_events would hand the k-NN search
    event_won_rows_index = {(2022, 5, 42): [league_a, league_b]}

    samples = price_confidence_samples([representative], [3.0], event_won_rows_index)
    values = sorted(round(v, 4) for v, _ in samples)
    assert values == [0.25, 0.3]  # league A's win, league B's win
    # One real event, one vote: both expanded samples still share the
    # representative's single k-NN weight (3.0), split evenly across the 2
    # real wins.
    assert sum(w for _, w in samples) == pytest.approx(3.0)
    assert all(w == pytest.approx(1.5) for _, w in samples)


def test_price_confidence_samples_falls_back_without_event_won_rows_index():
    # Omitting event_won_rows_index (e.g. an older caller) must reproduce
    # the original single-league behavior exactly, not silently drop data.
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    samples = price_confidence_samples([row], [2.0], None)
    assert samples == [(0.25, 2.0)]


def test_price_comp_bid_distribution_expands_across_leagues():
    # Same cross-league setup as the price_confidence_samples test above -
    # this is the same underlying data, just returned as a sorted list of
    # {value, source_league_id} dicts (so a UI can pick out which league)
    # for one comp row's own click-to-expand view, instead of k-NN samples.
    league_a = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a_winner")  # 0.25
    league_b = _bid_row(2, effective_cost_dollars=6.0, effective_starting_budget=20.0, team_id="b_winner")  # 0.30
    representative = dict(league_a)
    event_won_rows_index = {(2022, 5, 42): [league_a, league_b]}

    values = _price_comp_bid_distribution(representative, event_won_rows_index)
    assert [v["value"] for v in values] == pytest.approx([0.25, 0.3])
    assert [v["source_league_id"] for v in values] == [1, 2]


def test_price_comp_bid_distribution_falls_back_to_just_this_row_when_not_cross_league():
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    values = _price_comp_bid_distribution(row, None)
    assert [v["value"] for v in values] == pytest.approx([0.25])
    assert values[0]["source_league_id"] == 1


def test_price_comp_dicts_no_longer_expose_raw_dollars():
    # Dropped from the UI on purpose - a raw $ amount from an unfamiliar
    # pooled league's own budget scale isn't meaningfully comparable to
    # anything a reader already knows (see target_pct).
    row = _bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="winner")
    out = _price_comp_dicts([row], [0.5], None)
    assert "bid_dollars" not in out[0]
    assert [v["value"] for v in out[0]["bid_distribution"]] == pytest.approx([0.25])


def test_price_comp_dicts_expose_the_same_weight_used_in_the_weighted_average():
    rows = [_bid_row(1, effective_cost_dollars=10.0, effective_starting_budget=40.0, team_id="a"), _bid_row(1, add_player_id=99, team_id="b")]
    out = _price_comp_dicts(rows, [7.4, 1.2], None)
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
    assert "signal" not in _price_comp_dicts([row], [1.0], None)[0]
    assert "signal" not in _interest_comp_dicts([row], [1.0])[0]


def test_normalized_weights_sum_to_one():
    assert _normalized_weights([3.0, 1.0]) == pytest.approx([0.75, 0.25])
    assert sum(_normalized_weights([7.4, 1.2, 0.3])) == pytest.approx(1.0)


def test_normalized_weights_empty_list_is_safe():
    assert _normalized_weights([]) == []


def test_comp_based_estimate_price_confidence_samples_use_the_same_k_pool_as_the_comps_table():
    # An earlier version ran a separate, wider price_k search just for
    # price_confidence_samples - which meant the confidence slider's own "N
    # real winning prices" count could include real per-league wins from
    # neighbors that were never shown as one of the displayed comps (a live
    # 2026 wk2 Antonio Williams query counted 34 there vs 20 summed from the
    # visible comps' own expand views). Confidence samples must now come
    # from exactly the same k comps as the table below them - each of these
    # 3 single-league comps contributes exactly one real winning price, so
    # the sample count must equal the comp count exactly, not some wider
    # multiple of it.
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    interest_rows = [_bid_row(1, add_player_id=i, prior_week_actual_points=float(i)) for i in range(20)]
    price_rows = [
        _bid_row(1, add_player_id=i, prior_week_actual_points=float(i), effective_cost_dollars=5.0, effective_starting_budget=50.0)
        for i in range(20)
    ]
    out = comp_based_estimate(query, interest_rows, price_rows, k=3)
    assert len(out["comps"]) == 3
    assert len(out["price_confidence_samples"]) == 3
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


def test_comp_based_estimate_interest_uses_real_bid_rate_not_a_binary_signal():
    # Two interest comps, IDENTICAL distance to the query - one whose real
    # bid rate across the pooled leagues that saw it was low (4/50, 8%) and
    # one whose rate was high (45/50, 90%). Both would have collapsed to the
    # SAME binary "won" signal under the old design; the vote now has to
    # reflect their own real rates instead - see the conversation this was
    # built from (a 2026 wk2 AJ Barner query whose interest comps table was
    # full of single-digit bid rates, yet the OLD binary-signal design still
    # produced 66% aggregate interest, since every nonzero rate counted as a
    # full yes).
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    low_rate = _bid_row(1, add_player_id=1, prior_week_actual_points=10.0, signal="won", leagues_eligible=50, leagues_with_bid=4)
    high_rate = _bid_row(1, add_player_id=2, prior_week_actual_points=10.0, signal="won", leagues_eligible=50, leagues_with_bid=45)
    out = comp_based_estimate(query, [low_rate, high_rate], [], k=2)
    # Equal distance, equal backing count (so no other weighting in play) -
    # a plain average of the two real rates: (0.08 + 0.90) / 2 = 0.49.
    assert out["bid_probability"] == pytest.approx(0.49, abs=1e-6)


def test_comp_based_estimate_interest_does_not_double_count_backing_count():
    # Two comps with the SAME real bid rate (50%) but very different backing
    # (2 vs 40 leagues eligible) must contribute EQUALLY to bid_probability -
    # credibility must not ALSO multiply on top of a rate that already
    # divides by that same backing count in its own denominator (see
    # _interest_bid_fraction's docstring: doing both double-counts a comp's
    # size instead of treating credibility as a genuinely separate axis, the
    # way it still is for price - see the conversation this was built from).
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    thin = _bid_row(1, add_player_id=1, prior_week_actual_points=10.0, signal="won", leagues_eligible=2, leagues_with_bid=1)
    deep = _bid_row(1, add_player_id=2, prior_week_actual_points=10.0, signal="won", leagues_eligible=40, leagues_with_bid=20)
    out = comp_based_estimate(query, [thin, deep], [], k=2)
    assert out["bid_probability"] == pytest.approx(0.5, abs=1e-6)


def test_shrunk_interest_fractions_pulls_a_low_backing_outlier_toward_its_neighbors():
    # A 2-of-2 (100%) comp sitting alongside two well-backed ~14% comps -
    # exactly the AJ Barner-shaped case: a tiny sample's noisy rate should
    # get pulled hard toward what its neighborhood actually shows, not left
    # at its own raw extreme.
    low1 = {"leagues_with_bid": 7, "leagues_eligible": 50}
    low2 = {"leagues_with_bid": 7, "leagues_eligible": 50}
    thin_high = {"leagues_with_bid": 2, "leagues_eligible": 2}
    out = _shrunk_interest_fractions([low1, low2, thin_high])
    assert out[0] == pytest.approx(0.14763313609467457)
    assert out[1] == pytest.approx(0.14763313609467457)
    assert out[2] == pytest.approx(0.24117647058823527)  # pulled way down from a raw 100%
    assert out[2] < 1.0


def test_shrunk_interest_fractions_leaves_a_rate_matching_its_neighbors_unchanged():
    # Every comp already shows the SAME rate as its neighborhood's own
    # pooled prior - shrinkage should be a no-op here, not systematically
    # bias every comp downward just for existing.
    same_rate = [{"leagues_with_bid": 5, "leagues_eligible": 10}] * 3
    out = _shrunk_interest_fractions(same_rate)
    assert out == pytest.approx([0.5, 0.5, 0.5])


def test_shrunk_interest_fractions_falls_back_to_zero_prior_for_a_single_comp():
    # No other comp in the neighborhood to pool a local prior from at all -
    # only reachable via _knn's own single-nearest-neighbor fallback for a
    # query sitting outside interest_max_distance of everything else.
    single = [{"leagues_with_bid": 2, "leagues_eligible": 2}]
    out = _shrunk_interest_fractions(single)
    assert out[0] == pytest.approx(2 / 17)  # (2 + 15*0) / (2 + 15)


def test_cap_weights_leaves_weights_alone_when_already_within_ratio():
    assert _cap_weights([10.0, 6.0, 5.0]) == pytest.approx([10.0, 6.0, 5.0])


def test_cap_weights_caps_a_dominant_weight_down_to_the_ratio_ceiling():
    # min is 10.0, so at ratio=2 the ceiling is 2x that (20.0) - the
    # dominant weight gets clamped straight to it; the other three, already
    # within range, are left exactly as they were (no redistribution to
    # inflate them). Ratio passed explicitly - see
    # test_cap_weights_uses_the_wider_default_ratio for the real default.
    assert _cap_weights([100.0, 10.0, 10.0, 10.0], max_ratio=2.0) == pytest.approx([20.0, 10.0, 10.0, 10.0])


def test_cap_weights_bounds_the_full_range_not_just_top_vs_runner_up():
    # An earlier version capped only the top weight against the SECOND-
    # highest, which let this exact case sail through untouched (100 <=
    # 2*50) even though 100 is 20x the smallest weight (5) - not the "no
    # comp over 2x ANY other" guarantee this is supposed to provide.
    out = _cap_weights([100.0, 50.0, 5.0, 5.0], max_ratio=2.0)
    assert out == pytest.approx([10.0, 10.0, 5.0, 5.0])
    assert max(out) / min(out) == pytest.approx(2.0)


def test_cap_weights_uses_the_wider_default_ratio():
    # The real default (5x, not 2x) - deliberately looser so normal
    # distance-based k-NN weight spread isn't flattened alongside a real
    # credibility-driven outlier - see _cap_weights' own docstring.
    assert _cap_weights([100.0, 10.0, 10.0, 10.0]) == pytest.approx([50.0, 10.0, 10.0, 10.0])


def test_cap_weights_handles_fewer_than_two_weights():
    assert _cap_weights([]) == []
    assert _cap_weights([5.0]) == [5.0]


def test_weighted_median_basic_odd_count_equal_weights():
    assert _weighted_median([1, 2, 3], [1, 1, 1]) == 2


def test_weighted_median_resists_an_outlier_below_majority_weight():
    # The 100 carries only 1 of 4 equal weight shares (25%) - nowhere near
    # enough to move the crossover point away from the middle of the other
    # three.
    assert _weighted_median([1, 2, 3, 100], [1, 1, 1, 1]) == 2


def test_weighted_median_converges_to_a_true_majority_weight_outlier():
    # The 100 alone carries more than half the total weight (10 of 13) -
    # no central-tendency measure can avoid converging to it once one value
    # genuinely owns the majority of the evidence; this is the case
    # _cap_weights exists to prevent from arising in the first place.
    assert _weighted_median([1, 2, 3, 100], [1, 1, 1, 10]) == 100


def test_weighted_median_empty_is_zero():
    assert _weighted_median([], []) == 0.0


def test_comp_based_estimate_median_resists_a_dominant_credibility_outlier_that_mean_does_not():
    # The Carson Steele shape: nine modest, similarly-priced, single-league
    # comps (low credibility) alongside one comp priced way higher but
    # backed by 40 leagues (high credibility) - all at IDENTICAL distance
    # from the query, isolating credibility as the only reason the outlier's
    # weight differs. Nine "other" comps, matching comp_based_estimate's own
    # default k=10, not a smaller toy count - at the default 5x cap ratio, a
    # too-small "other" pool can leave the capped outlier still holding a
    # MAJORITY of total weight (5x one comp vs only 4 others already tips
    # past 50%), which would make the median converge right back to it, same
    # as the mean - this shape keeps the outlier's capped share (5 of 5+9=14)
    # comfortably under that.
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    low_comps = [
        _bid_row(i, add_player_id=i, prior_week_actual_points=10.0, consolidated_target_pct=p)
        for i, p in enumerate([0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10], start=1)
    ]
    outlier = _bid_row(
        99, add_player_id=99, prior_week_actual_points=10.0, consolidated_target_pct=0.90, consolidated_from_leagues=list(range(40))
    )
    price_rows = low_comps + [outlier]
    out = comp_based_estimate(query, price_rows, price_rows, k=10)
    assert out["conditional_price_mean"] == pytest.approx(0.5336563876651983)  # dragged way up by the outlier
    assert out["conditional_price_median"] == pytest.approx(0.08)  # lands on a modest, typical comp instead

    outlier_comp = next(c for c in out["comps"] if c["pct_of_remaining_budget"] == pytest.approx(0.90))
    other_comp = next(c for c in out["comps"] if c["pct_of_remaining_budget"] != pytest.approx(0.90))
    # weight (uncapped, drives the mean) reflects its real outsized
    # credibility. weight_capped (drives the median) is reined in to
    # EXACTLY the default 5x ratio of the smallest comp's own weight_capped -
    # the full-range guarantee _cap_weights provides, not just "less than
    # before."
    assert outlier_comp["weight"] > 0.5
    assert outlier_comp["weight_capped"] == pytest.approx(5 * other_comp["weight_capped"])


def test_comp_based_estimate_exposes_shrunk_bid_fraction_on_interest_comps():
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    low1 = _bid_row(1, add_player_id=1, prior_week_actual_points=10.0, add_player_name="Low A", leagues_eligible=50, leagues_with_bid=7)
    low2 = _bid_row(2, add_player_id=2, prior_week_actual_points=10.0, add_player_name="Low B", leagues_eligible=50, leagues_with_bid=7)
    thin_high = _bid_row(
        3, add_player_id=3, prior_week_actual_points=10.0, add_player_name="Thin High", leagues_eligible=2, leagues_with_bid=2
    )
    out = comp_based_estimate(query, [low1, low2, thin_high], [], k=3)
    fractions = {c["name"]: c["shrunk_bid_fraction"] for c in out["interest_comps"]}
    assert fractions["Thin High"] == pytest.approx(0.24117647058823527)  # pulled way down from a raw 100%
    assert fractions["Low A"] == pytest.approx(0.14763313609467457)


def test_knn_max_distance_excludes_neighbors_beyond_the_cutoff():
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    near = [_bid_row(1, add_player_id=i, prior_week_actual_points=10.0 + i * 0.1) for i in range(5)]
    far = _bid_row(1, add_player_id=99, prior_week_actual_points=500.0)
    pool = near + [far]
    stats = _feature_stats(pool)
    scored, _ = _knn(query, pool, stats, k=10, max_distance=0.5)
    assert far not in scored
    assert len(scored) == 5


def test_knn_max_distance_none_matches_uncapped_behavior():
    # Default (None) must reproduce today's plain nearest-k behavior exactly -
    # this is the existing production default, so adding the cutoff must be
    # a no-op unless a caller opts in.
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    pool = [_bid_row(1, add_player_id=i, prior_week_actual_points=float(i)) for i in range(20)]
    stats = _feature_stats(pool)
    uncapped = _knn(query, pool, stats, k=10)
    capped_none = _knn(query, pool, stats, k=10, max_distance=None)
    assert uncapped == capped_none


def test_knn_max_distance_falls_back_to_single_nearest_rather_than_empty():
    # Never an empty comp set - see _knn's docstring: an empty set would
    # read exactly like a confident "nobody would bid"/"$0", not "no
    # comparable situation was found."
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    pool = [_bid_row(1, add_player_id=i, prior_week_actual_points=200.0 + i) for i in range(5)]
    stats = _feature_stats(pool)
    scored, weights = _knn(query, pool, stats, k=10, max_distance=1e-9)
    assert len(scored) == 1
    assert len(weights) == 1


def test_comp_based_estimate_forwards_max_distance_and_never_returns_empty_comps():
    query = _bid_row(0, add_player_id=0, prior_week_actual_points=10.0)
    interest_rows = [_bid_row(1, add_player_id=i, prior_week_actual_points=float(i)) for i in range(9)]
    price_rows = [
        _bid_row(1, add_player_id=i, prior_week_actual_points=float(i), effective_cost_dollars=5.0, effective_starting_budget=50.0)
        for i in range(9)
    ]
    out = comp_based_estimate(query, interest_rows, price_rows, k=10, interest_max_distance=1e-9, price_max_distance=1e-9)
    # An impossibly tight cutoff still yields exactly one neighbor each (the
    # guaranteed single-nearest fallback - see _knn), not an empty set.
    assert len(out["interest_comps"]) == 1
    assert len(out["comps"]) == 1


def test_position_distance_cutoffs_returns_a_percentile_per_position():
    rows = [_bid_row(1, add_player_id=i, position="RB", prior_week_actual_points=float(i)) for i in range(20)]
    stats = _feature_stats(rows)
    cutoffs = _position_distance_cutoffs(rows, stats, k=5, pct=50.0)
    assert "RB" in cutoffs
    assert cutoffs["RB"] > 0
    # A stricter (lower) percentile must never produce a LARGER cutoff.
    tighter = _position_distance_cutoffs(rows, stats, k=5, pct=10.0)
    assert tighter["RB"] <= cutoffs["RB"]


def test_position_distance_cutoffs_skips_a_position_with_too_few_rows_for_k():
    rows = [_bid_row(1, add_player_id=i, position="TE", prior_week_actual_points=float(i)) for i in range(3)]
    stats = _feature_stats(rows)
    cutoffs = _position_distance_cutoffs(rows, stats, k=5)
    assert "TE" not in cutoffs


def test_is_faab_relevant_via_prior_points():
    assert is_faab_relevant(NO_BID_MIN_PRIOR_POINTS, None, None, "RB") is True
    assert is_faab_relevant(NO_BID_MIN_PRIOR_POINTS - 0.01, None, None, "RB") is False


def test_is_faab_relevant_via_snap_pct():
    assert is_faab_relevant(0.0, NO_BID_MIN_SNAP_PCT, None, "RB") is True
    assert is_faab_relevant(0.0, NO_BID_MIN_SNAP_PCT - 0.01, None, "RB") is False


def test_is_faab_relevant_via_ros_rank_alone_when_genuinely_inactive():
    # A startable player having a genuinely quiet week - NO stat row at all
    # (bye, benching, early return from injury: prior_points is None, not
    # just low) - but a real, good ROS rank.
    ceiling = NO_BID_MAX_ROS_RANK["RB"]
    assert is_faab_relevant(None, None, ceiling, "RB") is True
    assert is_faab_relevant(None, None, ceiling + 0.01, "RB") is False


def test_is_faab_relevant_rank_rescues_even_a_real_but_weak_stat_row():
    # Tank Bigsby, 2026 wk2: 1 carry, 0.3 points - a real, if weak, stat
    # row - with a ROS rank (48) that clears RB's ceiling (73). Deliberately
    # rescued anyway - see is_faab_relevant's own docstring: a version
    # gating this on "no stat row at all" was tried and rejected as too
    # narrow, accepting some "played weak but still rank-rescued" cases in
    # exchange for never missing a real speculative name over a near-empty
    # (rather than blank) stat line.
    assert is_faab_relevant(0.3, 0.11, 48, "RB") is True


def test_is_faab_relevant_false_when_nothing_clears_any_bar():
    assert is_faab_relevant(0.0, 0.0, None, "RB") is False


def test_is_faab_relevant_handles_none_prior_points_gracefully():
    # A live query where the player has no stat row at all this week -
    # must not raise on comparing None to a threshold.
    assert is_faab_relevant(None, None, None, "RB") is False
    assert is_faab_relevant(None, NO_BID_MIN_SNAP_PCT, None, "RB") is True


def test_is_faab_relevant_uses_each_positions_own_ros_rank_ceiling():
    # Same ROS rank (30) - QB's ceiling (25) is stricter than RB's (73), so
    # identical ROS-rank standing reads differently depending on position.
    assert is_faab_relevant(0.0, 0.0, 30, "QB") is False
    assert is_faab_relevant(0.0, 0.0, 30, "RB") is True
