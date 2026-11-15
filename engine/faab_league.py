"""League-specific FAAB history for the site's FAAB tab -> faab_league.json.

The global FAAB Lab (engine/faab_estimate.py) answers "what does this player
usually cost, across 100+ leagues". This answers the other half: "what does
THIS league actually pay" - every historical waiver/free-agent transaction,
plus the pooled-league baselines the page compares them against. The page does
the aggregating (by season/position/week/manager) in the browser from these
rows, so one small file serves every filter combination.

Row layout (COLS): a bid is a waiver claim or a free-agent add; `signal` is
won / outbid (lost to a higher claim) / failed (cancelled or invalid for a
reason unrelated to competition - kept for transparency, never a price signal).
Per auction (same season/week/player) every waiver row also carries how many
real bids it drew, the winning bid and the best losing bid.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

COLS = [
    "season", "week", "team_id", "player_id", "name", "pos", "kind", "signal",
    "bid", "cost", "budget", "n_bids", "win_bid", "runner_up", "date",
]
N_COMPS_LOGGED = 5
# Shared with tools/faab_history/build_global_baselines.py (a test keeps them identical).
WEEK_BUCKETS = [(1, 3), (4, 6), (7, 9), (10, 12), (13, 18)]


def _signal(raw: str | None) -> str:
    return raw if raw in ("won", "outbid") else "failed"


def normalize_rows(rows: list[dict], budgets: dict[int, float]) -> list[list]:
    """Raw classified bid rows (tools.faab_history.pull_o_league_bids.classify
    output plus a "position" key) -> COLS-ordered lists with per-auction
    stats attached. Rows from a season with no known budget keep budget None."""
    events: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("type") == "WAIVER":
            events[(r["season"], r["week"], r["add_player_id"])].append(r)

    event_stats: dict[tuple, tuple[int, float | None, float | None]] = {}
    for key, evt in events.items():
        contested = [r for r in evt if r.get("signal") in ("won", "outbid")]
        won = [r for r in contested if r["signal"] == "won"]
        lost = [r["bid_amount_dollars"] or 0.0 for r in contested if r["signal"] == "outbid"]
        win_bid = max((r["bid_amount_dollars"] or 0.0 for r in won), default=None)
        event_stats[key] = (len(contested), win_bid, max(lost, default=None))

    out: list[list] = []
    for r in sorted(rows, key=lambda r: (r["season"], r["week"], r.get("date") or 0)):
        is_waiver = r.get("type") == "WAIVER"
        n_bids, win_bid, runner_up = event_stats.get((r["season"], r["week"], r["add_player_id"]), (None, None, None)) if is_waiver else (None, None, None)
        signal = _signal(r.get("signal"))
        if is_waiver and signal == "failed":
            n_bids = win_bid = runner_up = None  # a failed row carries no auction information
        out.append([
            r["season"], r["week"], r.get("team_id"), r["add_player_id"], r.get("add_player_name"),
            r.get("position"), "W" if is_waiver else "FA", signal,
            round(r.get("bid_amount_dollars") or 0.0, 2), round(r.get("effective_cost_dollars") or 0.0, 2),
            budgets.get(r["season"]), n_bids, win_bid, runner_up, r.get("date"),
        ])
    return out


# ---------------------------------------------------------------------------
# Model tracking: what the FAAB estimator (engine/faab_estimate.py) said BEFORE
# a week's waivers cleared, kept so it can be held up against what the league
# then actually paid. The log is a persisted file (faab_estimate_log.json,
# carried between runs by the site-data branch, like players.json).
# ---------------------------------------------------------------------------


def snapshot_entries(estimates: dict, players_out: list[dict]) -> dict[str, dict]:
    """{espn_id: entry} for every player the model produced a real price read
    for. Players below the relevance threshold or unranked carry no price
    (the model declines to bid) so are not logged. Comps are the nearest
    neighbours behind the price: name, season, week and the winning price
    each one paid (fraction of budget), heaviest first."""
    espn_by_gsis = {p["id"]: p for p in players_out if p.get("espn_id") is not None}
    out: dict[str, dict] = {}
    for gsis, est in estimates.items():
        player = espn_by_gsis.get(gsis)
        if not player or est.get("below_relevance_threshold") or est.get("unranked") or "inputs" not in est:
            continue
        cond, prob = est.get("conditional_price") or {}, est.get("bid_probability") or {}
        comps = sorted(est.get("comps") or [], key=lambda c: -(c.get("weight_capped") or 0))[:N_COMPS_LOGGED]
        out[str(player["espn_id"])] = {
            "name": player.get("name"), "pos": player.get("position"),
            "med": cond.get("comp_based_median"), "mean": cond.get("comp_based_mean"), "reg": cond.get("regression"),
            "p": prob.get("comp_based_median"), "p_reg": prob.get("regression"),
            "single": bool(est.get("single_backing_flag")),
            "comps": [
                {"n": c.get("name"), "s": c.get("season"), "w": c.get("week"), "p": c.get("pct_of_remaining_budget"), "wt": round(c.get("weight_capped") or 0, 3)}
                for c in comps
            ],
        }
    return out


def update_estimate_log(
    prior: dict | None, entries: dict[str, dict], season: int, week: int, processed_weeks: set[int], taken_at: str,
) -> dict:
    """Fold this run's snapshot (for the week whose waivers are about to run)
    into the log. A week's snapshot is rewritten on every run UNTIL that
    week's waivers have processed (any real waiver row for it in the live
    pull) - from then on it is frozen, because a post-clear estimate would be
    contaminated by the result it is meant to predict. A week first seen
    already processed is never logged. A new season starts a fresh log."""
    log = prior if prior and prior.get("season") == season else {"season": season, "weeks": {}}
    weeks = log["weeks"]
    for w in list(weeks):
        if int(w) in processed_weeks:
            weeks[w]["frozen"] = True
    key = str(week)
    if week in processed_weeks or weeks.get(key, {}).get("frozen"):
        return log
    weeks[key] = {"taken_at": taken_at, "frozen": False, "players": entries}
    return log


def join_model_log(log: dict | None, rows: list[list], budgets: dict, processed_weeks: set[int]) -> list[dict]:
    """Logged estimates joined to what actually happened. status: won (a claim
    won - win_pct is the winning bid as a fraction of that season's budget),
    lost (only losing claims), no_bid (the week processed and nobody bid) or
    pending (the week hasn't processed yet). Comps are kept only for players
    who drew a real bid - the rows someone will want to inspect."""
    if not log:
        return []
    season = log["season"]
    budget = budgets.get(str(season)) or budgets.get(season)
    idx = {c: i for i, c in enumerate(COLS)}
    auctions: dict[tuple, list[list]] = defaultdict(list)
    for r in rows:
        if r[idx["kind"]] == "W" and r[idx["season"]] == season and r[idx["signal"]] in ("won", "outbid"):
            auctions[(r[idx["week"]], r[idx["player_id"]])].append(r)
    out = []
    for w_key, snap in sorted(log["weeks"].items(), key=lambda kv: int(kv[0])):
        week = int(w_key)
        for espn_id, e in snap["players"].items():
            evt = auctions.get((week, int(espn_id)), [])
            won = [r for r in evt if r[idx["signal"]] == "won"]
            if won:
                status, win_bid = "won", won[0][idx["bid"]]
            elif evt:
                status, win_bid = "lost", None
            else:
                status, win_bid = ("no_bid" if week in processed_weeks else "pending"), None
            has_bid = status in ("won", "lost")
            out.append({
                "week": week, "player_id": int(espn_id), "name": e["name"], "pos": e["pos"],
                "med": e["med"], "mean": e["mean"], "reg": e["reg"], "p": e["p"], "single": e["single"],
                "status": status,
                "win_pct": round(win_bid / budget, 5) if win_bid is not None and budget else None,
                "n_bids": len(evt) if evt else None,
                "comps": e["comps"] if has_bid else None,
            })
    return out


def build_faab_league(
    *,
    season: int,
    frozen: dict | None,
    live_rows: list[dict],
    live_budget: float | None,
    baselines: dict | None,
    estimate_log: dict | None = None,
    backtest: dict | None = None,
) -> dict:
    """faab_league.json. `frozen` is tools/faab_history/<slug>-tx-history.json
    ({"budgets": {season: budget}, "rows": [...]}) or None; `live_rows` is the
    current season's freshly-pulled classified rows (replacing any frozen rows
    for that same season, so a re-pull never double counts)."""
    budgets: dict[int, float] = {int(k): v for k, v in ((frozen or {}).get("budgets") or {}).items()}
    rows = [r for r in (frozen or {}).get("rows", []) if r["season"] != season]
    if live_rows:
        rows.extend(live_rows)
        if live_budget:
            budgets[season] = float(live_budget)
    norm = normalize_rows(rows, budgets)
    wk = COLS.index("week")
    processed = {r[wk] for r in norm if r[COLS.index("season")] == season and r[COLS.index("kind")] == "W" and r[COLS.index("signal")] in ("won", "outbid")}
    budgets_out = {str(k): v for k, v in sorted(budgets.items())}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "cols": COLS,
        "rows": norm,
        "budgets": budgets_out,
        "week_buckets": [list(b) for b in WEEK_BUCKETS],
        "baselines": baselines,
        "model_log": join_model_log(estimate_log, norm, budgets_out, processed),
        "model_backtest": backtest,
    }


def processed_weeks_of(rows: list[dict], season: int) -> set[int]:
    """Weeks of `season` whose waivers have really run: any contested waiver row
    (won or outbid) in the live pull. Mirrors the set build_faab_league derives."""
    return {r["week"] for r in rows if r["season"] == season and r.get("type") == "WAIVER" and r.get("signal") in ("won", "outbid")}
