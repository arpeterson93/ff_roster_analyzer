import json
from pathlib import Path

import pytest

from engine.team_strength import PlayerCtx, lineup_total_with_streaming_by_week
from engine.trades import evaluate, evaluate_with_streaming

SLOTS = {"QB": 1, "RB": 1}
ELIGIBILITY = {"QB": {"QB"}, "RB": {"RB"}}
WEEKS = [1, 2, 3]

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trade_case.json"


def _players():
    def p(pid, pos, ppw):
        return PlayerCtx(id=pid, position=pos, ros_total=ppw * len(WEEKS), weekly={w: ppw for w in WEEKS})

    return {
        "qbA1": p("qbA1", "QB", 18.0),
        "rbA1": p("rbA1", "RB", 20.0),
        "rbA2": p("rbA2", "RB", 5.0),
        "qbB1": p("qbB1", "QB", 10.0),
        "rbB1": p("rbB1", "RB", 8.0),
        "rbB2": p("rbB2", "RB", 22.0),
    }


ROSTER_A = ["qbA1", "rbA1", "rbA2"]
ROSTER_B = ["qbB1", "rbB1", "rbB2"]


def test_trade_favors_a():
    players = _players()
    result = evaluate(
        gives_a=["rbA2"], gives_b=["rbB2"], roster_a=ROSTER_A, roster_b=ROSTER_B,
        players=players, weeks=WEEKS, slots=SLOTS, eligibility=ELIGIBILITY,
    )
    assert result.side_a.before == pytest.approx(114.0)  # (18+20)*3
    assert result.side_a.after == pytest.approx(120.0)  # (18+22)*3
    assert result.side_a.gain == pytest.approx(6.0)
    assert result.side_b.before == pytest.approx(96.0)  # (10+22)*3
    assert result.side_b.after == pytest.approx(54.0)  # (10+8)*3
    assert result.side_b.gain == pytest.approx(-42.0)
    assert result.favors == "a"


def test_trade_favors_b():
    players = _players()
    result = evaluate(
        gives_a=["rbA1"], gives_b=["rbB1"], roster_a=ROSTER_A, roster_b=ROSTER_B,
        players=players, weeks=WEEKS, slots=SLOTS, eligibility=ELIGIBILITY,
    )
    assert result.side_a.gain == pytest.approx(-36.0)
    assert result.side_b.gain == pytest.approx(0.0)
    assert result.favors == "b"


def test_trade_even_when_neither_lineup_changes():
    players = _players()
    result = evaluate(
        gives_a=["rbA2"], gives_b=["rbB1"], roster_a=ROSTER_A, roster_b=ROSTER_B,
        players=players, weeks=WEEKS, slots=SLOTS, eligibility=ELIGIBILITY,
    )
    assert result.side_a.gain == pytest.approx(0.0)
    assert result.side_b.gain == pytest.approx(0.0)
    assert result.favors == "even"


def test_weekly_breakdown_sums_to_gain_and_matches_before_after():
    players = _players()
    result = evaluate(
        gives_a=["rbA2"], gives_b=["rbB2"], roster_a=ROSTER_A, roster_b=ROSTER_B,
        players=players, weeks=WEEKS, slots=SLOTS, eligibility=ELIGIBILITY,
    )
    assert len(result.side_a.weekly) == 3
    assert sum(w["delta"] for w in result.side_a.weekly) == pytest.approx(result.side_a.gain)
    assert sum(w["before"] for w in result.side_a.weekly) == pytest.approx(result.side_a.before)
    assert sum(w["after"] for w in result.side_a.weekly) == pytest.approx(result.side_a.after)
    # constant weekly inputs -> every week's delta should be identical (2.0/wk)
    assert all(w["delta"] == pytest.approx(2.0) for w in result.side_a.weekly)


def test_raw_given_received_are_ros_totals():
    players = _players()
    result = evaluate(
        gives_a=["rbA2"], gives_b=["rbB2"], roster_a=ROSTER_A, roster_b=ROSTER_B,
        players=players, weeks=WEEKS, slots=SLOTS, eligibility=ELIGIBILITY,
    )
    assert result.side_a.raw_given == pytest.approx(15.0)  # rbA2 ros_total = 5*3
    assert result.side_a.raw_received == pytest.approx(66.0)  # rbB2 ros_total = 22*3


def test_evaluate_with_streaming_discounts_a_trade_the_wire_could_replace():
    # Team A's only kicker is on bye in week 2. Team B offers a kicker who
    # doesn't help team A's own roster otherwise, but does fill that bye.
    # A plain evaluate() gives full credit for filling the gap; the
    # streaming-aware version should only credit the edge over what a free
    # agent kicker already on the wire would have scored that same week.
    slots = {"K": 1}
    eligibility = {"K": {"K"}}
    weeks = [1, 2]
    players = {
        "kA1": PlayerCtx(id="kA1", position="K", ros_total=10.0, weekly={1: 10.0, 2: 0.0}),
        "kB1": PlayerCtx(id="kB1", position="K", ros_total=16.0, weekly={1: 8.0, 2: 8.0}),
        "otherB": PlayerCtx(id="otherB", position="K", ros_total=2.0, weekly={1: 1.0, 2: 1.0}),
    }
    free_agents = {"K": [PlayerCtx(id="fa_k", position="K", ros_total=14.0, weekly={1: 7.0, 2: 7.0})]}
    roster_a = ["kA1"]
    roster_b = ["kB1", "otherB"]

    plain = evaluate(
        gives_a=[], gives_b=["kB1"], roster_a=roster_a, roster_b=roster_b,
        players=players, weeks=weeks, slots=slots, eligibility=eligibility,
    )
    streamed = evaluate_with_streaming(
        gives_a=[], gives_b=["kB1"], roster_a=roster_a, roster_b=roster_b,
        players=players, free_agents_by_pos=free_agents, weeks=weeks, slots=slots, eligibility=eligibility,
    )
    assert plain.side_a.gain == pytest.approx(8.0)  # full week-2 swing: 0 -> 8
    assert streamed.side_a.gain == pytest.approx(1.0)  # only kB1's edge over the week-2 FA (8 - 7)


def test_streaming_replacement_is_picked_per_week_not_by_season_total():
    # Free agent A has the higher ros_total overall but is worse in week 2
    # specifically; free agent B is the opposite. A real manager streams
    # whoever has the better matchup THAT week, so week 2's fill should come
    # from FA B (5.0), not from FA A (1.0) just because its season total is
    # bigger.
    slots = {"K": 1}
    eligibility = {"K": {"K"}}
    weeks = [1, 2]
    players = {"k1": PlayerCtx(id="k1", position="K", ros_total=10.0, weekly={1: 10.0, 2: 0.0})}
    fa_high_total = PlayerCtx(id="fa_a", position="K", ros_total=20.0, weekly={1: 19.0, 2: 1.0})
    fa_better_week2 = PlayerCtx(id="fa_b", position="K", ros_total=6.0, weekly={1: 1.0, 2: 5.0})
    free_agents = {"K": [fa_high_total, fa_better_week2]}

    by_week = lineup_total_with_streaming_by_week(["k1"], players, free_agents, weeks, slots, eligibility)
    assert by_week[2] == pytest.approx(5.0)


def test_fixture_matches_committed_json():
    """Regenerated by scripts in this file's __main__ block if the engine's
    trade math ever intentionally changes; docs/js/trade.js (Stage 6) is
    checked against this same fixture for parity."""
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        fixture = json.load(f)

    players = _players()
    for case in fixture["cases"]:
        result = evaluate(
            gives_a=case["gives_a"], gives_b=case["gives_b"], roster_a=ROSTER_A, roster_b=ROSTER_B,
            players=players, weeks=WEEKS, slots=SLOTS, eligibility=ELIGIBILITY,
        )
        assert result.side_a.gain == pytest.approx(case["expected"]["gain_a"])
        assert result.side_b.gain == pytest.approx(case["expected"]["gain_b"])
        assert result.favors == case["expected"]["favors"]
