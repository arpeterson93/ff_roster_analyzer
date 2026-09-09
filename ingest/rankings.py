"""FantasyPros ecrData scraper, format-aware (standard/half/PPR), with a
DynastyProcess (nflreadpy) fallback if the scrape fails or its shape changes."""
from __future__ import annotations

import json
import logging
import re
import time

import nflreadpy as nfl
import polars as pl
import requests

logger = logging.getLogger(__name__)

_ECR_RE = re.compile(r"var ecrData = (\{.*?\});", re.S)
_USER_AGENT = "Mozilla/5.0"
_TIMEOUT = 10
_RETRIES = 3
_SLEEP_BETWEEN = 1.0

_EXPECTED_SCORING = {"standard": "STD", "half": "HALF", "ppr": "PPR"}

# Positions with no scoring-format-specific page (their rank doesn't change
# with reception scoring): no URL prefix regardless of scoring_format.
_NO_VARIANT_POS = {"qb", "k", "dst"}
_PREFIX = {"standard": "", "half": "half-point-ppr-", "ppr": "ppr-"}

_POSITIONS = ["qb", "rb", "wr", "te", "k", "dst"]

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _USER_AGENT})


class RankingsFetchError(Exception):
    pass


def _page_url(pos: str, scoring_format: str, *, ros: bool) -> str:
    prefix = "" if pos in _NO_VARIANT_POS else _PREFIX[scoring_format]
    ros_prefix = "ros-" if ros else ""
    return f"https://www.fantasypros.com/nfl/rankings/{ros_prefix}{prefix}{pos}.php"


def _fetch_ecr(url: str) -> dict:
    last_exc: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            resp = _SESSION.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            match = _ECR_RE.search(resp.text)
            if not match:
                raise RankingsFetchError(f"no ecrData block found at {url}")
            return json.loads(match.group(1))
        except Exception as exc:  # noqa: BLE001 - retry loop, re-raised below
            last_exc = exc
            if attempt < _RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RankingsFetchError(f"failed to fetch {url}: {last_exc}") from last_exc


def _rows_from_ecr(data: dict, pos: str, *, weekly: bool) -> list[dict]:
    rows = []
    for p in data.get("players", []):
        row = {
            "fp_id": p["player_id"],
            "name": p["player_name"],
            "pos": pos.upper(),
            "team": p.get("player_team_id"),
            "rank_ecr": p["rank_ecr"],
            "rank_ave": float(p["rank_ave"]) if p.get("rank_ave") not in (None, "") else None,
            "rank_std": float(p["rank_std"]) if p.get("rank_std") not in (None, "") else None,
            "rank_min": p.get("rank_min"),
            "rank_max": p.get("rank_max"),
            "bye": p.get("player_bye_week"),
            "owned_pct": p.get("player_owned_avg"),
            "pos_rank": p.get("pos_rank"),
        }
        if weekly:
            row["opponent"] = p.get("player_opponent") or p.get("pro_opponent")
            row["r2p_pts"] = p.get("r2p_pts")
        rows.append(row)
    return rows


def fetch_ros(scoring_format: str) -> pl.DataFrame:
    """Rest-of-season consensus ranks for QB/RB/WR/TE/K/DST, one row per player."""
    all_rows: list[dict] = []
    scraped_ts = int(time.time())
    for i, pos in enumerate(_POSITIONS):
        url = _page_url(pos, scoring_format, ros=True)
        data = _fetch_ecr(url)
        if pos not in _NO_VARIANT_POS:
            expected = _EXPECTED_SCORING[scoring_format]
            if data.get("scoring") != expected:
                raise RankingsFetchError(
                    f"{url} returned scoring={data.get('scoring')!r}, expected {expected!r} "
                    "(FantasyPros page shape may have changed)"
                )
        rows = _rows_from_ecr(data, pos, weekly=False)
        for row in rows:
            row["scraped_ts"] = scraped_ts
            row["experts"] = data.get("total_experts")
        all_rows.extend(rows)
        if i < len(_POSITIONS) - 1:
            time.sleep(_SLEEP_BETWEEN)
    # Each FantasyPros page is scoped to one position, so rank_ecr (the page's
    # own ranking column) is already the positional rank, 1..N.
    df = pl.DataFrame(all_rows)
    return df.with_columns(pl.col("rank_ecr").alias("ros_pos_rank"))


def fetch_ros_overall(scoring_format: str) -> pl.DataFrame:
    """Cross-position ROS overall rank (FantasyPros' own combined consensus,
    same ros-overall.php page real fantasy sites use) - not our own
    points-based ranking, which skews QB-heavy in standard scoring since it
    ignores positional scarcity the way a real "overall" ranking wouldn't."""
    url = f"https://www.fantasypros.com/nfl/rankings/ros-{_PREFIX[scoring_format]}overall.php"
    data = _fetch_ecr(url)
    expected = _EXPECTED_SCORING[scoring_format]
    if data.get("scoring") != expected:
        raise RankingsFetchError(
            f"{url} returned scoring={data.get('scoring')!r}, expected {expected!r} "
            "(FantasyPros page shape may have changed)"
        )
    rows = [
        {
            "fp_id": p["player_id"],
            "name": p["player_name"],
            "pos": p.get("player_position_id"),
            "team": p.get("player_team_id"),
            "overall_rank": p["rank_ecr"],
        }
        for p in data.get("players", [])
    ]
    return pl.DataFrame(rows)


def fetch_weekly(scoring_format: str) -> pl.DataFrame:
    """Current-week consensus ranks, same shape as fetch_ros plus opponent/r2p_pts."""
    all_rows: list[dict] = []
    for i, pos in enumerate(_POSITIONS):
        url = _page_url(pos, scoring_format, ros=False)
        data = _fetch_ecr(url)
        rows = _rows_from_ecr(data, pos, weekly=True)
        for row in rows:
            row["week"] = data.get("week")
        all_rows.extend(rows)
        if i < len(_POSITIONS) - 1:
            time.sleep(_SLEEP_BETWEEN)
    df = pl.DataFrame(all_rows)
    return df.with_columns(pl.col("rank_ecr").alias("week_pos_rank"))


_DP_POS_MAP = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "DST": "dst"}


def fetch_ros_fallback() -> pl.DataFrame:
    """DynastyProcess/nflreadpy PPR-based fallback when the FantasyPros scrape fails."""
    logger.warning("FantasyPros ROS scrape failed; falling back to nflreadpy.load_ff_rankings (PPR-based)")
    draft = nfl.load_ff_rankings("draft")
    rows = []
    for pos_upper, pos_key in _DP_POS_MAP.items():
        page_type = f"redraft-{pos_key}"
        sub = draft.filter(pl.col("page_type") == page_type).sort("ecr")
        for rank, row in enumerate(sub.iter_rows(named=True), start=1):
            rows.append(
                {
                    "fp_id": row["id"],
                    "name": row["player"],
                    "pos": pos_upper,
                    "team": row.get("team"),
                    "rank_ecr": rank,
                    "ros_pos_rank": rank,
                    "rank_ave": row.get("ecr"),
                    "rank_std": row.get("sd"),
                    "bye": row.get("bye"),
                    "owned_pct": row.get("player_owned_avg"),
                    "pos_rank": f"{pos_upper}{rank}",
                    "scraped_ts": None,
                    "experts": None,
                }
            )
    return pl.DataFrame(rows)


def fetch_weekly_fallback() -> pl.DataFrame:
    logger.warning("FantasyPros weekly scrape failed; falling back to nflreadpy.load_ff_rankings (PPR-based)")
    weekly = nfl.load_ff_rankings("week")
    rows = []
    for pos_upper, pos_key in _DP_POS_MAP.items():
        sub = weekly.filter(pl.col("page_pos") == pos_upper).sort("ecr")
        for rank, row in enumerate(sub.iter_rows(named=True), start=1):
            rows.append(
                {
                    "fp_id": row["fantasypros_id"],
                    "name": row["player_name"],
                    "pos": pos_upper,
                    "team": row.get("team"),
                    "rank_ecr": rank,
                    "week_pos_rank": rank,
                    "opponent": row.get("player_opponent"),
                    "r2p_pts": row.get("r2p_pts"),
                    "pos_rank": row.get("pos_rank") or f"{pos_upper}{rank}",
                }
            )
    return pl.DataFrame(rows)
