"""Playoff-odds history: what the sim said about every team right after each
completed week, saved as the season goes (odds_history.json) so the Standings
page's Retrospective can plot real, as-published odds rather than a rebuilt
guess. The odds, rosters and projections behind each point are exactly what
that build had - nothing is replayed.

Persistence mirrors faab_estimate_log.json: the previous run's file is restored
from the site-data baseline, this run's snapshot is merged in, the file is
rewritten. A snapshot per completed week, FIRST build wins (the one right after
the week's games finished, before any later re-run changes the picture).
data/history/<slug>/odds_history_seed.json (tools/history/backfill_odds_history.py)
holds snapshots recovered from site-data's git history for weeks that predate
this file.
"""
from __future__ import annotations

SNAPSHOT_FIELDS = ("wins", "losses", "ties", "seed", "playoff_odds", "bye_odds", "expected_wins")


def snapshot_from_build(meta: dict, standings: list[dict]) -> dict | None:
    """One week's snapshot from a build's meta.json + standings.json, or None
    when no regular-season week has completed yet."""
    week = meta.get("weeks_complete") or 0
    if week < 1 or not standings:
        return None
    return {
        "week": week,
        "generated_at": meta.get("generated_at"),
        "teams": {
            str(s["team_id"]): {
                "playoff": s.get("playoff_odds"), "bye": s.get("bye_odds"), "xwins": s.get("expected_wins"),
                "wins": s.get("wins"), "losses": s.get("losses"), "ties": s.get("ties"), "seed": s.get("seed"),
            }
            for s in standings
        },
    }


def merge(season: int, prior: dict | None, seed: dict | None, snapshot: dict | None) -> dict:
    """Combine restored history, the backfill seed and this run's snapshot.
    Only the current season's snapshots are kept (a new season starts clean);
    when a week appears more than once the earliest source wins: prior file,
    then seed, then the new snapshot."""
    by_week: dict[int, dict] = {}
    for source in (prior, seed):
        if source and source.get("season") == season:
            for snap in source.get("snapshots", []):
                by_week.setdefault(snap["week"], snap)
    if snapshot is not None:
        by_week.setdefault(snapshot["week"], snapshot)
    return {"season": season, "snapshots": [by_week[w] for w in sorted(by_week)]}
