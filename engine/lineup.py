"""Exact optimal lineup assignment via scipy's Hungarian algorithm - no greedy
edge cases with overlapping FLEX/RB-WR/WR-TE/OP eligibility."""
from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment

_DISPLAY_LABEL = {
    "D/ST": "DST",
    "RB/WR/TE": "FLEX",
    "RB/WR": "FLEX",
    "WR/TE": "FLEX",
}

_INELIGIBLE_COST = -1e9


def display_label(slot_label: str) -> str:
    return _DISPLAY_LABEL.get(slot_label, slot_label)


def expand_slots(slots: dict[str, int]) -> list[tuple[str, str]]:
    """[(instance_label, base_slot_label), ...], stable order, numbered when count > 1.
    Public so callers (e.g. the pipeline's ESPN-lineup comparison) can group
    an assignment's slot instances back to their original slot-config key
    without re-deriving the same expansion logic."""
    instances = []
    for label, count in slots.items():
        display = display_label(label)
        if count == 1:
            instances.append((display, label))
        else:
            for i in range(1, count + 1):
                instances.append((f"{display}{i}", label))
    return instances


def optimal_lineup(
    players: list[tuple[str, str, float]],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> tuple[float, dict[str, str]]:
    """players: [(id, position, projected_points), ...].
    Returns (total_points, {slot_instance_label: player_id}); a slot with no
    eligible player on the roster is simply absent from the assignment."""
    slot_instances = expand_slots(slots)
    n_slots = len(slot_instances)
    if n_slots == 0:
        return 0.0, {}

    # Dummy "leave empty" rows guarantee a slot goes unfilled (cost 0) rather
    # than being force-filled by an ineligible player when scipy solves the
    # assignment as a bipartite perfect matching on the smaller side.
    dummy_ids = [f"__empty_{i}__" for i in range(n_slots)]
    all_players = list(players) + [(pid, None, 0.0) for pid in dummy_ids]

    cost = np.full((len(all_players), n_slots), _INELIGIBLE_COST)
    for i, (_, pos, pts) in enumerate(all_players):
        for j, (_, base_label) in enumerate(slot_instances):
            if pos is None or pos in eligibility[base_label]:
                cost[i, j] = pts if pos is not None else 0.0

    row_ind, col_ind = linear_sum_assignment(cost, maximize=True)

    total = 0.0
    assignment: dict[str, str] = {}
    for r, c in zip(row_ind, col_ind):
        pid, pos, pts = all_players[r]
        if pid in dummy_ids:
            continue
        inst_label, _ = slot_instances[c]
        assignment[inst_label] = pid
        total += pts
    return total, assignment
