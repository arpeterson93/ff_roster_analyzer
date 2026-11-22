from engine.odds_history import merge, snapshot_from_build


def _standings(p):
    return [{"team_id": 1, "wins": 3, "losses": 1, "ties": 0, "seed": 1, "playoff_odds": p, "bye_odds": 0.2, "expected_wins": 9.1},
            {"team_id": 2, "wins": 1, "losses": 3, "ties": 0, "seed": 2, "playoff_odds": 1 - p, "bye_odds": 0.0, "expected_wins": 6.0}]


def test_snapshot_needs_a_completed_week():
    assert snapshot_from_build({"weeks_complete": 0}, _standings(0.5)) is None
    snap = snapshot_from_build({"weeks_complete": 4, "generated_at": "t"}, _standings(0.7))
    assert snap["week"] == 4 and snap["teams"]["1"]["playoff"] == 0.7 and snap["teams"]["2"]["wins"] == 1


def test_merge_first_snapshot_per_week_wins_and_other_seasons_dropped():
    old = {"season": 2026, "snapshots": [snapshot_from_build({"weeks_complete": 3}, _standings(0.6))]}
    seed = {"season": 2026, "snapshots": [snapshot_from_build({"weeks_complete": 2}, _standings(0.5)),
                                          snapshot_from_build({"weeks_complete": 3}, _standings(0.9))]}
    new = snapshot_from_build({"weeks_complete": 3}, _standings(0.1))
    out = merge(2026, old, seed, new)
    assert [s["week"] for s in out["snapshots"]] == [2, 3]
    assert out["snapshots"][1]["teams"]["1"]["playoff"] == 0.6  # restored file beats seed and the re-run
    assert merge(2027, old, seed, None)["snapshots"] == []  # new season starts clean
    nxt = merge(2026, out, None, snapshot_from_build({"weeks_complete": 4}, _standings(0.8)))
    assert [s["week"] for s in nxt["snapshots"]] == [2, 3, 4]
