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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slug", default="o-league")
    args = ap.parse_args()
    out = build(args.slug)
    path = HERE / f"{args.slug}-tx-history.json"
    write_json(path, out, separators=(",", ":"))
    missing = sum(1 for r in out["rows"] if not r["position"])
    print(f"wrote {path.name}: {len(out['rows'])} rows, {missing} without a position, budgets {out['budgets']}")


if __name__ == "__main__":
    main()
