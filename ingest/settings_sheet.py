"""Reads league-tunable settings (matchup dampening, points-allowed basis,
playoff seeding options) from a public Google Sheet, so Alex can adjust them
from the site's Settings page instead of editing YAML. The sheet is read-only
from the pipeline's perspective - docs/js/settings.js writes to it via a
Google Apps Script Web App (see README's "Settings sheet" section)."""
from __future__ import annotations

import csv
import io
import logging

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
