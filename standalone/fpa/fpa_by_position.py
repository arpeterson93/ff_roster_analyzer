#!/usr/bin/env python3
"""Fantasy points allowed by position - which NFL defenses are the best and
worst matchups for QB / RB / WR / TE / K / D/ST, from real nflverse stats.

    pip install nflreadpy polars
    python fpa_by_position.py                         # current season, ESPN default half PPR
    python fpa_by_position.py --ppr 0                 # standard scoring
    python fpa_by_position.py --league-id 123456      # YOUR ESPN league's exact scoring (public leagues)
    python fpa_by_position.py --set PY=0.05 --set REC=1   # override any scoring item
    python fpa_by_position.py --chart DET:WR          # weekly line chart for one team + position
    python fpa_by_position.py --last 4 --out results  # last-4 average, plus CSVs in ./results

No accounts, logins or API keys. Data: nflverse (https://github.com/nflverse),
downloaded automatically on first run and cached by nflreadpy.

How the numbers work
- Every player's weekly line (and each team's D/ST line) is scored with the
  rules in effect, then summed per (team being rated, week, position): "points
  that team allowed to that position that week". Regular season only.
  For D/ST the team being rated is the OFFENSE: how many D/ST points the
  defenses facing it have scored against it.
- FPA/G is the plain average per game played (bye weeks don't count).
- "Opp Avg" is what the average opponent faced scores at that position in its
  OTHER games (the week it played this team is excluded, so a team never
  grades itself). "+/-" corrects for who was faced, in plain points: points
  allowed minus Opp Avg, averaged (+3.2 = gives up 3.2 more per game than
  those offenses normally score). "+/- Rk" ranks it; 1 = the best matchup.
- Scoring comes from, in priority order: your --set overrides, your ESPN
  league (--league-id), else ESPN's typical default template for the chosen
  --ppr (the most common settings across ~900 public ESPN leagues).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import nflreadpy as nfl
import polars as pl

OFFENSE_POSITIONS = ["QB", "RB", "WR", "TE", "K"]
POSITIONS = OFFENSE_POSITIONS + ["DST"]

# ESPN statId -> (abbr, label); adapted from the espn_api package's table.
ESPN_STAT_MAP = {
    0: ('PA', 'Each Pass Attempted'),
    1: ('PC', 'Each Pass Completed'),
    2: ('INC', 'Each Incomplete Pass'),
    3: ('PY', 'Passing Yards'),
    4: ('PTD', 'TD Pass'),
    5: ('PY5', 'Every 5 passing yards'),
    6: ('PY10', 'Every 10 passing yards'),
    7: ('PY20', 'Every 20 passing yards'),
    8: ('PY25', 'Every 25 passing yards'),
    9: ('PY50', 'Every 50 passing yards'),
    10: ('PY100', 'Every 100 passing yards'),
    11: ('PC5', 'Every 5 pass completions'),
    12: ('PC10', 'Every 10 pass completions'),
    13: ('IP5', 'Every 5 pass incompletions'),
    14: ('IP10', 'Every 10 pass incompletions'),
    15: ('PTD40', '40+ yard TD pass bonus'),
    16: ('PTD50', '50+ yard TD pass bonus'),
    17: ('P300', '300-399 yard passing game'),
    18: ('P400', '400+ yard passing game'),
    19: ('2PC', '2pt Passing Conversion'),
    20: ('INTT', 'Interceptions Thrown'),
    21: ('CPCT', 'Passing Completion Pct'),
    22: ('PYPG', 'Passing Yards Per Game'),
    23: ('RA', 'Rushing Attempts'),
    24: ('RY', 'Rushing Yards'),
    25: ('RTD', 'TD Rush'),
    26: ('2PR', '2pt Rushing Conversion'),
    27: ('RY5', 'Every 5 rushing yards'),
    28: ('RY10', 'Every 10 rushing yards'),
    29: ('RY20', 'Every 20 rushing yards'),
    30: ('RY25', 'Every 25 rushing yards'),
    31: ('RY50', 'Every 50 rushing yards'),
    32: ('R100', 'Every 100 rushing yards'),
    33: ('RA5', 'Every 5 rush attempts'),
    34: ('RA10', 'Every 10 rush attempts'),
    35: ('RTD40', '40+ yard TD rush bonus'),
    36: ('RTD50', '50+ yard TD rush bonus'),
    37: ('RY100', '100-199 yard rushing game'),
    38: ('RY200', '200+ yard rushing game'),
    39: ('RYPA', 'Rushing Yards Per Attempt'),
    40: ('RYPG', 'Rushing Yards Per Game'),
    41: ('RECS', 'Receptions'),
    42: ('REY', 'Receiving Yards'),
    43: ('RETD', 'TD Reception'),
    44: ('2PRE', '2pt Receiving Conversion'),
    45: ('RETD40', '40+ yard TD rec bonus'),
    46: ('RETD50', '50+ yard TD rec bonus'),
    47: ('REY5', 'Every 5 receiving yards'),
    48: ('REY10', 'Every 10 receiving yards'),
    49: ('REY20', 'Every 20 receiving yards'),
    50: ('REY25', 'Every 25 receiving yards'),
    51: ('REY50', 'Every 50 receiving yards'),
    52: ('RE100', 'Every 100 receiving yards'),
    53: ('REC', 'Each reception'),
    54: ('REC5', 'Every 5 receptions'),
    55: ('REC10', 'Every 10 receptions'),
    56: ('REY100', '100-199 yard receiving game'),
    57: ('REY200', '200+ yard receiving game'),
    58: ('RET', 'Receiving Target'),
    59: ('YAC', 'Receiving Yards After Catch'),
    60: ('YPC', 'Receiving Yards Per Catch'),
    61: ('REYPG', 'Receiving Yards Per Game'),
    62: ('PTL', 'Total 2pt Conversions'),
    63: ('FTD', 'Fumble Recovered for TD'),
    64: ('SKD', 'Sacked'),
    65: ('PFUM', 'Passing Fumbles'),
    66: ('RFUM', 'Rushing Fumbles'),
    67: ('REFUM', 'Receiving Fumbles'),
    68: ('FUM', 'Total Fumbles'),
    69: ('PFUML', 'Passing Fumbles Lost'),
    70: ('RFUML', 'Rushing Fumbles Lost'),
    71: ('REFUML', 'Receiving Fumbles Lost'),
    72: ('FUML', 'Total Fumbles Lost'),
    73: ('TT', 'Total Turnovers'),
    74: ('FG50P', 'FG Made (50+ yards)'),
    75: ('FGA50P', 'FG Attempted (50+ yards)'),
    76: ('FGM50P', 'FG Missed (50+ yards)'),
    77: ('FG40', 'FG Made (40-49 yards)'),
    78: ('FGA40', 'FG Attempted (40-49 yards)'),
    79: ('FGM40', 'FG Missed (40-49 yards)'),
    80: ('FG0', 'FG Made (0-39 yards)'),
    81: ('FGA0', 'FG Attempted (0-39 yards)'),
    82: ('FGM0', 'FG Missed (0-39 yards)'),
    83: ('FG', 'Total FG Made'),
    84: ('FGA', 'Total FG Attempted'),
    85: ('FGM', 'Total FG Missed'),
    86: ('PAT', 'Each PAT Made'),
    87: ('PATA', 'Each PAT Attempted'),
    88: ('PATM', 'Each PAT Missed'),
    89: ('PA0', '0 points allowed'),
    90: ('PA1', '1-6 points allowed'),
    91: ('PA7', '7-13 points allowed'),
    92: ('PA14', '14-17 points allowed'),
    93: ('BLKKRTD', 'Blocked Punt or FG return for TD'),
    94: ('DEFRETTD', 'Fumble or INT Return for TD'),
    95: ('INT', 'Each Interception'),
    96: ('FR', 'Each Fumble Recovered'),
    97: ('BLKK', 'Blocked Punt, PAT or FG'),
    98: ('SF', 'Each Safety'),
    99: ('SK', 'Each Sack'),
    100: ('HALFSK', '1/2 Sack'),
    101: ('KRTD', 'Kickoff Return TD'),
    102: ('PRTD', 'Punt Return TD'),
    103: ('INTTD', 'Interception Return TD'),
    104: ('FRTD', 'Fumble Return TD'),
    105: ('TRTD', 'Total Return TD'),
    106: ('FF', 'Each Fumble Forced'),
    107: ('TKA', 'Assisted Tackles'),
    108: ('TKS', 'Solo Tackles'),
    109: ('TK', 'Total Tackles'),
    110: ('TK3', 'Every 3 Total Tackles'),
    111: ('TK5', 'Every 5 Total Tackles'),
    112: ('STF', 'Stuffs'),
    113: ('PD', 'Passes Defensed'),
    114: ('KR', 'Kickoff Return Yards'),
    115: ('PR', 'Punt Return Yards'),
    116: ('KR10', 'Every 10 kickoff return yards'),
    117: ('KR25', 'Every 25 kickoff return yards'),
    118: ('PR10', 'Every 10 punt return yards'),
    119: ('PR25', 'Every 25 punt return yards'),
    120: ('PTSA', 'Points Allowed'),
    121: ('PA18', '18-21 points allowed'),
    122: ('PA22', '22-27 points allowed'),
    123: ('PA28', '28-34 points allowed'),
    124: ('PA35', '35-45 points allowed'),
    125: ('PA46', '46+ points allowed'),
    126: ('PAPG', 'Points Allowed Per Game'),
    127: ('YA', 'Yards Allowed'),
    128: ('YA100', 'Less than 100 total yards allowed'),
    129: ('YA199', '100-199 total yards allowed'),
    130: ('YA299', '200-299 total yards allowed'),
    131: ('YA349', '300-349 total yards allowed'),
    132: ('YA399', '350-399 total yards allowed'),
    133: ('YA449', '400-449 total yards allowed'),
    134: ('YA499', '450-499 total yards allowed'),
    135: ('YA549', '500-549 total yards allowed'),
    136: ('YA550', '550+ total yards allowed'),
    137: ('YAPG', 'Yards Allowed Per Game'),
    138: ('PT', 'Net Punts'),
    139: ('PTY', 'Punt Yards'),
    140: ('PT10', 'Punts Inside the 10'),
    141: ('PT20', 'Punts Inside the 20'),
    142: ('PTB', 'Blocked Punts'),
    143: ('PTR', 'Punts Returned'),
    144: ('PTRY', 'Punt Return Yards'),
    145: ('PTTB', 'Touchbacks'),
    146: ('PTFC', 'Fair Catches'),
    147: ('PTAVG', 'Punt Average'),
    148: ('PTA44', 'Punt Average 44.0+'),
    149: ('PTA42', 'Punt Average 42.0-43.9'),
    150: ('PTA40', 'Punt Average 40.0-41.9'),
    151: ('PTA38', 'Punt Average 38.0-39.9'),
    152: ('PTA36', 'Punt Average 36.0-37.9'),
    153: ('PTA34', 'Punt Average 34.0-35.9'),
    154: ('PTA33', 'Punt Average 33.9 or less'),
    155: ('TW', 'Team Win'),
    156: ('TL', 'Team Loss'),
    157: ('TIE', 'Team Tie'),
    158: ('PTS', 'Points Scored'),
    159: ('PPG', 'Points Scored Per Game'),
    160: ('MGN', 'Margin of Victory'),
    161: ('WM25', '25+ point Win Margin'),
    162: ('WM20', '20-24 point Win Margin'),
    163: ('WM15', '15-19 point Win Margin'),
    164: ('WM10', '10-14 point Win Margin'),
    165: ('WM5', '5-9 point Win Margin'),
    166: ('WM1', '1-4 point Win Margin'),
    167: ('LM1', '1-4 point Loss Margin'),
    168: ('LM5', '5-9 point Loss Margin'),
    169: ('LM10', '10-14 point Loss Margin'),
    170: ('LM15', '15-19 point Loss Margin'),
    171: ('LM20', '20-24 point Loss Margin'),
    172: ('LM25', '25+ point Loss Margin'),
    173: ('MGNPG', 'Margin of Victory Per Game'),
    174: ('WINPCT', 'Winning Pct'),
    175: ('PTD0', '0-9 yd TD pass bonus'),
    176: ('PTD10', '10-19 yd TD pass bonus'),
    177: ('PTD20', '20-29 yd TD pass bonus'),
    178: ('PTD30', '30-39 yd TD pass bonus'),
    179: ('RTD0', '0-9 yd TD rush bonus'),
    180: ('RTD10', '10-19 yd TD rush bonus'),
    181: ('RTD20', '20-29 yd TD rush bonus'),
    182: ('RTD30', '30-39 yd TD rush bonus'),
    183: ('RETD0', '0-9 yd TD rec bonus'),
    184: ('RETD10', '10-19 yd TD rec bonus'),
    185: ('RETD20', '20-29 yd TD rec bonus'),
    186: ('RETD30', '30-39 yd TD rec bonus'),
    187: ('DPTSA', 'D/ST Points Allowed'),
    188: ('DPA0', 'D/ST 0 points allowed'),
    189: ('DPA1', 'D/ST 1-6 points allowed'),
    190: ('DPA7', 'D/ST 7-13 points allowed'),
    191: ('DPA14', 'D/ST 14-17 points allowed'),
    192: ('DPA18', 'D/ST 18-21 points allowed'),
    193: ('DPA22', 'D/ST 22-27 points allowed'),
    194: ('DPA28', 'D/ST 28-34 points allowed'),
    195: ('DPA35', 'D/ST 35-45 points allowed'),
    196: ('DPA46', 'D/ST 46+ points allowed'),
    197: ('DPAPG', 'D/ST Points Allowed Per Game'),
    198: ('FG50', 'FG Made (50-59 yards)'),
    199: ('FGA50', 'FG Attempted (50-59 yards)'),
    200: ('FGM50', 'FG Missed (50-59 yards)'),
    201: ('FG60', 'FG Made (60+ yards)'),
    202: ('FGA60', 'FG Attempted (60+ yards)'),
    203: ('FGM60', 'FG Missed (60+ yards)'),
    204: ('O2PRET', 'Offensive 2pt Return'),
    205: ('D2PRET', 'Defensive 2pt Return'),
    206: ('2PRET', '2pt Return'),
    207: ('O1PSF', 'Offensive 1pt Safety'),
    208: ('D1PSF', 'Defensive 1pt Safety'),
    209: ('1PSF', '1pt Safety'),
    210: ('GP', 'Games Played'),
    211: ('PFD', 'Passing First Down'),
    212: ('RFD', 'Rushing First Down'),
    213: ('REFD', 'Receiving First Down'),
    214: ('FGY', 'FG Made Yards'),
    215: ('FGMY', 'FG Missed Yards'),
    216: ('FGAY', 'FG Attempt Yards'),
    217: ('FGY5', 'Every 5 FG Made yards'),
    218: ('FGY10', 'Every 10 FG Made yards'),
    219: ('FGY20', 'Every 20 FG Made yards'),
    220: ('FGY25', 'Every 25 FG Made yards'),
    221: ('FGY50', 'Every 50 FG Made yards'),
    222: ('FGY100', 'Every 100 FG Made yards'),
    223: ('FGMY5', 'Every 5 FG Missed yards'),
    224: ('FGMY10', 'Every 10 FG Missed yards'),
    225: ('FGMY20', 'Every 20 FG Missed yards'),
    226: ('FGMY25', 'Every 25 FG Missed yards'),
    227: ('FGMY50', 'Every 50 FG Missed yards'),
    228: ('FGMY100', 'Every 100 FG Missed yards'),
    229: ('FGAY5', 'Every 5 FG Attempt yards'),
    230: ('FGAY10', 'Every 10 FG Attempt yards'),
    231: ('FGAY20', 'Every 20 FG Attempt yards'),
    232: ('FGAY25', 'Every 25 FG Attempt yards'),
    233: ('FGAY50', 'Every 50 FG Attempt yards'),
    234: ('FGAY100', 'Every 100 FG Attempt yards'),
}

# ---------------------------------------------------------------------------
# Stat -> nflverse column expressions (adapted from ff_roster_analyzer's
# engine/scoring.py). Player expressions read one nflverse weekly player row;
# D/ST expressions read a context with that defense's team_stats row plus
# the points / yards it allowed in the game.
# ---------------------------------------------------------------------------
def _get(row: dict, col: str) -> float:
    return row.get(col) or 0.0

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
    "FUML": lambda r: _get(r, "fumbles_lost_total"),
    "TT": lambda r: _get(r, "passing_interceptions") + _get(r, "fumbles_lost_total"),
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
    "PY5": lambda r: _get(r, "passing_yards") // 5,
    "PY10": lambda r: _get(r, "passing_yards") // 10,
    "PY20": lambda r: _get(r, "passing_yards") // 20,
    "PY25": lambda r: _get(r, "passing_yards") // 25,
    "PY50": lambda r: _get(r, "passing_yards") // 50,
    "PY100": lambda r: _get(r, "passing_yards") // 100,
    "RY5": lambda r: _get(r, "rushing_yards") // 5,
    "RY10": lambda r: _get(r, "rushing_yards") // 10,
    "RY20": lambda r: _get(r, "rushing_yards") // 20,
    "RY25": lambda r: _get(r, "rushing_yards") // 25,
    "RY50": lambda r: _get(r, "rushing_yards") // 50,
    "R100": lambda r: _get(r, "rushing_yards") // 100,  # ESPN's own abbr for "every 100 rushing yards" - not RY100
    "REY5": lambda r: _get(r, "receiving_yards") // 5,
    "REY10": lambda r: _get(r, "receiving_yards") // 10,
    "REY20": lambda r: _get(r, "receiving_yards") // 20,
    "REY25": lambda r: _get(r, "receiving_yards") // 25,
    "REY50": lambda r: _get(r, "receiving_yards") // 50,
    "RE100": lambda r: _get(r, "receiving_yards") // 100,  # ESPN's own abbr for "every 100 receiving yards" - not REY100
    "REC5": lambda r: _get(r, "receptions") // 5,
    "REC10": lambda r: _get(r, "receptions") // 10,
    "IP10": lambda r: (_get(r, "attempts") - _get(r, "completions")) // 10,
    "PC10": lambda r: _get(r, "completions") // 10,
    "RA5": lambda r: _get(r, "carries") // 5,
    "RA10": lambda r: _get(r, "carries") // 10,
    "KR": lambda r: _get(r, "kickoff_return_yards"),
    "KR10": lambda r: _get(r, "kickoff_return_yards") // 10,
    "KR25": lambda r: _get(r, "kickoff_return_yards") // 25,
    "PR": lambda r: _get(r, "punt_return_yards"),
    "PR10": lambda r: _get(r, "punt_return_yards") // 10,
    "PR25": lambda r: _get(r, "punt_return_yards") // 25,
    "FGA50": lambda r: _get(r, "fg_made_50_59") + _get(r, "fg_missed_50_59"),
    "FGA60": lambda r: _get(r, "fg_made_60_") + _get(r, "fg_missed_60_"),
    "P300": lambda r: 1.0 if 300 <= _get(r, "passing_yards") < 400 else 0.0,
    "P400": lambda r: 1.0 if _get(r, "passing_yards") >= 400 else 0.0,
    "RY100": lambda r: 1.0 if 100 <= _get(r, "rushing_yards") < 200 else 0.0,
    "RY200": lambda r: 1.0 if _get(r, "rushing_yards") >= 200 else 0.0,
    "REY100": lambda r: 1.0 if 100 <= _get(r, "receiving_yards") < 200 else 0.0,
    "REY200": lambda r: 1.0 if _get(r, "receiving_yards") >= 200 else 0.0,
    "SKD": lambda r: _get(r, "sacks_suffered"),  # QB being sacked, not a defender's sack (that's "SK" in DST_STAT_EXPR)
    "RET": lambda r: _get(r, "targets"),
    "RFD": lambda r: _get(r, "rushing_first_downs"),
    "REFD": lambda r: _get(r, "receiving_first_downs"),
    "FGY": lambda r: (
        _get(r, "fg_made_0_19") * 18 + _get(r, "fg_made_20_29") * 25 + _get(r, "fg_made_30_39") * 35
        + _get(r, "fg_made_40_49") * 45 + _get(r, "fg_made_50_59") * 55 + _get(r, "fg_made_60_") * 62
    ),
}

_RETURN_TD_ABBRS = {"KRTD", "PRTD"}

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
    "BLKKRTD": lambda ctx: _get(ctx["team_row"], "def_tds"),
    "FRTD": lambda ctx: _get(ctx["team_row"], "fumble_recovery_tds"),
    "TRTD": lambda ctx: _get(ctx["team_row"], "special_teams_tds"),
    "2PRET": lambda ctx: 0.0,
    "1PSF": lambda ctx: 0.0,
    "PTSA": lambda ctx: ctx["points_allowed"],
    "DPTSA": lambda ctx: ctx["points_allowed"],
    "YA": lambda ctx: ctx["yards_allowed"],
}

_ALWAYS_ZERO_DST_ABBRS = {"2PRET", "1PSF"}
for _abbr in [b[2] for b in _PA_BRACKETS]:
    DST_STAT_EXPR[_abbr] = _pa_bracket_expr(_abbr)
    DST_STAT_EXPR[f"D{_abbr}"] = _pa_bracket_expr(f"D{_abbr}")
for _abbr in [b[2] for b in _YA_BRACKETS]:
    DST_STAT_EXPR[_abbr] = _ya_bracket_expr(_abbr)

_DEF_TD_ABBRS = {"DEFRETTD", "INTTD", "BLKKRTD"}  # all read nflverse's single def_tds column

# Items nflverse has no data for: scored as 0 and flagged to the user.
UNSUPPORTED_ALWAYS_ZERO = {"2PRET", "1PSF"}


class ScoringRules:
    """Active scoring items for one domain (players, or D/ST)."""

    def __init__(self, items: list[dict], is_dst: bool = False):
        self.is_dst = is_dst
        table = DST_STAT_EXPR if is_dst else PLAYER_STAT_EXPR
        key = "dst_points" if is_dst else "points"
        keep = [{"abbr": i["abbr"], "points": i.get(key, i["points"])} for i in items if i["abbr"] in table]
        keep = [i for i in keep if i["points"]]
        if not is_dst:
            # nflverse has one combined return-TD column, so a league scoring both
            # kickoff and punt return TDs must only count it once.
            returns = [i for i in keep if i["abbr"] in _RETURN_TD_ABBRS]
            if len(returns) > 1:
                keep = [i for i in keep if i["abbr"] not in _RETURN_TD_ABBRS] + [returns[0]]
        if is_dst:
            # nflverse's def_tds is ONE count for every defensive TD (interception, blocked kick,
            # generic return), so a league scoring several of these must only count it once.
            same = [i for i in keep if i["abbr"] in _DEF_TD_ABBRS]
            if len(same) > 1:
                keep = [i for i in keep if i["abbr"] not in _DEF_TD_ABBRS] + [same[0]]
        self.items = keep

    def points(self, row: dict, **ctx) -> float:
        if self.is_dst:
            c = {"team_row": row, **ctx}
            return sum(i["points"] * DST_STAT_EXPR[i["abbr"]](c) for i in self.items)
        return sum(i["points"] * PLAYER_STAT_EXPR[i["abbr"]](row) for i in self.items)


# ---------------------------------------------------------------------------
# Scoring sources
# ---------------------------------------------------------------------------
def espn_default_items(ppr: float) -> list[dict]:
    """ESPN's typical default template (the modal value of each item across
    ~900 public ESPN leagues, 2026): offense, kickers and D/ST."""
    pts = {
        # passing / rushing / receiving / misc
        "PY": 0.04, "PTD": 4, "INTT": -2, "2PC": 2,
        "RY": 0.1, "RTD": 6, "2PR": 2,
        "REY": 0.1, "RETD": 6, "2PRE": 2, "REC": ppr,
        "FUML": -2, "FTD": 6, "KRTD": 6, "PRTD": 6,
        # kickers
        "FG0": 3, "FG40": 4, "FG50": 5, "FG60": 5, "PAT": 1, "FGM": -1,
        # D/ST
        "SK": 1, "INT": 2, "FR": 2, "SF": 2, "BLKK": 2,
        "INTTD": 6, "FRTD": 6, "BLKKRTD": 6, "TRTD": 6,
        "PA0": 5, "PA1": 4, "PA7": 3, "PA14": 1, "PA18": 0, "PA22": 0, "PA28": -1, "PA35": -3, "PA46": -5,
        "YA100": 5, "YA199": 3, "YA299": 2, "YA349": 0, "YA399": -1, "YA449": -3, "YA499": -5, "YA549": -6, "YA550": -7,
    }
    labels = {a: l for a, l in ESPN_STAT_MAP.values()}
    return [{"abbr": a, "label": labels.get(a, a), "points": p, "dst_points": p} for a, p in pts.items()]


def fetch_league_items(league_id: int, season: int) -> list[dict]:
    """Scoring items of a PUBLIC ESPN league. Private leagues need login
    cookies, which this script deliberately never asks for."""
    url = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
           f"/segments/0/leagues/{league_id}?view=mSettings")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise SystemExit(f"League {league_id} is private - ESPN only shares its settings with logged-in members. "
                             "Use --ppr / --set instead, or make the league viewable.")
        if exc.code == 404:
            raise SystemExit(f"League {league_id} was not found for {season}.")
        raise SystemExit(f"ESPN returned HTTP {exc.code} for league {league_id}.")
    items = []
    for it in data["settings"]["scoringSettings"]["scoringItems"]:
        abbr, label = ESPN_STAT_MAP.get(it["statId"], (None, None))
        if abbr is None:
            continue
        # pointsOverrides["16"] is the D/ST slot's own value for this item (e.g. a league
        # that scores return TDs 6 for players but 0 for the defense).
        dst = it.get("pointsOverrides", {}).get("16", it["points"])
        items.append({"abbr": abbr, "label": label, "points": it["points"], "dst_points": dst})
    return items


def apply_overrides(items: list[dict], overrides: dict[str, float]) -> list[dict]:
    valid = {a for a, _ in ESPN_STAT_MAP.values()}
    bad = [a for a in overrides if a not in valid]
    if bad:
        raise SystemExit(f"Unknown scoring item(s) {bad}. Valid names include: {', '.join(sorted(valid))}")
    by = {i["abbr"]: dict(i) for i in items}
    labels = {a: l for a, l in ESPN_STAT_MAP.values()}
    for abbr, pts in overrides.items():
        by[abbr] = {"abbr": abbr, "label": labels.get(abbr, abbr), "points": pts, "dst_points": pts}
    return list(by.values())


def describe_scoring(items: list[dict]) -> list[str]:
    """Human-readable list of what's being scored, plus anything we can't."""
    usable = set(PLAYER_STAT_EXPR) | set(DST_STAT_EXPR)
    lines, skipped = [], []
    for i in sorted(items, key=lambda x: x["abbr"]):
        if not (i["points"] or i["dst_points"]):
            continue
        if i["abbr"] not in usable or i["abbr"] in UNSUPPORTED_ALWAYS_ZERO:
            skipped.append(f"{i['abbr']} ({i['label']})")
            continue
        extra = f"  [D/ST: {i['dst_points']:g}]" if i["dst_points"] != i["points"] else ""
        lines.append(f"  {i['abbr']:<8}{i['points']:>7g}  {i['label']}{extra}")
    if skipped:
        lines.append("  Not scored (no nflverse data for these): " + ", ".join(skipped))
    return lines


# ---------------------------------------------------------------------------
# Data + tables
# ---------------------------------------------------------------------------
def load_frames(season: int):
    players = nfl.load_player_stats([season]).filter(pl.col("season_type") == "REG")
    teams = nfl.load_team_stats([season]).filter(pl.col("season_type") == "REG")
    sched = nfl.load_schedules([season]).filter(pl.col("game_type") == "REG")
    return players, teams, sched


def collect_points(items, positions, players, teams, sched) -> pl.DataFrame:
    """One row per (team being rated, week, position): points the opposing
    unit scored against it. Columns: team, week, position, opponent, points."""
    rows = []
    offense_positions = [p for p in positions if p in OFFENSE_POSITIONS]
    if offense_positions:
        rules = ScoringRules(items)
        acc: dict[tuple, float] = {}
        sub = players.filter(pl.col("position").is_in(offense_positions) & pl.col("opponent_team").is_not_null())
        for r in sub.iter_rows(named=True):
            key = (r["opponent_team"], r["week"], r["position"], r["team"])
            acc[key] = acc.get(key, 0.0) + rules.points(r)
        rows += [{"team": k[0], "week": k[1], "position": k[2], "opponent": k[3], "points": v} for k, v in acc.items()]
    if "DST" in positions and teams.height:
        rules = ScoringRules(items, is_dst=True)
        scores = {}
        for g in sched.iter_rows(named=True):
            if g["home_score"] is not None and g["away_score"] is not None:
                scores[g["game_id"]] = {g["home_team"]: g["home_score"], g["away_team"]: g["away_score"]}
        by_game = {(r["game_id"], r["team"]): r for r in teams.iter_rows(named=True)}
        for r in teams.iter_rows(named=True):
            game = scores.get(r["game_id"])
            opp = by_game.get((r["game_id"], r["opponent_team"]))
            if game is None or opp is None or r["opponent_team"] not in game:
                continue
            yards = (opp.get("passing_yards") or 0) + (opp.get("rushing_yards") or 0)
            pts = rules.points(r, points_allowed=game[r["opponent_team"]], yards_allowed=yards)
            # r["team"] is the defense; the team being RATED for D/ST is the offense it faced
            rows.append({"team": r["opponent_team"], "week": r["week"], "position": "DST", "opponent": r["team"], "points": pts})
    return pl.DataFrame(rows, schema={"team": pl.Utf8, "week": pl.Int64, "position": pl.Utf8, "opponent": pl.Utf8, "points": pl.Float64})


def build_tables(points: pl.DataFrame, last: int | None):
    """-> (weekly, summary). weekly adds `opp_avg`: that week's opponent's
    average at this position in its OTHER games (leave-one-out)."""
    totals = points.group_by(["opponent", "position"]).agg(
        pl.col("points").sum().alias("opp_total"), pl.col("week").n_unique().alias("opp_games")
    )
    weekly = points.join(totals, on=["opponent", "position"]).with_columns(
        pl.when(pl.col("opp_games") > 1)
        .then((pl.col("opp_total") - pl.col("points")) / (pl.col("opp_games") - 1))
        .otherwise(None)
        .alias("opp_avg")
    ).drop(["opp_total", "opp_games"]).sort(["position", "team", "week"])
    rows = []
    for (pos, team), g in weekly.group_by(["position", "team"]):
        exp = g.filter(pl.col("opp_avg").is_not_null())
        rec = {
            "position": pos, "team": team, "games": g.height, "total": g["points"].sum(), "per_game": g["points"].mean(),
            # additive version: avg of (points allowed - that opponent's average elsewhere)
            "diff": (exp["points"] - exp["opp_avg"]).mean() if exp.height else None,
            # the average opponent this team faced (each opponent's own average elsewhere)
            "opp_avg": exp["opp_avg"].mean() if exp.height else None,
        }
        if last:
            tail = g.sort("week").tail(last)
            tail_exp = tail.filter(pl.col("opp_avg").is_not_null())
            rec["last_n"] = tail["points"].mean()
            rec["last_opp_avg"] = tail_exp["opp_avg"].mean() if tail_exp.height else None
            rec["last_diff"] = (tail_exp["points"] - tail_exp["opp_avg"]).mean() if tail_exp.height else None
        rows.append(rec)
    summary = pl.DataFrame(rows).with_columns(
        pl.col("per_game").rank("ordinal", descending=True).over("position").cast(pl.Int64).alias("rank"),
        pl.col("diff").rank("ordinal", descending=True).over("position").cast(pl.Int64).alias("diff_rank"),
    )
    if last:
        summary = summary.with_columns(
            pl.col("last_diff").rank("ordinal", descending=True).over("position").cast(pl.Int64).alias("last_diff_rank")
        )
    return weekly, summary


def print_table(summary: pl.DataFrame, pos: str, last: int | None) -> None:
    sub = summary.filter(pl.col("position") == pos).sort("diff_rank", nulls_last=True)
    who = "Offense" if pos == "DST" else "Defense"
    head = f"{who:<8}{'G':>3}{'FPA':>8}{'FPA/G':>8}{'RawRk':>6}{'OppAvg':>8}{'+/-':>8}{'+/-Rk':>6}"
    if last:
        head += f"  |{f'L{last}/G':>8}{f'L{last}Opp':>8}{f'L{last}+/-':>8}{f'L{last}Rk':>6}"
    note = "points opposing D/STs scored against each offense" if pos == "DST" else "fantasy points allowed"
    print(f"\n=== {pos}: {note} (sorted by season +/- Rk; 1 = best matchup) ===")
    print(head)

    def f(v, spec):
        return "-" if v is None else format(v, spec)

    for r in sub.iter_rows(named=True):
        line = (f"{r['team']:<8}{r['games']:>3}{r['total']:>8.1f}{r['per_game']:>8.2f}{r['rank']:>6}"
                f"{f(r['opp_avg'], '.2f'):>8}{f(r['diff'], '+.1f'):>8}{f(r['diff_rank'], 'd'):>6}")
        if last:
            line += (f"  |{r['last_n']:>8.2f}{f(r['last_opp_avg'], '.2f'):>8}"
                     f"{f(r['last_diff'], '+.1f'):>8}{f(r['last_diff_rank'], 'd'):>6}")
        print(line)


_HTML_CSS = """
<style>
.fpa-wrap{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;margin:8px 0 18px}
.fpa-wrap h3{margin:14px 0 2px;font-size:16px;color:inherit}
.fpa-wrap p{margin:0 0 6px;font-size:12px;color:#57606a}
.fpa-table{border-collapse:collapse;font-size:13px;background:#fff}
.fpa-table th,.fpa-table td{padding:4px 9px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap;color:#1f2328}
.fpa-table th{background:#f3f4f6;font-size:11px;text-transform:uppercase;letter-spacing:.03em;cursor:pointer;user-select:none}
.fpa-table th:hover{background:#e5e7eb}
.fpa-table td.team{text-align:left;font-weight:600}
</style>
<script>
function fpaSort(th){
  var table=th.closest("table"), idx=th.cellIndex, body=table.tBodies[0];
  var dir=th.dataset.dir==="asc"?"desc":"asc";
  Array.prototype.forEach.call(table.tHead.rows[0].cells,function(c){
    c.dataset.dir=""; c.textContent=c.textContent.replace(/ [\\u25B2\\u25BC]$/,"");
  });
  th.dataset.dir=dir; th.textContent+=dir==="asc"?" \\u25B2":" \\u25BC";
  var rows=Array.prototype.slice.call(body.rows);
  rows.sort(function(a,b){
    var x=a.cells[idx].dataset.v, y=b.cells[idx].dataset.v;
    var nx=parseFloat(x), ny=parseFloat(y);
    if(isNaN(nx)&&!isNaN(ny))return 1;
    if(!isNaN(nx)&&isNaN(ny))return -1;
    var cmp=(isNaN(nx)||isNaN(ny))?String(x).localeCompare(String(y)):nx-ny;
    return dir==="asc"?cmp:-cmp;
  });
  rows.forEach(function(r){body.appendChild(r);});
}
</script>
"""


def _heat(v, lo, hi, center=None):
    """Background color: green = good matchup (more allowed), red = tough. With
    `center`, values above it shade green and below it red (diverging)."""
    if v is None or lo is None or hi is None or hi == lo:
        return ""
    if center is None:
        t = (v - lo) / (hi - lo)  # 0 = fewest allowed .. 1 = most
        strength, good = abs(t - 0.5) * 2, t >= 0.5
    else:
        span = max(abs(hi - center), abs(lo - center)) or 1
        t = (v - center) / span
        strength, good = min(abs(t), 1.0), t >= 0
    rgb = "46,160,67" if good else "207,34,46"
    return f' style="background:rgba({rgb},{0.08 + 0.42 * strength:.2f})"'


def html_report(summary: pl.DataFrame, positions: list[str], last: int | None) -> str:
    """Sortable, color-coded HTML tables (one per position), best matchup
    (+/- Rk 1) first. Click a header to re-sort. Green = better matchup."""
    parts = [_HTML_CSS, '<div class="fpa-wrap">']
    for pos in positions:
        sub = summary.filter(pl.col("position") == pos).sort("diff_rank", nulls_last=True)  # default: best matchup (+/- Rk 1) first
        if sub.is_empty():
            continue
        who = "Offense" if pos == "DST" else "Defense"
        cols = [
            (who, "team", "text", "{}"), ("G", "games", None, "{:d}"),
            ("FPA", "total", "heat", "{:.1f}"), ("FPA/G", "per_game", "heat", "{:.2f}"), ("Raw Rk", "rank", None, "{:d}"),
        ]
        cols += [
            ("Opp Avg", "opp_avg", None, "{:.2f}"),
            ("+/-", "diff", "diff", "{:+.1f}"), ("+/- Rk", "diff_rank", None, "{:d}"),
        ]
        if last:
            cols += [
                (f"L{last}/G", "last_n", "heat", "{:.2f}"), (f"L{last} Opp Avg", "last_opp_avg", None, "{:.2f}"),
                (f"L{last} +/-", "last_diff", "diff", "{:+.1f}"), (f"L{last} +/- Rk", "last_diff_rank", None, "{:d}"),
            ]
        rng = {}
        for _, key, kind, _ in cols:
            if kind in ("heat", "diff"):
                vals = [v for v in sub[key].to_list() if v is not None]
                rng[key] = (min(vals), max(vals)) if vals else (None, None)
        note = "points opposing D/STs scored against each offense" if pos == "DST" else "fantasy points allowed to the position"
        parts.append(
            f"<h3>{pos}</h3><p>{note}. Raw Rk = rank by FPA/G alone (1 = most allowed). Opp Avg = what the average opponent faced scores at the "
            "position in its other games; +/- = FPA/G &minus; Opp Avg (+3.2 = gives up 3.2 more per game than those offenses normally score). "
            "Sorted by season +/- Rk; the L-columns repeat it for just the most recent games. Click a header to re-sort.</p>"
        )
        parts.append('<table class="fpa-table"><thead><tr>' + "".join(
            f'<th onclick="fpaSort(this)"' + (' data-dir="asc"' if key == "diff_rank" else "") + f">{label}" + (" \u25B2" if key == "diff_rank" else "") + "</th>"
            for label, key, *_ in cols
        ) + "</tr></thead><tbody>")
        for r in sub.iter_rows(named=True):
            cells = []
            for label, key, kind, fmt in cols:
                v = r[key]
                shown = "-" if v is None else fmt.format(v)
                dv = "" if v is None else v
                style = ""
                if kind == "heat":
                    style = _heat(v, *rng[key])
                elif kind == "diff":
                    style = _heat(v, *rng[key], center=0.0)
                cls = ' class="team"' if kind == "text" else ""
                cells.append(f'<td{cls} data-v="{dv}"{style}>{shown}</td>')
            parts.append("<tr>" + "".join(cells) + "</tr>")
        parts.append("</tbody></table>")
    parts.append("</div>")
    return "".join(parts)


def plot_team_position(weekly: pl.DataFrame, team: str, pos: str, path: str | None = None, show: bool = False):
    """Line chart: points the team allowed each week vs. the average of the
    opponent it faced (that opponent's other games). Needs matplotlib."""
    import matplotlib.pyplot as plt

    g = weekly.filter((pl.col("team") == team) & (pl.col("position") == pos)).sort("week")
    if g.is_empty():
        raise SystemExit(f"No {pos} data for {team}.")
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.plot(g["week"], g["points"], marker="o", linewidth=2, label=f"{team} {pos} points allowed")
    ax.plot(g["week"], g["opp_avg"], marker="s", linewidth=2, linestyle="--", label="Opponent's season avg (other games)")
    for w, o in zip(g["week"], g["opponent"]):
        ax.annotate(o, (w, g.filter(pl.col("week") == w)["points"][0]), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    ax.set_xlabel("Week")
    ax.set_ylabel("Fantasy points")
    ax.set_xticks(list(g["week"]))
    ax.set_title(f"{team} vs {pos}: points allowed each week vs. what that opponent usually scores")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    if path:
        fig.savefig(path, dpi=150)
    if show:
        plt.show()
        plt.close(fig)  # already drawn; don't let a notebook draw the returned figure a second time
    return fig


def default_season() -> int:
    today = date.today()
    return today.year if today.month >= 9 else today.year - 1  # Jan-Aug: the season that just finished


def resolve_scoring(ppr: float, league_id: int | None, season: int, overrides: dict[str, float]) -> list[dict]:
    items = fetch_league_items(league_id, season) if league_id else espn_default_items(ppr)
    return apply_overrides(items, overrides)


def main() -> int:
    ap = argparse.ArgumentParser(description="Fantasy points allowed by position, from nflverse data.")
    ap.add_argument("--season", type=int, default=default_season())
    ap.add_argument("--ppr", type=float, default=0.5, choices=[0.0, 0.5, 1.0],
                    help="ESPN default template: 0 (standard), 0.5 (half PPR, default) or 1 (full PPR)")
    ap.add_argument("--league-id", type=int, help="use this PUBLIC ESPN league's exact scoring (overrides --ppr)")
    ap.add_argument("--set", action="append", default=[], metavar="ITEM=POINTS",
                    help="override a scoring item by ESPN abbreviation, e.g. --set PY=0.05 --set REC=1 (repeatable)")
    ap.add_argument("--positions", default=",".join(POSITIONS), help=f"comma list from {','.join(POSITIONS)}")
    ap.add_argument("--last", type=int, metavar="N", help="also show each team's average over its last N games")
    ap.add_argument("--chart", metavar="TEAM:POS", help="save a weekly line chart, e.g. DET:WR (needs matplotlib)")
    ap.add_argument("--out", metavar="DIR", help="write fpa_summary.csv / fpa_weekly.csv (and the chart) here")
    ap.add_argument("--html", metavar="FILE", help="write a sortable, color-coded HTML report of the tables (open it in a browser)")
    ap.add_argument("--show-scoring", action="store_true", help="print the scoring in use and exit")
    a = ap.parse_args()

    positions = [p.strip().upper() for p in a.positions.split(",") if p.strip()]
    bad = [p for p in positions if p not in POSITIONS]
    if bad:
        ap.error(f"unsupported position(s): {', '.join(bad)} (choose from {', '.join(POSITIONS)})")
    overrides = {}
    for s in a.set:
        name, _, val = s.partition("=")
        try:
            overrides[name.strip().upper()] = float(val)
        except ValueError:
            ap.error(f"--set expects ITEM=NUMBER, got {s!r}")

    items = resolve_scoring(a.ppr, a.league_id, a.season, overrides)
    source = f"ESPN league {a.league_id}" if a.league_id else {0.0: "ESPN default, standard", 0.5: "ESPN default, half PPR", 1.0: "ESPN default, full PPR"}[a.ppr]
    print(f"Scoring: {source}" + (f" + overrides {overrides}" if overrides else ""))
    if a.show_scoring:
        print("\n".join(describe_scoring(items)))
        return 0

    print(f"Loading {a.season} stats from nflverse...", file=sys.stderr)
    try:
        players, teams, sched = load_frames(a.season)
    except Exception as exc:  # network trouble, or that season isn't published yet
        print(f"Could not load {a.season} data: {exc}", file=sys.stderr)
        return 1
    chart_pos = a.chart.partition(":")[2].upper() if a.chart else None
    if chart_pos and chart_pos not in POSITIONS:
        ap.error(f"--chart expects TEAM:POS with POS one of {', '.join(POSITIONS)}")
    points = collect_points(items, sorted(set(positions) | ({chart_pos} if chart_pos else set())), players, teams, sched)
    if points.is_empty():
        print(f"No regular-season data for {a.season} yet.", file=sys.stderr)
        return 1
    weekly, summary = build_tables(points, a.last)
    weeks = sorted(weekly["week"].unique().to_list())
    print(f"{a.season} regular season, weeks {weeks[0]}-{weeks[-1]}.")
    for pos in positions:
        print_table(summary, pos, a.last)

    if a.html:
        Path(a.html).write_text(
            "<!doctype html><meta charset='utf-8'><title>Fantasy points allowed</title><body style='background:#fff'>"
            + html_report(summary, positions, a.last),
            encoding="utf-8",
        )
        print(f"\nWrote {a.html} - open it in a browser")
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
        summary.sort(["position", "rank"]).write_csv(out / "fpa_summary.csv")
        weekly.write_csv(out / "fpa_weekly.csv")
        print(f"\nWrote {out / 'fpa_summary.csv'} and {out / 'fpa_weekly.csv'}")
    if a.chart:
        team, _, pos = a.chart.partition(":")
        path = str((out or Path(".")) / f"fpa_{team.upper()}_{pos.upper()}.png")
        try:
            plot_team_position(weekly, team.upper(), pos.upper(), path)
            print(f"Wrote {path}")
        except ImportError:
            print("Charts need matplotlib: pip install matplotlib", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
