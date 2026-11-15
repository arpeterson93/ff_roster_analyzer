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


# ---------------- model tracking (snapshot -> freeze -> join) ----------------

from engine.faab_league import join_model_log, processed_weeks_of, snapshot_entries, update_estimate_log  # noqa: E402


def _est(med=0.08, p=0.5, comps=None, **extra):
    base = {
        "inputs": {"week": 6}, "conditional_price": {"comp_based_median": med, "comp_based_mean": med + 0.01, "regression": 0.05},
        "bid_probability": {"comp_based_median": p, "regression": 0.4}, "single_backing_flag": False,
        "comps": comps or [{"name": "Old Guy", "season": 2023, "week": 5, "pct_of_remaining_budget": 0.07, "weight_capped": 0.4}],
    }
    base.update(extra)
    return base


PLAYERS = [
    {"id": "g1", "espn_id": 101, "name": "Alpha", "position": "RB"},
    {"id": "g2", "espn_id": 102, "name": "Beta", "position": "WR"},
    {"id": "g3", "espn_id": 103, "name": "Gamma", "position": "TE"},
]


def test_snapshot_skips_players_the_model_declines_to_price():
    est = {"g1": _est(), "g2": {"below_relevance_threshold": True, "inputs": {"week": 6}}, "g3": {"unranked": True}}
    snap = snapshot_entries(est, PLAYERS)
    assert list(snap) == ["101"]
    assert snap["101"]["med"] == 0.08 and snap["101"]["p"] == 0.5 and snap["101"]["comps"][0]["n"] == "Old Guy"


def test_log_rewrites_week_until_waivers_process_then_freezes():
    first = update_estimate_log(None, {"101": {"med": 0.1}}, 2026, 6, set(), "t1")
    second = update_estimate_log(first, {"101": {"med": 0.2}}, 2026, 6, set(), "t2")  # still before the clear: newer snapshot wins
    assert second["weeks"]["6"]["players"]["101"]["med"] == 0.2 and not second["weeks"]["6"]["frozen"]
    after = update_estimate_log(second, {"101": {"med": 0.9}}, 2026, 6, {6}, "t3")  # waivers ran: contaminated, ignored
    assert after["weeks"]["6"]["players"]["101"]["med"] == 0.2 and after["weeks"]["6"]["frozen"]
    # a week first seen already processed is never logged; a new season starts clean
    assert "7" not in update_estimate_log(after, {"102": {}}, 2026, 7, {6, 7}, "t4")["weeks"]
    assert update_estimate_log(after, {"101": {}}, 2027, 1, set(), "t5")["weeks"].keys() == {"1"}


def test_join_classifies_won_lost_no_bid_and_pending():
    log = {"season": 2026, "weeks": {
        "6": {"frozen": True, "players": {
            "101": {"name": "Alpha", "pos": "RB", "med": 0.08, "mean": 0.09, "reg": 0.05, "p": 0.6, "single": False, "comps": [{"n": "x"}]},
            "102": {"name": "Beta", "pos": "WR", "med": 0.03, "mean": 0.03, "reg": 0.02, "p": 0.2, "single": False, "comps": [{"n": "y"}]},
            "103": {"name": "Gamma", "pos": "TE", "med": 0.02, "mean": 0.02, "reg": 0.02, "p": 0.1, "single": False, "comps": []},
        }},
        "7": {"frozen": False, "players": {"101": {"name": "Alpha", "pos": "RB", "med": 0.1, "mean": 0.1, "reg": 0.1, "p": 0.5, "single": False, "comps": []}}},
    }}
    raw = [
        _row(6, 1, 101, "won", 8.0, season=2026), _row(6, 2, 101, "outbid", 6.0, season=2026),
        _row(6, 3, 102, "outbid", 2.0, season=2026),  # only losing claims on Beta
    ]
    norm = normalize_rows(raw, {2026: 100.0})
    got = {(r["week"], r["player_id"]): r for r in join_model_log(log, norm, {"2026": 100.0}, processed_weeks_of(raw, 2026))}
    assert got[(6, 101)]["status"] == "won" and got[(6, 101)]["win_pct"] == 0.08 and got[(6, 101)]["n_bids"] == 2 and got[(6, 101)]["comps"] == [{"n": "x"}]
    assert got[(6, 102)]["status"] == "lost" and got[(6, 102)]["win_pct"] is None
    assert got[(6, 103)]["status"] == "no_bid" and got[(6, 103)]["comps"] is None
    assert got[(7, 101)]["status"] == "pending"


def test_build_carries_model_log_and_backtest():
    out = build_faab_league(season=2026, frozen=None, live_rows=[], live_budget=None, baselines=None, estimate_log=None, backtest={"won": []})
    assert out["model_log"] == [] and out["model_backtest"] == {"won": []}
