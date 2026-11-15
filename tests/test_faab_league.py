from engine.faab_league import COLS, WEEK_BUCKETS, build_faab_league, normalize_rows


def _row(week, team, pid, signal, bid, typ="WAIVER", season=2025, pos="RB", name="P"):
    return {
        "season": season, "week": week, "team_id": team, "type": typ, "signal": signal,
        "bid_amount_dollars": bid, "effective_cost_dollars": bid if signal == "won" else 0.0,
        "add_player_id": pid, "add_player_name": name, "position": pos, "date": week * 1000 + team,
    }


def test_event_stats_attach_to_every_contested_row_and_not_to_failures():
    rows = [
        _row(3, 1, 10, "won", 20.0),
        _row(3, 2, 10, "outbid", 15.0),
        _row(3, 3, 10, "outbid", 9.0),
        _row(3, 4, 10, "other_failure", 30.0),  # a cancelled claim is not a bid
        _row(3, 5, 11, "won", 4.0),            # a different player: its own one-bid auction
    ]
    out = {(r[COLS.index("team_id")], r[COLS.index("player_id")]): dict(zip(COLS, r)) for r in normalize_rows(rows, {2025: 40.0})}
    won, second, third, failed, solo = out[(1, 10)], out[(2, 10)], out[(3, 10)], out[(4, 10)], out[(5, 11)]
    for r in (won, second, third):
        assert (r["n_bids"], r["win_bid"], r["runner_up"]) == (3, 20.0, 15.0)
    assert failed["signal"] == "failed" and failed["n_bids"] is None and failed["win_bid"] is None
    assert (solo["n_bids"], solo["win_bid"], solo["runner_up"]) == (1, 4.0, None)
    assert won["budget"] == 40.0 and won["kind"] == "W" and won["cost"] == 20.0 and second["cost"] == 0.0


def test_free_agent_adds_have_no_auction_fields():
    (row,) = normalize_rows([_row(4, 1, 12, "won", 0.0, typ="FREEAGENT")], {2025: 40.0})
    d = dict(zip(COLS, row))
    assert d["kind"] == "FA" and d["n_bids"] is None and d["win_bid"] is None


def test_live_season_replaces_frozen_rows_for_that_season_and_budget_is_per_season():
    frozen = {"budgets": {"2025": 44.2, "2026": 1.0}, "rows": [_row(2, 1, 10, "won", 5.0, season=2025), _row(2, 1, 10, "won", 5.0, season=2026)]}
    live = [_row(5, 2, 20, "won", 12.0, season=2026)]
    out = build_faab_league(season=2026, frozen=frozen, live_rows=live, live_budget=100.0, baselines={"all": None})
    assert out["cols"] == COLS
    seasons = sorted((r[0], r[3]) for r in out["rows"])
    assert seasons == [(2025, 10), (2026, 20)]  # stale frozen 2026 row dropped, 2025 kept
    assert out["budgets"] == {"2025": 44.2, "2026": 100.0}
    assert dict(zip(COLS, [r for r in out["rows"] if r[0] == 2026][0]))["budget"] == 100.0


def test_no_history_at_all_is_an_empty_but_valid_file():
    out = build_faab_league(season=2026, frozen=None, live_rows=[], live_budget=None, baselines=None)
    assert out["rows"] == [] and out["budgets"] == {}


def test_week_buckets_match_baseline_builder():
    from tools.faab_history.build_global_baselines import WEEK_BUCKETS as BASELINE_BUCKETS
    assert WEEK_BUCKETS == BASELINE_BUCKETS
