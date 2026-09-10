"""Monte Carlo playoff-odds simulation."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def matchup_win_probability(home_mean: float, home_sd: float, away_mean: float, away_sd: float) -> float:
    """P(home team's weekly total > away team's), modeling each team's total
    as Normal(mean, sd) - the same approximation simulate_playoffs draws from
    - and their difference as Normal(home_mean - away_mean, sqrt(home_sd**2 +
    away_sd**2)). Closed-form since this is one matchup rather than a whole
    season needing the Monte Carlo sim above. sd=0 for a team with every
    starter's game already final collapses to a step function (the outcome
    is no longer uncertain)."""
    diff_mean = home_mean - away_mean
    diff_sd = math.sqrt(home_sd**2 + away_sd**2)
    if diff_sd <= 0:
        return 1.0 if diff_mean > 0 else (0.0 if diff_mean < 0 else 0.5)
    return 0.5 * (1 + math.erf(diff_mean / (diff_sd * math.sqrt(2))))


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


# Every stat a seed's or division's tiebreak dropdown chain can be built
# from. "wins" always available; the other three need points_against /
# played-matchup data the Monte Carlo sim above doesn't track.
TIEBREAK_CRITERIA = ("wins", "points_for", "points_against", "head_to_head")


@dataclass
class SeedTeamState:
    """Real (not simulated) current team state for compute_current_seeds -
    TeamState plus points_against, which the probabilistic sim never needed."""
    team_id: int
    division_id: int
    wins: float
    losses: float
    ties: float
    points_for: float
    points_against: float


@dataclass
class PlayedMatchup:
    home_team_id: int
    away_team_id: int
    home_score: float
    away_score: float


@dataclass
class SeedConfig:
    """One seed slot's configured rule. division_priority=True means this
    seed is filled from the queue of division winners (ranked against each
    other by the shared division tiebreak chain) before any wildcard
    tiebreak is consulted; tiebreak_order is only used for a wildcard seed,
    or as a division-priority seed's fallback once the division-winner queue
    runs dry (e.g. more division-priority seeds configured than divisions)."""
    division_priority: bool
    tiebreak_order: list[str]


def _h2h_win_pct(team_id: int, opponent_ids: set[int], played: list[PlayedMatchup]) -> float:
    """This team's win% (ties = 0.5) in games played against `opponent_ids`
    only - the standard "mini standings within the tied group" approach for
    a head-to-head tiebreak among more than two teams."""
    wins = losses = ties = 0.0
    for m in played:
        if team_id == m.home_team_id and m.away_team_id in opponent_ids:
            my, opp = m.home_score, m.away_score
        elif team_id == m.away_team_id and m.home_team_id in opponent_ids:
            my, opp = m.away_score, m.home_score
        else:
            continue
        if my > opp:
            wins += 1
        elif my < opp:
            losses += 1
        else:
            ties += 1
    total = wins + losses + ties
    return (wins + 0.5 * ties) / total if total else 0.0


def _tiebreak_sort_key(order: list[str], pool_ids: set[int], played: list[PlayedMatchup]):
    def key(team: SeedTeamState) -> tuple:
        parts = []
        for criterion in order:
            if criterion == "wins":
                parts.append(-(team.wins + 0.5 * team.ties))
            elif criterion == "points_for":
                parts.append(-team.points_for)
            elif criterion == "points_against":
                parts.append(-team.points_against)  # higher PA is preferred (a tougher-schedule signal), per league convention - negate to sort descending like wins/PF
            elif criterion == "head_to_head":
                parts.append(-_h2h_win_pct(team.team_id, pool_ids - {team.team_id}, played))
        parts.append(team.team_id)  # deterministic final fallback if every configured criterion ties
        return tuple(parts)

    return key


def compute_current_seeds(
    teams: list[SeedTeamState],
    played_matchups: list[PlayedMatchup],
    playoff_team_count: int,
    division_tiebreak_order: list[str],
    seed_configs: dict[int, SeedConfig],
) -> dict[int, int]:
    """{team_id: seed} for the REAL current standings (not a simulation) -
    drives the Standings page's Seed column. Two passes: first rank each
    division's own members to find its winner, then rank the winners against
    each other (both using division_tiebreak_order); then walk seeds 1..N,
    each either pulling the next-best division winner or re-ranking every
    not-yet-seeded team by that seed's own configured tiebreak chain."""
    by_division: dict[int, list[SeedTeamState]] = {}
    for t in teams:
        by_division.setdefault(t.division_id, []).append(t)

    division_winners = []
    for members in by_division.values():
        ids = {t.team_id for t in members}
        division_winners.append(sorted(members, key=_tiebreak_sort_key(division_tiebreak_order, ids, played_matchups))[0])
    dw_ids = {t.team_id for t in division_winners}
    dw_queue = sorted(division_winners, key=_tiebreak_sort_key(division_tiebreak_order, dw_ids, played_matchups))

    seeds: dict[int, int] = {}
    assigned: set[int] = set()
    for n in range(1, playoff_team_count + 1):
        cfg = seed_configs.get(n)
        while dw_queue and dw_queue[0].team_id in assigned:
            dw_queue.pop(0)
        if cfg and cfg.division_priority and dw_queue:
            chosen = dw_queue.pop(0)
        else:
            pool = [t for t in teams if t.team_id not in assigned]
            if not pool:
                break
            order = cfg.tiebreak_order if cfg and cfg.tiebreak_order else list(division_tiebreak_order)
            pool_ids = {t.team_id for t in pool}
            chosen = sorted(pool, key=_tiebreak_sort_key(order, pool_ids, played_matchups))[0]
        seeds[chosen.team_id] = n
        assigned.add(chosen.team_id)
    return seeds


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
