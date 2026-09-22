"""ROS valuation: ESPN's own per-week projection is the primary number,
taken outright with no matchup or other adjustment layered on top - our
proprietary rank -> curve -> baseline*matchup method only fills in a week
ESPN hasn't published a projection for yet (see the conversation this was
built from: stacking our own opponent adjustment on ESPN's already
matchup-aware number would double-count it, and deep/unranked free agents
all collapsing to the same curve-floor value was producing suggestion lists
with several "different" players tied on an identical gain). The
proprietary number is still computed every week and exposed as
`our_projected` - a secondary, reference-only figure shown wherever the
site used to show ESPN's own number, not blended into `projected`."""
from __future__ import annotations

from dataclasses import dataclass, field

from engine.curve import Curve
from engine.matchups import MatchupIndex


@dataclass
class WeeklyProjection:
    week: int
    opponent: str | None
    index: float | None
    rank: int | None
    projected: float
    our_projected: float
    sd: float


@dataclass
class Projection:
    baseline_ppg: float
    ranked: bool
    ros_total: float
    reg_total: float
    playoff_total: float
    this_week: float
    zeroed_this_week: bool
    zero_reason: str | None
    weekly: list[WeeklyProjection] = field(default_factory=list)


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def project_player(
    *,
    position: str,
    nfl_team: str,
    ros_pos_rank: int | None,
    injury_status: str,
    curve: Curve,
    matchup_index: MatchupIndex,
    current_week: int,
    final_week: int,
    reg_season_count: int,
    opponent: dict[tuple[str, int], str | None],
    cfg: dict,
    espn_weekly_projections: dict[int, float] | None = None,
    ir_return_week: int | None = None,
) -> Projection:
    ranked = ros_pos_rank is not None
    baseline = curve.ppg_at(position, ros_pos_rank) if ranked else 0.0
    espn_weekly_projections = espn_weekly_projections or {}

    weeks = list(range(current_week, final_week + 1))
    d = cfg["matchup_dampening"]
    clamp = tuple(cfg["index_clamp"])

    opp_for_week: dict[int, str | None] = {}
    idx_for_week: dict[int, float | None] = {}
    rank_for_week: dict[int, int | None] = {}
    our_raw: dict[int, float] = {}  # proprietary baseline*matchup number - reference only, see module docstring
    raw: dict[int, float] = {}  # the real, primary projection

    for w in weeks:
        opp = opponent.get((nfl_team, w))
        opp_for_week[w] = opp
        if opp is None:  # bye week
            our_raw[w] = 0.0
            raw[w] = 0.0
            continue
        idx = _clamp(matchup_index.index.get(position, {}).get(opp, 1.0), clamp)
        idx_for_week[w] = idx
        rank_for_week[w] = matchup_index.rank.get(position, {}).get(opp)
        mult = 1 + d * (idx - 1)
        our_raw[w] = baseline * mult
        espn_val = espn_weekly_projections.get(w)
        raw[w] = espn_val if espn_val is not None else our_raw[w]

    zeroed = False
    zero_reason: str | None = None
    if current_week in raw and opp_for_week.get(current_week) is not None:
        if injury_status in cfg["zero_this_week_statuses"]:
            zeroed, zero_reason = True, f"injury:{injury_status}"
            raw[current_week] = 0.0

    # A known IR return week (see ingest/espn_injuries.py) extends the zero
    # past just the current week - every week strictly before it gets zeroed
    # here too. Unlike the old rescale-based method, a zeroed week's points
    # simply don't count anymore - no redistribution onto later weeks, since
    # that would itself be an adjustment on top of the raw per-week numbers.
    # A no-op if the player's already projected to be back by/before the
    # current week.
    if ir_return_week is not None:
        for w in weeks:
            if w >= ir_return_week:
                break
            if opp_for_week.get(w) is None:
                continue  # bye - already 0
            raw[w] = 0.0
            if w == current_week and not zeroed:
                zeroed, zero_reason = True, f"ir_return_week:{ir_return_week}"

    ros_total = sum(raw.values())

    weekly = [
        WeeklyProjection(
            week=w,
            opponent=opp_for_week[w],
            index=idx_for_week.get(w),
            rank=rank_for_week.get(w),
            projected=raw[w],
            our_projected=our_raw[w],
            sd=curve.sd_at(position, ros_pos_rank) if ranked else 0.0,
        )
        for w in weeks
    ]

    reg_total = sum(raw[w] for w in weeks if w <= reg_season_count)
    playoff_total = sum(raw[w] for w in weeks if w > reg_season_count)
    this_week = raw.get(current_week, 0.0)

    return Projection(
        baseline_ppg=baseline,
        ranked=ranked,
        ros_total=ros_total,
        reg_total=reg_total,
        playoff_total=playoff_total,
        this_week=this_week,
        zeroed_this_week=zeroed,
        zero_reason=zero_reason,
        weekly=weekly,
    )
