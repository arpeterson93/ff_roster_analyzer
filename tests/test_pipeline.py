import pytest

from engine.pipeline import _OFFENSE_STAT_FIELDS, _actual_weekly_stats, _faab_week_override, _positional_ranks_from_overall
from engine.scoring import ScoringRules

REY_SCORING = ScoringRules.from_espn([{"id": 42, "abbr": "REY", "points": 0.1}])


def test_positional_rank_is_derived_from_overall_order_within_each_position():
    players = [
        {"id": "wr-a", "position": "WR", "ros_overall_rank": 3, "ros_pos_rank": 99},
        {"id": "rb-a", "position": "RB", "ros_overall_rank": 5, "ros_pos_rank": 99},
        {"id": "wr-b", "position": "WR", "ros_overall_rank": 8, "ros_pos_rank": 99},
        {"id": "rb-b", "position": "RB", "ros_overall_rank": 12, "ros_pos_rank": 99},
    ]
    _positional_ranks_from_overall(players)
    by_id = {p["id"]: p["ros_pos_rank"] for p in players}
    # WR-a (overall 3) ranks ahead of WR-b (overall 8) among WRs -> WR1/WR2,
    # independently of RB-a/RB-b interleaving between them in the overall list.
    assert by_id == {"wr-a": 1, "wr-b": 2, "rb-a": 1, "rb-b": 2}


def test_a_player_missing_from_the_overall_list_keeps_their_existing_pos_rank():
    players = [
        {"id": "wr-a", "position": "WR", "ros_overall_rank": 3, "ros_pos_rank": 99},
        {"id": "wr-deep", "position": "WR", "ros_overall_rank": None, "ros_pos_rank": 47},
    ]
    _positional_ranks_from_overall(players)
    by_id = {p["id"]: p["ros_pos_rank"] for p in players}
    assert by_id["wr-a"] == 1
    assert by_id["wr-deep"] == 47  # untouched - not in FantasyPros' overall cutoff


def test_no_players_have_an_overall_rank_leaves_everything_untouched():
    players = [{"id": "wr-a", "position": "WR", "ros_overall_rank": None, "ros_pos_rank": 12}]
    _positional_ranks_from_overall(players)
    assert players[0]["ros_pos_rank"] == 12


def test_faab_week_bumps_once_current_weeks_games_have_started():
    # ESPN says week 1, and week 1's games have already started -> get an
    # early look at week 2's waiver picture.
    assert _faab_week_override(current_week=1, week_started=1) == 2


def test_faab_week_stays_put_once_espn_catches_up_but_new_week_hasnt_started():
    # ESPN just flipped to week 2 (week 1 is fully over), but week 2's own
    # games haven't started yet - must NOT double-bump to 3.
    assert _faab_week_override(current_week=2, week_started=1) == 2


def test_faab_week_bumps_again_once_the_new_current_weeks_games_start():
    assert _faab_week_override(current_week=2, week_started=2) == 3


def test_faab_week_before_the_season_has_started_at_all():
    # week_for_date returns None before the season's first game.
    assert _faab_week_override(current_week=1, week_started=None) == 1


# --- _actual_weekly_stats: a played week with zero recorded production is a
# real 0, not a missing week (see the 2026 wk2 Mike Gesicki case this was
# built from: active, zero targets, no player_stats row at all that week).

OPPONENT = {("CIN", 2): "PIT"}  # CIN played week 2; no entry at all = bye


def test_active_with_no_stat_row_counts_as_a_real_zero():
    result = _actual_weekly_stats(
        "TE", "CIN", "gesicki", 2, game_final={("CIN", 2): True},
        offense_lookup={}, dst_lookup={}, active_lookup={("gesicki", 2)},
        opponent=OPPONENT, player_rules=REY_SCORING,
    )
    assert result["points"] == 0.0
    assert result["stats"] == {**{f: 0 for f in _OFFENSE_STAT_FIELDS}, "two_pt_conversions": 0}


def test_a_real_stat_row_is_used_even_if_also_marked_active():
    row = {"receiving_yards": 55}
    result = _actual_weekly_stats(
        "TE", "CIN", "gesicki", 2, game_final={("CIN", 2): True},
        offense_lookup={("gesicki", 2): row}, dst_lookup={}, active_lookup={("gesicki", 2)},
        opponent=OPPONENT, player_rules=REY_SCORING,
    )
    assert result["points"] == pytest.approx(5.5)


def test_bye_week_stays_none_even_if_the_roster_status_says_active():
    # A player is still "ACT" on his roster during a bye - only the opponent
    # map (no game at all that week) can tell a bye apart from a real 0.
    result = _actual_weekly_stats(
        "TE", "CIN", "gesicki", 3, game_final={("CIN", 3): True},
        offense_lookup={}, dst_lookup={}, active_lookup={("gesicki", 3)},
        opponent=OPPONENT, player_rules=REY_SCORING,  # no ("CIN", 3) entry = bye
    )
    assert result is None


def test_not_active_and_no_stat_row_stays_none():
    # Genuinely inactive/practice-squad/not-on-the-team-yet - no fabricated 0.
    result = _actual_weekly_stats(
        "TE", "CIN", "gesicki", 2, game_final={("CIN", 2): True},
        offense_lookup={}, dst_lookup={}, active_lookup=set(),
        opponent=OPPONENT, player_rules=REY_SCORING,
    )
    assert result is None


def test_future_week_stays_none_regardless_of_active_status():
    result = _actual_weekly_stats(
        "TE", "CIN", "gesicki", 5, game_final={},
        offense_lookup={}, dst_lookup={}, active_lookup={("gesicki", 5)},
        opponent={("CIN", 5): "BAL"}, player_rules=REY_SCORING,
    )
    assert result is None


def test_a_teams_own_game_not_yet_final_stays_none_even_if_another_game_that_week_is():
    # The TNF bug this game_final gate exists to fix: another team's game
    # being final that week must not make THIS team's own not-yet-played game
    # look like a real, already-happened week.
    result = _actual_weekly_stats(
        "TE", "CIN", "gesicki", 2, game_final={("PIT", 2): True},  # CIN's own game-2 not final
        offense_lookup={}, dst_lookup={}, active_lookup={("gesicki", 2)},
        opponent=OPPONENT, player_rules=REY_SCORING,
    )
    assert result is None


def test_dst_path_is_unaffected_by_the_active_lookup():
    # DST never consults active_lookup/opponent at all - team_stats rows are
    # keyed straight off dst_lookup, same as before this change.
    result = _actual_weekly_stats(
        "DST", "CIN", "cin-dst", 2, game_final={("CIN", 2): True},
        offense_lookup={}, dst_lookup={("CIN", 2): {"_points": 7.0}}, active_lookup=set(),
        opponent=OPPONENT, player_rules=REY_SCORING,
    )
    assert result["points"] == 7.0
    assert result["stats"]["xpr"] is None
