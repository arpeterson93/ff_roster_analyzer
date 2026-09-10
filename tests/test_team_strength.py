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
    players = {
        "rb1": _player("rb1", "RB", 20.0),
        "rb2": _player("rb2", "RB", 15.0),
    }
    fa = _player("fa1", "RB", 18.0)  # a strong free agent backfill option
    free_agents = {"RB": [fa]}
    values = depth_values(["rb1", "rb2"], players, free_agents, WEEKS, SLOTS, ELIGIBILITY)
    # losing rb1 (20 ppw starter) without backfill costs the full 20ppw*2wk;
    # with an 18ppw free agent available, the loss is much smaller.
    assert values["rb1"]["value_delta_ww"] < values["rb1"]["value_delta"]


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
