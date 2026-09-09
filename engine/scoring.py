"""ESPN scoring_format -> fantasy points, computed from nflverse raw stat rows.

Two independent rule sets: offense/kicker player rows (from
ingest.nfl_data.player_stats) and team defense rows (from
ingest.nfl_data.team_stats, plus the opponent's points/yards that week).
Both fail loudly at construction if a league configures a non-zero scoring
item this module doesn't know how to compute, rather than silently scoring
it as zero.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _get(row: dict, col: str) -> float:
    return row.get(col) or 0.0


# ---------------------------------------------------------------------------
# Offense / kicker (player-level) stat expressions: abbr -> raw stat count
# from a single nflreadpy weekly player_stats row.
# ---------------------------------------------------------------------------
PLAYER_STAT_EXPR: dict[str, callable] = {
    "PA": lambda r: _get(r, "attempts"),
    "PC": lambda r: _get(r, "completions"),
    "INC": lambda r: _get(r, "attempts") - _get(r, "completions"),
    "PY": lambda r: _get(r, "passing_yards"),
    "PTD": lambda r: _get(r, "passing_tds"),
    "2PC": lambda r: _get(r, "passing_2pt_conversions"),
    "INTT": lambda r: _get(r, "passing_interceptions"),
    "RA": lambda r: _get(r, "carries"),
    "RY": lambda r: _get(r, "rushing_yards"),
    "RTD": lambda r: _get(r, "rushing_tds"),
    "2PR": lambda r: _get(r, "rushing_2pt_conversions"),
    "RECS": lambda r: _get(r, "receptions"),
    "REC": lambda r: _get(r, "receptions"),
    "REY": lambda r: _get(r, "receiving_yards"),
    "RETD": lambda r: _get(r, "receiving_tds"),
    "2PRE": lambda r: _get(r, "receiving_2pt_conversions"),
    "FTD": lambda r: _get(r, "fumble_recovery_tds"),
    "PFUM": lambda r: _get(r, "sack_fumbles"),
    "RFUM": lambda r: _get(r, "rushing_fumbles"),
    "REFUM": lambda r: _get(r, "receiving_fumbles"),
    "FUM": lambda r: _get(r, "fumbles_total"),
    "PFUML": lambda r: _get(r, "sack_fumbles_lost"),
    "RFUML": lambda r: _get(r, "rushing_fumbles_lost"),
    "REFUML": lambda r: _get(r, "receiving_fumbles_lost"),
    # The single most reliable "all fumbles this player lost" column - it is
    # NOT always exactly sack+rushing+receiving fumbles lost (verified: ~0.2%
    # of rows differ, apparently return fumbles), so use it directly rather
    # than summing the three components.
    "FUML": lambda r: _get(r, "fumbles_lost_total"),
    "TT": lambda r: _get(r, "passing_interceptions") + _get(r, "fumbles_lost_total"),
    # nflverse has one combined return-TD column; ESPN scores kickoff- and
    # punt-return TDs as separate items. Both map to the same column here -
    # ScoringRules.from_espn collapses a league that configures both KRTD and
    # PRTD into a single contribution (documented assumption, see below).
    "KRTD": lambda r: _get(r, "special_teams_tds"),
    "PRTD": lambda r: _get(r, "special_teams_tds"),
    "PAT": lambda r: _get(r, "pat_made"),
    "PATA": lambda r: _get(r, "pat_att"),
    "PATM": lambda r: _get(r, "pat_missed"),
    "FG": lambda r: _get(r, "fg_made"),
    "FGA": lambda r: _get(r, "fg_att"),
    "FGM": lambda r: _get(r, "fg_missed"),
    "FG0": lambda r: _get(r, "fg_made_0_19") + _get(r, "fg_made_20_29") + _get(r, "fg_made_30_39"),
    "FGM0": lambda r: _get(r, "fg_missed_0_19") + _get(r, "fg_missed_20_29") + _get(r, "fg_missed_30_39"),
    "FG40": lambda r: _get(r, "fg_made_40_49"),
    "FGM40": lambda r: _get(r, "fg_missed_40_49"),
    "FG50": lambda r: _get(r, "fg_made_50_59"),
    "FGM50": lambda r: _get(r, "fg_missed_50_59"),
    "FG60": lambda r: _get(r, "fg_made_60_"),
    "FGM60": lambda r: _get(r, "fg_missed_60_"),
    "FG50P": lambda r: _get(r, "fg_made_50_59") + _get(r, "fg_made_60_"),
    "FGM50P": lambda r: _get(r, "fg_missed_50_59") + _get(r, "fg_missed_60_"),
}

_RETURN_TD_ABBRS = {"KRTD", "PRTD"}

# ---------------------------------------------------------------------------
# Team defense (D/ST) stat expressions. Each function receives a context dict
# with `team_row` (that defense's team_stats row for the week), `points_allowed`,
# and `yards_allowed` (the opposing offense's total that game).
# ---------------------------------------------------------------------------
_PA_BRACKETS = [
    (0, 0, "PA0"), (1, 6, "PA1"), (7, 13, "PA7"), (14, 17, "PA14"),
    (18, 21, "PA18"), (22, 27, "PA22"), (28, 34, "PA28"), (35, 45, "PA35"),
    (46, None, "PA46"),
]
_YA_BRACKETS = [
    (0, 99, "YA100"), (100, 199, "YA199"), (200, 299, "YA299"), (300, 349, "YA349"),
    (350, 399, "YA399"), (400, 449, "YA449"), (450, 499, "YA499"), (500, 549, "YA549"),
    (550, None, "YA550"),
]


def _in_bracket(value: float, low: int, high: int | None) -> bool:
    return value >= low and (high is None or value <= high)


def _pa_bracket_expr(abbr: str):
    label = abbr[1:] if abbr.startswith("D") and abbr != "DPTSA" else abbr  # DPA7 -> PA7 boundaries
    bounds = next((b for b in _PA_BRACKETS if b[2] == label), None)
    if bounds is None:
        raise KeyError(abbr)
    low, high, _ = bounds
    return lambda ctx: 1.0 if _in_bracket(ctx["points_allowed"], low, high) else 0.0


def _ya_bracket_expr(abbr: str):
    bounds = next((b for b in _YA_BRACKETS if b[2] == abbr), None)
    if bounds is None:
        raise KeyError(abbr)
    low, high, _ = bounds
    return lambda ctx: 1.0 if _in_bracket(ctx["yards_allowed"], low, high) else 0.0


DST_STAT_EXPR: dict[str, callable] = {
    "SK": lambda ctx: _get(ctx["team_row"], "def_sacks"),
    "HALFSK": lambda ctx: _get(ctx["team_row"], "def_sacks"),
    "INT": lambda ctx: _get(ctx["team_row"], "def_interceptions"),
    "FR": lambda ctx: _get(ctx["team_row"], "fumble_recovery_opp"),
    "FF": lambda ctx: _get(ctx["team_row"], "def_fumbles_forced"),
    "SF": lambda ctx: _get(ctx["team_row"], "def_safeties"),
    "BLKK": lambda ctx: (
        _get(ctx["team_row"], "def_punt_blocks")
        + _get(ctx["team_row"], "def_pat_blocks")
        + _get(ctx["team_row"], "def_fg_blocks")
    ),
    "DEFRETTD": lambda ctx: _get(ctx["team_row"], "def_tds"),
    "INTTD": lambda ctx: _get(ctx["team_row"], "def_tds"),
    # nflverse's team_stats has no column isolating a blocked-punt/FG return
    # specifically - def_tds already lumps every defensive/return TD into one
    # count, so this folds into the same bucket as DEFRETTD/INTTD above. A
    # league scoring more than one of these three non-zero at once would
    # double-count; not the case for either of this repo's two leagues.
    "BLKKRTD": lambda ctx: _get(ctx["team_row"], "def_tds"),
    "FRTD": lambda ctx: _get(ctx["team_row"], "fumble_recovery_tds"),
    "TRTD": lambda ctx: _get(ctx["team_row"], "special_teams_tds"),
    # A defensive return of a failed 2-point try, and the rule-book oddity of
    # a 1-point safety off a botched PAT, are each a handful-of-times-per-
    # decade event across the whole NFL and nflverse's team_stats has no
    # column for either - always scored 0 rather than failing the pipeline.
    # from_espn() below warns if a league configures non-zero points here.
    "2PRET": lambda ctx: 0.0,
    "1PSF": lambda ctx: 0.0,
    "PTSA": lambda ctx: ctx["points_allowed"],
    "DPTSA": lambda ctx: ctx["points_allowed"],
    "YA": lambda ctx: ctx["yards_allowed"],
}

# Scoring items ScoringRules maps but can never compute a non-zero value for,
# because nflverse's data doesn't expose the underlying stat at all - see the
# comments above. from_espn() warns (rather than silently saying nothing) if
# a league actually configures points for one of these.
_ALWAYS_ZERO_DST_ABBRS = {"2PRET", "1PSF"}
for _abbr in [b[2] for b in _PA_BRACKETS]:
    DST_STAT_EXPR[_abbr] = _pa_bracket_expr(_abbr)
    DST_STAT_EXPR[f"D{_abbr}"] = _pa_bracket_expr(f"D{_abbr}")
for _abbr in [b[2] for b in _YA_BRACKETS]:
    DST_STAT_EXPR[_abbr] = _ya_bracket_expr(_abbr)


@dataclass
class ScoringRules:
    items: list[dict]  # [{id, abbr, points}], non-zero only
    is_dst: bool = False

    @classmethod
    def from_espn(cls, scoring_items: list[dict], *, is_dst: bool = False) -> "ScoringRules":
        # A league's scoring_format is one combined list covering every
        # position (offense/kicker AND D/ST items together) - only items in
        # this rule set's own domain apply; an item that belongs to the other
        # domain is not a mapping failure, just irrelevant here.
        own_domain = DST_STAT_EXPR if is_dst else PLAYER_STAT_EXPR
        other_domain = PLAYER_STAT_EXPR if is_dst else DST_STAT_EXPR

        all_nonzero = [item for item in scoring_items if item["points"]]
        nonzero = [item for item in all_nonzero if item["abbr"] in own_domain]

        if not is_dst:
            # Collapse KRTD+PRTD (same underlying nflverse column) into one
            # contribution so a league scoring both doesn't double-count.
            return_items = [i for i in nonzero if i["abbr"] in _RETURN_TD_ABBRS]
            if len(return_items) > 1:
                points_values = {i["points"] for i in return_items}
                if len(points_values) > 1:
                    logger.warning(
                        "league scores KRTD and PRTD at different point values (%s); "
                        "nflverse can't distinguish kickoff vs. punt return TDs, using %s",
                        points_values, return_items[0]["abbr"],
                    )
                keep = return_items[0]
                nonzero = [i for i in nonzero if i["abbr"] not in _RETURN_TD_ABBRS] + [keep]

        truly_unmapped = [
            item["abbr"] for item in all_nonzero if item["abbr"] not in own_domain and item["abbr"] not in other_domain
        ]
        if truly_unmapped:
            raise ValueError(
                f"scoring item(s) {truly_unmapped} have no point mapping anywhere in "
                "engine/scoring.py - add them before running the pipeline"
            )

        if is_dst:
            for item in nonzero:
                if item["abbr"] in _ALWAYS_ZERO_DST_ABBRS:
                    logger.warning(
                        "league scores %s at %s points but nflverse has no data for this stat "
                        "(see engine/scoring.py's DST_STAT_EXPR) - it will always score 0",
                        item["abbr"], item["points"],
                    )
        return cls(items=nonzero, is_dst=is_dst)

    def points_for_row(self, row: dict) -> float:
        if self.is_dst:
            raise TypeError("use dst_points_for_row for D/ST scoring rules")
        return sum(item["points"] * PLAYER_STAT_EXPR[item["abbr"]](row) for item in self.items)

    def dst_points_for_row(self, team_row: dict, points_allowed: float, yards_allowed: float) -> float:
        if not self.is_dst:
            raise TypeError("use points_for_row for offense/kicker scoring rules")
        ctx = {"team_row": team_row, "points_allowed": points_allowed, "yards_allowed": yards_allowed}
        return sum(item["points"] * DST_STAT_EXPR[item["abbr"]](ctx) for item in self.items)
