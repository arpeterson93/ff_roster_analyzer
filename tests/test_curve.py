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


def test_smooth_sd_monotone_positions_are_non_increasing_and_keep_the_trend():
    from engine.curve import smooth_sd
    raw = {1: 6.1, 2: 7.9, 3: 6.0, 4: 5.3, 5: 5.6, 6: 4.7, 7: 5.1, 8: 4.4, 9: 4.6, 10: 3.9, 11: 4.2, 12: 3.5}
    out = smooth_sd(raw, monotone=True)
    vals = [out[r] for r in sorted(out)]
    assert all(a >= b - 1e-12 for a, b in zip(vals, vals[1:]))  # TE1 no longer below TE2
    assert vals[0] > vals[-1] and vals[0] < max(raw.values())
    assert sum(vals) / len(vals) == pytest.approx(sum(raw.values()) / len(raw), rel=0.05)


def test_smooth_sd_flat_positions_reduce_noise_without_shifting_the_level():
    from engine.curve import smooth_sd
    raw = {r: 7.5 + (1.5 if r % 2 else -1.5) for r in range(1, 21)}
    out = smooth_sd(raw, monotone=False)
    assert max(out.values()) - min(out.values()) < 1.0
    assert sum(out.values()) / 20 == pytest.approx(7.5, abs=0.2)


def test_smooth_sd_leaves_tiny_tables_alone():
    from engine.curve import smooth_sd
    assert smooth_sd({1: 5.0, 2: 6.0}, monotone=True) == {1: 5.0, 2: 6.0}


def test_load_outcome_sd_reads_smoothed_column_and_clamps(tmp_path):
    import json
    from engine.curve import load_outcome_sd, outcome_sd_at

    p = tmp_path / "t.json"
    p.write_text(json.dumps({"positions": {"WR": {"weekly": [[1, 9.0, 11.0, 80, 8.5], [2, 7.0, 9.0, 80, 8.0]], "ros": [[1, 9.5, 11.0, 80, 9.0], [2, 7.5, 9.0, 80, 8.2]]}}}))
    t = load_outcome_sd(p)
    assert t["weekly"]["WR"] == {1: 8.5, 2: 8.0} and t["ros"]["WR"] == {1: 9.0, 2: 8.2}
    assert outcome_sd_at(t["ros"]["WR"], 50) == 8.2 and outcome_sd_at(t["ros"]["WR"], 0) == 9.0
    assert load_outcome_sd(tmp_path / "missing.json") is None
