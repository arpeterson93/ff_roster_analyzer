import itertools
import random

import pytest

from engine.lineup import optimal_lineup

SLOTS = {"QB": 1, "RB": 1, "WR": 2, "TE": 1, "RB/WR/TE": 1}
ELIGIBILITY = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "RB/WR/TE": {"RB", "WR", "TE"}}


def _brute_force_best(players, slots, eligibility) -> float:
    """Tries every partial assignment (any subset of slots may be left empty,
    matching optimal_lineup's behavior when a position is short on the roster)."""
    slot_instances = []
    for label, count in slots.items():
        for _ in range(count):
            slot_instances.append(label)
    best = 0.0
    n = len(slot_instances)
    for k in range(0, n + 1):
        for slot_subset in itertools.combinations(range(n), k):
            labels = [slot_instances[i] for i in slot_subset]
            for perm in itertools.permutations(players, k):
                valid = True
                total = 0.0
                for (pid, pos, pts), label in zip(perm, labels):
                    if pos not in eligibility[label]:
                        valid = False
                        break
                    total += pts
                if valid:
                    best = max(best, total)
    return best


def test_matches_brute_force_random():
    random.seed(42)
    positions = ["QB", "RB", "WR", "TE"]
    for trial in range(5):
        players = [
            (f"p{i}", random.choice(positions), round(random.uniform(0, 25), 1))
            for i in range(9)
        ]
        total, assignment = optimal_lineup(players, SLOTS, ELIGIBILITY)
        expected = _brute_force_best(players, SLOTS, ELIGIBILITY)
        assert total == pytest.approx(expected), f"trial {trial}: {players}"
        assert len(assignment) <= sum(SLOTS.values())


def test_op_superflex_picks_best_two_of_qb_and_flex_pool():
    slots = {"QB": 1, "OP": 1}
    eligibility = {"QB": {"QB"}, "OP": {"QB", "RB", "WR", "TE"}}
    players = [
        ("qb1", "QB", 30.0),
        ("qb2", "QB", 25.0),
        ("wr1", "WR", 20.0),
    ]
    total, assignment = optimal_lineup(players, slots, eligibility)
    # best total uses both QBs (30 in QB/OP slot, 25 in the other), beating
    # using the WR (30 + 20 = 50 < 30 + 25 = 55)
    assert total == pytest.approx(55.0)
    assert set(assignment.values()) == {"qb1", "qb2"}


def test_missing_position_leaves_slot_empty():
    slots = {"QB": 1, "K": 1}
    eligibility = {"QB": {"QB"}, "K": {"K"}}
    players = [("qb1", "QB", 20.0)]  # no kicker on the roster
    total, assignment = optimal_lineup(players, slots, eligibility)
    assert total == pytest.approx(20.0)
    assert assignment == {"QB": "qb1"}
    assert "K" not in assignment


def test_flex_slot_numbering_when_count_greater_than_one():
    slots = {"WR": 2}
    eligibility = {"WR": {"WR"}}
    players = [("w1", "WR", 10.0), ("w2", "WR", 8.0)]
    total, assignment = optimal_lineup(players, slots, eligibility)
    assert total == pytest.approx(18.0)
    assert set(assignment.keys()) == {"WR1", "WR2"}


def test_ties_prefer_top_scorer_in_dedicated_slot_over_flex():
    """Two ways to hit the same 38.0 total: best WR in WR + 2nd WR in FLEX, or
    vice versa. The tie-break should pick the natural-looking one (top scorer
    gets the dedicated slot) rather than an arbitrary solver pick - this was
    reported as Ja'Marr Chase (highest WR proj) showing up in FLEX instead of
    WR1 while a lower-projected WR sat in the dedicated slot."""
    slots = {"WR": 1, "RB/WR/TE": 1}
    eligibility = {"WR": {"WR"}, "RB/WR/TE": {"RB", "WR", "TE"}}
    players = [("chase", "WR", 22.0), ("w2", "WR", 16.0)]
    total, assignment = optimal_lineup(players, slots, eligibility)
    assert total == pytest.approx(38.0)
    assert assignment["WR"] == "chase"
    assert assignment["FLEX"] == "w2"
