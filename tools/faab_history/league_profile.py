"""Shared league-settings profiling: pulls full mSettings for a league/year
and extracts the handful of fields relevant to deciding whether that
league's FAAB data belongs in the same training set as The O League's -
team count, PPR format, whether it's IDP, and whether its scoring looks
like a normal points league at all rather than some novelty format (e.g.
scored entirely off return yardage).

The scoring-format stat id map (SETTINGS_SCORING_FORMAT_MAP) and lineup slot
map (POSITION_MAP) come straight from the espn_api package already used
elsewhere in this repo - not hand-guessed, since getting a stat id wrong
here would silently misclassify leagues.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests
from espn_api.football.constant import POSITION_MAP, SETTINGS_SCORING_FORMAT_MAP

# The user's own reasonable/not-reasonable calls on every scoring category
# found across the 144-league inventory (see tools/faab_history/scoring_
# inventory.py and the "Scoring Ledger" artifact) - reviewed by hand rather
# than mapped category-by-category as new unmapped abbreviations kept
# turning up. {abbr: {"status": "good"|"bad", "comment": free text}}.
SCORING_LEDGER_PATH = Path(__file__).resolve().parent.parent.parent / "scoring_ledger.json"

SETTINGS_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://fantasy.espn.com/",
    "Origin": "https://fantasy.espn.com",
}

# DT DE LB DL CB S DB DP - see POSITION_MAP. Any league starting these plays
# individual defensive players, which is a different game from a standard
# team-D/ST league and shouldn't share a FAAB model with one.
IDP_SLOT_IDS = {8, 9, 10, 11, 12, 13, 14, 15}

# ESPN's settings list two statIds that both mean "each reception" (53 is
# the one real leagues use; 41 shows up in some older-format responses) -
# whichever is present (if either) is this league's PPR value.
REC_STAT_IDS = (53, 41)

# Passing/rushing/receiving TD - bedrock scoring present in essentially
# every real points league. A league with none of these is either broken
# or a novelty format (all defense/return-yardage, etc.), not a normal
# league we'd want to mix FAAB data in from.
CORE_TD_STAT_IDS = (4, 25, 43)


def fetch_settings(league_id: int, year: int) -> tuple[str, dict | None]:
    """(status_label, raw `settings` dict). Mirrors discover_public_leagues.check_league's
    status vocabulary: does_not_exist(404) / exists_but_private(401) / http_NNN / error(...) / ok."""
    try:
        resp = requests.get(
            SETTINGS_URL.format(year=year, league_id=league_id),
            params={"view": "mSettings"},
            headers=HEADERS,
            timeout=10,
        )
    except requests.RequestException as exc:
        return f"error({exc.__class__.__name__})", None
    if resp.status_code == 404:
        return "does_not_exist(404)", None
    if resp.status_code == 401:
        return "exists_but_private(401)", None
    if resp.status_code != 200:
        return f"http_{resp.status_code}", None
    return "ok", resp.json().get("settings", {})


def profile_settings(settings: dict) -> dict[str, Any]:
    """Extract the fields relevant to FAAB-data compatibility from a raw settings dict."""
    acq = settings.get("acquisitionSettings", {})
    roster = settings.get("rosterSettings", {})
    scoring = settings.get("scoringSettings", {})
    slot_counts = roster.get("lineupSlotCounts", {})
    items_by_stat = {item["statId"]: item["points"] for item in scoring.get("scoringItems", [])}

    ppr_value = next((items_by_stat[sid] for sid in REC_STAT_IDS if sid in items_by_stat), 0.0)
    if ppr_value >= 0.9:
        ppr_label = "PPR"
    elif ppr_value >= 0.4:
        ppr_label = "Half PPR"
    elif ppr_value > 0:
        ppr_label = f"{ppr_value} PPR"
    else:
        ppr_label = "Standard"

    idp_slots = {
        POSITION_MAP.get(int(slot_id), slot_id): count
        for slot_id, count in slot_counts.items()
        if int(slot_id) in IDP_SLOT_IDS and count
    }

    return {
        "name": settings.get("name"),
        "size": settings.get("size"),
        "scoring_type": scoring.get("scoringType"),
        "uses_faab": bool(acq.get("isUsingAcquisitionBudget")),
        "acquisition_budget": acq.get("acquisitionBudget"),
        "ppr_value": ppr_value,
        "ppr_label": ppr_label,
        "is_idp": bool(idp_slots),
        "idp_slots": idp_slots,
        "has_core_offense_scoring": any(items_by_stat.get(sid, 0) > 0 for sid in CORE_TD_STAT_IDS),
        "matchup_period_count": settings.get("scheduleSettings", {}).get("matchupPeriodCount"),
        "scoring_item_labels": sorted(
            SETTINGS_SCORING_FORMAT_MAP.get(sid, {}).get("abbr", str(sid)) for sid in items_by_stat
        ),
    }


def scoring_format_items(settings: dict) -> list[dict]:
    """Raw settings.scoringSettings.scoringItems (statId/points) converted to
    the [{abbr, points}] shape engine.scoring.ScoringRules.from_espn expects
    (that's the same shape espn_api's own League.settings.scoring_format
    produces). Items whose statId isn't in SETTINGS_SCORING_FORMAT_MAP are
    dropped rather than raising here - ScoringRules.from_espn already raises
    loudly on a truly unmapped abbr, so a genuinely unknown category still
    fails hard downstream instead of silently mis-scoring."""
    scoring = settings.get("scoringSettings", {})
    return [
        {"abbr": SETTINGS_SCORING_FORMAT_MAP[item["statId"]]["abbr"], "points": item["points"]}
        for item in scoring.get("scoringItems", [])
        if item["statId"] in SETTINGS_SCORING_FORMAT_MAP
    ]


_LEDGER_VALUE_RE = re.compile(r"leagues with\s+(-?[\d.]+)(?:\s*-\s*(-?[\d.]+))?", re.IGNORECASE)


def _parse_ledger_constraint(comment: str) -> tuple | None:
    """Parses the free-text comment on a "good" scoring-ledger entry into a
    machine-checkable constraint. Recognizes "Only include leagues with X"
    / "...with X-Y" (a single acceptable value or inclusive range - every
    OTHER point value this league might use for that category fails) and
    "Exclude leagues with X" (the inverse - X specifically is disqualifying,
    every other value is fine) - the two phrasings the human review
    actually used (see SCORING_LEDGER_PATH). Returns None (no constraint -
    any point value is fine) for an empty or unrecognized comment,
    deliberately permissive rather than guessing at a phrasing that isn't
    actually in the ledger."""
    if not comment:
        return None
    m = _LEDGER_VALUE_RE.search(comment)
    if not m:
        return None
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) is not None else lo
    kind = "exclude" if comment.strip().lower().startswith("exclude") else "only"
    return (kind, lo, hi)


def _value_matches_constraint(value: float, constraint: tuple) -> bool:
    kind, lo, hi = constraint
    in_range = lo - 1e-9 <= value <= hi + 1e-9
    return not in_range if kind == "exclude" else in_range


def load_scoring_ledger(path: Path = SCORING_LEDGER_PATH) -> dict[str, dict]:
    """{abbr: {"status": "good"|"bad"|"unsure", "comment": str}}, exported
    from the "Scoring Ledger" artifact's per-category reasonable/not-
    reasonable review. Empty dict (permissive - check_scoring_ledger then
    never fails a league) if the file doesn't exist yet, so ledger-based
    filtering stays fully optional until that review has actually been
    exported here."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def check_scoring_ledger(scoring_items: list[dict], ledger: dict[str, dict]) -> list[str]:
    """Reasons (empty if none) this candidate's scoring settings fail the
    human-reviewed scoring ledger. A no-op (always []) when ledger is empty
    - see load_scoring_ledger. Three ways a real (nonzero-point) category
    can fail once a real ledger is loaded:
      1. Explicitly marked "bad" (IDP/punter/team-matchup-meta/underivable-
         from-nflverse categories, or a mapped one the human review still
         judged not worth trusting - e.g. the single-game yardage milestone
         bonuses, whose point values varied too wildly across leagues using
         the same category name to trust any of them).
      2. Marked "good" but with a value constraint the comment specifies
         (e.g. "Only include leagues with 6") that this league's own point
         value doesn't satisfy - catches exactly the units/decimal outliers
         the ledger review surfaced (a league scoring a passing TD at 60
         points instead of 4-6, etc.) without disqualifying the category
         everywhere else it's used normally.
      3. Never reviewed at all (not a key in the ledger) - fails closed
         rather than silently admitting a category nobody has actually
         looked at; re-run scoring_inventory.py and extend the ledger to
         clear a real new category, not this function's default.
    A zero-point item never fails regardless of status - it contributes
    nothing to that league's scoring either way."""
    if not ledger:
        return []
    reasons = []
    for item in scoring_items:
        points = item["points"]
        if not points:
            continue
        abbr = item["abbr"]
        entry = ledger.get(abbr)
        if entry is None:
            reasons.append(f"{abbr} (points={points}) never reviewed in the scoring ledger")
            continue
        if entry.get("status") != "good":
            reasons.append(f"{abbr} (points={points}) marked {entry.get('status')!r} in the scoring ledger")
            continue
        constraint = _parse_ledger_constraint(entry.get("comment", ""))
        if constraint is not None and not _value_matches_constraint(points, constraint):
            reasons.append(f"{abbr}={points} outside the scoring ledger's allowed value ({entry.get('comment')})")
    return reasons


def compare_to_baseline(
    candidate: dict, baseline: dict, scoring_items: list[dict] | None = None,
    ledger: dict[str, dict] | None = None, min_size: int = 10, max_size: int = 16,
) -> dict:
    """Hard-filter reasons decide `compatible`; PPR mismatch is reported but
    never disqualifying, per the human call that team count/scoring format/
    sanity matter but exact PPR match doesn't.

    scoring_items/ledger (see check_scoring_ledger) are optional so existing
    callers/tests that only care about size/scoring_type/IDP keep working
    unchanged - pass both to also gate on the human-reviewed scoring
    ledger, which is where a real per-category/per-point-value judgment
    call belongs instead of an ever-growing hardcoded abbr blocklist."""
    hard_reasons = []
    size = candidate["size"] or 0
    if not (min_size <= size <= max_size):
        hard_reasons.append(f"size {size} outside [{min_size}, {max_size}]")
    if candidate["scoring_type"] != baseline["scoring_type"]:
        hard_reasons.append(f"scoring_type {candidate['scoring_type']!r} != baseline {baseline['scoring_type']!r}")
    if candidate["is_idp"]:
        hard_reasons.append(f"IDP league (individual defensive slots: {candidate['idp_slots']})")
    if not candidate["has_core_offense_scoring"]:
        hard_reasons.append("no standard passing/rushing/receiving TD scoring found - exotic scoring format")
    if scoring_items is not None:
        hard_reasons.extend(check_scoring_ledger(scoring_items, ledger or {}))

    ppr_match = candidate["ppr_label"] == baseline["ppr_label"]
    soft_notes = [] if ppr_match else [f"PPR format {candidate['ppr_label']!r} != baseline {baseline['ppr_label']!r} (not disqualifying)"]

    return {
        "compatible": not hard_reasons,
        "ppr_match": ppr_match,
        "reasons": hard_reasons + soft_notes,
    }
