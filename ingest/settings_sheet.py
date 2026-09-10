"""Reads league-tunable settings (matchup dampening, points-allowed basis,
playoff seeding options) from a public Google Sheet, so Alex can adjust them
from the site's Settings page instead of editing YAML. The sheet is read-only
from the pipeline's perspective - docs/js/settings.js writes to it via a
Google Apps Script Web App (see README's "Settings sheet" section)."""
from __future__ import annotations

import csv
import io
import logging
import re

import requests

logger = logging.getLogger(__name__)

SHEET_TAB_NAME = "Settings"

# key -> (dotted path into cfg["valuation"] or cfg["sim"], caster)
_SETTINGS_SCHEMA: dict[str, tuple[tuple[str, ...], type]] = {
    "matchup_dampening": (("valuation", "matchup_dampening"), float),
    "pa_basis": (("valuation", "pa_basis"), str),
    "pa_l5_weight": (("valuation", "pa_l5_weight"), float),
    "division_winners_first": (("sim", "division_winners_first"), lambda v: str(v).strip().lower() in ("true", "1", "yes")),
}

_TRUE_STRINGS = ("true", "1", "yes")

# Playoff-seeding tiebreaker keys are per-seed (seed_<n>_division_priority,
# seed_<n>_tiebreak_<1-4>) plus a shared division_tiebreak_<1-4> chain used to
# rank division winners - too many possible <n> values for the flat
# _SETTINGS_SCHEMA above, so they're matched by pattern instead and collected
# into cfg["sim"]["seeding_raw"] verbatim (engine.standings.compute_current_
# seeds' caller parses that into SeedConfig objects). An invalid criterion
# name here is harmless - _tiebreak_sort_key just ignores it - so this only
# validates shape, not the criterion vocabulary.
_SEED_KEY_RE = re.compile(r"^seed_(\d+)_(division_priority|tiebreak_[1-4])$")
_DIVISION_TIEBREAK_KEY_RE = re.compile(r"^division_tiebreak_[1-4]$")


class SettingsSheetError(Exception):
    pass


def _csv_export_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={SHEET_TAB_NAME}"


def parse_settings_csv(text: str) -> dict[str, dict[str, str]]:
    """{league_slug: {key: raw_string_value}} from CSV text with league_slug/key/value columns."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or {"league_slug", "key", "value"} - set(reader.fieldnames):
        raise SettingsSheetError(
            f"settings sheet is missing expected columns (league_slug, key, value); got {reader.fieldnames}"
        )

    result: dict[str, dict[str, str]] = {}
    for row in reader:
        slug = (row.get("league_slug") or "").strip()
        key = (row.get("key") or "").strip()
        value = (row.get("value") or "").strip()
        if not slug or not key:
            continue
        result.setdefault(slug, {})[key] = value
    return result


def fetch_remote_settings(sheet_id: str) -> dict[str, dict[str, str]]:
    """{league_slug: {key: raw_string_value}} fetched live from the public sheet."""
    url = _csv_export_url(sheet_id)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise SettingsSheetError(f"failed to fetch settings sheet {sheet_id}: {exc}") from exc
    return parse_settings_csv(resp.text)


def apply_remote_settings(cfg: dict, remote_for_league: dict[str, str]) -> list[str]:
    """Mutates cfg in place with any recognized keys from remote_for_league.
    Returns a list of human-readable descriptions of what changed."""
    changes = []
    for key, raw_value in remote_for_league.items():
        if _SEED_KEY_RE.match(key) or _DIVISION_TIEBREAK_KEY_RE.match(key):
            seeding_raw = cfg.setdefault("sim", {}).setdefault("seeding_raw", {})
            old_value = seeding_raw.get(key)
            if old_value != raw_value:
                changes.append(f"{key}: {old_value} -> {raw_value} (from settings sheet)")
            seeding_raw[key] = raw_value
            continue

        schema_entry = _SETTINGS_SCHEMA.get(key)
        if schema_entry is None:
            logger.warning("settings sheet has unknown key %r; ignoring", key)
            continue
        path, caster = schema_entry
        try:
            value = caster(raw_value)
        except (TypeError, ValueError) as exc:
            logger.warning("settings sheet key %r has invalid value %r: %s; ignoring", key, raw_value, exc)
            continue

        target = cfg
        for part in path[:-1]:
            target = target[part]
        old_value = target.get(path[-1])
        if old_value != value:
            changes.append(f"{key}: {old_value} -> {value} (from settings sheet)")
        target[path[-1]] = value
    return changes


def parse_seeding_config(seeding_raw: dict[str, str]):
    """seeding_raw (cfg["sim"]["seeding_raw"], collected verbatim by
    apply_remote_settings) -> (division_tiebreak_order, {seed_n: SeedConfig}).
    Imported lazily to keep ingest/ independent of engine/ at module load."""
    from engine.standings import SeedConfig

    division_tiebreak_order = [
        seeding_raw[f"division_tiebreak_{i}"] for i in range(1, 5) if seeding_raw.get(f"division_tiebreak_{i}")
    ]

    seed_numbers = {int(m.group(1)) for k in seeding_raw if (m := _SEED_KEY_RE.match(k))}
    seed_configs = {}
    for n in seed_numbers:
        division_priority = str(seeding_raw.get(f"seed_{n}_division_priority", "")).strip().lower() in _TRUE_STRINGS
        tiebreak_order = [
            seeding_raw[f"seed_{n}_tiebreak_{i}"] for i in range(1, 5) if seeding_raw.get(f"seed_{n}_tiebreak_{i}")
        ]
        seed_configs[n] = SeedConfig(division_priority=division_priority, tiebreak_order=tiebreak_order)
    return division_tiebreak_order, seed_configs
