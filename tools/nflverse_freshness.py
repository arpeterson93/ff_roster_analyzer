"""Freshness check against nflverse-data's own per-dataset release
timestamps - lets the CI "refresh" stage (see
update-scripts-implementation-plan.md Part 0.2/2.7) wait for genuinely NEW
post-game data instead of firing on a fixed clock offset that might land
before nflverse has actually published, or waste a build re-running on data
it already has. Each nflverse-data GitHub release carries a `timestamp.txt`
asset (format "2026-09-18 09:46:17 EDT"), fetchable without auth or rate
limits."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

RELEASE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"

# The datasets the pipeline actually reads (see ingest/nfl_data.py) - one
# release tag per dataset.
TAGS = ["pbp", "stats_player", "stats_team", "snap_counts", "schedules", "injuries", "weekly_rosters"]

# Which of the above actually gates a post-game "refresh" run: pbp/
# stats_player/snap_counts publish tied to a specific game night (Part 0.2 -
# schedules/injuries/weekly_rosters publish on their own daily cadence, not
# worth waiting on here).
WAIT_TAGS = ["pbp", "stats_player", "snap_counts"]

# nflverse's timestamp.txt always prints US Eastern local time with its own
# abbreviation - America/New_York resolves EDT/EST from the wall-clock value
# itself (DST-correct), so both abbreviations map to the same zoneinfo key.
_EASTERN = ZoneInfo("America/New_York")
_TZ_ABBREVS = {"EDT", "EST"}


def _parse_timestamp(raw: str) -> str | None:
    """'2026-09-18 09:46:17 EDT' -> ISO-8601 UTC ('...Z'). None if the text
    doesn't match the expected shape (a truncated/HTML error response, say)."""
    parts = raw.strip().rsplit(" ", 1)
    if len(parts) != 2 or parts[1] not in _TZ_ABBREVS:
        return None
    try:
        naive = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    localized = naive.replace(tzinfo=_EASTERN)
    return localized.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_timestamp(tag: str, *, timeout: float = 10.0) -> str | None:
    try:
        resp = requests.get(f"{RELEASE_BASE}/{tag}/timestamp.txt", timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return _parse_timestamp(resp.text)


def read_timestamps(tags: list[str] = TAGS) -> dict[str, str]:
    """{tag: iso_utc} for every tag that fetched and parsed successfully - a
    tag that failed is simply omitted, not a hard error (same degrade-
    gracefully convention as ingest/nfl_data.py's own nflverse fetches)."""
    out: dict[str, str] = {}
    for tag in tags:
        ts = _fetch_timestamp(tag)
        if ts is not None:
            out[tag] = ts
    return out


def _is_fresh(current: dict[str, str], baseline: dict[str, str] | None) -> bool:
    """True if any WAIT_TAGS timestamp in `current` is strictly newer than
    the same tag in `baseline` - or unconditionally true if there's no
    baseline at all (first run, or a sources.json this run couldn't read),
    since there's nothing to compare against and a refresh should just go."""
    if not baseline:
        return True
    for tag in WAIT_TAGS:
        cur = current.get(tag)
        prev = baseline.get(tag)
        if cur is not None and (prev is None or cur > prev):
            return True
    return False


def wait_for_fresh(sources_path: Path, *, wait_minutes: int, poll_seconds: int = 300) -> bool:
    """Poll nflverse's release timestamps every `poll_seconds` until one of
    WAIT_TAGS is newer than `sources_path`'s last-recorded value, up to
    `wait_minutes`. Returns False on timeout - the caller (the CI "refresh"
    stage's gate job) skips the actual build unless `force`, since "no new
    data yet" is expected on a night with no game, not a failure."""
    baseline = None
    if sources_path.exists():
        try:
            baseline = json.loads(sources_path.read_text(encoding="utf-8")).get("nflverse")
        except (json.JSONDecodeError, OSError):
            baseline = None

    deadline = time.monotonic() + wait_minutes * 60
    while True:
        current = read_timestamps()
        if _is_fresh(current, baseline):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_seconds)


def _write_github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", default="docs/data/sources.json")
    parser.add_argument("--wait-minutes", type=int, default=60)
    parser.add_argument("--poll-seconds", type=int, default=300)
    args = parser.parse_args()

    fresh = wait_for_fresh(Path(args.sources), wait_minutes=args.wait_minutes, poll_seconds=args.poll_seconds)
    should_run = "true" if fresh else "false"
    print(f"should_run={should_run}")
    _write_github_output("should_run", should_run)
    return 0  # "no new data yet" is not a failure - see wait_for_fresh's docstring


if __name__ == "__main__":
    sys.exit(main())
