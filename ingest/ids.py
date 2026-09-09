"""Cross-platform player id resolution: gsis_id (preferred) -> fp:<id> -> espn:<id>,
with a name+position fallback for players missing from the DynastyProcess map
(mostly rookie kickers). DST ids are always dst:<canonical NFL team abbrev>."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ingest import nfl_data as nd

logger = logging.getLogger(__name__)

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_PUNCT_RE = re.compile(r"[.'`,]")
_SPACE_RE = re.compile(r"\s+")


def merge_name(name: str) -> str:
    """Normalize a player name the same way DynastyProcess's merge_name does,
    so 'JaMarr Chase' / 'Ja'Marr Chase' and 'Amon-Ra St Brown' /
    'Amon-Ra St. Brown' collide."""
    s = _PUNCT_RE.sub("", name.lower())
    s = _SPACE_RE.sub(" ", s).strip()
    parts = s.split(" ")
    if len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


_DP_POS_TO_CANON = {"PK": "K"}


def _canon_pos(pos: str | None) -> str | None:
    if pos is None:
        return None
    return _DP_POS_TO_CANON.get(pos, pos)


@dataclass
class Resolution:
    id: str
    source: str  # "gsis" | "fp" | "espn" | "name_fallback" | "unmapped"


@dataclass
class IdMap:
    by_gsis: dict[str, dict]
    by_fp: dict[int, dict]
    by_espn: dict[int, dict]
    by_name_pos: dict[tuple[str, str], dict]
    ambiguous_name_pos: set[tuple[str, str]]

    def resolve(
        self,
        *,
        fp_id: int | None = None,
        espn_id: int | None = None,
        gsis_id: str | None = None,
        name: str | None = None,
        pos: str | None = None,
        team: str | None = None,
    ) -> Resolution:
        pos = _canon_pos(pos)
        if pos == "DST":
            team_canon = nd.normalize_team(team) or "UNK"
            return Resolution(id=f"dst:{team_canon}", source="gsis")

        record = None
        source = None
        if gsis_id and gsis_id in self.by_gsis:
            record, source = self.by_gsis[gsis_id], "gsis"
        elif fp_id is not None and fp_id in self.by_fp:
            record, source = self.by_fp[fp_id], "gsis"
        elif espn_id is not None and espn_id in self.by_espn:
            record, source = self.by_espn[espn_id], "gsis"
        elif name and pos:
            key = (merge_name(name), pos)
            if key in self.by_name_pos:
                record, source = self.by_name_pos[key], "gsis"
            elif key in self.ambiguous_name_pos:
                logger.warning("ambiguous name+pos match for %r (%s); skipping name fallback", name, pos)

        if record is not None:
            rec_gsis = record.get("gsis_id")
            if rec_gsis:
                return Resolution(id=rec_gsis, source=source or "gsis")
            rec_fp = record.get("fantasypros_id")
            if rec_fp:
                return Resolution(id=f"fp:{rec_fp}", source="fp")
            rec_espn = record.get("espn_id")
            if rec_espn:
                return Resolution(id=f"espn:{rec_espn}", source="espn")

        if fp_id is not None:
            return Resolution(id=f"fp:{fp_id}", source="fp")
        if espn_id is not None:
            return Resolution(id=f"espn:{espn_id}", source="espn")
        if name:
            return Resolution(id=f"unmapped:{merge_name(name)}:{pos or ''}", source="unmapped")
        return Resolution(id="unmapped:unknown", source="unmapped")


def build_id_map() -> IdMap:
    frame = nd.playerids()
    by_gsis: dict[str, dict] = {}
    by_fp: dict[int, dict] = {}
    by_espn: dict[int, dict] = {}
    name_pos_groups: dict[tuple[str, str], list[dict]] = {}

    for row in frame.iter_rows(named=True):
        record = {
            "gsis_id": row.get("gsis_id"),
            "fantasypros_id": row.get("fantasypros_id"),
            "espn_id": row.get("espn_id"),
            "name": row.get("name"),
            "position": _canon_pos(row.get("position")),
            "team": row.get("team"),
        }
        if record["gsis_id"]:
            by_gsis[record["gsis_id"]] = record
        if record["fantasypros_id"] is not None:
            by_fp[int(record["fantasypros_id"])] = record
        if record["espn_id"] is not None:
            by_espn[int(record["espn_id"])] = record
        if record["name"] and record["position"]:
            key = (merge_name(record["name"]), record["position"])
            name_pos_groups.setdefault(key, []).append(record)

    by_name_pos: dict[tuple[str, str], dict] = {}
    ambiguous: set[tuple[str, str]] = set()
    for key, records in name_pos_groups.items():
        # Prefer the richest record if there are duplicates that still agree on
        # every populated id; only truly conflicting entries count as ambiguous.
        distinct_gsis = {r["gsis_id"] for r in records if r["gsis_id"]}
        if len(distinct_gsis) > 1:
            ambiguous.add(key)
            continue
        by_name_pos[key] = max(records, key=lambda r: sum(1 for v in r.values() if v))

    return IdMap(
        by_gsis=by_gsis,
        by_fp=by_fp,
        by_espn=by_espn,
        by_name_pos=by_name_pos,
        ambiguous_name_pos=ambiguous,
    )
