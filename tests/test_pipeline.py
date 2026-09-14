from engine.pipeline import _faab_week_override, _positional_ranks_from_overall


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
