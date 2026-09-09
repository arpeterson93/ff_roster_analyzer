"""Team strength: lineup-delta ("next man down") player values, position
strength vs. the league, pickup suggestions, and trade targets."""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.lineup import expand_slots, optimal_lineup


@dataclass
class PlayerCtx:
    id: str
    position: str
    ros_total: float
    weekly: dict[int, float] = field(default_factory=dict)  # week -> projected points


def lineup_total_by_week(
    player_ids: list[str],
    players: dict[str, PlayerCtx],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> dict[int, float]:
    result = {}
    for w in weeks:
        entries = [(pid, players[pid].position, players[pid].weekly.get(w, 0.0)) for pid in player_ids if pid in players]
        week_total, _ = optimal_lineup(entries, slots, eligibility)
        result[w] = week_total
    return result


def lineup_total(
    player_ids: list[str],
    players: dict[str, PlayerCtx],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> float:
    return sum(lineup_total_by_week(player_ids, players, weeks, slots, eligibility).values())


def lineup_total_with_streaming(
    player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> float:
    """Like lineup_total, but for any week where every rostered player at a
    position projects to 0 (a bye week with no depth, most commonly a lone
    K/DST) the best available free agent at that position is assumed to
    start instead. Models the ordinary waiver-wire streaming any manager
    would do, so a "pickup" suggestion doesn't take credit for solving a gap
    that free-agent streaming already trivially covers."""
    best_fa_by_pos = {pos: max(fas, key=lambda f: f.ros_total, default=None) for pos, fas in free_agents_by_pos.items()}
    total = 0.0
    for w in weeks:
        entries = [(pid, players[pid].position, players[pid].weekly.get(w, 0.0)) for pid in player_ids if pid in players]
        rostered_by_pos: dict[str, float] = {}
        for _, pos, pts in entries:
            rostered_by_pos[pos] = max(rostered_by_pos.get(pos, 0.0), pts)
        for pos, fa in best_fa_by_pos.items():
            if fa is not None and rostered_by_pos.get(pos, 0.0) <= 0:
                entries.append((f"__stream_{pos}__", pos, fa.weekly.get(w, 0.0)))
        week_total, _ = optimal_lineup(entries, slots, eligibility)
        total += week_total
    return total


def optimal_lineup_for_week(
    player_ids: list[str], players: dict[str, PlayerCtx], week: int, slots: dict[str, int], eligibility: dict[str, set[str]]
) -> tuple[float, dict[str, str]]:
    entries = [(pid, players[pid].position, players[pid].weekly.get(week, 0.0)) for pid in player_ids if pid in players]
    return optimal_lineup(entries, slots, eligibility)


def depth_values_by_week(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> dict[str, dict[str, dict[int, float]]]:
    """Per-player, per-week lineup-delta value: {player_id: {"value_delta":
    {week: delta}, "value_delta_ww": {week: delta}}}. depth_values() is just
    this summed across weeks; this is the finer-grained view for charting how
    a player's marginal value moves week to week (bye weeks, tough
    matchups elsewhere on the roster creating temporary scarcity, etc)."""
    base_by_week = lineup_total_by_week(team_player_ids, players, weeks, slots, eligibility)
    result: dict[str, dict[str, dict[int, float]]] = {}
    for pid in team_player_ids:
        if pid not in players:
            continue
        reduced = [p for p in team_player_ids if p != pid]
        reduced_by_week = lineup_total_by_week(reduced, players, weeks, slots, eligibility)
        value_delta = {w: base_by_week[w] - reduced_by_week[w] for w in weeks}

        pos = players[pid].position
        fa_pool = free_agents_by_pos.get(pos, [])
        best_fa = max(fa_pool, key=lambda f: f.ros_total, default=None)
        if best_fa is not None:
            ww_players = dict(players)
            ww_players[best_fa.id] = best_fa
            ww_by_week = lineup_total_by_week(reduced + [best_fa.id], ww_players, weeks, slots, eligibility)
            value_delta_ww = {w: base_by_week[w] - ww_by_week[w] for w in weeks}
        else:
            value_delta_ww = dict(value_delta)
        result[pid] = {"value_delta": value_delta, "value_delta_ww": value_delta_ww}
    return result


def depth_values(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> dict[str, dict[str, float]]:
    by_week = depth_values_by_week(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility)
    return {
        pid: {"value_delta": sum(v["value_delta"].values()), "value_delta_ww": sum(v["value_delta_ww"].values())}
        for pid, v in by_week.items()
    }


def position_strength(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    positions: list[str],
) -> dict[str, float]:
    """Points/week contributed by each position in the optimal lineup, with
    flex-slot points attributed to the assigned player's own position."""
    totals = {pos: 0.0 for pos in positions}
    for w in weeks:
        _, assignment = optimal_lineup_for_week(team_player_ids, players, w, slots, eligibility)
        for pid in assignment.values():
            pos = players[pid].position
            totals[pos] = totals.get(pos, 0.0) + players[pid].weekly.get(w, 0.0)
    n = len(weeks) or 1
    return {pos: totals.get(pos, 0.0) / n for pos in positions}


def slot_strength(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> tuple[dict[str, float], list[str]]:
    """Points/week per SPECIFIC starting slot (QB, RB, WR1, WR2, TE, FLEX1,
    FLEX2, K, ...) rather than aggregated by real position. For a multi-count
    slot (two WR spots, two FLEX spots), the two filled instances are
    re-ranked by that week's own points ("WR1" = whichever WR scored more
    that week) rather than trusting the optimizer's arbitrary column
    assignment, so the label consistently means "your stronger one" across
    weeks. Returns (ppw_by_slot, ordered_slot_labels)."""
    slot_instances = expand_slots(slots)
    ordered_labels = [inst for inst, _ in slot_instances]
    groups: dict[str, list[str]] = {}
    for inst, base in slot_instances:
        groups.setdefault(base, []).append(inst)

    totals = {inst: 0.0 for inst in ordered_labels}
    for w in weeks:
        _, assignment = optimal_lineup_for_week(team_player_ids, players, w, slots, eligibility)
        for insts in groups.values():
            pts = sorted(
                (players[assignment[inst]].weekly.get(w, 0.0) for inst in insts if assignment.get(inst) in players),
                reverse=True,
            )
            pts += [0.0] * (len(insts) - len(pts))  # unfilled instances (roster short at that slot) contribute 0
            for label, p in zip(insts, pts):
                totals[label] += p

    n = len(weeks) or 1
    return {label: totals[label] / n for label in ordered_labels}, ordered_labels


def rank_and_compare(team_ppw: dict[int, dict[str, float]], positions: list[str]) -> dict[int, dict[str, dict]]:
    result: dict[int, dict[str, dict]] = {tid: {} for tid in team_ppw}
    for pos in positions:
        values = {tid: ppw.get(pos, 0.0) for tid, ppw in team_ppw.items()}
        avg = sum(values.values()) / len(values) if values else 0.0
        ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
        rank_by_team = {tid: i + 1 for i, (tid, _) in enumerate(ordered)}
        for tid, v in values.items():
            result[tid][pos] = {"ppw": v, "vs_avg": v - avg, "rank": rank_by_team[tid]}
    return result


def fa_values(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    ir_player_ids: frozenset[str] = frozenset(),
    fa_pool_size: int = 30,
) -> dict[str, dict]:
    """{fa_id: {"gain": .., "drop": ..}} for every free agent considered (top
    `fa_pool_size` per position by ros_total) - the lineup-total gain from
    adding them and dropping the weakest same-position rostered player (or
    weakest overall if none at that position), whether or not it's actually a
    good pickup. pickups() is just this filtered/sorted/truncated to positive
    gains; this unfiltered version is for showing "value to your team" next
    to any free agent (e.g. in the Rankings view), not just recommended ones."""
    base_total = lineup_total_with_streaming(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility)
    depth = depth_values(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility)
    droppable = [pid for pid in team_player_ids if pid not in ir_player_ids and pid in depth]
    if not droppable:
        return {}

    result: dict[str, dict] = {}
    for fas in free_agents_by_pos.values():
        top_fas = sorted(fas, key=lambda f: f.ros_total, reverse=True)[:fa_pool_size]
        for fa in top_fas:
            # Prefer dropping the weakest rostered player at the SAME
            # position as the free agent - adding a better kicker should
            # suggest replacing your worse kicker, not stashing a redundant
            # second one while dropping an unrelated bench skill player.
            same_pos_droppable = [pid for pid in droppable if players[pid].position == fa.position]
            drop_pool = same_pos_droppable or droppable
            drop_pid = min(drop_pool, key=lambda pid: depth[pid]["value_delta"])
            new_roster = [p for p in team_player_ids if p != drop_pid] + [fa.id]
            new_players = dict(players)
            new_players[fa.id] = fa
            # Baseline already assumes bye/gap streaming (see
            # lineup_total_with_streaming), so a candidate only shows a gain
            # here when actually rostering them beats that default streaming
            # plan - e.g. a real talent upgrade, not just filling a bye week.
            new_total = lineup_total_with_streaming(new_roster, new_players, free_agents_by_pos, weeks, slots, eligibility)
            result[fa.id] = {"gain": new_total - base_total, "drop": drop_pid}
    return result


def pickups(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    max_pickups: int,
    ir_player_ids: frozenset[str] = frozenset(),
    fa_pool_size: int = 30,
) -> list[dict]:
    values = fa_values(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility, ir_player_ids, fa_pool_size)
    candidates = [{"add": fa_id, "drop": v["drop"], "gain": v["gain"]} for fa_id, v in values.items() if v["gain"] > 0]
    candidates.sort(key=lambda c: c["gain"], reverse=True)
    return candidates[:max_pickups]


def trade_targets(
    team_id: int,
    team_player_ids: list[str],
    other_teams: dict[int, list[str]],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    max_trade_targets: int,
    candidate_pool_size: int = 10,
    two_for_one_pool_size: int = 6,
) -> list[dict]:
    """1-for-1 swaps with every other team (top `candidate_pool_size` by
    ros_total each side) plus 2-for-1 swaps (top `two_for_one_pool_size` each
    side), scored by each side's real before/after lineup-total delta."""
    from engine.trades import evaluate  # local import: trades.py also imports this module

    base_total = lineup_total(team_player_ids, players, weeks, slots, eligibility)

    def top_n(pids: list[str], n: int) -> list[str]:
        return sorted((p for p in pids if p in players), key=lambda p: players[p].ros_total, reverse=True)[:n]

    results = []
    for partner_id, partner_roster in other_teams.items():
        if partner_id == team_id:
            continue
        my_candidates_1 = top_n(team_player_ids, candidate_pool_size)
        their_candidates_1 = top_n(partner_roster, candidate_pool_size)

        one_for_one = [
            ([give], [get]) for give in my_candidates_1 for get in their_candidates_1
        ]
        my_candidates_2 = top_n(team_player_ids, two_for_one_pool_size)
        their_candidates_2 = top_n(partner_roster, two_for_one_pool_size)
        two_for_one = []
        for i, give_a in enumerate(my_candidates_2):
            for give_b in my_candidates_2[i + 1 :]:
                for get in their_candidates_2:
                    two_for_one.append(([give_a, give_b], [get]))

        for gives, gets in one_for_one + two_for_one:
            result = evaluate(
                gives_a=gives,
                gives_b=gets,
                roster_a=team_player_ids,
                roster_b=partner_roster,
                players=players,
                weeks=weeks,
                slots=slots,
                eligibility=eligibility,
            )
            if result.side_a.gain > 0 and result.side_b.gain > 0:
                results.append(
                    {
                        "partner_team_id": partner_id,
                        "give": gives,
                        "get": gets,
                        "gain_self": result.side_a.gain,
                        "gain_partner": result.side_b.gain,
                    }
                )
    results.sort(key=lambda r: r["gain_self"], reverse=True)
    return results[:max_trade_targets]
