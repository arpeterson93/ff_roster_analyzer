from engine.pipeline import _positional_ranks_from_overall


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
