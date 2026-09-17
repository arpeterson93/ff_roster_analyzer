from datetime import date, datetime, timezone

import polars as pl

from ingest.nfl_data import game_context_from_schedule, kickoff_utc_from_schedule, week_for_date, week_for_kickoff

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


_KICKOFF_WEEK_SCHEDULE = pl.DataFrame(
    [
        {"season": 2025, "game_type": "REG", "week": 1, "gameday": "2025-09-04", "gametime": "20:20", "home_team": "KC", "away_team": "BAL"},
        {"season": 2025, "game_type": "REG", "week": 2, "gameday": "2025-09-11", "gametime": "20:15", "home_team": "KC", "away_team": "CIN"},
    ]
)


def test_week_for_kickoff_stays_on_the_prior_week_until_real_kickoff():
    # Week 2's calendar date has arrived, but its 20:15 ET kickoff hasn't -
    # this is exactly the gap week_for_date can't see (it would already
    # return week 2 off the calendar date alone). 20:00 ET = 00:00 UTC the
    # next day (EDT, UTC-4).
    before_kickoff = datetime(2025, 9, 12, 0, 0, tzinfo=timezone.utc)
    assert week_for_kickoff(before_kickoff, _KICKOFF_WEEK_SCHEDULE, 2025) == 1


def test_week_for_kickoff_advances_once_real_kickoff_passes():
    # 20:15 ET = 00:15 UTC the next day (EDT, UTC-4).
    after_kickoff = datetime(2025, 9, 12, 0, 16, tzinfo=timezone.utc)
    assert week_for_kickoff(after_kickoff, _KICKOFF_WEEK_SCHEDULE, 2025) == 2


def test_week_for_kickoff_before_the_season_starts_is_none():
    assert week_for_kickoff(datetime(2025, 8, 1, tzinfo=timezone.utc), _KICKOFF_WEEK_SCHEDULE, 2025) is None


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


def _schedule_row(**overrides):
    row = {
        "season": 2026, "game_type": "REG", "week": 1, "home_team": "CIN", "away_team": "TB",
        "location": "Home", "spread_line": 3.5, "total_line": 50.5, "roof": "outdoors",
        "stadium_id": "CIN00", "stadium": "Paycor Stadium",
    }
    row.update(overrides)
    return row


def test_game_context_implied_totals_match_espn_verified_sign_convention():
    # nflverse's spread_line is POSITIVE when the HOME team is favored - the
    # opposite of a bettor-facing "team -3.5" line - live-verified 2026-09-13
    # against ESPN's own odds for this exact game (Bengals -3.5 at home,
    # O/U 50.5 -> Bengals implied 27.0, Buccaneers implied 23.5).
    df = pl.DataFrame([_schedule_row()])
    ctx = game_context_from_schedule(df, 2026)
    assert ctx[("CIN", 1)]["implied_total"] == 27.0
    assert ctx[("CIN", 1)]["opponent_implied_total"] == 23.5
    assert ctx[("TB", 1)]["implied_total"] == 23.5
    assert ctx[("TB", 1)]["opponent_implied_total"] == 27.0


def test_game_context_no_line_posted_yet_is_none_not_zero():
    df = pl.DataFrame([_schedule_row(spread_line=None, total_line=None)])
    ctx = game_context_from_schedule(df, 2026)
    assert ctx[("CIN", 1)]["implied_total"] is None
    assert ctx[("TB", 1)]["opponent_implied_total"] is None


def test_game_context_neutral_site_and_venue_reflect_the_actual_game_not_either_teams_normal_home():
    # The 2026 Melbourne game: LA is nominally "home" for seeding purposes,
    # but the real venue and neutral-site flag must come from this row, not
    # from either team's usual stadium.
    df = pl.DataFrame([_schedule_row(
        home_team="LA", away_team="SF", location="Neutral", roof=None,
        stadium_id="MEL00", stadium="Melbourne Cricket Ground",
    )])
    ctx = game_context_from_schedule(df, 2026)
    assert ctx[("LA", 1)]["neutral_site"] is True
    assert ctx[("SF", 1)]["neutral_site"] is True
    assert ctx[("LA", 1)]["stadium_id"] == "MEL00"
    assert ctx[("SF", 1)]["stadium"] == "Melbourne Cricket Ground"


def test_game_context_ignores_other_seasons_and_game_types():
    df = pl.DataFrame([_schedule_row(game_type="POST"), _schedule_row(season=2025)])
    assert game_context_from_schedule(df, 2026) == {}
