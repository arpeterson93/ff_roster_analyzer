"""Distill the pooled (100+ public leagues) training table into a tiny,
committable `global_price_baselines.json` - the "what do winning waiver bids
usually cost, as a share of budget" reference the FAAB tab compares one
league's own history against (engine/faab_league.py).

The pooled table itself (tools/faab_history/combined-training-table.parquet,
~100 MB, gitignored in spirit - never shipped to the site) is only needed to
BUILD this, not to run the site or the pipeline. Re-run after the pooled
table is rebuilt:

    python -m tools.faab_history.build_global_baselines

Excludes the repo's own configured leagues (so a league is compared against
everyone ELSE, not itself) and counts only real contested wins: type WAIVER,
signal "won", a bid above $0, a known effective budget. Price is expressed as
a fraction of that (league, season)'s effective starting budget - the same
normalisation engine/faab_estimate.py's target_pct uses - so a $200-budget
league and a $100-budget league are comparable.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from tools.faab_history.atomic_json import write_json

TABLE = Path(__file__).parent / "combined-training-table.parquet"
OUT = Path(__file__).parent / "global_price_baselines.json"
OWN_LEAGUE_IDS = (355398, 544449541)

# Week buckets shared with engine/faab_league.py (kept in sync by a test).
WEEK_BUCKETS = [(1, 3), (4, 6), (7, 9), (10, 12), (13, 18)]


def bucket_label(lo: int, hi: int) -> str:
    return f"{lo}-{hi}"


def main() -> None:
    df = pl.read_parquet(TABLE, columns=[
        "source_league_id", "season", "week", "type", "signal", "position",
        "bid_amount_dollars", "effective_starting_budget",
    ])
    won = df.filter(
        (~pl.col("source_league_id").is_in(OWN_LEAGUE_IDS))
        & (pl.col("type") == "WAIVER")
        & (pl.col("signal") == "won")
        & (pl.col("bid_amount_dollars") > 0)
        & (pl.col("effective_starting_budget") > 0)
        & pl.col("position").is_not_null()
    ).with_columns((pl.col("bid_amount_dollars") / pl.col("effective_starting_budget")).alias("pct"))

    def stats(frame: pl.DataFrame) -> dict | None:
        n = frame.height
        if n == 0:
            return None
        q = frame.select(
            pl.col("pct").quantile(0.25, "linear").alias("p25"),
            pl.col("pct").quantile(0.5, "linear").alias("p50"),
            pl.col("pct").quantile(0.75, "linear").alias("p75"),
            pl.col("pct").quantile(0.9, "linear").alias("p90"),
            pl.col("pct").mean().alias("mean"),
        ).row(0, named=True)
        return {"n": n, **{k: round(float(v), 5) for k, v in q.items()}}

    positions = sorted(won["position"].unique().to_list())
    out: dict = {
        "source": "pooled public leagues, own leagues excluded",
        "n_leagues": int(won["source_league_id"].n_unique()),
        "n_wins": won.height,
        "week_buckets": [bucket_label(lo, hi) for lo, hi in WEEK_BUCKETS],
        "all": stats(won),
        "by_position": {pos: stats(won.filter(pl.col("position") == pos)) for pos in positions},
        "by_week_bucket": {},
        "by_position_week_bucket": {},
    }
    for lo, hi in WEEK_BUCKETS:
        label = bucket_label(lo, hi)
        in_bucket = won.filter((pl.col("week") >= lo) & (pl.col("week") <= hi))
        out["by_week_bucket"][label] = stats(in_bucket)
        for pos in positions:
            s = stats(in_bucket.filter(pl.col("position") == pos))
            if s:
                out["by_position_week_bucket"][f"{pos}|{label}"] = s
    write_json(OUT, out)
    print(f"wrote {OUT} - {out['n_wins']:,} wins across {out['n_leagues']} leagues")


if __name__ == "__main__":
    main()
