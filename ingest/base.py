"""Shared dataclasses and the LeagueClient protocol every platform adapter implements."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

# Canonical display order for player positions, shared by the pipeline output
# and the frontend's docs/js/colors.js POSITION_ORDER (keep them in sync).
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DST"]


def sort_positions(positions) -> list[str]:
    return sorted(positions, key=lambda p: POSITION_ORDER.index(p) if p in POSITION_ORDER else 99)


@dataclass
class LeagueSettings:
    name: str
    season: int
    current_week: int
    reg_season_count: int
    final_week: int  # max matchup period (regular season + playoffs)
    playoff_team_count: int
    seed_tie_rule: str
    tie_rule: str
    divisions: dict[int, str]
    slots: dict[str, int]  # non-zero starting slots only, excludes BE/IR
    slot_eligibility: dict[str, set[str]]  # slot label -> eligible canonical positions
    scoring_items: list[dict]  # [{id, abbr, points}]
    positions: list[str]  # canonical positions that can fill any starting slot


@dataclass
class RosterPlayer:
    espn_id: int
    name: str
    position: str  # canonical: QB RB WR TE K DST
    nfl_team: str  # canonical nflverse abbrev
    fantasy_team_id: int | None
    lineup_slot: str | None
    eligible_slots: list[str] = field(default_factory=list)
    injury_status: str = "ACTIVE"
    injured: bool = False
    percent_owned: float = 0.0
    percent_owned_delta: float | None = None
    percent_started: float = 0.0
    espn_projected_total: float = 0.0
    # None means ESPN hasn't published a week-specific projection for this
    # player (e.g. a deep free agent) - distinct from an explicit 0.0
    # (e.g. a correctly-zeroed OUT player), so callers can fall back to
    # their own estimate only in the former case.
    espn_projected_week: float | None = None


@dataclass
class PastLineupEntry:
    """One roster spot in a team's real, ESPN-set lineup for an already-played
    week (see LeagueClient.get_past_lineups) - name/position/nfl_team are
    carried alongside espn_id purely to help id resolution for a player who's
    since been dropped and isn't in the current roster/free-agent universe."""
    espn_id: int
    name: str
    position: str
    nfl_team: str
    lineup_slot: str
    points: float


@dataclass
class Matchup:
    week: int
    home_team_id: int
    away_team_id: int
    home_score: float | None
    away_score: float | None
    played: bool


@dataclass
class FantasyTeam:
    team_id: int
    team_name: str
    manager: str
    abbrev: str
    division_id: int
    wins: int
    losses: int
    ties: int
    points_for: float
    points_against: float
    roster: list[RosterPlayer] = field(default_factory=list)


class LeagueClient(Protocol):
    def get_settings(self) -> LeagueSettings: ...

    def get_teams(self) -> list[FantasyTeam]: ...

    def get_matchups(self) -> list[Matchup]: ...

    def get_free_agents(self, position: str, size: int) -> list[RosterPlayer]: ...

    def get_past_lineups(self, weeks: list[int]) -> dict[tuple[int, int], list[PastLineupEntry]]: ...

    def get_live_week_player_status(self, week: int) -> dict[int, tuple[float, bool]]: ...

    def get_future_espn_projections(self, weeks: list[int]) -> dict[int, dict[int, float]]: ...
