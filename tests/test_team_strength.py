import pytest

from engine.team_strength import (
    PlayerCtx,
    depth_values,
    depth_values_by_week,
    fa_values,
    lineup_total,
    lineup_total_with_streaming,
    optimal_lineup_for_week_with_bye_fill,
    pickups,
    position_strength,
    rank_and_compare,
    slot_strength,
    slot_value_matrix,
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


def test_depth_values_rb1_higher_than_rb2_with_steep_dropoff():
    # 3 RBs on a 2-RB-slot team: RB1/RB2 both start, RB3 is a zero-value bench arm.
    players = {
        "rb1": _player("rb1", "RB", 20.0),
        "rb2": _player("rb2", "RB", 15.0),
        "rb3": _player("rb3", "RB", 2.0),
    }
    values = depth_values(["rb1", "rb2", "rb3"], players, {}, WEEKS, SLOTS, ELIGIBILITY)
    assert values["rb1"]["value_delta"] > values["rb2"]["value_delta"] > 0
    assert values["rb3"]["value_delta"] == pytest.approx(0.0)  # never starts either way


def test_depth_values_by_week_spikes_on_starters_bye():
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={1: 20.0, 2: 0.0}),  # bye week 2
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=10.0, weekly={1: 5.0, 2: 5.0}),
    }
    by_week = depth_values_by_week(["rb1", "rb2"], players, {}, weeks, slots, eligibility)
    # rb2 is worthless while rb1 starts (week 1), but fully replaces the bye (week 2).
    assert by_week["rb2"]["value_delta"][1] == pytest.approx(0.0)
    assert by_week["rb2"]["value_delta"][2] == pytest.approx(5.0)
    # summing the weekly breakdown must match depth_values()'s ROS total
    ros = depth_values(["rb1", "rb2"], players, {}, weeks, slots, eligibility)
    assert sum(by_week["rb2"]["value_delta"].values()) == pytest.approx(ros["rb2"]["value_delta"])


def test_depth_values_waiver_backfill_reduces_value_delta():
    # A rostered player's own consolidated value already accounts for the
    # waiver wire - compare against the SAME roster with no free agents
    # available at all, rather than two parallel fields on one call.
    players = {
        "rb1": _player("rb1", "RB", 20.0),
        "rb2": _player("rb2", "RB", 15.0),
    }
    fa = _player("fa1", "RB", 18.0)  # a strong free agent backfill option
    values_no_fa = depth_values(["rb1", "rb2"], players, {}, WEEKS, SLOTS, ELIGIBILITY)
    values_with_fa = depth_values(["rb1", "rb2"], players, {"RB": [fa]}, WEEKS, SLOTS, ELIGIBILITY)
    # losing rb1 (20 ppw starter) without backfill costs the full 20ppw*2wk;
    # with an 18ppw free agent available, the loss is much smaller.
    assert values_with_fa["rb1"]["value_delta"] < values_no_fa["rb1"]["value_delta"]


def test_depth_values_by_week_prefers_bench_over_a_weaker_free_agent():
    # A weak free agent is available, but the bench teammate outscores him
    # every week - the free agent is only ADDED to the candidate pool, never
    # forced into the lineup, so the bench teammate must still be named.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=40.0, weekly={1: 20.0, 2: 20.0}),
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=13.0, weekly={1: 5.0, 2: 8.0}),
    }
    free_agents = {"RB": [PlayerCtx(id="fa_weak", position="RB", ros_total=2.0, weekly={1: 1.0, 2: 1.0})]}
    by_week = depth_values_by_week(["rb1", "rb2"], players, free_agents, weeks, slots, eligibility)
    rb1 = by_week["rb1"]
    assert rb1["replacement_id"] == {1: "rb2", 2: "rb2"}
    assert rb1["value_delta"][1] == pytest.approx(15.0)  # 20 - rb2's wk1 5
    assert rb1["value_delta"][2] == pytest.approx(12.0)  # 20 - rb2's wk2 8


def test_depth_values_by_week_names_a_free_agent_replacement_that_can_change_weekly():
    # Two free agents whose better week flips: FA-A the better play in
    # week 1, FA-B in week 2, both stronger than the bench teammate every
    # week - the NAMED replacement must flip with it, not stay pinned to
    # whichever one wins by season-long ros_total, and not fall back to the
    # (weaker) bench teammate.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1, 2]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=40.0, weekly={1: 20.0, 2: 20.0}),
        "rb2": PlayerCtx(id="rb2", position="RB", ros_total=13.0, weekly={1: 5.0, 2: 8.0}),
    }
    free_agents = {
        "RB": [
            PlayerCtx(id="fa_a", position="RB", ros_total=10.0, weekly={1: 9.0, 2: 1.0}),
            PlayerCtx(id="fa_b", position="RB", ros_total=8.0, weekly={1: 2.0, 2: 9.0}),
        ]
    }
    by_week = depth_values_by_week(["rb1", "rb2"], players, free_agents, weeks, slots, eligibility)
    rb1 = by_week["rb1"]
    assert rb1["replacement_id"] == {1: "fa_a", 2: "fa_b"}
    assert rb1["value_delta"][1] == pytest.approx(11.0)  # 20 - fa_a's wk1 9
    assert rb1["value_delta"][2] == pytest.approx(11.0)  # 20 - fa_b's wk2 9


def test_depth_values_by_week_value_delta_floored_at_zero():
    # A free agent who genuinely beats a mediocre rostered player can make
    # the candidate lineup score HIGHER than the real one even with that
    # player gone - a real negative raw delta, floored to 0 rather than
    # shown as a negative "value".
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1]
    players = {"rb1": PlayerCtx(id="rb1", position="RB", ros_total=5.0, weekly={1: 5.0})}
    free_agents = {"RB": [PlayerCtx(id="fa_better", position="RB", ros_total=12.0, weekly={1: 12.0})]}
    by_week = depth_values_by_week(["rb1"], players, free_agents, weeks, slots, eligibility)
    assert by_week["rb1"]["value_delta"][1] == pytest.approx(0.0)
    assert by_week["rb1"]["replacement_id"][1] == "fa_better"


def test_depth_values_by_week_replacement_id_none_when_bench_too_thin():
    # No bench RB at all - dropping the only RB starter changes nothing
    # about who else starts (nobody else is even RB-eligible).
    slots = {"RB": 1, "WR": 1}
    eligibility = {"RB": {"RB"}, "WR": {"WR"}}
    weeks = [1]
    players = {
        "rb1": PlayerCtx(id="rb1", position="RB", ros_total=20.0, weekly={1: 20.0}),
        "wr1": PlayerCtx(id="wr1", position="WR", ros_total=10.0, weekly={1: 10.0}),
    }
    by_week = depth_values_by_week(["rb1", "wr1"], players, {}, weeks, slots, eligibility)
    assert by_week["rb1"]["replacement_id"][1] is None


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


def test_pickups_does_not_suggest_covering_a_bye_the_baseline_already_assumes():
    # Rostering a single kicker with a bye week should NOT surface as a
    # "pickup" once the baseline already assumes bye-week streaming - the
    # only free agent kicker available is exactly the one the baseline
    # would already stream in, so actually rostering him full-time gains ~0.
    k_slots = {"K": 1}
    k_eligibility = {"K": {"K"}}
    weeks = [1, 2]
    players = {"k1": PlayerCtx(id="k1", position="K", ros_total=10.0, weekly={1: 10.0, 2: 0.0})}
    backup_fa = PlayerCtx(id="k_fa", position="K", ros_total=16.0, weekly={1: 8.0, 2: 8.0})
    free_agents = {"K": [backup_fa]}

    result = pickups(["k1"], players, free_agents, weeks, k_slots, k_eligibility, max_pickups=5)
    assert result == []


def test_fa_values_includes_negative_gains_unlike_pickups():
    players = {"rb1": _player("rb1", "RB", 20.0), "rb2": _player("rb2", "RB", 4.0)}
    strong_fa = _player("fa_strong", "RB", 25.0)
    weak_fa = _player("fa_weak", "RB", 1.0)
    free_agents = {"RB": [strong_fa, weak_fa]}
    values = fa_values(["rb1", "rb2"], players, free_agents, WEEKS, SLOTS, ELIGIBILITY)
    assert values["fa_strong"]["gain"] > 0
    assert values["fa_weak"]["gain"] <= 0  # worse than the worst rostered RB
    # pickups() filters fa_weak out; fa_values() keeps it visible.
    pickup_result = pickups(["rb1", "rb2"], players, free_agents, WEEKS, SLOTS, ELIGIBILITY, max_pickups=5)
    assert {c["add"] for c in pickup_result} == {"fa_strong"}


def test_fa_values_weekly_breakdown_sums_to_gain():
    # Same sum-check pattern as test_depth_values_by_week_spikes_on_starters_
    # bye above - a player modal's NMD week-by-week view is only trustworthy
    # if the weekly numbers it shows actually add up to the headline total.
    players = {"rb1": _player("rb1", "RB", 20.0), "rb2": _player("rb2", "RB", 4.0)}
    strong_fa = _player("fa_strong", "RB", 25.0)
    free_agents = {"RB": [strong_fa]}
    values = fa_values(["rb1", "rb2"], players, free_agents, WEEKS, SLOTS, ELIGIBILITY)
    assert sum(values["fa_strong"]["weekly"].values()) == pytest.approx(values["fa_strong"]["gain"])
    assert set(values["fa_strong"]["weekly"]) == set(WEEKS)


def test_pickups_prefers_same_position_drop_over_unrelated_bench_player():
    # A weak kicker plus an unrelated, even-weaker bench WR: a strong FA
    # kicker should suggest dropping the weak KICKER, not the bench WR
    # (keeping two kickers while cutting an unrelated skill player).
    slots = {"K": 1, "WR": 1}
    eligibility = {"K": {"K"}, "WR": {"WR"}}
    players = {
        "k_weak": _player("k_weak", "K", 5.0),
        "wr_bench": _player("wr_bench", "WR", 1.0),
    }
    strong_fa_k = _player("k_strong", "K", 15.0)
    result = pickups(["k_weak", "wr_bench"], players, {"K": [strong_fa_k]}, WEEKS, slots, eligibility, max_pickups=5)
    assert len(result) == 1
    assert result[0]["add"] == "k_strong"
    assert result[0]["drop"] == "k_weak"


def test_pickups_only_returns_positive_gain_sorted():
    players = {"rb1": _player("rb1", "RB", 5.0), "rb2": _player("rb2", "RB", 4.0)}
    strong_fa = _player("fa_strong", "RB", 25.0)
    weak_fa = _player("fa_weak", "RB", 1.0)
    free_agents = {"RB": [strong_fa, weak_fa]}
    result = pickups(["rb1", "rb2"], players, free_agents, WEEKS, SLOTS, ELIGIBILITY, max_pickups=5)
    assert len(result) == 1  # only the strong FA beats a rostered starter
    assert result[0]["add"] == "fa_strong"
    assert result[0]["gain"] > 0


# --- trade_targets fairness ratio: both sides > 0 alone lets through wildly
# lopsided "trades" (send a duplicate kicker, take back a real bench RB) -
# see the conversation this was built from. Two positions (RB/K) with a weak
# K free-agent pool, week-varying RB output (a bye week), and 3 weeks so a
# "throwaway" bench RB can earn real value by covering a bye - the same shape
# a real roster produces, not just a toy constant-ppw swap.
_TRADE_SLOTS = {"RB": 1, "K": 1}
_TRADE_ELIGIBILITY = {"RB": {"RB"}, "K": {"K"}}
_TRADE_WEEKS = [1, 2, 3]


def _wk(pid, pos, weekly: dict) -> PlayerCtx:
    return PlayerCtx(id=pid, position=pos, ros_total=sum(weekly.values()), weekly=weekly)


def _trade_setup(a_k2_ppw: float):
    """My team: a real RB1, a weak everyday RB2 (covers RB1's bye), and TWO
    kickers - a starter and a near-duplicate backup (a_k2) I'd give away.
    Partner: a real RB1 plus a genuinely idle bench RB2 (never once beats
    RB1, even on RB1's off weeks - not a real trade chip on its own) and NO
    rostered kicker at all (relies on a weak FA kicker). a_k2_ppw controls
    how much the kicker side of the trade is worth to the partner, without
    touching my own side's gain at all."""
    players = {
        "a_rb1": _wk("a_rb1", "RB", {1: 20.0, 2: 0.0, 3: 20.0}),  # bye week 2
        "a_rb2": _wk("a_rb2", "RB", {1: 1.0, 2: 1.0, 3: 1.0}),
        "a_k1": _wk("a_k1", "K", {1: 5.0, 2: 5.0, 3: 5.0}),
        "a_k2": _wk("a_k2", "K", {w: a_k2_ppw for w in _TRADE_WEEKS}),
        "b_rb1": _wk("b_rb1", "RB", {1: 10.0, 2: 10.0, 3: 10.0}),
        "b_rb2": _wk("b_rb2", "RB", {1: 9.0, 2: 9.0, 3: 9.0}),
    }
    free_agents = {"K": [_wk("fa_k", "K", {1: 0.5, 2: 0.5, 3: 0.5})]}
    team_a = ["a_rb1", "a_rb2", "a_k1", "a_k2"]
    team_b = ["b_rb1", "b_rb2"]
    return players, free_agents, team_a, team_b


def test_trade_targets_excludes_a_lopsided_trade_below_the_fairness_ratio():
    # a_k2 barely edges out the FA kicker (0.6 > 0.5) - the partner gains
    # almost nothing from it, while I gain a lot from b_rb2 covering my RB1's
    # bye (real, sizeable gain). Both sides are positive but nowhere close.
    players, free_agents, team_a, team_b = _trade_setup(a_k2_ppw=0.6)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=free_agents, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
        eligibility=_TRADE_ELIGIBILITY, max_trade_targets=20, fairness_ratio=0.5,
    )
    matches = [r for r in results if r["give"] == ["a_k2"] and r["get"] == ["b_rb2"]]
    assert matches == []


def test_trade_targets_includes_a_trade_within_the_fairness_ratio():
    # Same roster shape, but a_k2 is a real, useful kicker - the partner's
    # gain (a real kicker over a weak FA stream) is now comparable in size
    # to my own gain, so the trade clears the fairness bar.
    players, free_agents, team_a, team_b = _trade_setup(a_k2_ppw=4.0)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=free_agents, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
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
    players, free_agents, team_a, team_b = _trade_setup(a_k2_ppw=0.6)
    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=free_agents, weeks=_TRADE_WEEKS, slots=_TRADE_SLOTS,
        eligibility=_TRADE_ELIGIBILITY, max_trade_targets=20, fairness_ratio=0.0,
    )
    matches = [r for r in results if r["give"] == ["a_k2"] and r["get"] == ["b_rb2"]]
    assert len(matches) == 1


def test_trade_targets_does_not_crash_on_a_zero_gain_candidate():
    # Regression test for a real live pipeline crash (ZeroDivisionError in
    # the fairness check's min/max ratio) - a candidate whose gain is
    # exactly 0 for one or both sides must be excluded, never evaluated
    # through the ratio at all. Identical-value 1-for-1 swap: both sides'
    # lineup total is unchanged by the trade, so gain_self == gain_partner
    # == 0 exactly for this candidate.
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
    # symmetric in BOTH directions - I give away a luxury duplicate RB
    # that's worth 0 to ME for a modest kicker upgrade (tiny gain for me,
    # huge gain for the partner from covering their bye) - ratio ~0.012,
    # nowhere near 0.5, so this stays excluded even though I'm the one
    # "overpaying" (see the conversation this was built from).
    slots = {"RB": 1, "K": 1}
    eligibility = {"RB": {"RB"}, "K": {"K"}}
    weeks = [1, 2, 3]
    players = {
        "a_rb1": _wk("a_rb1", "RB", {1: 20.0, 2: 20.0, 3: 20.0}),  # no bye - always starts
        "a_rb2": _wk("a_rb2", "RB", {1: 15.0, 2: 15.0, 3: 15.0}),  # never beats a_rb1 - worth 0 to me
        "b_rb1": _wk("b_rb1", "RB", {1: 10.0, 2: 0.0, 3: 10.0}),  # bye week 2, no RB depth
        "b_k1": _wk("b_k1", "K", {1: 0.6, 2: 0.6, 3: 0.6}),
    }
    free_agents = {"K": [_wk("fa_k", "K", {1: 0.5, 2: 0.5, 3: 0.5})]}  # no RB free agents - a real bye-week gap
    team_a = ["a_rb1", "a_rb2"]
    team_b = ["b_rb1", "b_k1"]

    results = trade_targets(
        team_id=1, team_player_ids=team_a, other_teams={2: team_b}, players=players,
        free_agents_by_pos=free_agents, weeks=weeks, slots=slots,
        eligibility=eligibility, max_trade_targets=20, fairness_ratio=0.5,
    )
    matches = [r for r in results if r["give"] == ["a_rb2"] and r["get"] == ["b_k1"]]
    assert matches == []


# --- slot_value_matrix: points-above-replacement PER SLOT (not per player) -
# see the conversation this was built from. This fixture reproduces the
# real Week-4 "Balls Deep" (O-League) worked example hand-derived in that
# conversation, using the league's real slot config (QB/RB/WR x2/TE/K/FLEX
# x2) and real (rounded) projections, so these numbers are a direct check
# against that derivation, not just internally-consistent arithmetic.
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


def test_slot_value_matrix_matches_the_real_worked_example():
    players, team, free_agents = _svm_team()
    result = slot_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)

    expected = {
        "QB": (9.02, 0.21),
        "RB": (11.48, 15.86),
        "WR1": (5.75, 1.69),
        "WR2": (1.13, 0.56),
        "TE": (1.00, 0.02),
        "FLEX1": (5.07, 6.19),
        "FLEX2": (3.77, 2.42),
        "K": (-1.22, 0.0),
    }
    for label, (starting, depth) in expected.items():
        assert result[label]["starting_value"] == pytest.approx(starting, abs=1e-2), label
        assert result[label]["depth_value"] == pytest.approx(depth, abs=1e-2), label
        assert result[label]["total"] == pytest.approx(starting + 0.5 * depth, abs=1e-2), label


def test_slot_value_matrix_wr1_and_wr2_depth_reads_genuinely_differ():
    # WR1's depth pool excludes only its own pick (nothing WR-type precedes
    # it); WR2's depth pool ALSO excludes WR1's starter, since WR1 precedes
    # WR2 in the fill order - the two numbers must NOT be equal.
    players, team, free_agents = _svm_team()
    result = slot_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    assert result["WR1"]["depth_value"] != pytest.approx(result["WR2"]["depth_value"])
    assert result["WR1"]["depth_value"] == pytest.approx(1.69, abs=1e-2)
    assert result["WR2"]["depth_value"] == pytest.approx(0.56, abs=1e-2)


def test_slot_value_matrix_flex1_and_flex2_depth_reads_genuinely_differ():
    players, team, free_agents = _svm_team()
    result = slot_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    assert result["FLEX1"]["depth_value"] != pytest.approx(result["FLEX2"]["depth_value"])


def test_slot_value_matrix_starter_can_go_negative():
    # This team's only kicker projects below the best available kicker -
    # a real, informative signal, not something to hide behind a floor.
    players, team, free_agents = _svm_team()
    result = slot_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    assert result["K"]["starting_value"] < 0


def test_slot_value_matrix_true_bye_contributes_zero_not_negative():
    # A single-RB team whose only RB is on bye (0.0 that week, same sentinel
    # engine/valuation.py already uses) has no real asset to devalue - 0,
    # not "0 minus a positive replacement value" (which would be negative).
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1]
    players = {"only_rb": _wk("only_rb", "RB", {1: 0.0})}
    free_agents = {"RB": [_wk("fa_rb", "RB", {1: 12.0})]}
    result = slot_value_matrix(["only_rb"], players, free_agents, weeks, slots, eligibility)
    assert result["RB"]["starting_value"] == 0.0
    assert result["RB"]["depth_value"] == 0.0


def test_slot_value_matrix_below_replacement_starter_is_negative_not_zeroed():
    # Contrast with the bye case above - a REAL rostered player who's worse
    # than the best free agent is a genuine negative-value asset.
    slots = {"RB": 1}
    eligibility = {"RB": {"RB"}}
    weeks = [1]
    players = {"weak_rb": _wk("weak_rb", "RB", {1: 3.0})}
    free_agents = {"RB": [_wk("fa_rb", "RB", {1: 12.0})]}
    result = slot_value_matrix(["weak_rb"], players, free_agents, weeks, slots, eligibility)
    assert result["RB"]["starting_value"] == pytest.approx(-9.0)


def test_slot_value_matrix_montgomery_style_double_counts_by_design():
    # A player not claimed as a base-position starter contributes to BOTH
    # his own position's depth chain AND a FLEX slot's starting value -
    # intentional (two independent "what if this exact role needed him"
    # lenses, not one real simultaneous lineup) - see the conversation this
    # was built from.
    players, team, free_agents = _svm_team()
    result = slot_value_matrix(team, players, free_agents, _SVM_WEEKS, _SVM_SLOTS, _SVM_ELIGIBILITY)
    # Montgomery (11.59) is the top of RB's OWN depth chain (RB depth's
    # first/largest contributor) AND separately FLEX1's starting pick -
    # confirmed indirectly via the exact depth/starting totals above, which
    # only reconcile to the hand-derived numbers if both readings happened.
    assert result["RB"]["depth_value"] == pytest.approx(15.86, abs=1e-2)
    assert result["FLEX1"]["starting_value"] == pytest.approx(5.07, abs=1e-2)
