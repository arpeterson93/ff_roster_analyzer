import polars as pl

from ingest.nfl_data import kickoff_utc_from_schedule


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
