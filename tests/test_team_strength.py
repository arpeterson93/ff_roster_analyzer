import pytest

from engine.team_strength import (
    PlayerCtx,
    fa_pool_value,
    lineup_total,
    lineup_total_with_streaming,
    optimal_lineup_for_week_with_bye_fill,
    pickups,
    position_strength,
    position_value_by_player,
    position_value_matrix,
    rank_and_compare,
    slot_strength,
    trade_targets,
)

SLOTS = {"RB": 2}
ELIGIBILITY = {"RB": {"RB"}}
WEEKS = [1, 2]


def _player(pid, pos, ppw):
    return PlayerCtx(id=pid, position=pos, ros_total=ppw * len(WEEKS), weekly={w: ppw for w in WEEKS})


def test_lineup_total_sums_over_weeks():
    players = {"rb1": _player("rb1", "RB", 20.0), "rb2": _player("rb2", "RB", 10.0)}
    total = lineup_total(["rb1", "rb2"], players, WEEKS, SLOTS, ELIGIBILITY)
    assert total == pytest.approx(60.0)  # (20+10) * 2 weeks


def test_position_value_by_player_starter_scored_against_best_available_fa():
    # rb1 starts (the only RB slot); rb2 is bench. Both scored against the
    # single available free agent - rb1 as a STARTER (starting_value),
    # rb2 as DEPTH (depth_value, floored at 0 if he'd lose to the FA too).
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=40.0, weekly={1: 20.0, 2: 20.0}),
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=13.0, weekly={1: 5.0, 2: 8.0}),
    }
    free_agents = {"RB": [PlayerCtx(id="fa1", position="RB", ros_total=12.0, weekly={1: 6.0, 2: 6.0})]}
    result = position_value_by_player(["rb1", "rb2"], players, free_agents, weeks, slots, eligibility)
    # rb1 starts every week, scored against fa1 (6 pts both weeks).
    assert result["rb1"]["starting_value"] == pytest.approx((20 - 6) + (20 - 6))
    assert result["rb1"]["depth_value"] == pytest.approx(0.0)
    assert [w["replacement_id"] for w in result["rb1"]["weekly"]] == ["fa1", "fa1"]
    # rb2 never starts (rb1 always wins the lone slot) - depth-scored against
    # fa1 too, floored at 0 since he's below fa1 both weeks (5<6, but 8>6).
    assert result["rb2"]["starting_value"] == pytest.approx(0.0)
    assert result["rb2"]["depth_value"] == pytest.approx(0.0 + (8 - 6))


def test_position_value_by_player_replacement_is_always_a_free_agent_never_a_teammate():
    # Unlike the old lineup-delta engine, a starter's replacement level is
    # ALWAYS the best free agent for his slot - even when a real bench
    # teammate exists and outscores that free agent every week.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={1: 20.0}),
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=15.0, weekly={1: 15.0}),  # strong bench teammate
    }
    free_agents = {"RB": [PlayerCtx(id="fa_weak", position="RB", ros_total=2.0, weekly={1: 2.0})]}
    result = position_value_by_player(["rb1", "rb2"], players, free_agents, weeks, slots, eligibility)
    assert result["rb1"]["weekly"][0]["replacement_id"] == "fa_weak"
    assert result["rb1"]["starting_value"] == pytest.approx(18.0)  # 20 - fa_weak's 2, not 20 - rb2's 15


def test_position_value_by_player_true_bye_excluded_entirely():
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {"rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={1: 20.0, 2: 0.0})}
    result = position_value_by_player(["rb1"], players, {}, weeks, slots, eligibility)
    assert result["rb1"]["weekly"][1] == {"week": 2, "starting_value": 0.0, "depth_value": 0.0, "replacement_id": None}


def test_position_value_by_player_sums_match_position_value_matrix():
    # Direct invariant: position_value_matrix is now a thin wrapper that
    # sums this per-player breakdown by position - the two must agree.
    slots = {"RB": 1, "WR": 1, "FLEX": 1}
    eligibility = {"RB": {"RB"}, "WR": {"WR"}, "FLEX": {"RB", "WR"}}
    weeks = [1, 2, 3]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=60.0, weekly={1: 20.0, 2: 22.0, 3: 18.0}),
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=30.0, weekly={1: 10.0, 2: 11.0, 3: 9.0}),
        "wr1": PlayerCtx(id="wr1", position="WR", ros_total=45.0, weekly={1: 15.0, 2: 16.0, 3: 14.0}),
        "wr2": PlayerCtx(id="wr2", position="WR", ros_total=15.0, weekly={1: 5.0, 2: 4.0, 3: 6.0}),
    }
    free_agents = {
        "RB": [PlayerCtx(id="fa_rb", position="RB", ros_total=24.0, weekly={1: 8.0, 2: 8.0, 3: 8.0})],
        "WR": [PlayerCtx(id="fa_wr", position="WR", ros_total=21.0, weekly={1: 7.0, 2: 7.0, 3: 7.0})],
    }
    team = ["rb1", "rb2", "wr1", "wr2"]
    by_player = position_value_by_player(team, players, free_agents, weeks, slots, eligibility)
    by_position = position_value_matrix(team, players, free_agents, weeks, slots, eligibility)
    for pos in ("RB", "WR"):
        starting_sum = sum(v["starting_value"] for pid, v in by_player.items() if players[pid].position == pos)
        depth_sum = sum(v["depth_value"] for pid, v in by_player.items() if players[pid].position == pos)
        assert starting_sum == pytest.approx(by_position[pos]["starting_value"])
        assert depth_sum == pytest.approx(by_position[pos]["depth_value"])


def test_position_strength_attributes_flex_to_real_position():
    slots = {"RB": 1, "FLEX": 1}
    eligibility = {"RB": {"RB"}, "FLEX": {"RB", "WR"}}
    players = {
        "rb1": _player("rb1", "RB", 20.0),
        "rb2": _player("rb2", "RB", 15.0),  # fills the FLEX slot
        "wr1": _player("wr1", "WR", 5.0),
    }
    ppw = position_strength(["rb1", "rb2", "wr1"], players, WEEKS, slots, eligibility, ["RB", "WR"], {}, {})
    assert ppw["RB"] == pytest.approx(35.0)  # both RBs start (one via FLEX), attributed to RB
    assert ppw["WR"] == pytest.approx(0.0)


def test_slot_strength_reranks_by_that_weeks_points_not_optimizer_column():
    slots = {"WR": 2}
    eligibility = {"WR": {"WR"}}
    weeks = [1, 2]
    players = {
        "wr_a": PlayerCtx(id="wr_a", position="WR", ros_total=25.0, weekly={1: 20.0, 2: 5.0}),
        "wr_b": PlayerCtx(id="wr_b", position="WR", ros_total=25.0, weekly={1: 10.0, 2: 15.0}),
    }
    result, labels = slot_strength(["wr_a", "wr_b"], players, weeks, slots, eligibility, {}, {})
    assert labels == ["WR1", "WR2"]
    # WR1 = the stronger performer each week (20, then 15) -> avg 17.5
    assert result["WR1"] == pytest.approx(17.5)
    # WR2 = the weaker performer each week (10, then 5) -> avg 7.5
    assert result["WR2"] == pytest.approx(7.5)


def test_slot_strength_unfilled_instance_contributes_zero():
    slots = {"WR": 2}
    eligibility = {"WR": {"WR"}}
    weeks = [1]
    players = {"wr_a": PlayerCtx(id="wr_a", position="WR", ros_total=10.0, weekly={1: 10.0})}
    result, labels = slot_strength(["wr_a"], players, weeks, slots, eligibility, {}, {})
    assert result["WR1"] == pytest.approx(10.0)
    assert result["WR2"] == pytest.approx(0.0)


def test_bye_fill_replaces_only_the_byed_slot():
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    week = 3
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={3: 0.0}),  # on bye
        "fa1": PlayerCtx(id="fa1", position="RB", ros_total=8.0, weekly={3: 6.0}),
    }
    free_agents = {"RB": [players["fa1"]]}
    total, assignment, streamed = optimal_lineup_for_week_with_bye_fill(
        ["rb1"], players, free_agents, week, slots, eligibility, {"rb1": 3}
    )
    assert assignment["RB"] == "fa1"
    assert streamed == {"fa1"}
    assert total == pytest.approx(6.0)


def test_bye_fill_never_displaces_a_non_bye_starter():
    # fa1 projects HIGHER than rb1, but rb1 isn't on bye - streaming is only
    # a fill-in for a confirmed bye gap, never a "take the best player"
    # optimizer that would bench a real, healthy starter.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    week = 3
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={3: 10.0}),
        "fa1": PlayerCtx(id="fa1", position="RB", ros_total=25.0, weekly={3: 15.0}),
    }
    free_agents = {"RB": [players["fa1"]]}
    total, assignment, streamed = optimal_lineup_for_week_with_bye_fill(
        ["rb1"], players, free_agents, week, slots, eligibility, {}
    )
    assert assignment["RB"] == "rb1"
    assert streamed == set()
    assert total == pytest.approx(10.0)


def test_bye_fill_does_not_double_book_the_same_free_agent():
    slots = {"RB": 2}
    eligibility = {"RB": {"RB"}}
    week = 3
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={3: 0.0}),
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=18.0, weekly={3: 0.0}),
        "fa1": PlayerCtx(id="fa1", position="RB", ros_total=10.0, weekly={3: 8.0}),
        "fa2": PlayerCtx(id="fa2", position="RB", ros_total=6.0, weekly={3: 4.0}),
    }
    free_agents = {"RB": [players["fa1"], players["fa2"]]}
    total, assignment, streamed = optimal_lineup_for_week_with_bye_fill(
        ["rb1", "rb2"], players, free_agents, week, slots, eligibility, {"rb1": 3, "rb2": 3}
    )
    assert streamed == {"fa1", "fa2"}
    assert set(assignment.values()) == {"fa1", "fa2"}
    assert total == pytest.approx(12.0)


def test_rank_and_compare():
    team_ppw = {1: {"RB": 30.0}, 2: {"RB": 10.0}, 3: {"RB": 20.0}}
    result = rank_and_compare(team_ppw, ["RB"])
    assert result[1]["RB"]["rank"] == 1
    assert result[2]["RB"]["rank"] == 3
    assert result[3]["RB"]["vs_avg"] == pytest.approx(0.0)  # league avg is 20


def test_streaming_backfills_a_lone_kickers_bye_week():
    k_slots = {"K": 1}
    k_eligibility = {"K": {"K"}}
    weeks = [1, 2]
    # The only rostered kicker is on bye in week 2 (0 projected).
    players = {"k1": PlayerCtx(id="k1", position="K", ros_total=10.0, weekly={1: 10.0, 2: 0.0})}
    backup_fa = PlayerCtx(id="k_fa", position="K", ros_total=16.0, weekly={1: 8.0, 2: 8.0})
    free_agents = {"K": [backup_fa]}

    plain = lineup_total(["k1"], players, weeks, k_slots, k_eligibility)
    streamed = lineup_total_with_streaming(["k1"], players, free_agents, weeks, k_slots, k_eligibility)
    assert plain == pytest.approx(10.0)  # week 2 scores 0, nothing fills the bye
    assert streamed == pytest.approx(18.0)  # week1 k1(10) + week2 streamed FA(8)


def test_fa_pool_value_leave_one_out_credits_the_best_fa_against_the_second_best():
    # The single best FA at a position must be compared against someone
    # ELSE, not himself - otherwise he'd always score exactly 0.
    free_agents = {
        "RB": [
            PlayerCtx(id="fa_best", position="RB", ros_total=20.0, weekly={1: 20.0}),
            PlayerCtx(id="fa_second", position="RB", ros_total=12.0, weekly={1: 12.0}),
        ]
    }
    result = fa_pool_value(free_agents, [1])
    assert result["fa_best"]["depth_value"] == pytest.approx(8.0)  # 20 - fa_second's 12
    assert result["fa_best"]["weekly"][0]["replacement_id"] == "fa_second"
    # fa_second is compared against fa_best (the only OTHER RB) - he's worse, floored at 0.
    assert result["fa_second"]["depth_value"] == pytest.approx(0.0)
    assert result["fa_second"]["starting_value"] == pytest.approx(0.0)  # never a starter, by definition


def test_fa_pool_value_scales_by_depth_weight():
    free_agents = {
        "RB": [
            PlayerCtx(id="fa_a", position="RB", ros_total=20.0, weekly={1: 20.0}),
            PlayerCtx(id="fa_b", position="RB", ros_total=10.0, weekly={1: 10.0}),
        ]
    }
    result = fa_pool_value(free_agents, [1], depth_weight=0.5)
    assert result["fa_a"]["value_delta"] == pytest.approx(0.5 * 10.0)  # 0.5 * (20 - 10)


def test_fa_pool_value_never_widens_to_flex_stays_position_only():
    # A separate WR pool never enters an RB's comparison - fa_pool_value only
    # ever takes a free_agents_by_pos dict, no slots/eligibility, so there's
    # no FLEX concept for it to widen into in the first place.
    free_agents = {
        "RB": [PlayerCtx(id="fa_rb", position="RB", ros_total=10.0, weekly={1: 10.0})],
        "WR": [PlayerCtx(id="fa_wr", position="WR", ros_total=50.0, weekly={1: 50.0})],
    }
    result = fa_pool_value(free_agents, [1])
    # fa_rb is the ONLY RB - no other RB to compare against (best_other
    # defaults to 0), so his full raw value counts regardless of the much
    # higher-scoring WR sitting in a different position bucket.
    assert result["fa_rb"]["depth_value"] == pytest.approx(10.0)


def test_pickups_prefers_same_position_drop_over_unrelated_bench_player():
    # A weak kicker plus an unrelated, even-weaker bench WR: a strong FA
    # kicker should suggest dropping the weak KICKER, not the bench WR
    # (keeping two kickers while cutting an unrelated skill player).
    slots = {"K": 1, "WR": 1}
    eligibility = {"K": {"K"}, "WR": {"WR"}}
    weeks = [1, 2]
    players = {
        "k_weak": _player("k_weak", "K", 5.0),
        "wr_bench": _player("wr_bench", "WR", 1.0),
    }
    strong_fa_k = _player("k_strong", "K", 15.0)
    free_agents = {"K": [strong_fa_k]}
    team_values = position_value_by_player(["k_weak", "wr_bench"], players, free_agents, weeks, slots, eligibility)
    fa_values = fa_pool_value(free_agents, weeks)
    result = pickups(["k_weak", "wr_bench"], players, team_values, fa_values, free_agents, max_pickups=5)
    assert len(result) == 1
    assert result[0]["add"] == "k_strong"
    assert result[0]["drop"] == "k_weak"


def test_pickups_excludes_fa_that_does_not_beat_the_weakest_droppable():
    players = {"rb1": _player("rb1", "RB", 20.0), "rb2": _player("rb2", "RB", 15.0)}
    weak_fa = _player("fa_weak", "RB", 1.0)
    free_agents = {"RB": [weak_fa]}
    team_values = position_value_by_player(["rb1", "rb2"], players, free_agents, WEEKS, SLOTS, ELIGIBILITY)
    fa_values = fa_pool_value(free_agents, WEEKS)
    result = pickups(["rb1", "rb2"], players, team_values, fa_values, free_agents, max_pickups=5)
    assert result == []


def test_pickups_excludes_ir_players_from_the_droppable_pool():
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {
        "rb1": _player("rb1", "RB", 20.0),  # the starter
        "rb2": _player("rb2", "RB", 3.0),  # the obvious drop, but on IR
    }
    strong_fa = _player("fa_strong", "RB", 30.0)
    free_agents = {"RB": [strong_fa]}
    team_values = position_value_by_player(["rb1", "rb2"], players, free_agents, weeks, slots, eligibility)
    fa_values = fa_pool_value(free_agents, weeks)
    result = pickups(
        ["rb1", "rb2"], players, team_values, fa_values, free_agents, max_pickups=5, ir_player_ids=frozenset(["rb2"])
    )
    # rb2 (the real drop candidate) is unavailable - rb1 must be named instead.
    assert len(result) == 1
    assert result[0]["drop"] == "rb1"


# --- trade_targets fairness ratio: both sides > 0 alone lets through wildly
# lopsided "trades" - see the conversation this was built from. Now scored
# by position_value_team_total (points above replacement), not a lineup-
# total delta - a "change of scenery" shape: a_k2 is genuinely good but
# buried on the bench behind a_k1 (discounted 50% as depth), while B has NO
# kicker at all, so receiving a_k2 makes him B's full-value STARTER;
# symmetrically, b_rb2 is a real RB but buried behind a dominant b_rb1
# (discounted depth), becoming pure discounted depth on A's side too (A's
# own a_rb1 is even bigger, so it never displaces him as starter either).
_TRADE_SLOTS = {"RB": 1, "K": 1}
_TRADE_ELIGIBILITY = {"RB": {"RB"}, "K": {"K"}}
_TRADE_WEEKS = [1, 2]
_TRADE_FA = {
    "RB": [PlayerCtx(id="fa_rb", position="RB", ros_total=10.0, weekly={1: 5.0, 2: 5.0})],
    "K": [PlayerCtx(id="fa_k", position="K", ros_total=2.0, weekly={1: 1.0, 2: 1.0})],
}


def _wk(pid, pos, weekly: dict) -> PlayerCtx:
    return PlayerCtx(id=pid, position=pos, ros_total=sum(weekly.values()), weekly=weekly)


def _trade_setup(a_k2_ppw: float, b_rb2_ppw: float):
    """My team (A): a dominant RB1 (a_rb1=20, never displaced) and a
    dominant starting kicker (a_k1=10, never displaced by a_k2), plus a_k2 -
    a real bench kicker (tunable ppw) I'd give away, discounted 50% as
    depth behind a_k1. Partner (B): a dominant RB1 (b_rb1=15, never
    displaced by b_rb2) plus b_rb2 - a real bench RB (tunable ppw),
    discounted 50% as depth behind b_rb1 - and NO rostered kicker at all,
    so a_k2 would become B's own full-value STARTER if traded there."""
    players = {
        "a_rb1": _wk("a_rb1", "RB", {1: 20.0, 2: 20.0}),
        "a_k1": _wk("a_k1", "K", {1: 10.0, 2: 10.0}),
        "a_k2": _wk("a_k2", "K", {1: a_k2_ppw, 2: a_k2_ppw}),
        "b_rb1": _wk("b_rb1", "RB", {1: 15.0, 2: 15.0}),
        "b_rb2": _wk("b_rb2", "RB", {1: b_rb2_ppw, 2: b_rb2_ppw}),
    }
    team_a = ["a_rb1", "a_k1", "a_k2"]
    team_b = ["b_rb1", "b_rb2"]
    return players, team_a, team_b


def test_trade_targets_excludes_a_lopsided_trade_below_the_fairness_ratio():
    # a_k2=3 (+2 over the K bar, discounted to +1 as MY depth) vs.
    # b_rb2=8.9 (+3.9 over the RB bar, discounted to +1.9 as depth either
    # side) - my gain (1.9) dwarfs the partner's gain from a_k2 becoming
    # their full-value starter (2*2 - 3.9 = 0.1). Both positive, ratio
    # ~0.05, nowhere near 0.5.
    players, team_a, team_b = _trade_setup(a_k2_ppw=3.0, b_rb2_ppw=8.9)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=_TRADE_FA, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
        eligibility=_TRADE_ELIGIBILITY, max_trade_targets=20, fairness_ratio=0.5,
    )
    matches = [r for r in results if r["give"] == ["a_k2"] and r["get"] == ["b_rb2"]]
    assert matches == []


def test_trade_targets_includes_a_trade_within_the_fairness_ratio():
    # Same a_k2, but b_rb2=8.0 (+3 over the RB bar, discounted to +1.5 as
    # depth) - now my gain (1.5-1=... see gain_self below) and the
    # partner's gain from a_k2's full-value promotion are comparable.
    players, team_a, team_b = _trade_setup(a_k2_ppw=3.0, b_rb2_ppw=8.0)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=_TRADE_FA, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
        eligibility=_TRADE_ELIGIBILITY, max_trade_targets=20, fairness_ratio=0.5,
    )
    matches = [r for r in results if r["give"] == ["a_k2"] and r["get"] == ["b_rb2"]]
    assert len(matches) == 1
    assert matches[0]["gain_self"] > 0
    assert matches[0]["gain_partner"] > 0


def test_trade_targets_fairness_ratio_zero_reduces_to_the_old_both_positive_check():
    # fairness_ratio=0.0 (min/max always >= 0) is a no-op on top of the
    # existing > 0 check - the lopsided trade from the exclusion test above
    # reappears once fairness is switched off.
    players, team_a, team_b = _trade_setup(a_k2_ppw=3.0, b_rb2_ppw=8.9)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=_TRADE_FA, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
        eligibility=_TRADE_ELIGIBILITY, max_trade_targets=20, fairness_ratio=0.0,
    )
    matches = [r for r in results if r["give"] == ["a_k2"] and r["get"] == ["b_rb2"]]
    assert len(matches) == 1


def test_trade_targets_does_not_crash_on_a_zero_gain_candidate():
    # Regression test for a real live pipeline crash (ZeroDivisionError in
    # the fairness check's min/max ratio) - a candidate whose gain is
    # exactly 0 for one or both sides must be excluded, never evaluated
    # through the ratio at all. Identical-value 1-for-1 swap: both rosters'
    # points-above-replacement total is unchanged by the trade, so
    # gain_self == gain_partner == 0 exactly for this candidate.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {
        "a1": _wk("a1", "RB", {1: 10.0, 2: 10.0}),
        "b1": _wk("b1", "RB", {1: 10.0, 2: 10.0}),
    }
    team_a = ["a1"]
    team_b = ["b1"]
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos={}, weeks=weeks, slots=slots,
        eligibility=eligibility, max_trade_targets=20, fairness_ratio=0.5,
    )
    assert results == []


def test_trade_targets_stays_symmetric_even_when_my_team_would_be_overpaying():
    # Unlike the Trade Calculator's client-side twin (docs/js/trade.js's
    # tradeSuggestions), these passive/browse-only suggestions stay
    # symmetric in BOTH directions. a_k2=5 (discounted depth gain for me:
    # 0.1) vs. b_rb2=9.1 (the partner's full-value-promotion gain: 3.9) -
    # my own gain is barely positive while the partner's is ~40x bigger,
    # nowhere near the 0.5 fairness ratio, so this stays excluded even
    # though I'm the one giving up disproportionately more value (see the
    # conversation this was built from).
    players, team_a, team_b = _trade_setup(a_k2_ppw=5.0, b_rb2_ppw=9.1)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=_TRADE_FA, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
        eligibility=_TRADE_ELIGIBILITY, max_trade_targets=20, fairness_ratio=0.5,
    )
    matches = [r for r in results if r["give"] == ["a_k2"] and r["get"] == ["b_rb2"]]
    assert matches == []


# --- position_value_matrix: points-above-replacement PER POSITION (not per
# slot, not per player) - see the conversation this was built from. An
# earlier version bucketed by lineup slot instance (QB/RB/WR1/WR2/TE/K/
# FLEX1/FLEX2) with a greedy fill order, which meant a single bench RB could
# get "depth" credit counted separately under RB AND FLEX1 AND FLEX2 - the
# same asset valued three times over (see the now-removed
# test_slot_value_matrix_montgomery_style_double_counts_by_design). This
# fixture reproduces the real Week-4 "Balls Deep" (O-League) worked example
# from that conversation, using the league's real slot config (QB/RB/WR x2/
# TE/K/FLEX x2) and real (rounded) projections, so these numbers are a
# direct check against that derivation, not just internally-consistent
# arithmetic.
_SVM_SLOTS = {"QB": 1, "RB": 1, "WR": 2, "TE": 1, "K": 1, "RB/WR/TE": 2}
_SVM_ELIGIBILITY = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "RB/WR/TE": {"RB", "WR", "TE"}}
_SVM_WEEKS = [4]


def _svm_team():
    players = {
        "williams": _wk("williams", "QB", {4: 26.26}),
        "jones": _wk("jones", "QB", {4: 17.45}),
        "walker": _wk("walker", "RB", {4: 16.28}),
        "montgomery": _wk("montgomery", "RB", {4: 11.59}),
        "stevenson": _wk("stevenson", "RB", {4: 10.29}),
        "monangai": _wk("monangai", "RB", {4: 8.38}),
        "allen": _wk("allen", "RB", {4: 4.27}),
        "stbrown": _wk("stbrown", "WR", {4: 12.27}),
        "adams": _wk("adams", "WR", {4: 7.65}),
        "johnston": _wk("johnston", "WR", {4: 7.08}),
        "stribling": _wk("stribling", "WR", {4: 5.47}),
        "ridley": _wk("ridley", "WR", {4: 4.84}),
        "pitts": _wk("pitts", "TE", {4: 6.01}),
        "ferguson": _wk("ferguson", "TE", {4: 5.03}),
        "pineiro": _wk("pineiro", "K", {4: 8.80}),
    }
    team = list(players.keys())
    free_agents = {
        "QB": [_wk("willis", "QB", {4: 17.24})],
        "RB": [_wk("harris", "RB", {4: 4.80})],
        "WR": [_wk("reed", "WR", {4: 6.52})],
        "TE": [_wk("schultz", "TE", {4: 5.01})],
        "K": [_wk("santos", "K", {4: 10.02})],
    }
    return players, team, free_agents


def test_position_value_matrix_matches_the_real_worked_example():
    players, team, free_agents = _svm_team()
    result = position_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)

    expected = {
        "QB": (9.02, 0.21),
        "RB": (20.32, 3.58),
        "WR": (6.88, 0.56),
        "TE": (1.00, 0.02),
        "K": (-1.22, 0.0),
    }
    for pos, (starting, depth) in expected.items():
        assert result[pos]["starting_value"] == pytest.approx(starting, abs=1e-2), pos
        assert result[pos]["depth_value"] == pytest.approx(depth, abs=1e-2), pos
        assert result[pos]["total"] == pytest.approx(starting + 0.5 * depth, abs=1e-2), pos


def test_position_value_matrix_starter_can_go_negative():
    # This team's only kicker projects below the best available kicker -
    # a real, informative signal, not something to hide behind a floor.
    players, team, free_agents = _svm_team()
    result = position_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    assert result["K"]["starting_value"] < 0


def test_position_value_matrix_true_bye_contributes_zero_not_negative():
    # A single-RB team whose only RB is on bye (0.0 that week, same sentinel
    # engine/valuation.py already uses) has no real asset to devalue - 0,
    # not "0 minus a positive replacement value" (which would be negative).
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1]
    players = {"only_rb": _wk("only_rb", "RB", {1: 0.0})}
    free_agents = {"RB": [_wk("fa_rb", "RB", {1: 12.0})]}
    result = position_value_matrix(["only_rb"], players, free_agents, weeks, slots, eligibility)
    assert result["RB"]["starting_value"] == 0.0
    assert result["RB"]["depth_value"] == 0.0


def test_position_value_matrix_below_replacement_starter_is_negative_not_zeroed():
    # Contrast with the bye case above - a REAL rostered player who's worse
    # than the best free agent is a genuine negative-value asset.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1]
    players = {"weak_rb": _wk("weak_rb", "RB", {1: 3.0})}
    free_agents = {"RB": [_wk("fa_rb", "RB", {1: 12.0})]}
    result = position_value_matrix(["weak_rb"], players, free_agents, weeks, slots, eligibility)
    assert result["RB"]["starting_value"] == pytest.approx(-9.0)


def test_position_value_matrix_montgomery_counted_exactly_once():
    # Montgomery (11.59) is the best-projected RB left after Walker claims
    # the dedicated RB slot, so the real optimizer starts him at FLEX1 - his
    # value belongs to RB's starting_value (his own position), not RB's
    # depth chain and not a separate FLEX bucket. The old slot-instance
    # version counted him three times over (RB depth + FLEX1 starting +
    # FLEX2 depth, see test_slot_value_matrix_montgomery_style_double_counts
    # _by_design in this file's prior revision) - RB's depth_value here
    # (3.58) is far smaller than that version's (15.86) because Montgomery
    # moved out of the depth chain entirely once he's the one actually
    # starting.
    players, team, free_agents = _svm_team()
    result = position_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    assert result["RB"]["depth_value"] == pytest.approx(3.58, abs=1e-2)
    assert result["RB"]["starting_value"] == pytest.approx(20.32, abs=1e-2)


def test_position_value_matrix_team_total_is_lower_than_old_slot_instance_scheme():
    # Regression guard for the fix itself: summing every position's total
    # must come out lower than the old per-slot-instance scheme's sum for
    # this same worked example (49.475, from the now-removed slot_value_
    # matrix version) precisely because multi-slot-eligible bench players
    # no longer get valued more than once.
    players, team, free_agents = _svm_team()
    result = position_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    team_total = sum(v["total"] for v in result.values())
    assert team_total == pytest.approx(38.19, abs=1e-2)
    assert team_total < 49.475
