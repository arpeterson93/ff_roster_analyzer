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
        curve=CURVE,
        matchup_index=MATCHUP,
        current_week=1,
        final_week=4,
        reg_season_count=4,
        opponent=OPPONENT,
        cfg=CFG,
        espn_weekly_projections=None,
    )
    kwargs.update(overrides)
    return project_player(**kwargs)


def test_espn_projection_is_used_outright_with_no_matchup_adjustment():
    proj = _project(espn_weekly_projections={1: 15.0, 2: 8.0, 4: 9.0})
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(15.0)  # ESPN's number, not 10 * matchup mult
    assert weekly[2] == pytest.approx(8.0)
    assert weekly[3] == pytest.approx(0.0)  # bye
    assert weekly[4] == pytest.approx(9.0)
    assert proj.ros_total == pytest.approx(32.0)  # plain sum - no rescale to a curve-derived total


def test_falls_back_to_our_proprietary_number_for_a_week_espn_has_not_published():
    # ESPN only has weeks 1 and 4 so far (a real, not hypothetical, gap - see
    # ingest/espn_client.py's own "hasn't published that far out yet" log) -
    # week 2 falls back to baseline*matchup.
    proj = _project(espn_weekly_projections={1: 15.0, 4: 9.0})
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(15.0)
    assert weekly[2] == pytest.approx(9.0)  # 10 * (1 + 0.5*(0.8-1)) - our own number
    assert weekly[4] == pytest.approx(9.0)


def test_our_projected_field_always_carries_the_proprietary_number_even_when_espn_wins():
    proj = _project(espn_weekly_projections={1: 15.0})
    week1 = next(w for w in proj.weekly if w.week == 1)
    assert week1.projected == pytest.approx(15.0)  # ESPN wins for the primary field
    assert week1.our_projected == pytest.approx(11.0)  # 10 * (1 + 0.5*(1.2-1)) - unaffected, reference-only


def test_no_espn_data_falls_back_to_baseline_times_matchup_every_week():
    proj = _project(espn_weekly_projections=None)
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(11.0)  # 10 * (1 + 0.5*(1.2-1))
    assert weekly[2] == pytest.approx(9.0)  # 10 * (1 + 0.5*(0.8-1))
    assert weekly[3] == pytest.approx(0.0)  # bye
    assert weekly[4] == pytest.approx(10.0)  # 10 * (1 + 0.5*(1.0-1))
    assert proj.ros_total == pytest.approx(30.0)


def test_out_status_zeroes_this_week_without_redistributing_it_elsewhere():
    proj = _project(espn_weekly_projections={1: 15.0}, injury_status="OUT")
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(0.0)
    assert proj.zeroed_this_week is True
    assert proj.zero_reason == "injury:OUT"
    # No redistribution onto weeks 2/4 - a zeroed week is a real, visible loss now.
    assert proj.ros_total == pytest.approx(weekly[2] + weekly[3] + weekly[4])


def test_questionable_status_not_zeroed():
    proj = _project(injury_status="QUESTIONABLE")
    assert proj.zeroed_this_week is False


def test_unranked_player_with_no_espn_data_gets_zero_projection():
    proj = _project(ros_pos_rank=None)
    assert proj.ranked is False
    assert proj.baseline_ppg == 0.0
    assert proj.ros_total == 0.0
    assert all(w.projected == 0.0 for w in proj.weekly)


def test_unranked_player_still_uses_espns_own_number_when_published():
    # A free agent FantasyPros doesn't rank at all can still get a real,
    # differentiated ESPN projection - the actual fix for "every deep
    # waiver kicker shows the identical gain" (engine/curve.py clamps every
    # unranked/deep-ranked player to the same curve-floor baseline; ESPN's
    # own per-player number doesn't have that ceiling).
    proj = _project(ros_pos_rank=None, espn_weekly_projections={1: 3.5})
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(3.5)
    assert proj.baseline_ppg == 0.0  # still unranked on our own curve...
    assert proj.ros_total == pytest.approx(3.5)  # ...but ESPN's number still wins


def test_ir_return_week_zeroes_weeks_before_it_without_redistribution():
    proj = _project(espn_weekly_projections={1: 15.0, 2: 8.0, 4: 9.0}, ir_return_week=3)
    weekly = {w.week: w.projected for w in proj.weekly}
    assert weekly[1] == pytest.approx(0.0)
    assert weekly[2] == pytest.approx(0.0)
    assert weekly[3] == pytest.approx(0.0)  # bye
    assert weekly[4] == pytest.approx(9.0)  # untouched - no longer absorbs weeks 1/2's zeroed points
    assert proj.zeroed_this_week is True
    assert proj.zero_reason == "ir_return_week:3"
    assert proj.ros_total == pytest.approx(9.0)


def test_ir_return_week_beyond_final_week_zeroes_ros_total_too():
    proj = _project(espn_weekly_projections={1: 15.0, 2: 8.0, 4: 9.0}, ir_return_week=5)
    weekly = {w.week: w.projected for w in proj.weekly}
    assert all(v == pytest.approx(0.0) for v in weekly.values())
    assert proj.ros_total == pytest.approx(0.0)


def test_ir_return_week_in_the_past_is_a_no_op():
    proj = _project(current_week=2, ir_return_week=1)
    baseline = _project(current_week=2, ir_return_week=None)
    weekly = {w.week: w.projected for w in proj.weekly}
    baseline_weekly = {w.week: w.projected for w in baseline.weekly}
    assert weekly == baseline_weekly  # an already-elapsed return week changes nothing
    assert proj.zeroed_this_week is False


def test_ir_return_week_does_not_override_an_existing_injury_zero_reason():
    proj = _project(injury_status="OUT", ir_return_week=3)
    assert proj.zero_reason == "injury:OUT"  # first-set reason wins, not overwritten


def test_reg_and_playoff_split():
    proj = _project(espn_weekly_projections={1: 15.0, 2: 8.0, 4: 9.0}, current_week=1, final_week=4, reg_season_count=2)
    weekly = {w.week: w.projected for w in proj.weekly}
    assert proj.reg_total == pytest.approx(weekly[1] + weekly[2])
    assert proj.playoff_total == pytest.approx(weekly[3] + weekly[4])


def test_matchup_index_and_rank_still_attached_for_informational_display():
    # Points no longer come from the matchup index, but the index/rank
    # themselves are still exposed per week - opponent-difficulty context
    # (e.g. for the schedule table) doesn't disappear just because it no
    # longer shapes the point value.
    proj = _project(espn_weekly_projections={1: 15.0})
    week1 = next(w for w in proj.weekly if w.week == 1)
    assert week1.index == pytest.approx(1.2)
    assert week1.rank is None  # MATCHUP fixture never populated rank for T1
