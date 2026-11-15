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


def build_faab_league(
    *,
    season: int,
    frozen: dict | None,
    live_rows: list[dict],
    live_budget: float | None,
    baselines: dict | None,
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
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "season": season,
        "cols": COLS,
        "rows": normalize_rows(rows, budgets),
        "budgets": {str(k): v for k, v in sorted(budgets.items())},
        "week_buckets": [list(b) for b in WEEK_BUCKETS],
        "baselines": baselines,
    }
