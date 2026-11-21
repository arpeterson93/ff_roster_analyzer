"""docs/js/playoffodds.js (the Trade Calculator's playoff-odds sim) must agree
with engine/standings.py's simulate_playoffs on the same league state. The two
use different RNGs, so agreement is statistical (tolerances below), not exact."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.standings import RemainingMatchup, TeamState, simulate_playoffs
from ingest.settings_sheet import parse_seeding_config

ROOT = Path(__file__).parent.parent
RUNNER = ROOT / "tests" / "js" / "run_playoff_odds.js"
_PLAYWRIGHT_NODE = Path.home() / "AppData/Local/Programs/Python/Python310/Lib/site-packages/playwright/driver/node.exe"
NODE = shutil.which("node") or (str(_PLAYWRIGHT_NODE) if _PLAYWRIGHT_NODE.exists() else None)

SEEDING_RAW = {
    "division_tiebreak_1": "wins", "division_tiebreak_2": "points_for", "division_tiebreak_3": "points_against",
    "seed_1_division_priority": "TRUE", "seed_2_division_priority": "TRUE", "seed_3_division_priority": "TRUE",
    **{f"seed_{n}_tiebreak_{i}": c for n in (1, 2, 3, 4, 5) for i, c in ((1, "wins"), (2, "points_for"), (3, "points_against"))},
    "seed_6_tiebreak_1": "points_for", "seed_6_tiebreak_2": "points_against",
}
DIVISIONS = {0: "A", 1: "B", 2: "C"}


def _league():
    """12 teams / 3 divisions, 3 weeks played, weeks 4-8 remaining (round-robin-ish pairings)."""
    ids = list(range(1, 13))
    wins = [3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 0, 0]
    teams = [
        TeamState(team_id=t, division_id=(t - 1) % 3, wins=wins[t - 1], losses=3 - wins[t - 1], ties=0,
                  points_for=330 + 12 * (12 - t) + 7 * (t % 3), points_against=300 + 5 * t)
        for t in ids
    ]
    remaining = []
    for week in range(4, 9):
        rot = ids[week % 12:] + ids[:week % 12]
        for k in range(6):
            remaining.append(RemainingMatchup(week=week, home_team_id=rot[k], away_team_id=rot[11 - k]))
    mean = {(t, w): 105 - 2.5 * t + (t * w) % 5 for t in ids for w in range(4, 9)}
    sd = {(t, w): 18.0 for t in ids for w in range(4, 9)}
    return teams, remaining, mean, sd


def _parts(teams, remaining, mean, sd):
    return {
        "meta": {"reg_season_count": 8, "current_week": 4, "playoff_team_count": 6,
                 "settings": {"division_winners_first": True, "seeding_raw": SEEDING_RAW}},
        "standings": [
            {"team_id": t.team_id, "wins": t.wins, "losses": t.losses, "ties": 0, "points_for": t.points_for,
             "points_against": t.points_against, "division": DIVISIONS[t.division_id]}
            for t in teams
        ],
        "schedule": [
            {"week": m.week, "home_team_id": m.home_team_id, "away_team_id": m.away_team_id, "played": False}
            for m in remaining
        ],
        "lineups": {
            str(t.team_id): {"weeks": {str(w): {"total": mean[(t.team_id, w)], "sd": sd[(t.team_id, w)]} for w in range(4, 9)}}
            for t in teams
        },
    }


def _py_odds(teams, remaining, mean, sd, iterations=60000):
    order, cfgs = parse_seeding_config(SEEDING_RAW)
    res = simulate_playoffs(
        teams, remaining, mean, sd, iterations=iterations, seed=99, playoff_team_count=6,
        division_winners_first=True, seed_configs=cfgs, division_tiebreak_order=order,
    )
    return {t: (r["playoff_odds"], r["bye_odds"]) for t, r in res.items()}


@pytest.mark.skipif(NODE is None, reason="no node binary available")
def test_js_odds_match_python_sim_and_trade_shift_is_directional(tmp_path):
    teams, remaining, mean, sd = _league()
    adjustments = {"5": {str(w): 6.0 for w in range(4, 9)}, "6": {str(w): -6.0 for w in range(4, 9)}}
    case = {"parts": _parts(teams, remaining, mean, sd), "iterations": 40000, "adjustments": adjustments}
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(RUNNER), str(case_path)], capture_output=True, text=True, check=True).stdout)

    py = _py_odds(teams, remaining, mean, sd)
    for t in teams:
        js_playoff = out["before"][str(t.team_id)]["playoff"]
        js_bye = out["before"][str(t.team_id)]["bye"]
        assert js_playoff == pytest.approx(py[t.team_id][0], abs=0.015), t.team_id
        assert js_bye == pytest.approx(py[t.team_id][1], abs=0.015), t.team_id

    # Same adjustment applied to the Python inputs -> same shifted odds.
    mean_adj = dict(mean)
    for w in range(4, 9):
        mean_adj[(5, w)] += 6.0
        mean_adj[(6, w)] -= 6.0
    py_after = _py_odds(teams, remaining, mean_adj, sd)
    for t in teams:
        assert out["after"][str(t.team_id)]["playoff"] == pytest.approx(py_after[t.team_id][0], abs=0.015), t.team_id

    # Direction: the boosted team improves, the weakened one declines (common random numbers -> clean signal).
    assert out["after"]["5"]["playoff"] > out["before"]["5"]["playoff"]
    assert out["after"]["6"]["playoff"] < out["before"]["6"]["playoff"]
    # Probabilities across the field still sum to the playoff field size.
    assert sum(v["playoff"] for v in out["after"].values()) == pytest.approx(6.0, abs=1e-6)


@pytest.mark.skipif(NODE is None, reason="no node binary available")
def test_js_mutual_trade_scoring_rules():
    out = json.loads(subprocess.run([NODE, str(ROOT / "tests" / "js" / "run_odds_score.js")], capture_output=True, text=True, check=True).stdout)
    c = out["cases"]
    assert c["both_gain"]["mutual"] and c["both_gain"]["score"] == pytest.approx(0.02)  # min of the two gains
    assert c["both_gain"]["rival"] == {"teamId": 3, "delta": pytest.approx(-0.03)}  # worst-hit other team
    assert not c["one_loses"]["mutual"]
    assert c["within_tolerance"]["mutual"]  # -0.2 pts is inside the half-point tolerance
    assert c["partner_locked_ok"]["mutual"] and c["partner_locked_ok"]["score"] == pytest.approx(0.1)  # locked side ignored for score
    assert not c["partner_locked_loses_points"]["mutual"]  # a locked side must not lose points
    assert c["both_locked"]["score"] is None  # -Infinity serialises to null: odds can't rank this trade
    assert not c["no_gain"]["mutual"]  # nobody gains
    # Any mutual trade outranks any non-mutual one.
    assert c["both_gain"]["rank"] > c["one_loses"]["rank"]
    # Diversity cap: the same incoming player appears at most twice; -Infinity entries dropped.
    assert out["picked"] == [5, 4, 2]
