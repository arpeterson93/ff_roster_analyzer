"""ESPN adapter implementing ingest.base.LeagueClient over espn_api."""
from __future__ import annotations

import json
import logging
import os

from espn_api.football import League as EspnLeague
from espn_api.football import Player as EspnPlayer

from ingest import nfl_data as nd
from ingest.base import FantasyTeam, LeagueSettings, Matchup, PastLineupEntry, RosterPlayer, sort_positions

logger = logging.getLogger(__name__)

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

# Every-position free-agent pool for get_future_espn_projections - one call
# covering everyone (see that method), so this needs to be roughly as deep
# as _FA_SIZE_BY_POS's per-position sizes summed (~264) rather than any one
# position's own cap.
_FUTURE_PROJECTIONS_FA_SIZE = 350


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

    def _week_projected_points(self, p, week: int) -> float | None:
        """The CURRENT WEEK's specific projection, or None if ESPN hasn't
        published one for this player - never projected_avg_points (a
        season-long average that doesn't reflect this week's injury status,
        bye, or matchup; e.g. an OUT player's avg stays nonzero even though
        their actual week-N projection correctly drops to ~0)."""
        week_stats = (getattr(p, "stats", None) or {}).get(week)
        if week_stats and week_stats.get("projected_points") is not None:
            return float(week_stats["projected_points"])
        # BoxPlayer (free agents, already queried for a specific week) carries
        # the value directly as a top-level attribute instead of via .stats.
        direct = getattr(p, "projected_points", None)
        return float(direct) if direct is not None else None

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
            percent_started=float(p.percent_started or 0.0),
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

    def get_past_lineups(self, weeks: list[int]) -> dict[tuple[int, int], list[PastLineupEntry]]:
        """Real, ESPN-set lineup + actual points for each already-played week,
        via box_scores - a historical per-week snapshot, unlike get_teams()'s
        live current-roster lineupSlot (which only reflects right now).
        box_scores only covers week <= current_week, so later weeks are
        silently skipped rather than erroring."""
        result: dict[tuple[int, int], list[PastLineupEntry]] = {}
        player_team_cache: dict[int, int] = {}
        for week in weeks:
            if week >= self._league.current_week:
                continue
            for box in self._league.box_scores(week=week, player_team_cache=player_team_cache):
                for team_id, lineup in ((box.home_team, box.home_lineup), (box.away_team, box.away_lineup)):
                    if team_id is None:
                        continue
                    result[(team_id, week)] = [
                        PastLineupEntry(
                            espn_id=bp.playerId,
                            name=bp.name,
                            position=_canon_pos(bp.position),
                            nfl_team=nd.normalize_team(bp.proTeam),
                            lineup_slot=bp.slot_position,
                            points=float(bp.points or 0.0),
                        )
                        for bp in lineup
                    ]
        return result

    def get_live_week_player_status(self, week: int) -> dict[int, tuple[float, bool]]:
        """{espn_id: (points_so_far, game_completed)} for the given week's
        live box scores. game_completed is espn_api's own ~3-hours-past-
        kickoff heuristic (BoxPlayer.game_played == 100) - lets a live
        matchup win-probability read collapse a finished player's remaining
        uncertainty to 0 without needing minute-by-minute polling; an
        in-progress or not-yet-started player keeps their full projected SD."""
        result: dict[int, tuple[float, bool]] = {}
        for box in self._league.box_scores(week=week):
            for lineup in (box.home_lineup, box.away_lineup):
                for bp in lineup:
                    result[bp.playerId] = (float(bp.points or 0.0), bp.game_played == 100)
        return result

    def get_future_espn_projections(self, weeks: list[int]) -> dict[int, dict[int, float]]:
        """ESPN's own per-week projection (not our proprietary one) for
        every given week, covering both rostered and free-agent players -
        {espn_id: {week: projected_points}}. Two requests per week rather
        than one per roster slot/position:
        - Rostered: one mRoster?scoringPeriodId=<week> request covers every
          team's whole roster at once. Built from the raw response by hand
          (bypassing League.load_roster_week, which mutates
          league.teams[*].roster in place) so this can never leave shared
          state pointing at some other week for whichever call happens to
          run after this one (get_teams() in particular).
        - Free agents: one free_agents(week=<week>) call with no position
          filter, same filterSlotIds:[] trick get_waiver_status_espn_ids
          already relies on to get every position back in a single request
          instead of one call per position."""
        result: dict[int, dict[int, float]] = {}
        for week in weeks:
            found_this_week = 0
            try:
                data = self._league.espn_request.league_get(params={"view": "mRoster", "scoringPeriodId": week})
                sample_entry = None
                for team_data in data.get("teams", []):
                    for entry in team_data.get("roster", {}).get("entries", []):
                        if sample_entry is None:
                            sample_entry = entry
                        p = EspnPlayer(entry, self._league.year)
                        proj = self._week_projected_points(p, week)
                        if proj is not None:
                            result.setdefault(p.playerId, {})[week] = proj
                            found_this_week += 1
                if found_this_week == 0 and sample_entry is not None:
                    # Nothing usable this week even though rosters came back -
                    # log one raw entry's stats array so a live run pinpoints
                    # exactly which key/shape assumption is wrong (e.g. no
                    # statSourceId==1 row for this scoringPeriodId at all,
                    # meaning ESPN just hasn't published that far out yet,
                    # vs. a shape this parsing doesn't expect).
                    raw_player = sample_entry.get("playerPoolEntry", {}).get("player") or sample_entry.get("player", {})
                    logger.warning(
                        "get_future_espn_projections: week %s - 0 rostered players had a usable projection; "
                        "sample player %r stats=%r",
                        week, raw_player.get("fullName"), raw_player.get("stats"),
                    )
            except Exception:
                logger.warning("get_future_espn_projections: week %s rostered-roster fetch failed", week, exc_info=True)

            try:
                fa_found = 0
                for p in self._league.free_agents(week=week, size=_FUTURE_PROJECTIONS_FA_SIZE):
                    proj = self._week_projected_points(p, week)
                    if proj is not None:
                        result.setdefault(p.playerId, {})[week] = proj
                        fa_found += 1
                found_this_week += fa_found
            except Exception:
                logger.warning("get_future_espn_projections: week %s free-agent fetch failed", week, exc_info=True)

            logger.info("get_future_espn_projections: week %s - %s players with a projection", week, found_this_week)
        return result

    def get_waiver_status_espn_ids(self) -> set[int]:
        """ESPN ids of players currently on waivers (need a FAAB claim to
        add) rather than true free agents (instant, no-bid add). The status
        this actually needs lives on the raw player-list ROW returned by
        kona_player_info, not on the nested "player" object (espn_api's own
        wrappers don't expose it at all) - verified live 2026-09-10: 280
        FREEAGENT / 20 WAIVERS out of the first 300 by %owned."""
        filters = {
            "players": {
                "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
                "filterSlotIds": {"value": []},
                "limit": 3000,
                "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                "sortDraftRanks": {"sortPriority": 100, "sortAsc": True, "value": "STANDARD"},
            }
        }
        headers = {"x-fantasy-filter": json.dumps(filters)}
        data = self._league.espn_request.league_get(
            params={"view": "kona_player_info", "scoringPeriodId": self._league.current_week}, headers=headers
        )
        return {row["player"]["id"] for row in data.get("players", []) if row.get("status") == "WAIVERS"}
