import pytest

from engine.scoring import ScoringRules

# Subset of the verified 355398 ("O" League) scoring_format (Fable's Part 0.2).
O_LEAGUE_ITEMS = [
    {"id": 3, "abbr": "PY", "points": 0.05},
    {"id": 4, "abbr": "PTD", "points": 4},
    {"id": 20, "abbr": "INTT", "points": -1},
    {"id": 24, "abbr": "RY", "points": 0.1},
    {"id": 25, "abbr": "RTD", "points": 6},
    {"id": 42, "abbr": "REY", "points": 0.1},
    {"id": 43, "abbr": "RETD", "points": 6},
    {"id": 72, "abbr": "FUML", "points": -1},
    {"id": 86, "abbr": "PAT", "points": 1},
    {"id": 77, "abbr": "FG40", "points": 3},
    {"id": 80, "abbr": "FG0", "points": 3},
    {"id": 198, "abbr": "FG50", "points": 4},
    {"id": 201, "abbr": "FG60", "points": 5},
]


def test_qb_stat_line():
    rules = ScoringRules.from_espn(O_LEAGUE_ITEMS)
    row = {"passing_yards": 300, "passing_tds": 3, "passing_interceptions": 1, "rushing_yards": 20}
    # 300*0.05 + 3*4 + 1*(-1) + 20*0.1 = 15 + 12 - 1 + 2 = 28
    assert rules.points_for_row(row) == pytest.approx(28.0)


def test_wr_with_fumble():
    rules = ScoringRules.from_espn(O_LEAGUE_ITEMS)
    row = {"receiving_yards": 80, "receiving_tds": 1, "receptions": 5, "fumbles_lost_total": 1}
    # 80*0.1 + 1*6 + 1*(-1) = 8 + 6 - 1 = 13; REC not scored (standard league)
    assert rules.points_for_row(row) == pytest.approx(13.0)


def test_kicker_52_yard_fg():
    rules = ScoringRules.from_espn(O_LEAGUE_ITEMS)
    row = {"fg_made_50_59": 1, "pat_made": 2}
    # 1*4 (FG50 bracket) + 2*1 (PAT) = 6
    assert rules.points_for_row(row) == pytest.approx(6.0)


def test_half_ppr_variant():
    half_ppr_items = O_LEAGUE_ITEMS + [{"id": 53, "abbr": "REC", "points": 0.5}]
    rules = ScoringRules.from_espn(half_ppr_items)
    row = {"receiving_yards": 80, "receiving_tds": 1, "receptions": 5, "fumbles_lost_total": 1}
    # 13 (as above) + 5*0.5 = 15.5
    assert rules.points_for_row(row) == pytest.approx(15.5)


def test_unmapped_nonzero_item_fails_loudly():
    with pytest.raises(ValueError, match="TOTALLY_MADE_UP"):
        ScoringRules.from_espn([{"id": 999, "abbr": "TOTALLY_MADE_UP", "points": 1}])


def test_zero_point_items_never_raise():
    ScoringRules.from_espn([{"id": 999, "abbr": "TOTALLY_MADE_UP", "points": 0}])


def test_krtd_prtd_collapse_avoids_double_counting():
    items = [{"id": 101, "abbr": "KRTD", "points": 6}, {"id": 102, "abbr": "PRTD", "points": 6}]
    rules = ScoringRules.from_espn(items)
    row = {"special_teams_tds": 1}
    assert rules.points_for_row(row) == pytest.approx(6.0)


DST_ITEMS = [
    {"id": 99, "abbr": "SK", "points": 1},
    {"id": 95, "abbr": "INT", "points": 2},
    {"id": 96, "abbr": "FR", "points": 2},
    {"id": 94, "abbr": "DEFRETTD", "points": 6},
    {"id": 98, "abbr": "SF", "points": 2},
    {"id": 91, "abbr": "PA7", "points": 4},
    {"id": 130, "abbr": "YA299", "points": 3},
]


def test_dst_stat_line():
    rules = ScoringRules.from_espn(DST_ITEMS, is_dst=True)
    team_row = {"def_sacks": 3, "def_interceptions": 1, "fumble_recovery_opp": 1, "def_tds": 1, "def_safeties": 0}
    # 3*1 + 1*2 + 1*2 + 1*6 = 13, plus points_allowed=10 -> PA7 bracket (4), yards_allowed=250 -> YA299 (3)
    assert rules.dst_points_for_row(team_row, points_allowed=10, yards_allowed=250) == pytest.approx(20.0)


def test_dst_points_for_row_rejects_player_rules():
    rules = ScoringRules.from_espn(O_LEAGUE_ITEMS)
    with pytest.raises(TypeError):
        rules.dst_points_for_row({}, 0, 0)


def test_mixed_offense_and_dst_items_split_by_domain():
    # A real ESPN league's scoring_format is one combined list covering both
    # offense/kicker and D/ST items; each rule set should silently ignore the
    # other's items rather than treating them as unmapped.
    mixed_items = O_LEAGUE_ITEMS + DST_ITEMS
    player_rules = ScoringRules.from_espn(mixed_items)
    dst_rules = ScoringRules.from_espn(mixed_items, is_dst=True)

    row = {"receiving_yards": 80, "receiving_tds": 1, "receptions": 5, "fumbles_lost_total": 1}
    assert player_rules.points_for_row(row) == pytest.approx(13.0)

    team_row = {"def_sacks": 3, "def_interceptions": 1, "fumble_recovery_opp": 1, "def_tds": 1, "def_safeties": 0}
    assert dst_rules.dst_points_for_row(team_row, points_allowed=10, yards_allowed=250) == pytest.approx(20.0)


def test_points_for_row_rejects_dst_rules():
    rules = ScoringRules.from_espn(DST_ITEMS, is_dst=True)
    with pytest.raises(TypeError):
        rules.points_for_row({})


def test_blkkrtd_folds_into_def_tds():
    rules = ScoringRules.from_espn([{"id": 93, "abbr": "BLKKRTD", "points": 6}], is_dst=True)
    assert rules.dst_points_for_row({"def_tds": 1}, points_allowed=0, yards_allowed=0) == pytest.approx(6.0)


def test_uncomputable_dst_stats_always_score_zero(caplog):
    rules = ScoringRules.from_espn(
        [{"id": 206, "abbr": "2PRET", "points": 2}, {"id": 209, "abbr": "1PSF", "points": 1}], is_dst=True
    )
    assert rules.dst_points_for_row({}, points_allowed=0, yards_allowed=0) == pytest.approx(0.0)
    assert "2PRET" in caplog.text
    assert "1PSF" in caplog.text
