import datetime as dt

import polars as pl

from engine.scoring import ScoringRules
from ingest.ids import IdMap
from tools.faab_history.build_training_table import (
    _compute_weekly_blackouts,
    _ros_ranked_candidates,
    build_no_bid_rows,
    build_season_index,
    enrich_player_week,
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


_EMPTY_STATS = pl.DataFrame(
    schema={"player_id": pl.Utf8, "week": pl.Int64, "position": pl.Utf8, "team": pl.Utf8, "carries": pl.Int64, "targets": pl.Int64, "target_share": pl.Float64}
)
_EMPTY_INJURIES = pl.DataFrame(schema={"gsis_id": pl.Utf8, "week": pl.Int64, "team": pl.Utf8, "position": pl.Utf8, "report_status": pl.Utf8})
_EMPTY_SNAPS = pl.DataFrame(schema={"pfr_player_id": pl.Utf8, "week": pl.Int64, "offense_pct": pl.Float64})
_EMPTY_ROSTERS_WEEKLY = pl.DataFrame(schema={"gsis_id": pl.Utf8, "week": pl.Int64, "team": pl.Utf8, "position": pl.Utf8, "status": pl.Utf8})
_EMPTY_SEASON_INDEX = build_season_index(_EMPTY_STATS, _EMPTY_INJURIES, _EMPTY_SNAPS, _EMPTY_ROSTERS_WEEKLY)


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
        # A single unrelated real bid, just to satisfy the Tier 1 league-
        # season validity gate (see build_no_bid_rows' own docstring) -
        # without ANY real transaction that season, no no_bid rows get
        # generated at all, regardless of this test's actual subject.
        bids=[{"season": 2024, "week": 2, "add_player_id": -1}],
        rostered_by_week={"2024": {"2": []}},  # nobody rostered him - a genuine free agent
        idmap=idmap,
        gsis_to_pfr={},
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        season_index_by_season={2024: _EMPTY_SEASON_INDEX},
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
        # See the Tier 1 gate comment in the test above - without this,
        # rows == [] here for the wrong reason (no season activity at all),
        # not because this player's rank is actually above the ceiling.
        bids=[{"season": 2024, "week": 2, "add_player_id": -1}],
        rostered_by_week={"2024": {"2": []}},
        idmap=idmap,
        gsis_to_pfr={},
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        season_index_by_season={2024: _EMPTY_SEASON_INDEX},
        rank_index=rank_index,
    )
    assert rows == []


def test_enrich_player_week_snap_pct_falls_back_to_two_weeks_prior():
    # A real bye/inactive week 4 (week-1 relative to a week-5 lookup) with
    # real snap data sitting right there two weeks back (week 3) - must fall
    # back to it, matching engine.faab_estimate.recent_snap_pct's own
    # fallback (the LIVE query's own snap_pct_prior_week uses that exact
    # function) - an earlier version here tracked week-1/week-2 as two
    # separate, non-falling-back fields, which read this real situation as
    # "no data" purely because the immediate prior week happened to be
    # empty, a genuine train/predict mismatch against the live path.
    snaps = pl.DataFrame({"pfr_player_id": ["p1"], "week": [3], "offense_pct": [0.55]})
    season_index = build_season_index(_EMPTY_STATS, _EMPTY_INJURIES, snaps, _EMPTY_ROSTERS_WEEKLY)
    enriched = enrich_player_week(
        gsis_id="g1", position="RB", season=2024, week=5,
        season_index=season_index,
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        idmap=_idmap({}), gsis_to_pfr={"g1": "p1"}, rank_index={"snapshots": {}, "week_starts": {}, "blackout": {}},
    )
    assert enriched["snap_pct_prior_week"] == 0.55


def test_enrich_player_week_flags_a_teammate_on_reserve_status_with_no_injury_report_row():
    # A real, live case (Isiah Pacheco, KC, 2024): a real ~10-week IR stint
    # starting week 3 had ZERO load_injuries (practice-report) rows for 8 of
    # those weeks - the report only tracks day-to-day game-status
    # uncertainty, not "already on reserve, not practicing at all." His REAL
    # roster status (load_rosters_weekly) showed RES the whole time. Without
    # folding that second source in, a backup (Carson Steele here) sitting
    # behind him would never get teammate_position_injury_flag even though
    # the real-world crowding-out situation the flag exists to catch was
    # exactly what was happening. injuries_df is left EMPTY here on purpose -
    # simulating the exact gap this fix closes.
    stats = pl.DataFrame({"player_id": ["backup"], "week": [3], "position": ["RB"], "team": ["KC"], "carries": [10], "targets": [1], "target_share": [0.1]})
    rosters_weekly = pl.DataFrame({"gsis_id": ["starter"], "week": [4], "team": ["KC"], "position": ["RB"], "status": ["RES"]})
    snaps = pl.DataFrame({"pfr_player_id": ["starter_pfr"], "week": [2], "offense_pct": [0.65]})
    season_index = build_season_index(stats, _EMPTY_INJURIES, snaps, rosters_weekly)

    enriched = enrich_player_week(
        gsis_id="backup", position="RB", season=2024, week=4,
        season_index=season_index,
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        idmap=_idmap({}), gsis_to_pfr={"starter": "starter_pfr"},
        rank_index={"snapshots": {}, "week_starts": {}, "blackout": {}},
    )
    assert enriched["teammate_position_injury_flag"] is True
    # He wasn't flagged (by either source) the week before either - week 4
    # is the first week his absence shows up at all, so this reads as a
    # fresh, newly-relevant opportunity for the backup.
    assert enriched["teammate_position_injury_is_new"] is True


def test_enrich_player_week_does_not_flag_a_merely_questionable_own_status():
    # own_injury_status should only reflect near-certain absences (the same
    # Out/Doubtful/RESERVE tier as the teammate flag), not the weekly
    # practice-report's Questionable tag - most Questionable-tagged players
    # still suit up, so it shouldn't read as "injured" for the bid target's
    # own feature any more than it does for a teammate's.
    injuries = pl.DataFrame({"gsis_id": ["g1"], "week": [5], "team": ["KC"], "position": ["RB"], "report_status": ["Questionable"]})
    season_index = build_season_index(_EMPTY_STATS, injuries, _EMPTY_SNAPS, _EMPTY_ROSTERS_WEEKLY)
    enriched = enrich_player_week(
        gsis_id="g1", position="RB", season=2024, week=5,
        season_index=season_index,
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        idmap=_idmap({}), gsis_to_pfr={}, rank_index={"snapshots": {}, "week_starts": {}, "blackout": {}},
    )
    assert enriched["own_injury_status"] is None


def test_enrich_player_week_does_not_flag_an_ongoing_injury_as_new():
    # Same shape as the test above, but the starter was ALSO on reserve the
    # week before - a real, already-known, ongoing absence by the time this
    # week's bid happens, not a fresh one. Anecdotally, FAAB bids on the
    # backup spike hardest the very first week and cool off once the market
    # has had a week to price him in - see the conversation this was built
    # from.
    stats = pl.DataFrame({"player_id": ["backup"], "week": [3], "position": ["RB"], "team": ["KC"], "carries": [10], "targets": [1], "target_share": [0.1]})
    rosters_weekly = pl.DataFrame(
        {"gsis_id": ["starter", "starter"], "week": [3, 4], "team": ["KC", "KC"], "position": ["RB", "RB"], "status": ["RES", "RES"]}
    )
    snaps = pl.DataFrame({"pfr_player_id": ["starter_pfr"], "week": [2], "offense_pct": [0.65]})
    season_index = build_season_index(stats, _EMPTY_INJURIES, snaps, rosters_weekly)

    enriched = enrich_player_week(
        gsis_id="backup", position="RB", season=2024, week=4,
        season_index=season_index,
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        idmap=_idmap({}), gsis_to_pfr={"starter": "starter_pfr"},
        rank_index={"snapshots": {}, "week_starts": {}, "blackout": {}},
    )
    assert enriched["teammate_position_injury_flag"] is True
    assert enriched["teammate_position_injury_is_new"] is False


def test_enrich_player_week_rank_rescues_a_teammate_with_no_recent_snap_data():
    # A real, well-ranked starter can go down with no recent snap data of
    # his own recorded yet - his own real ROS rank should still be enough
    # to count him as fantasy relevant for the crowding-out flag, same as
    # is_faab_relevant already rank-rescues the bid TARGET'S OWN row (see
    # the conversation this was built from).
    stats = pl.DataFrame({"player_id": ["backup"], "week": [3], "position": ["RB"], "team": ["KC"], "carries": [10], "targets": [1], "target_share": [0.1]})
    injuries = pl.DataFrame({"gsis_id": ["starter"], "week": [4], "team": ["KC"], "position": ["RB"], "report_status": ["Out"]})
    rank_index = {
        "week_starts": WEEK_STARTS_2024,
        "snapshots": {("redraft-rb", 777): [(dt.date(2024, 9, 1), 50.0)]},  # well under RB's 73 ceiling
        "blackout": {},
    }
    season_index = build_season_index(stats, injuries, _EMPTY_SNAPS, _EMPTY_ROSTERS_WEEKLY)

    enriched = enrich_player_week(
        gsis_id="backup", position="RB", season=2024, week=4,
        season_index=season_index,
        scoring=ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 1}]),
        idmap=_idmap({"starter": 777}), gsis_to_pfr={},  # no pfr crosswalk at all - snap_pct lookup will be None
        rank_index=rank_index,
    )
    assert enriched["teammate_position_injury_flag"] is True
