"""The 5-step ROS valuation method: rank -> baseline PPG -> ROS total ->
opponent-adjusted weekly projection -> rescaled back to the ROS total."""
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
    week_pos_rank: int | None,
    curve: Curve,
    matchup_index: MatchupIndex,
    current_week: int,
    final_week: int,
    reg_season_count: int,
    opponent: dict[tuple[str, int], str | None],
    cfg: dict,
) -> Projection:
    ranked = ros_pos_rank is not None
    baseline = curve.ppg_at(position, ros_pos_rank) if ranked else 0.0

    weeks = list(range(current_week, final_week + 1))
    d = cfg["matchup_dampening"]
    clamp = tuple(cfg["index_clamp"])

    opp_for_week: dict[int, str | None] = {}
    idx_for_week: dict[int, float | None] = {}
    rank_for_week: dict[int, int | None] = {}
    mult_for_week: dict[int, float] = {}
    raw: dict[int, float] = {}

    for w in weeks:
        opp = opponent.get((nfl_team, w))
        opp_for_week[w] = opp
        if opp is None:  # bye week
            mult_for_week[w] = 0.0
            raw[w] = 0.0
            continue
        idx = _clamp(matchup_index.index.get(position, {}).get(opp, 1.0), clamp)
        idx_for_week[w] = idx
        rank_for_week[w] = matchup_index.rank.get(position, {}).get(opp)
        mult = 1 + d * (idx - 1)
        mult_for_week[w] = mult
        raw[w] = baseline * mult

    active_weeks = [w for w in weeks if opp_for_week[w] is not None]
    ros_total = baseline * len(active_weeks)

    # Current week only: map FantasyPros' WEEKLY consensus rank directly to
    # the curve baseline, instead of our own ROS-rank baseline * matchup
    # multiplier. Their weekly rank already reflects this week's specific
    # opponent (their experts rank him knowing who he plays), so layering our
    # own opponent adjustment on top of it would double-count the matchup.
    # Every other remaining week still uses our own baseline*matchup method,
    # since we only have a weekly consensus rank for the current week.
    if week_pos_rank is not None and current_week in raw and opp_for_week.get(current_week) is not None:
        direct = curve.ppg_at(position, week_pos_rank)
        raw[current_week] = direct
        mult_for_week[current_week] = (direct / baseline) if baseline > 0 else 0.0

    zeroed = False
    zero_reason: str | None = None
    if current_week in raw and opp_for_week.get(current_week) is not None:
        if injury_status in cfg["zero_this_week_statuses"]:
            zeroed, zero_reason = True, f"injury:{injury_status}"
            raw[current_week] = 0.0
            mult_for_week[current_week] = 0.0

    total_raw = sum(raw.values())
    scale = (ros_total / total_raw) if total_raw > 0 else 0.0
    adjusted = {w: raw[w] * scale for w in weeks}

    weekly = [
        WeeklyProjection(
            week=w,
            opponent=opp_for_week[w],
            index=idx_for_week.get(w),
            rank=rank_for_week.get(w),
            projected=adjusted[w],
            sd=(curve.sd_at(position, ros_pos_rank) if ranked else 0.0) * mult_for_week[w],
        )
        for w in weeks
    ]

    reg_total = sum(adjusted[w] for w in weeks if w <= reg_season_count)
    playoff_total = sum(adjusted[w] for w in weeks if w > reg_season_count)
    this_week = adjusted.get(current_week, 0.0)

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
