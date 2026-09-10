from datetime import date

import polars as pl

from ingest.nfl_data import kickoff_utc_from_schedule, week_for_date

_WEEK_SCHEDULE = pl.DataFrame(
    [
        {"season": 2025, "game_type": "REG", "week": 1, "gameday": "2025-09-04", "home_team": "KC", "away_team": "BAL"},
        {"season": 2025, "game_type": "REG", "week": 1, "gameday": "2025-09-07", "home_team": "DAL", "away_team": "NYG"},
        {"season": 2025, "game_type": "REG", "week": 2, "gameday": "2025-09-11", "home_team": "KC", "away_team": "CIN"},
        {"season": 2025, "game_type": "REG", "week": 2, "gameday": "2025-09-14", "home_team": "DAL", "away_team": "WAS"},
        {"season": 2025, "game_type": "REG", "week": 3, "gameday": "2025-09-18", "home_team": "KC", "away_team": "NYG"},
        {"season": 2025, "game_type": "REG", "week": 3, "gameday": "2025-09-21", "home_team": "DAL", "away_team": "BAL"},
    ]
)


def test_week_for_date_maps_a_date_to_the_week_it_falls_within():
    assert week_for_date(date(2025, 9, 7), _WEEK_SCHEDULE, 2025) == 1  # the Sunday of week 1
    assert week_for_date(date(2025, 9, 10), _WEEK_SCHEDULE, 2025) == 1  # between week 1's Sun and week 2's Thu
    assert week_for_date(date(2025, 9, 11), _WEEK_SCHEDULE, 2025) == 2  # week 2's own Thursday


def test_week_for_date_before_the_season_starts_is_none():
    assert week_for_date(date(2025, 8, 1), _WEEK_SCHEDULE, 2025) is None


def test_week_for_date_after_the_last_known_week_clamps_to_it():
    assert week_for_date(date(2025, 12, 1), _WEEK_SCHEDULE, 2025) == 3


def test_kickoff_converts_eastern_to_utc_for_both_teams():
    # September game (EDT, UTC-4): 13:00 ET -> 17:00 UTC.
    df = pl.DataFrame(
        [{"season": 2025, "game_type": "REG", "week": 1, "gameday": "2025-09-07", "gametime": "13:00", "home_team": "DAL", "away_team": "NYG"}]
    )
    kickoff = kickoff_utc_from_schedule(df, 2025)
    assert kickoff[("DAL", 1)] == "2025-09-07T17:00:00Z"
    assert kickoff[("NYG", 1)] == "2025-09-07T17:00:00Z"


def test_kickoff_handles_daylight_saving_transition():
    # January game (EST, UTC-5): 13:00 ET -> 18:00 UTC - a fixed +4h offset
    # would get this wrong, which is exactly why conversion goes through
    # America/New_York rather than a hardcoded UTC offset.
    df = pl.DataFrame(
        [{"season": 2025, "game_type": "REG", "week": 18, "gameday": "2026-01-04", "gametime": "13:00", "home_team": "DAL", "away_team": "NYG"}]
    )
    kickoff = kickoff_utc_from_schedule(df, 2025)
    assert kickoff[("DAL", 18)] == "2026-01-04T18:00:00Z"


def test_kickoff_ignores_other_seasons_and_game_types():
    df = pl.DataFrame(
        [
            {"season": 2025, "game_type": "POST", "week": 19, "gameday": "2026-01-10", "gametime": "13:00", "home_team": "DAL", "away_team": "NYG"},
            {"season": 2024, "game_type": "REG", "week": 1, "gameday": "2024-09-08", "gametime": "13:00", "home_team": "DAL", "away_team": "NYG"},
        ]
    )
    kickoff = kickoff_utc_from_schedule(df, 2025)
    assert kickoff == {}
