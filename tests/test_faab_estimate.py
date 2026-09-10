import pytest

from engine.faab_estimate import compute_season_avg_team_spend, target_pct


def test_season_avg_team_spend_divides_by_twelve_teams():
    rows = [
        {"season": 2020, "effective_cost_dollars": 120.0},
        {"season": 2020, "effective_cost_dollars": 0.0},
        {"season": 2021, "effective_cost_dollars": 24.0},
    ]
    avg = compute_season_avg_team_spend(rows)
    assert avg[2020] == pytest.approx(10.0)  # 120/12
    assert avg[2021] == pytest.approx(2.0)  # 24/12


def test_target_pct_uses_effective_cost_not_raw_bid_amount():
    # An uncontested "FREEAGENT" win: ESPN records bid_amount_dollars=0 (no
    # auction happened), but the league's real house rule still charges the
    # flat $2 fee, captured in effective_cost_dollars - target_pct must use
    # that, not the raw (and here, misleading) bid_amount_dollars.
    row = {"season": 2022, "signal": "won", "type": "FREEAGENT", "bid_amount_dollars": 0.0, "effective_cost_dollars": 2.0}
    season_avg_spend = {2022: 40.0}
    assert target_pct(row, season_avg_spend) == pytest.approx(2.0 / 40.0)


def test_target_pct_zero_for_no_bid_and_outbid():
    season_avg_spend = {2022: 40.0}
    no_bid = {"season": 2022, "signal": "no_bid", "type": "NONE", "bid_amount_dollars": 0.0, "effective_cost_dollars": 0.0}
    outbid = {"season": 2022, "signal": "outbid", "type": "WAIVER", "bid_amount_dollars": 5.0, "effective_cost_dollars": 0.0}
    assert target_pct(no_bid, season_avg_spend) == 0.0
    assert target_pct(outbid, season_avg_spend) == 0.0  # a losing bid never actually drew down the budget


def test_target_pct_matches_bid_amount_for_a_real_waiver_win():
    row = {"season": 2022, "signal": "won", "type": "WAIVER", "bid_amount_dollars": 8.0, "effective_cost_dollars": 8.0}
    season_avg_spend = {2022: 40.0}
    assert target_pct(row, season_avg_spend) == pytest.approx(0.2)
