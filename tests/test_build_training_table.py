import datetime as dt

import polars as pl

from engine.scoring import ScoringRules
from ingest.ids import IdMap
from tools.faab_history.build_training_table import (
    _compute_weekly_blackouts,
    _ros_ranked_candidates,
    build_no_bid_rows,
    forward_rank_features,
)

# A 4-week 2024 season, one week apart, mirroring the conversation this was
# built from: 2024's real weekly-RB data didn't start until Sept 27, AFTER
# week 4's own kickoff (Sept 26) - so weeks 1-4 are all a genuine blackout,
# not just week 1-3.
WEEK_STARTS_2024 = {
    (2024, 1): dt.date(2024, 9, 5),
    (2024, 2): dt.date(2024, 9, 12),
    (2024, 3): dt.date(2024, 9, 19),
    (2024, 4): dt.date(2024, 9, 26),
    (2024, 5): dt.date(2024, 10, 3),
}
# A different, earlier season - present so a season-scoping bug (treating
# ANY prior snapshot, regardless of season, as "not blacked out") would be
# caught: 2023 has real weekly-rb data well before 2024's season even
# starts.
WEEK_STARTS_2023 = {(2023, 1): dt.date(2023, 9, 7)}


def _idmap(gsis_to_fp: dict[str, int]) -> IdMap:
    by_gsis, by_fp = {}, {}
    for gsis_id, fp_id in gsis_to_fp.items():
        record = {"gsis_id": gsis_id, "fantasypros_id": fp_id}
        by_gsis[gsis_id] = record
        by_fp[fp_id] = record
    return IdMap(by_gsis=by_gsis, by_fp=by_fp, by_espn={}, by_name_pos={}, ambiguous_name_pos=set())


def test_compute_weekly_blackouts_true_before_the_seasons_first_snapshot():
    week_starts = {**WEEK_STARTS_2023, **WEEK_STARTS_2024}
    snapshots = {
        ("weekly-rb", 999): [(dt.date(2024, 9, 27), 29.44)],
        ("weekly-rb", 111): [(dt.date(2023, 9, 1), 10.0)],  # 2023's own real data - must not leak into 2024
    }
    blackout = _compute_weekly_blackouts(snapshots, week_starts, [2023, 2024])
    for week in (1, 2, 3, 4):
        assert blackout[("weekly-rb", 2024, week)] is True
    # 2023, by contrast, already had real data before its own week 1.
    assert blackout[("weekly-rb", 2023, 1)] is False


def test_compute_weekly_blackouts_false_once_a_real_snapshot_exists():
    snapshots = {("weekly-rb", 999): [(dt.date(2024, 9, 27), 29.44)]}
    blackout = _compute_weekly_blackouts(snapshots, WEEK_STARTS_2024, [2024])
    assert blackout[("weekly-rb", 2024, 5)] is False  # Sept 27 < Oct 3 cutoff


def test_compute_weekly_blackouts_true_for_a_position_with_no_snapshots_at_all():
    blackout = _compute_weekly_blackouts({}, WEEK_STARTS_2024, [2024])
    # No weekly page types at all in snapshots - nothing to report, but a
    # caller's .get(..., False) default degrades safely rather than KeyError.
    assert blackout == {}


def test_compute_weekly_blackouts_ignores_out_of_window_snapshots():
    # A snapshot dated absurdly far outside the season window (e.g. a data
    # error, or truly unrelated) must not count as "this season has data."
    week_starts = {(2024, 1): dt.date(2024, 9, 5)}
    snapshots = {("weekly-rb", 999): [(dt.date(2020, 1, 1), 5.0)]}
    blackout = _compute_weekly_blackouts(snapshots, week_starts, [2024])
    assert blackout[("weekly-rb", 2024, 1)] is True


def test_forward_rank_features_substitutes_ros_rank_during_a_real_blackout():
    idmap = _idmap({"00-1": 555})
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {
            ("redraft-rb", 555): [(dt.date(2024, 9, 1), 80.38)],  # real ROS coverage, well before week 2
            # no weekly-rb snapshot for this player at all
        },
        "blackout": {("weekly-rb", 2024, 2): True},
    }
    out = forward_rank_features("00-1", "RB", 2024, 2, idmap, rank_index)
    assert out["ros_rank"] == 80.38
    assert out["weekly_rank"] == 80.38  # substituted


def test_forward_rank_features_leaves_a_real_individual_gap_alone_outside_blackout():
    # Same setup, but the position is NOT in a leaguewide blackout this week
    # (other players do have a real weekly rank) - this player's own missing
    # weekly rank is real signal, not a data gap, so it must stay None.
    idmap = _idmap({"00-1": 555})
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {("redraft-rb", 555): [(dt.date(2024, 9, 1), 80.38)]},
        "blackout": {("weekly-rb", 2024, 2): False},
    }
    out = forward_rank_features("00-1", "RB", 2024, 2, idmap, rank_index)
    assert out["ros_rank"] == 80.38
    assert out["weekly_rank"] is None


def test_forward_rank_features_stays_none_when_ros_rank_is_also_missing():
    idmap = _idmap({"00-1": 555})
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {},  # no coverage of any kind for this player
        "blackout": {("weekly-rb", 2024, 2): True},
    }
    out = forward_rank_features("00-1", "RB", 2024, 2, idmap, rank_index)
    assert out["ros_rank"] is None
    assert out["weekly_rank"] is None


def test_forward_rank_features_uses_the_real_weekly_rank_when_one_exists():
    # A real weekly-rb snapshot exists for this player before the cutoff -
    # used as-is, even if the position happens to be flagged blacked out
    # (shouldn't come up in practice, but confirms substitution only ever
    # fills a genuine gap, never overrides a real value).
    idmap = _idmap({"00-1": 555})
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {
            ("weekly-rb", 555): [(dt.date(2024, 9, 10), 12.0)],
            ("redraft-rb", 555): [(dt.date(2024, 9, 1), 80.38)],
        },
        "blackout": {("weekly-rb", 2024, 2): True},
    }
    out = forward_rank_features("00-1", "RB", 2024, 2, idmap, rank_index)
    assert out["weekly_rank"] == 12.0


def test_ros_ranked_candidates_returns_gsis_id_to_rank_for_qualifying_players():
    idmap = _idmap({"00-1": 555})
    rank_index = {"week_starts": WEEK_STARTS_2024, "snapshots": {("redraft-rb", 555): [(dt.date(2024, 9, 1), 80.38)]}}
    assert _ros_ranked_candidates(rank_index, idmap, "RB", 2024, 2) == {"00-1": 80.38}


def test_ros_ranked_candidates_excludes_a_snapshot_dated_after_the_cutoff():
    idmap = _idmap({"00-1": 555})
    rank_index = {"week_starts": WEEK_STARTS_2024, "snapshots": {("redraft-rb", 555): [(dt.date(2024, 9, 30), 80.38)]}}
    assert _ros_ranked_candidates(rank_index, idmap, "RB", 2024, 2) == {}


def test_ros_ranked_candidates_skips_an_fp_id_with_no_gsis_mapping():
    idmap = IdMap(by_gsis={}, by_fp={}, by_espn={}, by_name_pos={}, ambiguous_name_pos=set())
    rank_index = {"week_starts": WEEK_STARTS_2024, "snapshots": {("redraft-rb", 555): [(dt.date(2024, 9, 1), 80.38)]}}
    assert _ros_ranked_candidates(rank_index, idmap, "RB", 2024, 2) == {}


_EMPTY_STATS = pl.DataFrame(schema={"player_id": pl.Utf8, "week": pl.Int64, "position": pl.Utf8, "team": pl.Utf8})
_EMPTY_INJURIES = pl.DataFrame(schema={"gsis_id": pl.Utf8, "week": pl.Int64, "team": pl.Utf8, "position": pl.Utf8, "report_status": pl.Utf8})
_EMPTY_SNAPS = pl.DataFrame(schema={"pfr_player_id": pl.Utf8, "week": pl.Int64, "offense_pct": pl.Float64})


def test_build_no_bid_rows_widens_to_a_ros_ranked_player_with_no_stats_row_at_all():
    # A real ROS-ranked RB (rank 65, under RB's 73 ceiling) who has NO
    # stats row for week 1 at all (a bye, an inactive, hasn't debuted) and
    # was nobody's roster player that week - the exact population
    # build_no_bid_rows previously had no way to represent at all, since
    # its only candidate source was players with a real stats row (see the
    # conversation this was built from).
    idmap = IdMap(
        by_gsis={"00-9": {"gsis_id": "00-9", "fantasypros_id": 777, "espn_id": 9001, "name": "Emerging Guy", "position": "RB", "team": "SF"}},
        by_fp={777: {"gsis_id": "00-9", "fantasypros_id": 777, "espn_id": 9001, "name": "Emerging Guy", "position": "RB", "team": "SF"}},
        by_espn={}, by_name_pos={}, ambiguous_name_pos=set(),
    )
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {("redraft-rb", 777): [(dt.date(2024, 9, 1), 65.0)]},
        "blackout": {},
    }
    rows = build_no_bid_rows(
        league_id=1,
        bids=[],
        rostered_by_week={"2024": {"2": []}},  # nobody rostered him - a genuine free agent
        idmap=idmap,
        gsis_to_pfr={},
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        stats_by_season={2024: _EMPTY_STATS},
        injuries_by_season={2024: _EMPTY_INJURIES},
        snaps_by_season={2024: _EMPTY_SNAPS},
        rank_index=rank_index,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["gsis_id"] == "00-9"
    assert row["signal"] == "no_bid"
    assert row["prior_week_had_stat_row"] is False
    assert row["prior_week_actual_points"] is None
    assert row["ros_rank"] == 65.0


def test_build_no_bid_rows_does_not_widen_a_player_above_the_ros_rank_ceiling():
    idmap = IdMap(
        by_gsis={"00-9": {"gsis_id": "00-9", "fantasypros_id": 777, "espn_id": 9001, "name": "Deep Bench Guy", "position": "RB", "team": "SF"}},
        by_fp={777: {"gsis_id": "00-9", "fantasypros_id": 777, "espn_id": 9001, "name": "Deep Bench Guy", "position": "RB", "team": "SF"}},
        by_espn={}, by_name_pos={}, ambiguous_name_pos=set(),
    )
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {("redraft-rb", 777): [(dt.date(2024, 9, 1), 150.0)]},  # well above RB's 73 ceiling
        "blackout": {},
    }
    rows = build_no_bid_rows(
        league_id=1,
        bids=[],
        rostered_by_week={"2024": {"2": []}},
        idmap=idmap,
        gsis_to_pfr={},
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        stats_by_season={2024: _EMPTY_STATS},
        injuries_by_season={2024: _EMPTY_INJURIES},
        snaps_by_season={2024: _EMPTY_SNAPS},
        rank_index=rank_index,
    )
    assert rows == []
