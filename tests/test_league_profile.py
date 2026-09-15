import pytest

from tools.faab_history.league_profile import _lineup_slot_group_counts, compare_to_baseline


def _slot_counts(qb=1, rb=2, wr=2, te=1, flex=1, k=1, dst=1, op=0, idp=0):
    """A raw ESPN lineupSlotCounts dict (str slot id -> count) shaped like a
    normal, O-League-like 1-QB roster by default."""
    counts = {"0": qb, "2": rb, "4": wr, "6": te, "23": flex, "17": k, "16": dst, "7": op}
    if idp:
        counts["10"] = idp  # LB
    return counts


def _profile(**slot_overrides):
    return {
        "size": 12, "scoring_type": "H2H_POINTS", "is_idp": bool(slot_overrides.get("idp")),
        "idp_slots": {}, "has_core_offense_scoring": True, "ppr_label": "Standard",
        "roster_slot_groups": _lineup_slot_group_counts(_slot_counts(**slot_overrides)),
    }


BASELINE = {"size": 12, "scoring_type": "H2H_POINTS", "ppr_label": "Standard"}


def test_lineup_slot_group_counts_normal_roster():
    groups = _lineup_slot_group_counts(_slot_counts())
    assert groups == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DST": 1, "OP": 0}


def test_lineup_slot_group_counts_unions_flex_variants():
    # RB/WR (3) + WR/TE (5) + RB/WR/TE (23) all count toward FLEX together.
    counts = {"3": 1, "5": 1, "23": 1}
    assert _lineup_slot_group_counts(counts)["FLEX"] == 3


def test_compare_to_baseline_accepts_a_normal_roster():
    verdict = compare_to_baseline(_profile(), BASELINE)
    assert verdict["compatible"] is True


@pytest.mark.parametrize("size", [10, 11, 13, 14, 16])
def test_compare_to_baseline_rejects_any_size_other_than_baselines_own(size):
    # Team count must EXACTLY match the baseline league's own size, not
    # just fall within some fixed absolute range - a 10-team (or 14-team)
    # league's FAAB market isn't the same game as this 12-team baseline's.
    verdict = compare_to_baseline({**_profile(), "size": size}, BASELINE)
    assert verdict["compatible"] is False
    assert any(f"size {size} != baseline 12" in r for r in verdict["reasons"])


def test_compare_to_baseline_accepts_offense_only_no_kicker_no_dst():
    # K:0 and DST:0 are both in-range - plenty of real leagues skip both.
    verdict = compare_to_baseline(_profile(k=0, dst=0), BASELINE)
    assert verdict["compatible"] is True


def test_compare_to_baseline_rejects_superflex_op_slot():
    verdict = compare_to_baseline(_profile(op=1), BASELINE)
    assert verdict["compatible"] is False
    assert any("superflex/OP" in r for r in verdict["reasons"])


def test_compare_to_baseline_rejects_two_qb_slots():
    verdict = compare_to_baseline(_profile(qb=2), BASELINE)
    assert verdict["compatible"] is False
    assert any("QB slots 2" in r for r in verdict["reasons"])


@pytest.mark.parametrize(
    "overrides,expected_group",
    [
        ({"rb": 3}, "RB"),
        ({"wr": 1}, "WR"),
        ({"wr": 4}, "WR"),
        ({"te": 2}, "TE"),
        ({"flex": 3}, "FLEX"),
        ({"k": 2}, "K"),
        ({"dst": 2}, "DST"),
    ],
)
def test_compare_to_baseline_rejects_out_of_range_slot_counts(overrides, expected_group):
    verdict = compare_to_baseline(_profile(**overrides), BASELINE)
    assert verdict["compatible"] is False
    assert any(r.startswith(f"{expected_group} slots") for r in verdict["reasons"])
