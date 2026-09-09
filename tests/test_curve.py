import polars as pl
import pytest

from engine.curve import Curve, blend_current, build_curve
from engine.scoring import ScoringRules

REC_SCORING = ScoringRules.from_espn([{"id": 1, "abbr": "REC", "points": 1}])


def _weekly_rows(season: int, player_id: str, position: str, receptions: int, n_games: int) -> list[dict]:
    return [
        {
            "season": season,
            "season_type": "REG",
            "position": position,
            "player_id": player_id,
            "receptions": receptions,
        }
        for _ in range(n_games)
    ]


def test_build_curve_averages_by_rank_across_seasons():
    rows = (
        _weekly_rows(2023, "A", "WR", 10, 8)
        + _weekly_rows(2023, "B", "WR", 8, 8)
        + _weekly_rows(2023, "C", "WR", 6, 8)
        + _weekly_rows(2024, "A", "WR", 12, 8)
        + _weekly_rows(2024, "B", "WR", 9, 8)
        + _weekly_rows(2024, "D", "WR", 5, 8)
    )
    df = pl.DataFrame(rows)
    curve = build_curve({2023: df, 2024: df}, REC_SCORING, min_games=8, positions=["WR"], starter_pool={"WR": 3})

    assert curve.ppg_at("WR", 1) == pytest.approx(11.0)  # mean(10, 12)
    assert curve.ppg_at("WR", 2) == pytest.approx(8.5)  # mean(8, 9)
    assert curve.ppg_at("WR", 3) == pytest.approx(5.5)  # mean(6, 5)
    assert curve.position_avg["WR"] == pytest.approx((11.0 + 8.5 + 5.5) / 3)
    # beyond max_rank, clamp to the last value
    assert curve.ppg_at("WR", 10) == pytest.approx(5.5)


def test_build_curve_monotone_fixup_clips_spikes():
    # 2023 has 4 eligible WRs (ranks 1-4); 2024 only has 3, and its rank-3 value
    # is unusually low, which would otherwise make the rank-4 average (fed only
    # by 2023) exceed the rank-3 average.
    rows = (
        _weekly_rows(2023, "A", "WR", 10, 8)
        + _weekly_rows(2023, "B", "WR", 8, 8)
        + _weekly_rows(2023, "C", "WR", 6, 8)
        + _weekly_rows(2023, "D", "WR", 5, 8)
        + _weekly_rows(2024, "E", "WR", 12, 8)
        + _weekly_rows(2024, "F", "WR", 9, 8)
        + _weekly_rows(2024, "G", "WR", 1, 8)
    )
    df = pl.DataFrame(rows)
    curve = build_curve({2023: df, 2024: df}, REC_SCORING, min_games=8, positions=["WR"], starter_pool={"WR": 4})

    raw_rank3 = (6 + 1) / 2  # 3.5
    raw_rank4 = 5.0  # only 2023 contributes
    assert raw_rank4 > raw_rank3  # confirms the spike this test is checking for
    assert curve.ppg_at("WR", 3) == pytest.approx(3.5)
    assert curve.ppg_at("WR", 4) == pytest.approx(3.5)  # clipped down to rank 3's value
    # sequence is non-increasing
    values = [curve.ppg_at("WR", r) for r in range(1, 5)]
    assert values == sorted(values, reverse=True)


def test_min_games_excludes_short_samples():
    rows = _weekly_rows(2023, "A", "WR", 10, 8) + _weekly_rows(2023, "B", "WR", 20, 3)
    df = pl.DataFrame(rows)
    curve = build_curve({2023: df}, REC_SCORING, min_games=8, positions=["WR"], starter_pool={"WR": 5})
    assert curve.max_rank["WR"] == 1
    assert curve.ppg_at("WR", 1) == pytest.approx(10.0)


def test_blend_current_zero_weeks_returns_history_untouched():
    hist = Curve(ppg={"WR": {1: 20.0, 2: 15.0}}, sd={"WR": {1: 5.0, 2: 4.0}}, max_rank={"WR": 2}, position_avg={"WR": 17.5})
    cur = Curve(ppg={"WR": {1: 30.0, 2: 25.0}}, sd={"WR": {1: 1.0, 2: 1.0}}, max_rank={"WR": 2}, position_avg={"WR": 27.5})
    blended = blend_current(hist, cur, weeks_played=0, max_weight=0.5, full_weight_weeks=8)
    assert blended is hist


def test_blend_current_partial_weight():
    hist = Curve(ppg={"WR": {1: 20.0}}, sd={"WR": {1: 5.0}}, max_rank={"WR": 1}, position_avg={"WR": 20.0})
    cur = Curve(ppg={"WR": {1: 30.0}}, sd={"WR": {1: 1.0}}, max_rank={"WR": 1}, position_avg={"WR": 30.0})
    # weeks_played=4, full_weight_weeks=8 -> weight = (4/8) * 0.5 = 0.25
    blended = blend_current(hist, cur, weeks_played=4, max_weight=0.5, full_weight_weeks=8)
    assert blended.ppg_at("WR", 1) == pytest.approx(0.75 * 20.0 + 0.25 * 30.0)
    assert blended.sd_at("WR", 1) == pytest.approx(0.75 * 5.0 + 0.25 * 1.0)


def test_blend_current_caps_at_max_weight():
    hist = Curve(ppg={"WR": {1: 20.0}}, sd={"WR": {1: 5.0}}, max_rank={"WR": 1}, position_avg={"WR": 20.0})
    cur = Curve(ppg={"WR": {1: 30.0}}, sd={"WR": {1: 1.0}}, max_rank={"WR": 1}, position_avg={"WR": 30.0})
    # weeks_played=16 >> full_weight_weeks=8 -> weight caps at max_weight=0.5
    blended = blend_current(hist, cur, weeks_played=16, max_weight=0.5, full_weight_weeks=8)
    assert blended.ppg_at("WR", 1) == pytest.approx(0.5 * 20.0 + 0.5 * 30.0)
