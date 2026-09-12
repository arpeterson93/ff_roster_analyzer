"""One-off historical FAAB bid puller for The O League (id 355398).

Not part of the daily pipeline - run manually when you want to (re)build the
historical dataset (e.g. once a season wraps). Requires ESPN_S2_OLEAGUE /
SWID_OLEAGUE env vars (same espn_api credential pair AGS already uses, just
under this league's own name - see README).

Pulls every FREEAGENT/WAIVER/WAIVER_ERROR transaction across the given
seasons, drops in-flight "isPending" snapshots (superseded by a later
terminal record), collapses same-team/same-week/same-add-target contingent
bids (submitted with different drop targets as a fallback in case one drop
target got claimed first - these are one real bid, not independent competing
offers) down to one representative record, and classifies each survivor's
competitive signal:
  - "won"           - the bid that actually executed
  - "outbid"        - lost specifically because a competing bid got the
                       player first (FAILED_INVALIDPLAYERSOURCE) - the
                       useful "losing bid" market signal
  - "other_failure" - failed for a reason unrelated to bid competition
                       (the bidder's own roster was full, their intended
                       drop target was already gone, etc.) - kept in the
                       output for transparency, but not a market signal and
                       should be excluded from any bid-prediction model
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from espn_api.football import League

LEAGUE_ID = 355398
WEEKS = range(1, 18)

OUTBID_STATUSES = {"FAILED_INVALIDPLAYERSOURCE"}

# ESPN never recorded a bid_amount for uncontested "FREEAGENT" adds (no
# competing bid means nothing to auction), but the league's actual house
# rule still charged a flat fee out of the same season budget for any add,
# contested or not - so a team's TRUE total spend has to add this back in
# rather than trust ESPN's bid_amount=0 at face value.
FREEAGENT_FLAT_COST_DOLLARS = 2.0


def fetch_season(league: League, year: int, *, cents_scale: bool = True) -> list[dict]:
    """cents_scale=True (The O League's own default - real money changes
    hands here, and ESPN's bidAmount for this league really is in cents,
    e.g. raw 2225 -> a real $22.25 bid) divides bidAmount by 100 to get
    real dollars. Confirmed live 2026-09 this does NOT hold universally:
    pooling other public leagues, several showed a single top bid's raw
    bidAmount landing suspiciously close to (or exactly at) their own
    reported acquisitionBudget - e.g. one league's real $200 budget saw a
    top raw bid of exactly 200, an all-in bid reported as a plain dollar
    integer, not cents - so pull_public_league_bids.py calls this with
    cents_scale=False and keeps every other league's nominal bidAmount as
    ESPN reports it, undivided."""
    rows = []
    for week in WEEKS:
        params = {"view": "mTransactions2", "scoringPeriodId": week}
        filters = {"transactions": {"filterType": {"value": ["FREEAGENT", "WAIVER", "WAIVER_ERROR"]}}}
        headers = {"x-fantasy-filter": json.dumps(filters)}
        try:
            data = league.espn_request.league_get(params=params, headers=headers)
        except Exception as exc:
            print(f"  {year} week {week}: request failed ({exc}), skipping", file=sys.stderr)
            continue
        for t in data.get("transactions", []):
            if t.get("isPending"):
                continue
            add_item = next((i for i in t.get("items", []) if i["type"] == "ADD"), None)
            if add_item is None:
                continue  # a pure DROP or a TRADE, not a FAAB bid
            drop_items = [i for i in t.get("items", []) if i["type"] == "DROP"]
            rows.append(
                {
                    "season": year,
                    "week": week,
                    "transaction_id": t.get("id"),
                    "team_id": t.get("teamId"),
                    "type": t.get("type"),  # FREEAGENT adds are always a $0 uncontested pickup, not a real bid - keep this so the FAAB model can exclude them
                    "status": t.get("status"),
                    "bid_amount_raw": t.get("bidAmount"),
                    "bid_amount_dollars": (t.get("bidAmount") or 0) / 100 if cents_scale else (t.get("bidAmount") or 0),
                    "add_player_id": add_item["playerId"],
                    "add_player_name": league.player_map.get(add_item["playerId"], "Unknown"),
                    "drop_player_ids": [d["playerId"] for d in drop_items],
                    "date": t.get("processDate") or t.get("proposedDate"),
                }
            )
    return rows


def collapse_contingent_bids(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["season"], r["week"], r["team_id"], r["add_player_id"])
        groups.setdefault(key, []).append(r)

    result = []
    for group in groups.values():
        executed = [r for r in group if r["status"] == "EXECUTED"]
        chosen = dict(executed[0] if executed else max(group, key=lambda r: r["bid_amount_raw"] or 0))
        chosen["contingent_variants_dropped"] = len(group) - 1
        result.append(chosen)
    return result


def classify(rows: list[dict], freeagent_flat_cost_dollars: float = FREEAGENT_FLAT_COST_DOLLARS) -> list[dict]:
    """freeagent_flat_cost_dollars defaults to THIS league's own $2 house
    rule - a real fee its human managers charge for uncontested pickups on
    top of what ESPN itself records (ESPN's own bid_amount is $0 for a
    FREEAGENT add; there was no auction). That's specific to The O League,
    not a platform default - a caller pulling any OTHER league's bids (see
    pull_public_league_bids.py) has no evidence its managers do the same and
    must pass 0.0, or every uncontested pickup in that league gets
    overstated by a fee that league never actually charged."""
    for r in rows:
        if r["status"] == "EXECUTED":
            r["signal"] = "won"
        elif r["status"] in OUTBID_STATUSES:
            r["signal"] = "outbid"
        else:
            r["signal"] = "other_failure"

        # What this row actually cost the team, if anything - a losing or
        # otherwise-failed transaction never draws down the budget at all,
        # regardless of type or bid_amount.
        if r["signal"] != "won":
            r["effective_cost_dollars"] = 0.0
        elif r["type"] == "FREEAGENT":
            r["effective_cost_dollars"] = freeagent_flat_cost_dollars
        else:
            r["effective_cost_dollars"] = r["bid_amount_dollars"]
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start-year", type=int, default=2019, help="auction FAAB started in 2019 for this league (2018 had no bid amounts)")
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "o-league-bids.json")
    args = parser.parse_args()

    espn_s2 = os.environ.get("ESPN_S2_OLEAGUE")
    swid = os.environ.get("SWID_OLEAGUE")
    if not espn_s2 or not swid:
        sys.exit("Set ESPN_S2_OLEAGUE and SWID_OLEAGUE env vars first (see README's Watch list/FAAB history section).")

    # 2019-2025 only - see build_training_table.py's O_LEAGUE_LEGACY_LAST_
    # SEASON (duplicated here, not imported - this script doesn't depend on
    # that one). 2026 onward, this league is treated like any other public
    # league: real, nominal (not cents-scaled) bidAmount and a real
    # enforced acquisitionBudget - per the human who runs it, confirmed
    # 2026-09.
    O_LEAGUE_LEGACY_LAST_SEASON = 2025

    all_rows = []
    for year in range(args.start_year, args.end_year + 1):
        print(f"season {year}...", file=sys.stderr)
        league = League(league_id=LEAGUE_ID, year=year, espn_s2=espn_s2, swid=swid)
        all_rows.extend(fetch_season(league, year, cents_scale=year <= O_LEAGUE_LEGACY_LAST_SEASON))

    deduped = collapse_contingent_bids(all_rows)
    classified = classify(deduped)
    classified.sort(key=lambda r: (r["season"], r["week"], r["team_id"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(classified, indent=2))
    signal_counts: dict[str, int] = {}
    for r in classified:
        signal_counts[r["signal"]] = signal_counts.get(r["signal"], 0) + 1
    print(f"wrote {len(classified)} bids ({len(all_rows) - len(classified)} contingent duplicates collapsed) to {args.out}")
    print(f"signal breakdown: {signal_counts}")


if __name__ == "__main__":
    main()
