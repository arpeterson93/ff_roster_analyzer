"""Monte Carlo playoff-odds simulation."""
from __future__ import annotations

import base64
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
    points_against: float = 0.0  # only the per-seed tiebreak chain reads it (see simulate_playoffs' seed_configs)


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
    drives the Standings page's Seed column, for every team (not just the
    playoff field). Two passes: first rank each division's own members to
    find its winner, then rank the winners against each other (both using
    division_tiebreak_order); then walk seeds 1..len(teams), each either
    pulling the next-best division winner or re-ranking every not-yet-seeded
    team by that seed's own configured tiebreak chain. Seeds beyond
    playoff_team_count (i.e. the non-playoff teams) reuse the last playoff
    seed's own configured criteria, since nothing is configured for them
    specifically."""
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
    last_cfg = seed_configs.get(playoff_team_count)
    for n in range(1, len(teams) + 1):
        cfg = seed_configs.get(n) if n <= playoff_team_count else last_cfg
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


def bracket_bye_count(playoff_team_count: int) -> int:
    """How many top seeds get a first-round bye in a standard single-
    elimination bracket. A real bracket's ROUND 1 needs a power-of-2 number
    of slots; with fewer real teams than that, the top seeds simply have no
    round-1 opponent to fill the extra slots with, so they sit out and wait
    for round 2 - e.g. 6 teams still needs an 8-slot bracket, so the top 2
    seeds (8 - 6) bye through round 1. Already a power of 2 (4, 8, 16...) or
    just 1 team -> 0 byes, nothing to skip."""
    if playoff_team_count <= 1:
        return 0
    bracket_size = 1
    while bracket_size < playoff_team_count:
        bracket_size *= 2
    return bracket_size - playoff_team_count


def bracket_rounds(playoff_team_count: int) -> int:
    """Number of single-elimination rounds for the playoff field (6 teams -> an
    8-slot bracket -> 3 rounds: quarterfinal, semifinal, championship)."""
    size, rounds = 1, 0
    while size < playoff_team_count:
        size *= 2
        rounds += 1
    return rounds


def _bracket_slots(playoff_team_count: int) -> list[int | None]:
    """Round-1 slots in bracket-tree order (adjacent slots meet, winners of
    adjacent pairs meet next): seed s sits across from seed size+1-s, and a
    seed above the field size is an empty slot (its opponent has a bye)."""
    size = 1
    while size < playoff_team_count:
        size *= 2
    order = [1]
    while len(order) < size:
        m = len(order) * 2
        order = [x for sd in order for x in (sd, m + 1 - sd)]
    return [sd if sd <= playoff_team_count else None for sd in order]


def play_bracket(seeds: list[int], scores: list[dict[int, float]], reseed: bool, fixed: list[dict[frozenset, int]] | None = None):
    """One simulated playoff bracket. `seeds` = team indices in seed order;
    `scores[r][team]` = that team's score in round r; `fixed[r]` = actual
    results already played ({frozenset({a, b}): winner}) that override the
    scores. Round 1 always follows the standard bracket (1 vs the last seed,
    and so on, byes for the top seeds when the field is short of a power of
    2). Later rounds either follow the bracket tree (reseed=False) or - the
    "reseed" rule some leagues use - pair the best remaining seed with the
    worst remaining, the next best with the next worst, and so on.
    Returns (finalists, champion), as team indices (or (None, None))."""
    if not seeds:
        return [], None
    seed_of = {t: k for k, t in enumerate(seeds)}
    slots = [None if sd is None else seeds[sd - 1] for sd in _bracket_slots(len(seeds))]
    alive = list(slots)
    rounds = bracket_rounds(len(seeds))
    finalists: list[int] = []
    for r in range(rounds):
        if r > 0 and reseed:
            teams = sorted((t for t in alive if t is not None), key=lambda t: seed_of[t])
            alive = [x for k in range(len(teams) // 2) for x in (teams[k], teams[len(teams) - 1 - k])]
        nxt = []
        for k in range(0, len(alive), 2):
            a, b = alive[k], alive[k + 1]
            if a is None or b is None:
                nxt.append(a if b is None else b)
                continue
            winner = fixed[r].get(frozenset((a, b))) if fixed and r < len(fixed) else None
            if winner is None:
                sa, sb = scores[r][a], scores[r][b]
                winner = a if (sa > sb or (sa == sb and seed_of[a] < seed_of[b])) else b
            nxt.append(winner)
        alive = nxt
        if r == rounds - 2:  # the two teams left going into the final
            finalists = [t for t in alive if t is not None]
        if rounds == 1:
            finalists = [t for t in slots if t is not None]
    return finalists, alive[0]


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
    bye_seeds: int | None = None,
    paths: dict | None = None,
    seed_configs: dict[int, "SeedConfig"] | None = None,
    division_tiebreak_order: list[str] | None = None,
    playoff_weeks: list[int] | None = None,
    playoff_reseed: bool = False,
    playoff_results: dict[tuple[int, frozenset], int] | None = None,
) -> dict[int, dict]:
    """playoff_weeks: the NFL week of each playoff round, in order (e.g. [15, 16, 17]
    for a 6-team field). When given, each simulated season's bracket is played out
    on the same per-week team mean/sd and the result adds `title_game_odds` (reach the
    championship game) and `title_odds` (win it) per team. playoff_reseed: pair the
    best remaining seed with the worst after round 1 instead of following a fixed
    bracket tree. playoff_results: playoff games already played, {(week,
    frozenset({team_a, team_b})): winner team_id}, which override the simulated score.

    paths: optional out-parameter. When a dict is passed it is filled with
    the raw per-iteration outcomes the Playoff Paths view needs ("team_ids",
    "base_wins", "games" as (week, home_id, away_id, home_won bool array) and
    "seed_of", an iterations x teams int array, 0 = missed the playoffs) -
    see encode_paths. Leaves the return value untouched.

    seed_configs / division_tiebreak_order: the league's per-seed seeding rules
    (the same SeedConfig compute_current_seeds uses for the real standings).
    When given, every simulated season is seeded by them - seed by seed, each
    either the next-best division winner or the best remaining team by that
    seed's own tiebreak chain (e.g. The O League's seed 6 = most points for
    among teams outside seeds 1-5, regardless of record). head_to_head cannot
    be evaluated per simulated season and is skipped if configured. Left None,
    the older fixed rule applies: division winners first (when
    division_winners_first), then everyone by wins and points scored.

    Ties count as 0.5 win (tie_rule=NONE); the default tiebreak is total points
    scored, per the verified league settings (playoff_seed_tie_rule=TOTAL_POINTS_SCORED).

    bye_seeds: how many top seeds the Bye% column credits with a first-round
    bye - defaults to bracket_bye_count(playoff_team_count) (the standard
    bracket rule) rather than a fixed number, so a league whose playoff
    field isn't 6 teams doesn't silently inherit O-League's own 2-bye
    answer. Still overridable by a caller (e.g. a league with a genuinely
    non-standard bracket)."""
    if bye_seeds is None:
        bye_seeds = bracket_bye_count(playoff_team_count)
    rng = np.random.default_rng(seed)
    team_ids = [t.team_id for t in teams]
    n = len(team_ids)
    idx = {tid: i for i, tid in enumerate(team_ids)}

    wins = np.tile(np.array([t.wins + 0.5 * t.ties for t in teams], dtype=float), (iterations, 1))
    pf = np.tile(np.array([t.points_for for t in teams], dtype=float), (iterations, 1))
    pa = np.tile(np.array([t.points_against for t in teams], dtype=float), (iterations, 1))

    matchups_by_week: dict[int, list[RemainingMatchup]] = {}
    for m in remaining_matchups:
        matchups_by_week.setdefault(m.week, []).append(m)

    game_outcomes: list[tuple[int, int, int, np.ndarray]] = []
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
            pa[:, hi] += a_score
            pa[:, ai] += h_score

            home_wins = h_score > a_score
            away_wins = a_score > h_score
            tie = ~home_wins & ~away_wins
            game_outcomes.append((week, m.home_team_id, m.away_team_id, home_wins))
            wins[:, hi] += home_wins * 1.0 + tie * 0.5
            wins[:, ai] += away_wins * 1.0 + tie * 0.5

    # Playoff-round scores are drawn AFTER every regular-season game so adding the
    # playoff rounds leaves the regular-season outcomes (and so every existing odds) untouched.
    round_scores: list[np.ndarray] = []
    for week in (playoff_weeks or [])[: bracket_rounds(playoff_team_count)]:
        known = {t: [team_week_mean[(t, w)] for w in sorted({k[1] for k in team_week_mean}) if (t, w) in team_week_mean] for t in team_ids}
        means = np.array([team_week_mean.get((t, week), float(np.mean(known[t])) if known[t] else 0.0) for t in team_ids], dtype=float)
        known_sd = [v for v in team_week_sd.values() if v]
        sds = np.array([max(team_week_sd.get((t, week), float(np.mean(known_sd)) if known_sd else 0.0), 1e-9) for t in team_ids], dtype=float)
        round_scores.append(rng.normal(means, sds, size=(iterations, n)))
    fixed_by_round: list[dict[frozenset, int]] = []
    for week in (playoff_weeks or [])[: len(round_scores)]:
        fixed_by_round.append({
            frozenset(idx[t] for t in pair): idx[winner]
            for (w, pair), winner in (playoff_results or {}).items()
            if w == week and all(t in idx for t in pair) and winner in idx
        })
    title_game = np.zeros((iterations, n), dtype=bool)
    champion = np.zeros((iterations, n), dtype=bool)

    division_of = [t.division_id for t in teams]

    made_playoffs = np.zeros((iterations, n), dtype=bool)
    seed_of = np.zeros((iterations, n), dtype=int)
    division_winner = np.zeros((iterations, n), dtype=bool)

    div_order = [c for c in (division_tiebreak_order or ["wins", "points_for"]) if c != "head_to_head"] or ["wins", "points_for"]
    division_ids = sorted(set(division_of))
    members_of = {d: [i for i in range(n) if division_of[i] == d] for d in division_ids}

    def seed_one_season(w, p, a) -> tuple[list[int], list[int]]:
        """(seeds in order, division winners) for one simulated season."""
        def key_for(order):
            def key(i):
                parts = []
                for c in order:
                    if c == "wins":
                        parts.append(-w[i])
                    elif c == "points_for":
                        parts.append(-p[i])
                    elif c == "points_against":
                        parts.append(-a[i])  # higher PA preferred, same convention as compute_current_seeds
                parts.append(i)
                return tuple(parts)
            return key

        if seed_configs is None:
            remaining = list(range(n))
            seeds: list[int] = []
            div_winners: list[int] = []
            if division_winners_first:
                for d in division_ids:
                    if members_of[d]:
                        div_winners.append(min(members_of[d], key=lambda i: (-w[i], -p[i])))
                div_winners.sort(key=lambda i: (-w[i], -p[i]))
                seeds.extend(div_winners)
                remaining = [i for i in remaining if i not in div_winners]
            remaining.sort(key=lambda i: (-w[i], -p[i]))
            seeds.extend(remaining)
            return seeds[:playoff_team_count], div_winners

        div_key = key_for(div_order)
        div_winners = [min(members_of[d], key=div_key) for d in division_ids if members_of[d]]
        dw_queue = sorted(div_winners, key=div_key)
        assigned: set[int] = set()
        seeds = []
        for seed_n in range(1, playoff_team_count + 1):
            cfg = seed_configs.get(seed_n)
            while dw_queue and dw_queue[0] in assigned:
                dw_queue.pop(0)
            if cfg and cfg.division_priority and dw_queue:
                chosen = dw_queue.pop(0)
            else:
                pool = [i for i in range(n) if i not in assigned]
                if not pool:
                    break
                order = [c for c in (cfg.tiebreak_order if cfg and cfg.tiebreak_order else div_order) if c != "head_to_head"] or div_order
                chosen = min(pool, key=key_for(order))
            seeds.append(chosen)
            assigned.add(chosen)
        return seeds, div_winners

    for it in range(iterations):
        seeds, div_winners = seed_one_season(wins[it], pf[it], pa[it])
        for s_, team_i in enumerate(seeds, start=1):
            made_playoffs[it, team_i] = True
            seed_of[it, team_i] = s_
        for team_i in div_winners:
            division_winner[it, team_i] = True
        if round_scores:
            finalists, champ = play_bracket(
                seeds, [{i: float(rs[it, i]) for i in seeds} for rs in round_scores], playoff_reseed, fixed_by_round,
            )
            for team_i in finalists:
                title_game[it, team_i] = True
            if champ is not None:
                champion[it, champ] = True

    if paths is not None:
        paths.update(
            team_ids=team_ids,
            base_wins=[t.wins + 0.5 * t.ties for t in teams],
            games=game_outcomes,
            seed_of=seed_of,
            pf=pf,
            base_pf=[t.points_for for t in teams],
            last_seed_tiebreak=(seed_configs.get(playoff_team_count).tiebreak_order if seed_configs and seed_configs.get(playoff_team_count) else None),
            iterations=iterations,
            playoff_team_count=playoff_team_count,
            bye_seeds=bye_seeds,
        )

    result: dict[int, dict] = {}
    for tid in team_ids:
        i = idx[tid]
        seed_probs = {s: float(np.mean(seed_of[:, i] == s)) for s in range(1, playoff_team_count + 1)}
        result[tid] = {
            "expected_wins": float(np.mean(wins[:, i])),
            "playoff_odds": float(np.mean(made_playoffs[:, i])),
            "bye_odds": float(np.mean(np.isin(seed_of[:, i], list(range(1, bye_seeds + 1))))),
            "division_win_odds": float(np.mean(division_winner[:, i])),
            "title_game_odds": float(np.mean(title_game[:, i])) if round_scores else None,
            "title_odds": float(np.mean(champion[:, i])) if round_scores else None,
            "seed_probs": seed_probs,
            "iterations": iterations,
        }
    return result


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(arr.tobytes()).decode("ascii")


def encode_paths(paths: dict) -> dict:
    """JSON-able form of simulate_playoffs' `paths` out-parameter, for the
    Standings page's Playoff Paths view (docs/js/paths.js decodes it).

    Per game: the home-won outcome of every iteration as a bitstring (numpy
    packbits, MSB first, base64); a tie counts as an away win, which is
    immaterial for continuous scores. Per team: one byte per iteration, its
    seed (0 = missed the playoffs). A team's final win total is NOT stored -
    the client rebuilds it from base_wins plus the game bits."""
    seed_of = paths["seed_of"]
    return {
        "iterations": paths["iterations"],
        "playoff_team_count": paths["playoff_team_count"],
        "bye_seeds": paths["bye_seeds"],
        "team_ids": paths["team_ids"],
        "base_wins": paths["base_wins"],
        "games": [
            {"week": week, "home": home, "away": away, "bits": _b64(np.packbits(home_won))}
            for week, home, away, home_won in paths["games"]
        ],
        "seeds": {str(tid): _b64(seed_of[:, i].astype(np.uint8)) for i, tid in enumerate(paths["team_ids"])},
        # Final points scored per team per sim, in tenths as little-endian uint16 (a season's PF is far below 6553.5).
        "pf": {str(tid): _b64(np.round(paths["pf"][:, i] * 10).astype("<u2")) for i, tid in enumerate(paths["team_ids"])},
        "base_pf": paths["base_pf"],
        "last_seed_tiebreak": paths.get("last_seed_tiebreak"),
    }
