from tools.nflverse_freshness import _is_fresh, _parse_timestamp


def test_parse_timestamp_edt_converts_to_utc():
    # EDT is UTC-4 - 09:46:17 EDT -> 13:46:17 UTC.
    assert _parse_timestamp("2026-09-18 09:46:17 EDT") == "2026-09-18T13:46:17Z"


def test_parse_timestamp_est_converts_to_utc():
    # EST is UTC-5 - 09:46:17 EST -> 14:46:17 UTC.
    assert _parse_timestamp("2026-01-18 09:46:17 EST") == "2026-01-18T14:46:17Z"


def test_parse_timestamp_rejects_unexpected_shapes():
    assert _parse_timestamp("") is None
    assert _parse_timestamp("not a timestamp") is None
    assert _parse_timestamp("2026-09-18 09:46:17 PDT") is None  # not an Eastern abbreviation
    assert _parse_timestamp("<html>error</html>") is None


def test_is_fresh_with_no_baseline_is_always_fresh():
    assert _is_fresh({"pbp": "2026-09-18T13:46:17Z"}, None) is True
    assert _is_fresh({}, {}) is True


def test_is_fresh_true_when_a_wait_tag_advances():
    baseline = {"pbp": "2026-09-18T04:30:00Z", "stats_player": "2026-09-18T04:30:00Z"}
    current = {"pbp": "2026-09-18T04:35:00Z", "stats_player": "2026-09-18T04:30:00Z"}
    assert _is_fresh(current, baseline) is True


def test_is_fresh_false_when_no_wait_tag_advanced():
    baseline = {"pbp": "2026-09-18T04:30:00Z", "stats_player": "2026-09-18T04:30:00Z", "snap_counts": "2026-09-18T04:30:00Z"}
    current = dict(baseline)  # unchanged
    assert _is_fresh(current, baseline) is False


def test_is_fresh_ignores_non_wait_tags():
    # schedules is fetched (part of TAGS) but not a WAIT_TAGS gate.
    baseline = {"pbp": "2026-09-18T04:30:00Z", "schedules": "2026-09-18T04:30:00Z"}
    current = {"pbp": "2026-09-18T04:30:00Z", "schedules": "2026-09-18T05:00:00Z"}
    assert _is_fresh(current, baseline) is False
