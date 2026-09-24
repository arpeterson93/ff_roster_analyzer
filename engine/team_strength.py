"""Team strength: points-above-replacement player values (position_value_
by_player/position_value_matrix/fa_pool_value), position strength vs. the
league, pickup suggestions, and trade targets."""
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


def position_value_by_player(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    depth_weight: float = 0.5,
) -> dict[str, dict]:
    """Per-player points-above-replacement: {player_id: {"starting_value",
    "depth_value", "value_delta", "weekly": [{"week", "starting_value",
    "depth_value", "replacement_id"}]}}. The per-player twin of
    position_value_matrix (see that function's own docstring for the full
    claim-order/replacement-level derivation - this runs the SAME claim
    loop, just remembering which player earned each week's credit instead
    of only accumulating it into a per-position bucket); position_value_
    matrix is now a thin wrapper that sums this by position. value_delta =
    starting_value + depth_weight * depth_value, the same blend
    position_value_matrix's own "total" uses - kept under this name
    deliberately, so it can replace the old lineup-delta engine's
    value_delta everywhere without every consumer needing a field rename.

    Unlike the old lineup-delta engine this replaces, a starter's
    replacement is ALWAYS the best available free agent for his slot,
    never a bench teammate - position_value_matrix never modeled "drop this
    guy, see who else on the roster steps up," only "how does he compare to
    what's on the wire," so replacement_id here only ever names a free
    agent (or None, when no free agent exists at that slot that week)."""
    instances = _robust_slot_order(slots, eligibility)

    best_available_cache: dict[tuple[str, ...], dict[int, tuple[float, str | None]]] = {}

    def best_available(base_positions: set[str], w: int) -> tuple[float, str | None]:
        key = tuple(sorted(base_positions))
        by_week = best_available_cache.setdefault(key, {})
        if w not in by_week:
            best_val, best_id = 0.0, None
            for pos in base_positions:
                for fa in free_agents_by_pos.get(pos, []):
                    v = fa.weekly.get(w, 0.0)
                    if v > best_val:
                        best_val, best_id = v, fa.id
            by_week[w] = (best_val, best_id)
        return by_week[w]

    starting_value = {pid: 0.0 for pid in team_player_ids}
    depth_value = {pid: 0.0 for pid in team_player_ids}
    weekly: dict[str, dict[int, dict]] = {pid: {} for pid in team_player_ids}

    for w in weeks:
        candidates = {
            pid: (players[pid].position, players[pid].weekly.get(w, 0.0))
            for pid in team_player_ids
            if pid in players and players[pid].weekly.get(w, 0.0) > 0
        }
        claimed: set[str] = set()
        for _, base_label in instances:
            elig = eligibility.get(base_label, set())
            pool = [pid for pid, (pos, _) in candidates.items() if pos in elig and pid not in claimed]
            if not pool:
                continue  # true bye/no candidate - no asset here, contributes nothing
            starter = max(pool, key=lambda pid: candidates[pid][1])
            replacement, replacement_id = best_available(elig, w)
            v = candidates[starter][1] - replacement
            starting_value[starter] += v
            weekly[starter][w] = {"starting_value": v, "depth_value": 0.0, "replacement_id": replacement_id}
            claimed.add(starter)
        for pid, (pos, pts) in candidates.items():
            if pid in claimed:
                continue
            replacement, replacement_id = best_available({pos}, w)
            v = max(0.0, pts - replacement)
            depth_value[pid] += v
            weekly[pid][w] = {"starting_value": 0.0, "depth_value": v, "replacement_id": replacement_id}

    return {
        pid: {
            "starting_value": starting_value[pid],
            "depth_value": depth_value[pid],
            "value_delta": starting_value[pid] + depth_weight * depth_value[pid],
            "weekly": [
                {"week": w, **weekly[pid].get(w, {"starting_value": 0.0, "depth_value": 0.0, "replacement_id": None})}
                for w in weeks
            ],
        }
        for pid in team_player_ids
        if pid in players
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


def fa_pool_value(
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    depth_weight: float = 0.5,
) -> dict[str, dict]:
    """{fa_id: {"starting_value": 0.0, "depth_value": float, "value_delta":
    float, "weekly": [{"week", "depth_value", "replacement_id"}]}} - same
    field shape position_value_by_player returns for a rostered player
    (starting_value always 0.0 here - a free agent is never anyone's
    starter by definition), so both can feed the exact same UI. depth_value
    is this free agent's OWN value, leave-one-out against the REST of the
    free-agent pool at his own base position (no flex-widening - a bench
    RB's value is what he's worth as a straight RB, never a hypothetical
    FLEX play, same restriction position_value_by_player's own depth_value
    already applies to a rostered bench player). replacement_id names
    whichever OTHER free agent set that week's bar. Comparing against every
    OTHER free agent at his position (not the single best FA overall,
    including himself) means the single best free agent at a position gets
    real credit for being the best available, rather than being compared to
    himself and always scoring zero.

    Team-agnostic by design - computed once for the whole pool, not per
    team like the old lineup-delta engine's fa_values() was, since a free
    agent's value here has nothing to do with what any specific roster
    could gain by adding him. value_delta applies the SAME depth_weight
    position_value_by_player's own depth_value gets when rolled into a
    player's value_delta, so a free agent and a rostered bench player land
    on one consistent scale."""
    result: dict[str, dict] = {}
    for fas in free_agents_by_pos.values():
        for fa in fas:
            depth_total = 0.0
            weekly: list[dict] = []
            for w in weeks:
                pts = fa.weekly.get(w, 0.0)
                if pts <= 0:
                    weekly.append({"week": w, "depth_value": 0.0, "replacement_id": None})
                    continue
                best_other, best_other_id = 0.0, None
                for other in fas:
                    if other.id == fa.id:
                        continue
                    v = other.weekly.get(w, 0.0)
                    if v > best_other:
                        best_other, best_other_id = v, other.id
                v = max(0.0, pts - best_other)
                depth_total += v
                weekly.append({"week": w, "depth_value": v, "replacement_id": best_other_id})
            result[fa.id] = {
                "starting_value": 0.0,
                "depth_value": depth_total,
                "value_delta": depth_weight * depth_total,
                "weekly": weekly,
            }
    return result


def pickups(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    team_values: dict[str, dict],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    max_pickups: int,
    depth_weight: float = 0.5,
    ir_player_ids: frozenset[str] = frozenset(),
) -> list[dict]:
    """A pickup candidate is any free agent where swapping him in for the
    weakest droppable rostered player at his position (or the weakest
    overall droppable player, if the team has none at that position - an
    unrelated bench player shouldn't get cut to make room for a redundant
    second kicker) raises the team's WHOLE-ROSTER points-above-replacement
    total (position_value_team_total, before vs. after).

    This used to compare the free agent's OWN value_delta (from
    fa_pool_value) directly against the dropped player's value_delta - cheap,
    but wrong: a rostered STARTER's value_delta is scored against the single
    best free agent at his slot (position_value_by_player), which can go
    negative at a thin-replacement position like QB even for a clearly-good
    player, while a free agent's value_delta is only ever his BENCH-DEPTH
    value against the rest of the free-agent pool (floored at 0) - it never
    credits him for what he'd be worth if he actually took over the starting
    slot. Comparing those two numbers directly meant any free agent, however
    replacement-level, could "beat" a real starter the moment that starter's
    cumulative value dipped negative (see the conversation this was built
    from).

    Dropping the free agent onto the ACTUAL roster and rerunning the same
    claim loop (position_value_by_player, via position_value_team_total)
    sidesteps the mismatch: he only gets full starting-level credit when he'd
    genuinely start, exactly like trade_targets' own before/after scoring
    already does for trade swaps. team_values is still used (not recomputed)
    to pick WHICH rostered player is weakest - only the scoring of the
    resulting swap needs the full recompute. Costlier than the old O(1)
    compare - this reruns the claim loop once per free agent, the same
    per-candidate cost trade_targets already pays for every trade
    combination it considers."""
    droppable = [pid for pid in team_player_ids if pid not in ir_player_ids and pid in team_values]
    if not droppable:
        return []

    before = position_value_team_total(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility, depth_weight)

    candidates = []
    for fas in free_agents_by_pos.values():
        for fa in fas:
            # A free agent projected for exactly 0 points every remaining
            # week (off an NFL roster, practice squad, retired, etc. - ESPN's
            # free-agent pool still lists plenty of these, more of them now
            # that the pool isn't capped to the top 40-60/position) is never
            # a real pickup, whatever the before/after math below says. He
            # contributes literally NOTHING most weeks (never a candidate in
            # position_value_by_player's own claim loop, since that requires
            # weekly points > 0), which nets out to a plain 0 rather than a
            # penalty - and 0 can still beat a real rostered player whose
            # cumulative value has gone negative at a thin-replacement
            # position (the same "best-available-FA re-picked fresh every
            # week" dynamic behind the original Herbert/QB bug this before/
            # after rewrite fixed). Confirmed live: dead kickers with no NFL
            # team were getting suggested over a real, if mediocre, starter.
            if fa.ros_total <= 0:
                continue
            same_pos_droppable = [pid for pid in droppable if players[pid].position == fa.position]
            drop_pool = same_pos_droppable or droppable
            drop_pid = min(drop_pool, key=lambda pid: team_values[pid]["value_delta"])
            after_roster = [pid for pid in team_player_ids if pid != drop_pid] + [fa.id]
            # players is expected to already carry every free agent (the
            # pipeline's players_ctx does), but fall back to a one-off merge
            # so a caller/test that only stocks it with rostered players
            # doesn't silently drop the added FA out of the recompute.
            after_players = players if fa.id in players else {**players, fa.id: fa}
            # fa is no longer a free agent once he's on the hypothetical
            # roster - leave him in the pool and he'd be his own "best
            # available replacement" on any week he's the top FA, silently
            # zeroing out exactly the credit he should get (the same self-
            # comparison fa_pool_value's own leave-one-out design avoids).
            after_free_agents = {
                pos: [other for other in fas if other.id != fa.id] for pos, fas in free_agents_by_pos.items()
            }
            after = position_value_team_total(
                after_roster, after_players, after_free_agents, weeks, slots, eligibility, depth_weight
            )
            gain = after - before
            if gain > 0:
                candidates.append({"add": fa.id, "drop": drop_pid, "gain": gain})
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
    roster_size: int | None = None,
) -> list[dict]:
    """1-for-1 swaps with every other team (top `candidate_pool_size` by
    ros_total each side) plus 2-for-1 swaps (top `two_for_one_pool_size` each
    side), scored by each side's before/after points-above-replacement delta
    (position_value_team_total - see that function's own docstring). Same
    cheap greedy-claim calc the Trade Calculator's own client-side trade
    search (docs/js/trade.js's tradeSuggestions) already uses, replacing the
    old per-candidate full-lineup-reoptimization cost (evaluate_with_
    streaming) - a real behavior change, not just a faster equivalent: a
    reported gain here is now "points above replacement gained/lost," not
    "lineup total gained/lost," matching every other value number this site
    shows post-overhaul (see the conversation this was built from). A
    precise lineup-total before/after for a SPECIFIC chosen trade is still
    available - see engine/trades.py's evaluate_with_streaming, the same
    twin the Trade Calculator's own single-trade detail view calls once
    you've actually picked one of these suggestions to inspect.

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
    built from).

    roster_size (optional, real total roster spots - starting + BE + IR)
    resolves every before/after roster through _apply_roster_constraints
    first (see that function's own docstring) - closes the same "trade away
    your only bad player at a position for a free win" exploit and missing
    roster-cap check the Trade Calculator's own engine had before its
    matching fix (see the conversation this was built from). None (the
    default) skips this - unconstrained scoring, the old behavior."""

    def top_n(pids: list[str], n: int) -> list[str]:
        return sorted((p for p in pids if p in players), key=lambda p: players[p].ros_total, reverse=True)[:n]

    def team_total(roster: list[str]) -> float:
        return position_value_team_total(roster, players, free_agents_by_pos, weeks, slots, eligibility, roster_size=roster_size)

    my_before = team_total(team_player_ids)

    results = []
    for partner_id, partner_roster in other_teams.items():
        if partner_id == team_id:
            continue
        partner_before = team_total(partner_roster)

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
            after_mine = [p for p in team_player_ids if p not in gives] + gets
            after_theirs = [p for p in partner_roster if p not in gets] + gives
            gain_self = team_total(after_mine) - my_before
            gain_partner = team_total(after_theirs) - partner_before
            if (
                gain_self > 0
                and gain_partner > 0
                and min(gain_self, gain_partner) / max(gain_self, gain_partner) >= fairness_ratio
            ):
                results.append(
                    {
                        "partner_team_id": partner_id,
                        "give": gives,
                        "get": gets,
                        "gain_self": gain_self,
                        "gain_partner": gain_partner,
                    }
                )
    results.sort(key=lambda r: r["gain_self"], reverse=True)
    return results[:max_trade_targets]


def _robust_slot_order(slots: dict[str, int], eligibility: dict[str, set[str]]) -> list[tuple[str, str]]:
    """expand_slots' own instance order, stably resorted so every single-
    position slot (QB/RB/WR/TE/K) fills before any multi-position slot
    (FLEX) - position_value_matrix's claim order depends on FLEX only ever
    seeing leftovers (every other position's real starter already
    claimed), and a league's raw slots dict has no guaranteed key order to
    rely on for that. Stable, so WR1 still precedes WR2 and FLEX1 still
    precedes FLEX2 exactly as expand_slots already ordered them."""
    instances = expand_slots(slots)
    return sorted(instances, key=lambda inst: len(eligibility.get(inst[1], set())) > 1)


def position_value_matrix(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    depth_weight: float = 0.5,
) -> dict[str, dict[str, float]]:
    """{position: {"starting_value", "depth_value", "total"}}, summed across
    `weeks` - see the conversation this was built from for the full
    derivation. Grouped by the player's own real POSITION (QB/RB/WR/TE/K),
    not by lineup slot: an earlier version bucketed by slot instance
    (QB/RB/WR1/WR2/TE/K/FLEX1/FLEX2), and a player left unclaimed after a
    slot's claim pass got "depth" credit again at every OTHER eligible slot
    he then passed through unclaimed - a bench RB behind a full RB corps
    could be valued once under RB's own depth chain, again under FLEX1's,
    and again under FLEX2's. This version still uses the same cheap greedy
    claim (see _robust_slot_order - single-position slots claim before any
    FLEX slot, deliberately NOT the exact Hungarian/bitmask-DP optimizer
    optimal_lineup uses, since this function is also the hot path behind
    tradeSuggestions' large per-search verify budget and the exact solver
    is too expensive to run there thousands of times), but only credits
    depth ONCE per player, after every slot has had its turn to claim him -
    so a player is either that week's starter (in exactly one slot) or that
    week's bench (in exactly one position bucket), never both and never
    more than once. A claimed starter's starting_value is scored against
    the best AVAILABLE (unrostered) player for the SLOT he fills - so a
    bench RB who ends up claimed by FLEX is compared to the FLEX-wide
    replacement level, not the single-position one - but credited to his
    own position's bucket, not FLEX's. Starting value CAN go negative: a
    real rostered starter who's actually worse than a free agent is a real,
    informative signal, not something to hide. An unclaimed player's
    depth_value is instead scored against the best available free agent at
    THEIR OWN position (never a FLEX-widened bar - a bench RB's depth value
    is what he's worth as a straight RB2, not as a hypothetical FLEX play)
    and floored at 0 - depth can only ever help, never hurt, since a real
    bench player is never forced into the lineup. A player who projects 0
    that week (a true bye) is excluded entirely, from both the claim pass
    and the depth pass - there's no real asset there to score, in either
    direction. A thin wrapper now - see position_value_by_player, which runs
    this exact claim loop and does all the real work, just keyed by player
    instead of by position; this sums that by each player's own real
    position."""
    by_player = position_value_by_player(team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility, depth_weight)
    positions: list[str] = []
    for base_positions in eligibility.values():
        for pos in base_positions:
            if pos not in positions:
                positions.append(pos)

    starting = {pos: 0.0 for pos in positions}
    depth = {pos: 0.0 for pos in positions}
    for pid, v in by_player.items():
        pos = players[pid].position
        if pos not in starting:
            continue
        starting[pos] += v["starting_value"]
        depth[pos] += v["depth_value"]

    return {
        pos: {
            "starting_value": starting[pos],
            "depth_value": depth[pos],
            "total": starting[pos] + depth_weight * depth[pos],
        }
        for pos in positions
    }


def _apply_roster_constraints(
    roster: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    roster_size: int,
    depth_weight: float = 0.5,
) -> list[str]:
    """Python twin of docs/js/trade.js's applyRosterConstraints - see that
    function's own docstring for the full derivation. Resolves a
    hypothetical roster against real ESPN roster rules so a candidate trade
    can never score better than it actually would in practice:
      1. Backfill: any real position with ZERO currently-rostered players
         hypothetically signs the best available free agent there (highest
         ros_total) - without this, trading away your only (negative-value)
         player at a position looks like a free win instead of the real
         roster gap it creates.
      2. Cap enforcement: if the roster is now over `roster_size` (real
         total roster spots - starting + BE + IR), cut the single lowest-
         value_delta player - ANY current roster member is eligible, not
         just players actually in the trade.
    Looped (bounded) since either step can trigger the other. A cut must
    never take a position down to zero - otherwise the very next iteration's
    backfill just re-signs someone there, and if THAT replacement-level
    signing is itself the roster's lowest-value_delta player (routine - a
    backfill exists BECAUSE nothing better was available), the cut step
    removes him again next iteration, forever (confirmed live against the
    JS twin, not just theoretical - see the conversation this was built
    from). Only the resolved roster is returned (no transaction log) -
    unlike the Trade Calculator's interactive Team Value panel, Team
    Strength's Trade Targets is a passive suggestion list with nowhere to
    show hypothetical moves."""
    roster = list(roster)
    players = dict(players)  # local copy - a backfilled FA gets added here, never leaking into the caller's own dict
    positions: list[str] = []
    for base_positions in eligibility.values():
        for pos in base_positions:
            if pos not in positions:
                positions.append(pos)

    for _ in range(20):
        changed = False

        rostered_positions = {players[pid].position for pid in roster if pid in players}
        for pos in positions:
            if pos in rostered_positions:
                continue
            pool = [fa for fa in free_agents_by_pos.get(pos, []) if fa.id not in roster]
            if not pool:
                continue  # nobody available at this position at all
            best = max(pool, key=lambda fa: fa.ros_total)
            roster.append(best.id)
            players.setdefault(best.id, best)
            changed = True

        if len(roster) > roster_size:
            by_player = position_value_by_player(roster, players, free_agents_by_pos, weeks, slots, eligibility, depth_weight)
            position_counts: dict[str, int] = {}
            for pid in roster:
                if pid in players:
                    position_counts[players[pid].position] = position_counts.get(players[pid].position, 0) + 1
            cuttable = [pid for pid in roster if pid in players and position_counts[players[pid].position] > 1]
            pool = cuttable or roster  # every position down to exactly 1 - no cut can avoid a gap, so cut the worst overall
            worst = min(pool, key=lambda pid: by_player.get(pid, {}).get("value_delta", 0.0))
            roster = [pid for pid in roster if pid != worst]
            changed = True

        if not changed:
            break

    return roster


def position_value_team_total(
    team_player_ids: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
    depth_weight: float = 0.5,
    roster_size: int | None = None,
) -> float:
    """Sum of every player's own value_delta (see position_value_by_player) -
    one roster reduced to a single points-above-replacement number, for
    before/after trade comparison. Python twin of docs/js/trade.js's
    positionValueTeamTotal - same cheap greedy claim underneath (no
    exponential DP), which is what makes this cheap enough to call twice per
    candidate trade (before/after) across trade_targets' whole search
    without the old per-candidate full-lineup-reoptimization cost
    evaluate_with_streaming needed.

    roster_size is optional (backward compatible) - when given, the roster
    is resolved through _apply_roster_constraints first, so a candidate that
    would leave a position empty or bust the real roster cap can't score
    better than it actually would."""
    resolved_ids = team_player_ids
    if roster_size is not None:
        resolved_ids = _apply_roster_constraints(
            team_player_ids, players, free_agents_by_pos, weeks, slots, eligibility, roster_size, depth_weight
        )
    by_player = position_value_by_player(resolved_ids, players, free_agents_by_pos, weeks, slots, eligibility, depth_weight)
    return sum(v["value_delta"] for v in by_player.values())
