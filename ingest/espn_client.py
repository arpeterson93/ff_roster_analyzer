"""ESPN adapter implementing ingest.base.LeagueClient over espn_api."""
from __future__ import annotations

import json
import os

from espn_api.football import League as EspnLeague

from ingest import nfl_data as nd
from ingest.base import FantasyTeam, LeagueSettings, Matchup, RosterPlayer, sort_positions

_NON_STARTING_SLOTS = {"BE", "IR", "", "IR", "Rookie"}

# Static per ESPN's own slot vocabulary (espn_api.football.constant.POSITION_MAP).
# Composite labels ("RB/WR", "RB/WR/TE") are eligible for exactly the positions
# named in the label; "OP" is a full superflex slot; "D/ST" is its own position.
_SLOT_ELIGIBILITY = {
    "QB": {"QB"},
    "TQB": {"QB"},
    "RB": {"RB"},
    "RB/WR": {"RB", "WR"},
    "WR": {"WR"},
    "WR/TE": {"WR", "TE"},
    "TE": {"TE"},
    "OP": {"QB", "RB", "WR", "TE"},
    "D/ST": {"DST"},
    "K": {"K"},
    "RB/WR/TE": {"RB", "WR", "TE"},
}

_ESPN_POS_TO_CANON = {"D/ST": "DST"}


def _canon_pos(pos: str) -> str:
    return _ESPN_POS_TO_CANON.get(pos, pos)


_FA_SIZE_BY_POS = {"QB": 40, "RB": 60, "WR": 60, "TE": 40, "K": 32, "DST": 32}


class EspnClient:
    def __init__(self, league_id: int, season: int, credentials: dict[str, str] | None = None):
        espn_s2 = swid = None
        if credentials:
            espn_s2 = os.environ.get(credentials["espn_s2"])
            swid = os.environ.get(credentials["swid"])
        self._league = EspnLeague(league_id=league_id, year=season, espn_s2=espn_s2, swid=swid)
        self._ownership_delta_cache: dict[int, float] | None = None

    def get_settings(self) -> LeagueSettings:
        s = self._league.settings
        slots = {
            label: count
            for label, count in s.position_slot_counts.items()
            if label not in _NON_STARTING_SLOTS and count > 0
        }
        slot_eligibility = {}
        for label in slots:
            if label not in _SLOT_ELIGIBILITY:
                raise ValueError(f"unknown ESPN roster slot {label!r}; add it to _SLOT_ELIGIBILITY")
            slot_eligibility[label] = _SLOT_ELIGIBILITY[label]
        positions = sort_positions({pos for elig in slot_eligibility.values() for pos in elig})

        scoring_items = [
            {"id": item["id"], "abbr": item["abbr"], "points": item["points"]} for item in s.scoring_format
        ]

        return LeagueSettings(
            name=s.name,
            season=self._league.year,
            current_week=self._league.current_week,
            reg_season_count=s.reg_season_count,
            final_week=max(int(k) for k in s.matchup_periods),
            playoff_team_count=s.playoff_team_count,
            seed_tie_rule=s.playoff_seed_tie_rule,
            tie_rule=s.tie_rule,
            divisions=dict(s.division_map),
            slots=slots,
            slot_eligibility=slot_eligibility,
            scoring_items=scoring_items,
            positions=positions,
        )

    def _ownership_deltas(self) -> dict[int, float]:
        """ESPN's own {espn_id: percentChange} - the same "% change" number
        shown on their site, straight from the raw API (espn_api's Player/
        BoxPlayer wrappers don't expose it, so this bypasses them). Fetched
        once per client and cached; ~1000+ players in a single fast request."""
        if self._ownership_delta_cache is None:
            filters = {"players": {"limit": 3000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
            headers = {"x-fantasy-filter": json.dumps(filters)}
            data = self._league.espn_request.league_get(params={"view": "kona_player_info"}, headers=headers)
            self._ownership_delta_cache = {
                row["player"]["id"]: row["player"]["ownership"]["percentChange"]
                for row in data.get("players", [])
                if row.get("player", {}).get("ownership", {}).get("percentChange") is not None
            }
        return self._ownership_delta_cache

    def _week_projected_points(self, p, week: int) -> float:
        """The CURRENT WEEK's specific projection - never projected_avg_points
        (a season-long average that doesn't reflect this week's injury status,
        bye, or matchup; e.g. an OUT player's avg stays nonzero even though
        their actual week-N projection correctly drops to ~0)."""
        week_stats = (getattr(p, "stats", None) or {}).get(week)
        if week_stats and week_stats.get("projected_points") is not None:
            return float(week_stats["projected_points"])
        # BoxPlayer (free agents, already queried for a specific week) carries
        # the value directly as a top-level attribute instead of via .stats.
        direct = getattr(p, "projected_points", None)
        return float(direct) if direct is not None else 0.0

    def _roster_player(self, p, fantasy_team_id: int | None) -> RosterPlayer:
        pos = _canon_pos(p.position)
        return RosterPlayer(
            espn_id=p.playerId,
            name=p.name,
            position=pos,
            nfl_team=nd.normalize_team(p.proTeam),
            fantasy_team_id=fantasy_team_id,
            lineup_slot=getattr(p, "lineupSlot", None) or getattr(p, "slot_position", None),
            eligible_slots=list(getattr(p, "eligibleSlots", []) or []),
            injury_status=p.injuryStatus or "ACTIVE",
            injured=bool(p.injured),
            percent_owned=float(p.percent_owned or 0.0),
            percent_owned_delta=self._ownership_deltas().get(p.playerId),
            espn_projected_total=float(p.projected_total_points or 0.0),
            espn_projected_week=self._week_projected_points(p, self._league.current_week),
        )

    def get_teams(self) -> list[FantasyTeam]:
        teams = []
        for t in self._league.teams:
            manager = t.owners[0].get("firstName", t.team_name) if t.owners else t.team_name
            teams.append(
                FantasyTeam(
                    team_id=t.team_id,
                    team_name=t.team_name,
                    manager=manager,
                    abbrev=t.team_abbrev,
                    division_id=t.division_id,
                    wins=t.wins,
                    losses=t.losses,
                    ties=t.ties,
                    points_for=t.points_for,
                    points_against=t.points_against,
                    roster=[self._roster_player(p, t.team_id) for p in t.roster],
                )
            )
        return teams

    def get_matchups(self) -> list[Matchup]:
        settings = self.get_settings()
        matchups: list[Matchup] = []
        for week in range(1, settings.final_week + 1):
            for m in self._league.scoreboard(week=week):
                if m.home_team is None or m.away_team is None:
                    continue  # bye in an odd-team playoff bracket
                played = week < self._league.current_week
                matchups.append(
                    Matchup(
                        week=week,
                        home_team_id=m.home_team.team_id,
                        away_team_id=m.away_team.team_id,
                        home_score=m.home_score if played else None,
                        away_score=m.away_score if played else None,
                        played=played,
                    )
                )
        return matchups

    def get_free_agents(self, position: str, size: int | None = None) -> list[RosterPlayer]:
        espn_pos = "D/ST" if position == "DST" else position
        size = size or _FA_SIZE_BY_POS.get(position, 50)
        players = self._league.free_agents(size=size, position=espn_pos)
        return [self._roster_player(p, None) for p in players]
