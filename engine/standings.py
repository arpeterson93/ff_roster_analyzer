"""Monte Carlo playoff-odds simulation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TeamState:
    team_id: int
    division_id: int
    wins: float
    losses: float
    ties: float
    points_for: float


@dataclass
class RemainingMatchup:
    week: int
    home_team_id: int
    away_team_id: int


def simulate_playoffs(
    teams: list[TeamState],
    remaining_matchups: list[RemainingMatchup],
    team_week_mean: dict[tuple[int, int], float],
    team_week_sd: dict[tuple[int, int], float],
    *,
    iterations: int,
    seed: int,
    playoff_team_count: int,
    division_winners_first: bool,
    bye_seeds: int = 2,
) -> dict[int, dict]:
    """Ties count as 0.5 win (tie_rule=NONE); tiebreak is total points scored,
    per the verified league settings (playoff_seed_tie_rule=TOTAL_POINTS_SCORED)."""
    rng = np.random.default_rng(seed)
    team_ids = [t.team_id for t in teams]
    n = len(team_ids)
    idx = {tid: i for i, tid in enumerate(team_ids)}

    wins = np.tile(np.array([t.wins + 0.5 * t.ties for t in teams], dtype=float), (iterations, 1))
    pf = np.tile(np.array([t.points_for for t in teams], dtype=float), (iterations, 1))

    matchups_by_week: dict[int, list[RemainingMatchup]] = {}
    for m in remaining_matchups:
        matchups_by_week.setdefault(m.week, []).append(m)

    for week in sorted(matchups_by_week):
        for m in matchups_by_week[week]:
            h_mean = team_week_mean.get((m.home_team_id, week), 0.0)
            h_sd = max(team_week_sd.get((m.home_team_id, week), 0.0), 1e-9)
            a_mean = team_week_mean.get((m.away_team_id, week), 0.0)
            a_sd = max(team_week_sd.get((m.away_team_id, week), 0.0), 1e-9)

            h_score = rng.normal(h_mean, h_sd, size=iterations)
            a_score = rng.normal(a_mean, a_sd, size=iterations)

            hi, ai = idx[m.home_team_id], idx[m.away_team_id]
            pf[:, hi] += h_score
            pf[:, ai] += a_score

            home_wins = h_score > a_score
            away_wins = a_score > h_score
            tie = ~home_wins & ~away_wins
            wins[:, hi] += home_wins * 1.0 + tie * 0.5
            wins[:, ai] += away_wins * 1.0 + tie * 0.5

    division_of = [t.division_id for t in teams]

    made_playoffs = np.zeros((iterations, n), dtype=bool)
    seed_of = np.zeros((iterations, n), dtype=int)
    division_winner = np.zeros((iterations, n), dtype=bool)

    for it in range(iterations):
        w, p = wins[it], pf[it]
        remaining = list(range(n))
        seeds: list[int] = []
        div_winners: list[int] = []

        if division_winners_first:
            for d in sorted(set(division_of)):
                members = [i for i in remaining if division_of[i] == d]
                if members:
                    div_winners.append(min(members, key=lambda i: (-w[i], -p[i])))
            div_winners.sort(key=lambda i: (-w[i], -p[i]))
            seeds.extend(div_winners)
            remaining = [i for i in remaining if i not in div_winners]

        remaining.sort(key=lambda i: (-w[i], -p[i]))
        seeds.extend(remaining)
        seeds = seeds[:playoff_team_count]

        for s, team_i in enumerate(seeds, start=1):
            made_playoffs[it, team_i] = True
            seed_of[it, team_i] = s
        for team_i in div_winners:
            division_winner[it, team_i] = True

    result: dict[int, dict] = {}
    for tid in team_ids:
        i = idx[tid]
        seed_probs = {s: float(np.mean(seed_of[:, i] == s)) for s in range(1, playoff_team_count + 1)}
        result[tid] = {
            "expected_wins": float(np.mean(wins[:, i])),
            "playoff_odds": float(np.mean(made_playoffs[:, i])),
            "bye_odds": float(np.mean(np.isin(seed_of[:, i], list(range(1, bye_seeds + 1))))),
            "division_win_odds": float(np.mean(division_winner[:, i])),
            "seed_probs": seed_probs,
            "iterations": iterations,
        }
    return result
