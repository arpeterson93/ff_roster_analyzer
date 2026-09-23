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


def lineup_total_with_streaming_by_week(
    player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> dict[int, float]:
    """Per-week breakdown behind lineup_total_with_streaming (see there for
    why bye-week free-agent streaming is modeled here). The replacement is
    picked FRESH each week by that week's own projection, not by a single
    free agent's season-long ros_total chosen once up front - a real manager
    streams whoever has the best matchup that particular week, and the best
    ros_total kicker/DST on the wire isn't necessarily the one projected
    highest for any given week."""
    result = {}
    for w in weeks:
        entries = [(pid, players[pid].position, players[pid].weekly.get(w, 0.0)) for pid in player_ids if pid in players]
        rostered_by_pos: dict[str, float] = {}
        for _, pos, pts in entries:
            rostered_by_pos[pos] = max(rostered_by_pos.get(pos, 0.0), pts)
        for pos, fas in free_agents_by_pos.items():
            if rostered_by_pos.get(pos, 0.0) > 0:
                continue
            best_fa_week_pts = max((fa.weekly.get(w, 0.0) for fa in fas), default=0.0)
            if best_fa_week_pts > 0:
                entries.append((f"__stream_{pos}__", pos, best_fa_week_pts))
        week_total, _ = optimal_lineup(entries, slots, eligibility)
        result[w] = week_total
    return result


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
    return sum(lineup_total_with_streaming_by_week(player_ids, players, free_agents_by_pos, weeks, slots, eligibility).values())


def optimal_lineup_for_week(
    player_ids: list[str], players: dict[str, PlayerCtx], week: int, slots: dict[str, int], eligibility: dict[str, set[str]]
) -> tuple[float, dict[str, str]]:
    entries = [(pid, players[pid].position, players[pid].weekly.get(week, 0.0)) for pid in player_ids if pid in players]
    return optimal_lineup(entries, slots, eligibility)


def optimal_lineup_for_week_with_bye_fill(
    player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    week: int,
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    bye_week_by_player: dict[str, int | None],
) -> tuple[float, dict[str, str], set[str]]:
    """Like optimal_lineup_for_week, but any starting slot whose optimal
    ROSTERED occupant is on bye that specific week gets that slot replaced by
    the single best-projected eligible free agent for that week instead -
    picking up a real, displayable stream for an obvious bye gap. Narrower
    than lineup_total_with_streaming (which fires whenever a position
    aggregate is fully zeroed, for valuation purposes only): this only ever
    displaces a player confirmed on bye that week, never a real non-bye
    starter just because a free agent happens to project higher - streaming
    is an opt-in suggestion here, not a silent "always take the best
    available player" optimizer. Returns (total, assignment, streamed_ids) -
    streamed_ids is the subset of assignment's values that are free-agent
    fill-ins (not on the roster), for the frontend to flag distinctly."""
    total, assignment = optimal_lineup_for_week(player_ids, players, week, slots, eligibility)
    instance_to_base = dict(expand_slots(slots))
    streamed: set[str] = set()
    used_fa_ids: set[str] = set()
    for slot_label, pid in list(assignment.items()):
        if bye_week_by_player.get(pid) != week:
            continue
        base_label = instance_to_base[slot_label]
        candidates = [
            fa
            for pos in eligibility[base_label]
            for fa in free_agents_by_pos.get(pos, [])
            if fa.id not in used_fa_ids
        ]
        best_fa = max(candidates, key=lambda f: f.weekly.get(week, 0.0), default=None)
        best_fa_pts = best_fa.weekly.get(week, 0.0) if best_fa else 0.0
        if best_fa is None or best_fa_pts <= 0:
            continue
        total += best_fa_pts  # the byed player already contributed ~0 to `total`
        assignment[slot_label] = best_fa.id
        streamed.add(best_fa.id)
        used_fa_ids.add(best_fa.id)
    return total, assignment, streamed


def depth_values_by_week(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> dict[str, dict]:
    """Per-player, per-week lineup-delta value: {player_id: {"value_delta":
    {week: delta}, "replacement_id": {week: teammate_or_fa_id or None}}} -
    ONE consolidated value per week, not two parallel ones (a rostered
    player's real marginal value already accounts for the waiver wire,
    since that's a real alternative to keeping him around, not a separate
    hypothetical - see the conversation this was built from). Floored at 0
    - a free agent who genuinely beats a mediocre rostered player can make
    the candidate lineup (see below) score HIGHER than the real one even
    with the rostered player gone, i.e. a real negative raw delta; that
    means this specific player contributes nothing incremental (you'd be
    equally or better off with a wire pickup in his place), not that he's
    worth a negative number. depth_values() is just this summed/collapsed
    across weeks; this is the finer-grained view for charting how a
    player's marginal value moves week to week (bye weeks, tough matchups
    elsewhere on the roster creating temporary scarcity, etc).

    The replacement is picked FRESH each week, not once for the whole
    season - a real bench player who'd actually step in depends on THAT
    week's own matchups/byes, and the best streaming free agent depends on
    THAT week's own projection, the same "pick fresh each week" philosophy
    lineup_total_with_streaming_by_week already applies to the aggregate
    roster-total calc (see that function's docstring) - just applied here
    to a single player's own marginal value instead. This can genuinely
    name a DIFFERENT specific replacement in different weeks for the exact
    same rostered player - a real reflection of how bench/waiver decisions
    actually work, not an inconsistency.

    replacement_id is found by diffing optimal_lineup's own slot assignment
    for the full roster against the CANDIDATE roster (pid dropped, and -
    when a viable free agent exists at his position that week - that free
    agent added to the pool, NOT forced into the lineup; the optimizer is
    still free to prefer an existing bench player over him), THAT SAME
    WEEK - whichever player newly starts a slot they weren't starting
    before is the real one absorbing the vacated production, whether
    that's an existing bench teammate or the free agent. None when the
    roster (plus the free agent, if any) was deep enough that dropping pid
    doesn't change who starts at all. Prefers a same-position newly-started
    player when the optimizer's reshuffle touched more than one slot (a
    chain reaction, e.g. dropping a flex-eligible player) - same "same
    position first" preference fa_values() uses when picking which player
    TO drop, just applied to who fills back in instead."""
    base_by_week: dict[int, float] = {}
    base_assignment_by_week: dict[int, dict[str, str]] = {}
    for w in weeks:
        entries = [(pid, players[pid].position, players[pid].weekly.get(w, 0.0)) for pid in team_player_ids if pid in players]
        total, assignment = optimal_lineup(entries, slots, eligibility)
        base_by_week[w] = total
        base_assignment_by_week[w] = assignment

    result: dict[str, dict] = {}
    for pid in team_player_ids:
        if pid not in players:
            continue
        reduced = [p for p in team_player_ids if p != pid]
        pos = players[pid].position
        fa_pool = free_agents_by_pos.get(pos, [])

        value_delta: dict[int, float] = {}
        replacement_id: dict[int, str | None] = {}

        for w in weeks:
            reduced_entries = [(rpid, players[rpid].position, players[rpid].weekly.get(w, 0.0)) for rpid in reduced if rpid in players]
            best_fa = max(fa_pool, key=lambda f: f.weekly.get(w, 0.0), default=None)
            candidate_entries = reduced_entries
            if best_fa is not None and best_fa.weekly.get(w, 0.0) > 0:
                candidate_entries = reduced_entries + [(best_fa.id, best_fa.position, best_fa.weekly.get(w, 0.0))]
            candidate_total, candidate_assignment = optimal_lineup(candidate_entries, slots, eligibility)
            value_delta[w] = max(0.0, base_by_week[w] - candidate_total)

            candidate_positions = {cid: cpos for cid, cpos, _ in candidate_entries}
            newly_started = set(candidate_assignment.values()) - set(base_assignment_by_week[w].values())
            same_pos_replacement = next((npid for npid in newly_started if candidate_positions.get(npid) == pos), None)
            replacement_id[w] = same_pos_replacement or next(iter(newly_started), None)

        result[pid] = {"value_delta": value_delta, "replacement_id": replacement_id}
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
        # replacement_id is per-week (see depth_values_by_week) - the
        # season-long aggregate has no single "the" replacement to report,
        # so it's dropped here rather than picking one week arbitrarily.
        # Callers that want to know who filled in should use
        # depth_values_by_week directly.
        pid: {"value_delta": sum(v["value_delta"].values())}
        for pid, v in by_week.items()
    }


def position_strength(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    positions: list[str],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    bye_week_by_player: dict[str, int | None],
) -> dict[str, float]:
    """Points/week contributed by each position in the optimal lineup, with
    flex-slot points attributed to the assigned player's own position. A
    bye-week slot counts its streamed-in free agent's points (see
    optimal_lineup_for_week_with_bye_fill) - the free agent is NOT added to
    team_player_ids, so it never shows up as a roster/depth-chart entry
    anywhere else, only in this points/week aggregate."""
    totals = {pos: 0.0 for pos in positions}
    for w in weeks:
        _, assignment, _ = optimal_lineup_for_week_with_bye_fill(
            team_player_ids, players, free_agents_by_pos, w, slots, eligibility, bye_week_by_player
        )
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
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    bye_week_by_player: dict[str, int | None],
) -> tuple[dict[str, float], list[str]]:
    """Points/week per SPECIFIC starting slot (QB, RB, WR1, WR2, TE, FLEX1,
    FLEX2, K, ...) rather than aggregated by real position. For a multi-count
    slot (two WR spots, two FLEX spots), the two filled instances are
    re-ranked by that week's own points ("WR1" = whichever WR scored more
    that week) rather than trusting the optimizer's arbitrary column
    assignment, so the label consistently means "your stronger one" across
    weeks. Bye-week slots stream a free agent in the same way
    position_strength does. Returns (ppw_by_slot, ordered_slot_labels)."""
    slot_instances = expand_slots(slots)
    ordered_labels = [inst for inst, _ in slot_instances]
    groups: dict[str, list[str]] = {}
    for inst, base in slot_instances:
        groups.setdefault(base, []).append(inst)

    totals = {inst: 0.0 for inst in ordered_labels}
    for w in weeks:
        _, assignment, _ = optimal_lineup_for_week_with_bye_fill(
            team_player_ids, players, free_agents_by_pos, w, slots, eligibility, bye_week_by_player
        )
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
    """{fa_id: {"gain": .., "drop": .., "weekly": {week: delta}}} for every
    free agent considered (top `fa_pool_size` per position by ros_total) -
    the lineup-total gain from adding them and dropping the weakest
    same-position rostered player (or weakest overall if none at that
    position), whether or not it's actually a good pickup. pickups() is
    just this filtered/sorted/truncated to positive gains; this unfiltered
    version is for showing "value to your team" next to any free agent
    (e.g. in the Rankings view), not just recommended ones.

    weekly is the SAME add/drop pairing's per-week breakdown, not a
    separately-optimized week-by-week choice - a real manager makes one
    add/drop decision, not a different trade every week - computed via
    lineup_total_with_streaming_by_week rather than summing it away
    immediately, since callers wanting week-by-week detail (a player
    modal's NMD breakdown) shouldn't have to redo this exact computation."""
    base_by_week = lineup_total_with_streaming_by_week(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility)
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
            new_by_week = lineup_total_with_streaming_by_week(new_roster, new_players, free_agents_by_pos, weeks, slots, eligibility)
            weekly = {w: new_by_week[w] - base_by_week[w] for w in weeks}
            result[fa.id] = {"gain": sum(weekly.values()), "drop": drop_pid, "weekly": weekly}
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
    fairness_ratio: float = 0.5,
) -> list[dict]:
    """1-for-1 swaps with every other team (top `candidate_pool_size` by
    ros_total each side) plus 2-for-1 swaps (top `two_for_one_pool_size` each
    side), scored by each side's real before/after lineup-total delta (with
    waiver-wire streaming assumed for any position a trade leaves empty - see
    evaluate_with_streaming - so a trade isn't flagged as a big loss for a
    side that could trivially backfill the position on the wire instead).

    Both sides' gain must be positive AND within `fairness_ratio` of each
    other (min/max >= fairness_ratio) - `gain_self > 0 and gain_partner > 0`
    alone lets through wildly lopsided "trades" like offering a bench kicker
    (near-zero gain to you, since dropping him barely moves your lineup) for
    a real bench upgrade (any positive gain, however large, satisfies
    "> 0") that no actual manager would accept. A ratio (not a fixed point
    gap) scales correctly with trade size and time of season - early-season
    gains run much larger in absolute points than late-season ones.

    Deliberately symmetric here, unlike the Trade Calculator's client-side
    twin (docs/js/trade.js's tradeSuggestions/passesFairness), which leaves
    the "I'm overpaying" direction unbounded - these are passive, browse-
    only suggestions with no specific trade already in mind, so there's no
    real "my call to make" the way there is once you're actively building a
    specific offer around a player you want (see the conversation this was
    built from)."""
    from engine.trades import evaluate_with_streaming  # local import: trades.py also imports this module

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
            result = evaluate_with_streaming(
                gives_a=gives,
                gives_b=gets,
                roster_a=team_player_ids,
                roster_b=partner_roster,
                players=players,
                free_agents_by_pos=free_agents_by_pos,
                weeks=weeks,
                slots=slots,
                eligibility=eligibility,
            )
            if (
                result.side_a.gain > 0
                and result.side_b.gain > 0
                and min(result.side_a.gain, result.side_b.gain) / max(result.side_a.gain, result.side_b.gain) >= fairness_ratio
            ):
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


def _robust_slot_order(slots: dict[str, int], eligibility: dict[str, set[str]]) -> list[tuple[str, str]]:
    """expand_slots' own instance order, stably resorted so every single-
    position slot (QB/RB/WR/TE/K) fills before any multi-position slot
    (FLEX) - slot_value_matrix's fill order depends on FLEX only ever
    seeing leftovers (every other position's real starter already
    claimed), and a league's raw slots dict has no guaranteed key order to
    rely on for that. Stable, so WR1 still precedes WR2 and FLEX1 still
    precedes FLEX2 exactly as expand_slots already ordered them."""
    instances = expand_slots(slots)
    return sorted(instances, key=lambda inst: len(eligibility.get(inst[1], set())) > 1)


def slot_value_matrix(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    depth_weight: float = 0.5,
) -> dict[str, dict[str, float]]:
    """{slot_instance_label: {"starting_value", "depth_value", "total"}},
    summed across `weeks` - see the conversation this was built from for
    the full derivation. Unlike depth_values_by_week (a per-PLAYER lineup-
    delta via the real optimizer), this is a per-SLOT points-above-
    replacement read: each starting slot, in a fixed fill order (see
    _robust_slot_order), claims the single best-projected still-unclaimed
    rostered player eligible for it that week - so FLEX only ever sees
    leftovers, never a player RB/WR/TE/etc. already claimed - and is
    scored against the best AVAILABLE (unrostered, same eligibility)
    player that week. Starting value CAN go negative: a real rostered
    player who's actually worse than a free agent is a real, informative
    signal (this slot should probably be upgraded), not something to
    hide. Depth value is every OTHER still-unclaimed eligible rostered
    player after that slot's own pick (reduced by what PRECEDES it in the
    fill order, never by what comes after - WR2's depth excludes WR1's
    starter, but RB's own depth is untouched by what FLEX later claims)
    scored the same way but floored at 0 per player - depth can only ever
    help, never hurt, since a real bench player is never forced into the
    lineup. A slot/depth-layer with no eligible unclaimed candidate that
    week (a true bye, or a thin position with nothing left) contributes
    exactly 0 - skipped entirely, never computed as 0 minus replacement,
    since there's no real asset there to devalue."""
    instances = _robust_slot_order(slots, eligibility)
    best_available_cache: dict[tuple[str, ...], dict[int, float]] = {}

    def best_available(base_positions: set[str], w: int) -> float:
        key = tuple(sorted(base_positions))
        by_week = best_available_cache.setdefault(key, {})
        if w not in by_week:
            candidates = [fa.weekly.get(w, 0.0) for pos in base_positions for fa in free_agents_by_pos.get(pos, [])]
            by_week[w] = max(candidates, default=0.0)
        return by_week[w]

    starting_by_instance = {label: 0.0 for label, _ in instances}
    depth_by_instance = {label: 0.0 for label, _ in instances}

    for w in weeks:
        claimed: set[str] = set()
        for instance_label, base_label in instances:
            elig = eligibility.get(base_label, set())
            pool = [
                pid
                for pid in team_player_ids
                if pid in players
                and players[pid].position in elig
                and pid not in claimed
                and players[pid].weekly.get(w, 0.0) > 0
            ]
            if not pool:
                continue  # true bye/no candidate - no asset here, contributes nothing
            pool.sort(key=lambda pid: players[pid].weekly.get(w, 0.0), reverse=True)
            replacement = best_available(elig, w)
            starter = pool[0]
            starting_by_instance[instance_label] += players[starter].weekly.get(w, 0.0) - replacement
            claimed.add(starter)
            for pid in pool[1:]:
                depth_by_instance[instance_label] += max(0.0, players[pid].weekly.get(w, 0.0) - replacement)

    return {
        label: {
            "starting_value": starting_by_instance[label],
            "depth_value": depth_by_instance[label],
            "total": starting_by_instance[label] + depth_weight * depth_by_instance[label],
        }
        for label, _ in instances
    }
