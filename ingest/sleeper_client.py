"""Sleeper adapter for an OPT-IN alternative projections source (see
ingest/settings_sheet.py's "projection_source" key). ESPN's own weekly
number is still this app's primary/default source (see ingest/espn_client.py
and engine/valuation.py's module docstring) - this module only ever runs for
a league that has explicitly flipped projection_source to "sleeper".

Sleeper's public API (api.sleeper.app, no auth required) exposes weekly
projections as RAW per-stat fields (pass_yd, rush_td, rec_tgt, etc.), not a
final fantasy-point total - its own precomputed pts_std/pts_half_ppr/pts_ppr
fields assume standard/half/full-PPR scoring, which may not match this
league's actual scoring_items (see engine/scoring.py). So rather than trust
those, this module runs the raw stats through the SAME ScoringRules the
pipeline already uses for real nflverse performances - a Sleeper projection
is scored exactly like a real game would be, just with projected stat counts
in place of actual ones."""
from __future__ import annotations

import logging

import requests

from engine.scoring import ScoringRules
from ingest import nfl_data as nd
from ingest.ids import IdMap

logger = logging.getLogger(__name__)

_PROJECTIONS_URL = "https://api.sleeper.app/v1/projections/nfl/regular/{season}/{week}"
_REQUEST_TIMEOUT_SEC = 15

# Sleeper raw per-stat projection field -> the nflverse-style column name
# engine/scoring.py's PLAYER_STAT_EXPR reads off a real player_stats row -
# these ARE that module's own column names, so this is the only translation
# layer needed to run a Sleeper projection through the exact same scoring
# rules a real nflverse performance uses.
_OFFENSE_STAT_MAP = {
    "pass_att": "attempts",
    "pass_cmp": "completions",
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "pass_2pt": "passing_2pt_conversions",
    "pass_int": "passing_interceptions",
    "pass_sack": "sacks_suffered",
    "rush_att": "carries",
    "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds",
    "rush_2pt": "rushing_2pt_conversions",
    "rush_fd": "rushing_first_downs",
    "rec": "receptions",
    "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds",
    "rec_2pt": "receiving_2pt_conversions",
    "rec_fd": "receiving_first_downs",
    "rec_tgt": "targets",
    # Sleeper only gives TOTAL fumbles/fumbles-lost, not split by pass/rush/
    # rec like nflverse - fumbles_total/fumbles_lost_total (the FUM/FUML
    # abbrs) are the only fumble categories a Sleeper-sourced projection can
    # feed; a league scoring the by-type PFUML/RFUML/REFUML abbrs instead
    # gets 0 from the PROJECTION only (a real played game still scores them
    # correctly off nflverse's actual stat line).
    "fum": "fumbles_total",
    "fum_lost": "fumbles_lost_total",
    "xpm": "pat_made",
    "xpa": "pat_att",
    "xpmiss": "pat_missed",
    "fgm": "fg_made",
    "fga": "fg_att",
    "fgm_20_29": "fg_made_20_29",
    "fgm_30_39": "fg_made_30_39",
    "fgm_40_49": "fg_made_40_49",
    # Sleeper lumps every 50+ make/miss into one bucket where nflverse (and
    # this app's own FG50/FG60/FG50P abbrs) splits 50-59 vs 60+ - credited
    # entirely to the 50-59 bucket, since 60+ attempts are rare enough that
    # this rarely matters and FG50P (both buckets summed) is unaffected
    # either way. fg_made/missed_0_19 and _20_29-miss have no Sleeper
    # equivalent at all (left unmapped, i.e. always 0 from this source) -
    # kickers essentially never attempt either extreme, so FG0's abbr will
    # mildly undercount for a league that scores it.
    "fgm_50p": "fg_made_50_59",
    "fgmiss_30_39": "fg_missed_30_39",
    "fgmiss_40_49": "fg_missed_40_49",
    "fgmiss_50p": "fg_missed_50_59",
}

# Same idea for D/ST: Sleeper raw field -> the nflverse team_stats-style
# column DST_STAT_EXPR reads off ctx["team_row"].
_DST_STAT_MAP = {
    "sack": "def_sacks",
    "int": "def_interceptions",
    "fum_rec": "fumble_recovery_opp",
    "ff": "def_fumbles_forced",
    "safe": "def_safeties",
    "def_fum_td": "fumble_recovery_tds",
    "def_td": "def_tds",
    # Sleeper has one combined blocked-kick count where DST_STAT_EXPR's BLKK
    # sums three separate columns (punt/PAT/FG block) - dumping the whole
    # value into one of the three avoids double-counting while still
    # contributing the right total to BLKK.
    "blk_kick": "def_punt_blocks",
    # Not mapped: a kick/punt-return TD by the defense's own return unit
    # (TRTD's special_teams_tds column) - Sleeper gives return YARDAGE
    # (def_kr_yd/def_pr_yd) but no TD count split out from def_td, so a
    # league scoring TRTD separately from DEFRETTD/INTTD gets 0 for this
    # category from a Sleeper-sourced projection.
}

_REAL_STAT_KEYS = frozenset(_OFFENSE_STAT_MAP) | frozenset(_DST_STAT_MAP)


def _is_stub(stats: dict) -> bool:
    """Sleeper returns HTTP 200 with a near-empty {"adp_dd_ppr": ...}-style
    record (no real per-stat fields at all) for a week it hasn't actually
    generated projections for yet (e.g. a far-future week) rather than
    erroring - this presence check is the only signal that distinguishes
    that stub from a real (possibly genuinely zero) projection, so every
    record is checked against it before scoring."""
    return not any(k in stats for k in _REAL_STAT_KEYS)


def _offense_row(stats: dict) -> dict:
    row = {col: stats[key] for key, col in _OFFENSE_STAT_MAP.items() if key in stats}
    if "fg_att" in row and "fg_made" in row:
        row["fg_missed"] = row["fg_att"] - row["fg_made"]
    if "pat_att" in row and "pat_made" in row and "pat_missed" not in row:
        row["pat_missed"] = row["pat_att"] - row["pat_made"]
    return row


def _dst_row(stats: dict) -> tuple[dict, float, float]:
    team_row = {col: stats[key] for key, col in _DST_STAT_MAP.items() if key in stats}
    points_allowed = stats.get("pts_allow") or 0.0
    yards_allowed = stats.get("yds_allow") or 0.0
    return team_row, points_allowed, yards_allowed


def fetch_week_projections(season: int, week: int) -> dict[str, dict]:
    """Raw {sleeper_player_id_or_team_abbrev: {raw_stat: value}} for one
    week, straight off Sleeper's public API - offense/kicker records are
    keyed by Sleeper's own numeric player id (as a string), D/ST records by
    NFL team abbreviation."""
    url = _PROJECTIONS_URL.format(season=season, week=week)
    resp = requests.get(url, timeout=_REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"unexpected Sleeper projections response shape for {season} week {week}: {type(data)}")
    return data


def get_future_sleeper_projections(
    weeks: list[int],
    season: int,
    id_map: IdMap,
    player_rules: ScoringRules,
    dst_rules: ScoringRules | None,
) -> dict[str, dict[int, float]]:
    """{internal_player_id: {week: projected_points}} for every given week -
    the Sleeper-sourced twin of ingest.espn_client.get_future_espn_projections
    - but already resolved to this app's own internal ids (unlike that
    function, which returns ESPN-id-keyed and leaves resolution to
    pipeline.py's _register), since a Sleeper player_id has no other use
    anywhere else in the pipeline worth keeping around. One request per week,
    covering every week given (including the current one - Sleeper has no
    separate "live" endpoint the way ESPN does, so there's no current-week/
    future-week split to make here)."""
    result: dict[str, dict[int, float]] = {}
    for week in weeks:
        try:
            raw = fetch_week_projections(season, week)
        except Exception:
            logger.warning("get_future_sleeper_projections: week %s fetch failed", week, exc_info=True)
            continue

        found = 0
        for key, stats in raw.items():
            if not isinstance(stats, dict) or _is_stub(stats):
                continue
            if key.isdigit():
                row = _offense_row(stats)
                if not row:
                    continue
                points = player_rules.points_for_row(row)
                internal_id = id_map.resolve(sleeper_id=key).id
            else:
                if dst_rules is None:
                    continue
                team_row, points_allowed, yards_allowed = _dst_row(stats)
                if not team_row and not points_allowed and not yards_allowed:
                    continue
                points = dst_rules.dst_points_for_row(team_row, points_allowed, yards_allowed)
                internal_id = f"dst:{nd.normalize_team(key) or key}"
            result.setdefault(internal_id, {})[week] = points
            found += 1
        logger.info("get_future_sleeper_projections: week %s - %s players with a projection", week, found)
    return result
