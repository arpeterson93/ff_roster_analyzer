from datetime import date

from ingest.espn_injuries import _parse_return_date


def test_parse_return_date_within_the_season_year():
    assert _parse_return_date("Oct 11", 2026) == date(2026, 10, 11)


def test_parse_return_date_early_calendar_months_roll_into_next_year():
    # A season's playoffs/offseason run into the following January/February.
    assert _parse_return_date("Feb 15", 2026) == date(2027, 2, 15)


def test_parse_return_date_handles_leading_trailing_whitespace():
    assert _parse_return_date("  Oct 11  ", 2026) == date(2026, 10, 11)


def test_parse_return_date_unparseable_string_returns_none():
    assert _parse_return_date("Week to week", 2026) is None
