import pytest

from engine.curve import Curve
from engine.matchups import MatchupIndex
from engine.valuation import project_player

CURVE = Curve(ppg={"WR": {5: 10.0, 15: 4.0}}, sd={"WR": {5: 2.0, 15: 1.0}}, max_rank={"WR": 15}, position_avg={"WR": 10.0})
MATCHUP = MatchupIndex(index={"WR": {"T1": 1.2, "T2": 0.8, "T4": 1.0}}, rank={"WR": {}})
OPPONENT = {("DET", 1): "T1", ("DET", 2): "T2", ("DET", 3): None, ("DET", 4): "T4"}

CFG = {
    "matchup_dampening": 0.5,
    "index_clamp": (0.5, 1.5),
    "zero_this_week_statuses": ["OUT", "INJURY_RESERVE", "SUSPENSION", "DOUBTFUL"],
}


def _project(**overrides):
    kwargs = dict(
        position="WR",
        nfl_team="DET",
        ros_pos_rank=5,
        injury_status="ACTIVE",
        week_pos_rank=None,
        curve=CURVE,
        matchup_index=MATCHUP,
        current_week=1,
        final_week=4,
        reg_season_count=4,
        opponent=OPPONENT,
        cfg=CFG,
    )
    kwargs.update(overrides)
    return project_player(**kwargs)


def test_weekly_adjustment_and_rescale_to_ros_total():
    proj = _project()
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(11.0)  # 10 * (1 + 0.5*(1.2-1))
    assert weekly[2] == pytest.approx(9.0)  # 10 * (1 + 0.5*(0.8-1))
    assert weekly[3] == pytest.approx(0.0)  # bye
    assert weekly[4] == pytest.approx(10.0)  # 10 * (1 + 0.5*(1.0-1))
    assert proj.ros_total == pytest.approx(30.0)  # 10 * 3 active weeks
    assert sum(weekly.values()) == pytest.approx(proj.ros_total)


def test_out_status_zeroes_this_week_and_rescales():
    proj = _project(injury_status="OUT")
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(0.0)
    assert proj.zeroed_this_week is True
    assert proj.zero_reason == "injury:OUT"
    assert sum(weekly.values()) == pytest.approx(proj.ros_total)
    assert proj.ros_total == pytest.approx(30.0)  # ROS total itself is unaffected


def test_questionable_status_not_zeroed():
    proj = _project(injury_status="QUESTIONABLE")
    assert proj.zeroed_this_week is False


def test_week_pos_rank_maps_directly_to_curve_baseline_for_current_week():
    # week_pos_rank=15 -> curve.ppg_at("WR", 15) = 4.0, used directly for week 1
    # instead of baseline(10) * matchup mult(1.1) = 11.0 - FantasyPros' weekly
    # rank already reflects the week-1 matchup, so no double-adjustment.
    proj = _project(week_pos_rank=15)
    weekly = {w.week: w.projected for w in proj.weekly}
    raw_week1 = 4.0
    total_raw = raw_week1 + 9.0 + 0.0 + 10.0  # weeks 2 and 4 unaffected, week 3 is a bye
    scale = 30.0 / total_raw
    assert weekly[1] == pytest.approx(raw_week1 * scale)
    assert weekly[1] != pytest.approx(11.0)  # not the old baseline*mult value
    assert sum(weekly.values()) == pytest.approx(proj.ros_total)  # still rescaled to ROS total


def test_week_pos_rank_direct_mapping_still_overridden_by_out_status():
    proj = _project(week_pos_rank=15, injury_status="OUT")
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(0.0)
    assert proj.zeroed_this_week is True
    assert sum(weekly.values()) == pytest.approx(proj.ros_total)


def test_no_week_pos_rank_falls_back_to_baseline_times_matchup():
    proj = _project(week_pos_rank=None)
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(11.0)  # unchanged from the no-direct-mapping case


def test_unranked_player_gets_zero_projection():
    proj = _project(ros_pos_rank=None)
    assert proj.ranked is False
    assert proj.baseline_ppg == 0.0
    assert proj.ros_total == 0.0
    assert all(w.projected == 0.0 for w in proj.weekly)


def test_reg_and_playoff_split():
    proj = _project(current_week=1, final_week=4, reg_season_count=2)
    weekly = {w.week: w.projected for w in proj.weekly}
    assert proj.reg_total == pytest.approx(weekly[1] + weekly[2])
    assert proj.playoff_total == pytest.approx(weekly[3] + weekly[4])
