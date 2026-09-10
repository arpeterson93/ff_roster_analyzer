"""Evaluate a specific proposed trade between two teams. Pure function over
plain dicts/lists so a JS twin (docs/js/trade.js) can be a line-by-line port
for the live in-browser trade calculator."""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.team_strength import PlayerCtx, lineup_total_by_week, lineup_total_with_streaming_by_week

EVEN_THRESHOLD_PER_WEEK = 1.0


@dataclass
class TradeSideResult:
    before: float
    after: float
    gain: float
    raw_given: float
    raw_received: float
    weekly: list[dict] = field(default_factory=list)  # [{week, before, after, delta}]


@dataclass
class TradeResult:
    side_a: TradeSideResult
    side_b: TradeSideResult
    favors: str  # "a" | "b" | "even"


def evaluate(
    *,
    gives_a: list[str],
    gives_b: list[str],
    roster_a: list[str],
    roster_b: list[str],
    players: dict[str, PlayerCtx],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> TradeResult:
    before_a_wk = lineup_total_by_week(roster_a, players, weeks, slots, eligibility)
    before_b_wk = lineup_total_by_week(roster_b, players, weeks, slots, eligibility)

    after_roster_a = [p for p in roster_a if p not in gives_a] + gives_b
    after_roster_b = [p for p in roster_b if p not in gives_b] + gives_a

    after_a_wk = lineup_total_by_week(after_roster_a, players, weeks, slots, eligibility)
    after_b_wk = lineup_total_by_week(after_roster_b, players, weeks, slots, eligibility)

    before_a, before_b = sum(before_a_wk.values()), sum(before_b_wk.values())
    after_a, after_b = sum(after_a_wk.values()), sum(after_b_wk.values())

    raw_given_a = sum(players[p].ros_total for p in gives_a)
    raw_given_b = sum(players[p].ros_total for p in gives_b)

    weekly_a = [{"week": w, "before": before_a_wk[w], "after": after_a_wk[w], "delta": after_a_wk[w] - before_a_wk[w]} for w in weeks]
    weekly_b = [{"week": w, "before": before_b_wk[w], "after": after_b_wk[w], "delta": after_b_wk[w] - before_b_wk[w]} for w in weeks]

    side_a = TradeSideResult(
        before=before_a, after=after_a, gain=after_a - before_a, raw_given=raw_given_a, raw_received=raw_given_b,
        weekly=weekly_a,
    )
    side_b = TradeSideResult(
        before=before_b, after=after_b, gain=after_b - before_b, raw_given=raw_given_b, raw_received=raw_given_a,
        weekly=weekly_b,
    )

    diff = side_a.gain - side_b.gain
    threshold = EVEN_THRESHOLD_PER_WEEK * len(weeks)
    if abs(diff) < threshold:
        favors = "even"
    else:
        favors = "a" if diff > 0 else "b"

    return TradeResult(side_a=side_a, side_b=side_b, favors=favors)


def evaluate_with_streaming(
    *,
    gives_a: list[str],
    gives_b: list[str],
    roster_a: list[str],
    roster_b: list[str],
    players: dict[str, PlayerCtx],
    free_agents_by_pos: dict[str, list[PlayerCtx]],
    weeks: list[int],
    slots: dict[str, int],
    eligibility: dict[str, set[str]],
) -> TradeResult:
    """Like evaluate(), but assumes each side would stream in a waiver-wire
    replacement for any position a trade leaves empty (see
    lineup_total_with_streaming_by_week) - so trading away, say, a lone
    kicker before his bye week isn't scored as a real loss when a comparable
    kicker is sitting on the wire to fill that same bye."""
    before_a_wk = lineup_total_with_streaming_by_week(roster_a, players, free_agents_by_pos, weeks, slots, eligibility)
    before_b_wk = lineup_total_with_streaming_by_week(roster_b, players, free_agents_by_pos, weeks, slots, eligibility)

    after_roster_a = [p for p in roster_a if p not in gives_a] + gives_b
    after_roster_b = [p for p in roster_b if p not in gives_b] + gives_a

    after_a_wk = lineup_total_with_streaming_by_week(after_roster_a, players, free_agents_by_pos, weeks, slots, eligibility)
    after_b_wk = lineup_total_with_streaming_by_week(after_roster_b, players, free_agents_by_pos, weeks, slots, eligibility)

    before_a, before_b = sum(before_a_wk.values()), sum(before_b_wk.values())
    after_a, after_b = sum(after_a_wk.values()), sum(after_b_wk.values())

    raw_given_a = sum(players[p].ros_total for p in gives_a)
    raw_given_b = sum(players[p].ros_total for p in gives_b)

    weekly_a = [{"week": w, "before": before_a_wk[w], "after": after_a_wk[w], "delta": after_a_wk[w] - before_a_wk[w]} for w in weeks]
    weekly_b = [{"week": w, "before": before_b_wk[w], "after": after_b_wk[w], "delta": after_b_wk[w] - before_b_wk[w]} for w in weeks]

    side_a = TradeSideResult(
        before=before_a, after=after_a, gain=after_a - before_a, raw_given=raw_given_a, raw_received=raw_given_b,
        weekly=weekly_a,
    )
    side_b = TradeSideResult(
        before=before_b, after=after_b, gain=after_b - before_b, raw_given=raw_given_b, raw_received=raw_given_a,
        weekly=weekly_b,
    )

    diff = side_a.gain - side_b.gain
    threshold = EVEN_THRESHOLD_PER_WEEK * len(weeks)
    if abs(diff) < threshold:
        favors = "even"
    else:
        favors = "a" if diff > 0 else "b"

    return TradeResult(side_a=side_a, side_b=side_b, favors=favors)
