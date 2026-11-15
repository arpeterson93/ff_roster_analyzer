"""Freeze one league's historical FAAB transactions into a small
`<slug>-tx-history.json` the FAAB tab's pipeline step (engine/faab_league.py)
reads at build time:

    python -m tools.faab_history.build_league_tx_history --slug o-league

For The O League this joins three things already in this directory:
  - o-league-bids.json            every 2019-2025 waiver/free-agent transaction
                                  (won / outbid / failed), see pull_o_league_bids.py
  - o-league-training-table.json  adds each player's position (the raw ESPN
                                  pull has none) - read ONCE here, never at
                                  site-build time, since it is ~35 MB
  - the same table's per-season effective_starting_budget (for 2019-2025 that is
                                  the season's own average total team spend - the
                                  league had no enforced cap then; see
                                  build_training_table.build_effective_budgets)

It also writes `<slug>-model-backtest.json` - the FAAB model's held-out historical
predictions (eval_pooled.json) next to what actually happened, for the FAAB tab's
"model vs reality" section: {"won": [[season, week, name, pos, actual_pct,
predicted_median_pct, p_bid]], "calib": [[p_bid, bid_happened 0/1]]}. Only rows
from this league; a "won" row is a real winning bid, "calib" covers every row
where the outcome was a real bid or no bid at all (cancelled/invalid claims are
neither, so they are left out).

Output: {"budgets": {season: budget}, "rows": [raw bid rows + "position"]}.
Rows keep only the fields engine/faab_league.py reads. Seasons from 2026 on are
NOT frozen here - the pipeline pulls the live season itself.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.faab_history.atomic_json import write_json

HERE = Path(__file__).parent
KEEP = (
    "season", "week", "transaction_id", "team_id", "type", "status", "signal", "bid_amount_dollars",
    "effective_cost_dollars", "add_player_id", "add_player_name", "date",
)


def build(slug: str) -> dict:
    bids = json.loads((HERE / f"{slug}-bids.json").read_text(encoding="utf-8"))
    table = json.loads((HERE / f"{slug}-training-table.json").read_text(encoding="utf-8"))

    position_by_player: dict[int, str] = {}
    budget_by_season: dict[int, float] = {}
    for r in table:
        pos = r.get("position")
        if pos and r.get("add_player_id") is not None:
            position_by_player.setdefault(r["add_player_id"], pos)
        b = r.get("effective_starting_budget")
        if b and r.get("season") is not None:
            budget_by_season.setdefault(r["season"], round(float(b), 4))

    rows = []
    for r in bids:
        row = {k: r.get(k) for k in KEEP}
        row["position"] = position_by_player.get(r["add_player_id"])
        rows.append(row)
    rows.sort(key=lambda r: (r["season"], r["week"], r["date"] or 0))
    return {"budgets": {str(k): v for k, v in sorted(budget_by_season.items())}, "rows": rows}


def build_backtest(slug: str) -> dict | None:
    from ingest.config import load_league_config

    path = HERE / "eval_pooled.json"
    if not path.exists():
        return None
    league_id = load_league_config(slug)["league_id"]
    rows = [r for r in json.loads(path.read_text(encoding="utf-8")) if r.get("source_league_id") == league_id]
    if not rows:
        return None
    won = [
        [r["season"], r["week"], r["name"], r["position"], round(r["actual_pct"], 5), round(r["comp_conditional_price"], 5), round(r["comp_bid_probability"], 4)]
        for r in rows if r["signal"] == "won"
    ]
    calib = [[round(r["comp_bid_probability"], 4), 1 if r["signal"] in ("won", "outbid") else 0] for r in rows if r["signal"] in ("won", "outbid", "no_bid")]
    return {"source": "held-out evaluation of the pooled model on this league's own past bids", "won": won, "calib": calib}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", default="o-league")
    args = ap.parse_args()
    out = build(args.slug)
    path = HERE / f"{args.slug}-tx-history.json"
    write_json(path, out, separators=(",", ":"))
    missing = sum(1 for r in out["rows"] if not r["position"])
    print(f"wrote {path.name}: {len(out['rows'])} rows, {missing} without a position, budgets {out['budgets']}")
    backtest = build_backtest(args.slug)
    if backtest:
        write_json(HERE / f"{args.slug}-model-backtest.json", backtest, separators=(",", ":"))
        print(f"wrote {args.slug}-model-backtest.json: {len(backtest['won'])} won bids, {len(backtest['calib'])} calibration rows")


if __name__ == "__main__":
    main()
