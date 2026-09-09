import polars as pl
import pytest

from engine.points_against import dst_points_against_detail, points_against_detail, stat_columns
from engine.scoring import ScoringRules

REY_SCORING = ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 0.1}, {"id": 43, "abbr": "RETD", "points": 6}])


def _row(player_id, name, week, team, opp, yards, tds=0, targets=None, receptions=None):
    return {
        "season": 2025, "season_type": "REG", "position": "WR", "player_id": player_id,
        "player_display_name": name, "team": team, "opponent_team": opp, "week": week,
        "receiving_yards": yards, "receiving_tds": tds,
        "targets": targets, "receptions": receptions,
    }


def test_no_filtering_every_player_included():
    rows = [
        _row("p1", "Star", 1, "DAL", "NYG", 150, 2),
        _row("p2", "Scrub", 1, "DAL", "NYG", 5, 0),
    ]
    df = pl.DataFrame(rows)
    result = points_against_detail(df, 2025, "WR", REY_SCORING)
    names = {e["name"] for e in result["NYG"][1]["players"]}
    assert names == {"Star", "Scrub"}


def test_team_totals_aggregate_across_players():
    rows = [
        _row("p1", "A", 1, "DAL", "NYG", 100, 1, targets=8, receptions=6),
        _row("p2", "B", 1, "DAL", "NYG", 50, 0, targets=4, receptions=3),
    ]
    df = pl.DataFrame(rows)
    result = points_against_detail(df, 2025, "WR", REY_SCORING)
    week1 = result["NYG"][1]
    assert week1["points"] == pytest.approx((100 * 0.1 + 6) + (50 * 0.1))
    assert week1["stats"]["receiving_yards"] == 150
    assert week1["stats"]["targets"] == 12
    assert week1["stats"]["receptions"] == 9


def test_opponent_and_home_recorded_from_the_defenses_perspective():
    rows = [_row("p1", "A", 1, "DAL", "NYG", 100)]
    df = pl.DataFrame(rows)
    is_home = {("NYG", 1): False}  # NYG (the defense) played on the road
    result = points_against_detail(df, 2025, "WR", REY_SCORING, is_home)
    week1 = result["NYG"][1]
    assert week1["opponent"] == "DAL"  # the offense NYG's defense faced
    assert week1["home"] is False


def test_players_sorted_by_points_descending():
    rows = [
        _row("p1", "Low", 1, "SF", "NYG", 10, 0),
        _row("p2", "High", 1, "SF", "NYG", 100, 1),
    ]
    df = pl.DataFrame(rows)
    result = points_against_detail(df, 2025, "WR", REY_SCORING)
    names = [e["name"] for e in result["NYG"][1]["players"]]
    assert names == ["High", "Low"]


def test_qb_block_order_includes_receiving_but_leads_with_passing():
    columns = [c["key"] for c in stat_columns("QB")]
    assert columns.index("passing_yards") < columns.index("rushing_yards") < columns.index("receiving_yards")


def test_rb_block_order_leads_with_rushing():
    columns = [c["key"] for c in stat_columns("RB")]
    assert columns.index("rushing_yards") < columns.index("receiving_yards") < columns.index("passing_yards")


def test_two_pt_conversions_summed_across_types():
    rows = [{
        "season": 2025, "season_type": "REG", "position": "QB", "player_id": "q1", "player_display_name": "QB1",
        "team": "SEA", "opponent_team": "DAL", "week": 1,
        "passing_2pt_conversions": 1, "rushing_2pt_conversions": 1, "receiving_2pt_conversions": 0,
    }]
    df = pl.DataFrame(rows)
    scoring = ScoringRules.from_espn([{"id": 20, "abbr": "2PC", "points": 2}])
    result = points_against_detail(df, 2025, "QB", scoring)
    assert result["DAL"][1]["stats"]["two_pt_conversions"] == 2


def test_kicker_fields_are_distance_brackets():
    rows = [{
        "season": 2025, "season_type": "REG", "position": "K", "player_id": "k1", "player_display_name": "K1",
        "team": "SEA", "opponent_team": "DAL", "week": 1,
        "fg_made_0_19": 1, "fg_made_50_59": 1, "pat_made": 3,
    }]
    df = pl.DataFrame(rows)
    scoring = ScoringRules.from_espn([{"id": 60, "abbr": "FG0", "points": 3}, {"id": 61, "abbr": "PAT", "points": 1}])
    result = points_against_detail(df, 2025, "K", scoring)
    stats = result["DAL"][1]["stats"]
    assert stats["fg_made_0_19"] == 1
    assert stats["fg_made_50_59"] == 1
    assert stats["pat_made"] == 3
    assert "fg_made" not in stats


def test_dst_points_against_detail_keyed_by_offense_faced():
    schedules_df = pl.DataFrame(
        [{"season": 2025, "game_type": "REG", "game_id": "G1", "home_team": "A", "away_team": "B", "home_score": 10, "away_score": 20}]
    )
    team_stats_df = pl.DataFrame(
        [
            {
                "season": 2025, "season_type": "REG", "game_id": "G1", "week": 1, "team": "A", "opponent_team": "B",
                "def_sacks": 2, "def_interceptions": 1, "def_safeties": 0, "fumble_recovery_opp": 0, "def_tds": 0,
                "def_punt_blocks": 0, "def_pat_blocks": 0, "def_fg_blocks": 0, "special_teams_tds": 0,
                "passing_yards": 15, "rushing_yards": 5,
            },
            {
                "season": 2025, "season_type": "REG", "game_id": "G1", "week": 1, "team": "B", "opponent_team": "A",
                "def_sacks": 0, "def_interceptions": 0, "def_safeties": 0, "fumble_recovery_opp": 0, "def_tds": 0,
                "def_punt_blocks": 0, "def_pat_blocks": 0, "def_fg_blocks": 0, "special_teams_tds": 0,
                "passing_yards": 200, "rushing_yards": 100,
            },
        ]
    )
    dst_scoring = ScoringRules.from_espn(
        [{"id": 99, "abbr": "SK", "points": 1}, {"id": 95, "abbr": "INT", "points": 2}], is_dst=True
    )
    is_home = {("B", 1): False}
    result = dst_points_against_detail(team_stats_df, schedules_df, is_home, 2025, dst_scoring)
    # Team A's defense (2 sacks, 1 INT) played against B's offense - so B's
    # vulnerability is recorded under B, with A named as the opponent faced.
    assert result["B"][1]["points"] == pytest.approx(4.0)
    assert result["B"][1]["opponent"] == "A"
    assert result["B"][1]["home"] is False
    assert result["B"][1]["stats"]["xpr"] is None  # not available from nflverse


def test_dst_position_specific_fields_used():
    columns = [c["key"] for c in stat_columns("DST")]
    assert "points_allowed" in columns
    assert "def_sacks" in columns
    assert "receiving_yards" not in columns
