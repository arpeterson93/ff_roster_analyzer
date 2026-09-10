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
    espn_week_projection: float | None = None,
    ir_return_week: int | None = None,
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

    # Current week only: prefer a source that already knows this week's
    # specific matchup/injury/game-script over our own ROS-rank baseline *
    # matchup multiplier (layering our own opponent adjustment on top of one
    # of these would double-count the matchup). Preference order:
    #   1. ESPN's own weekly projection - their model reacts same-week to
    #      news our nightly FantasyPros scrape may not have caught yet.
    #   2. FantasyPros' weekly consensus rank mapped to the curve baseline.
    #   3. (fall through) our own baseline*matchup value, computed above.
    # Every other remaining week always uses our own baseline*matchup method,
    # since neither source publishes a rank/projection beyond the current week.
    if current_week in raw and opp_for_week.get(current_week) is not None:
        direct = None
        if espn_week_projection is not None:
            direct = espn_week_projection
        elif week_pos_rank is not None:
            direct = curve.ppg_at(position, week_pos_rank)
        if direct is not None:
            raw[current_week] = direct
            mult_for_week[current_week] = (direct / baseline) if baseline > 0 else 0.0

    zeroed = False
    zero_reason: str | None = None
    if current_week in raw and opp_for_week.get(current_week) is not None:
        if injury_status in cfg["zero_this_week_statuses"]:
            zeroed, zero_reason = True, f"injury:{injury_status}"
            raw[current_week] = 0.0
            mult_for_week[current_week] = 0.0

    # A known IR return week (see ingest/espn_injuries.py) extends the zero
    # past just the current week - every week strictly before it gets zeroed
    # here too, BEFORE the raw -> ros_total rescale below, so those points
    # get redistributed onto the weeks they're actually expected to play
    # instead of just vanishing. A no-op if they're already projected to be
    # back by/before the current week.
    if ir_return_week is not None:
        for w in weeks:
            if w >= ir_return_week:
                break
            if opp_for_week.get(w) is None:
                continue  # bye - already 0
            raw[w] = 0.0
            mult_for_week[w] = 0.0
            if w == current_week and not zeroed:
                zeroed, zero_reason = True, f"ir_return_week:{ir_return_week}"

    total_raw = sum(raw.values())
    # The IR-zeroing loop above only zeroes weeks strictly before
    # ir_return_week - if that's beyond final_week (a long-term injury not
    # expected back within the tracked window at all), every remaining week
    # ends up zeroed with nothing left to redistribute onto. ros_total is
    # otherwise guaranteed by construction to equal sum(weekly[].projected)
    # (that's what the rescale below does), so it has to collapse to 0 here
    # too rather than staying at its pre-zeroing value - the player's ROS
    # total within this window really is 0 if they're not projected to play
    # any of it.
    if total_raw <= 0:
        ros_total = 0.0
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
