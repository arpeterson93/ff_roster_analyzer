# FF Roster Analyzer - Implementation Plan

Handoff plan for the implementing agent (Sonnet). Produced from
`initial-plan-fable-prompt.md` after live verification of the libraries, the
ESPN league, the FantasyPros pages, the nflverse data files, and both of Alex's
Google Sheets on 2026-09-09. Alex answered every open decision in the prompt;
those answers are recorded in Part 1 and are final unless marked otherwise.

Conventions used below: "verified" means checked live in this session; "verify"
means the implementer must confirm during the stage where it matters.

---

## Part 0: Verified facts (trust these; spot-check at most)

### 0.1 Environment and repo

1. Repo `arpeterson93/ff_roster_analyzer` on GitHub, branch `main`, one commit
   (scaffold: README, .gitignore). Nothing else exists yet. Local Python is
   3.10.9; target 3.11 in CI (matches numberball's workflows).
2. `nflreadpy 0.1.5` and `espn-api 0.46.0` install cleanly on Windows/py3.10.
   nflreadpy returns **polars** DataFrames (not pandas). Add `polars`, `pyyaml`,
   `requests`, `scipy`, `numpy`, `pytest` to `requirements.txt`.
3. Windows console default codepage cannot print some player names; run the
   pipeline with `PYTHONIOENCODING=utf-8` locally and write JSON with
   `ensure_ascii=False`, `encoding="utf-8"`.

### 0.2 The two leagues (both ESPN, season 2026)

| | Main league | Second league |
|---|---|---|
| Name | The "O" League | Any Given Sunday |
| ESPN league id | 355398 | 544449541 |
| Access | **Public** (verified: loads with no cookies) | **Private** (verified: `ESPNAccessDenied: espn_s2 and swid are required`) |
| Reception scoring | **Standard** (0 per reception; verified: no `REC` item in `scoring_format`) | **Half PPR** (per Alex) |
| Lineup | QB, RB, WR×2, TE, FLEX(RB/WR/TE)×2, K, BE×7, IR×2 (verified) | Different slots, includes D/ST (per Alex; pull from ESPN once secrets exist) |
| Positions in play | QB RB WR TE K | QB RB WR TE K DST |
| Reference sheet | `1a2Wvw3Pjxl0HpKMMwjDnKjGgP5Is7nG229V1DGgQ6z8` | `1QxBFWIwpMu9hkR1iU0mxSNRVHTGVJnQhJc-qqWYmzyI` |

Verified settings for 355398 (via `espn_api`): 12 teams in 3 divisions
(Strike/Spare/Split, 4 each); `reg_season_count=14`; `matchup_periods` 1..17
(so playoffs are weeks 15, 16, 17 = 3 rounds); `playoff_team_count=6`;
`playoff_seed_tie_rule="TOTAL_POINTS_SCORED"`; `playoffReseed=False`;
`tie_rule="NONE"`; FAAB budget 1000; trade deadline epoch-ms 1796839200000;
`current_week=1`, `nfl_week=1` as of 2026-09-09 (no games played).

Verified `scoring_format` for 355398 (ESPN statId -> points). This is the
whole list; anything not here scores 0 in this league:

```
3  PY   Passing yards            0.05      42  REY  Receiving yards        0.1
4  PTD  TD pass                  4         43  RETD TD reception          6
19 2PC  2pt pass                 1         44  2PRE 2pt reception         2
20 INTT Interception thrown     -1         63  FTD  Fumble rec. for TD     6
24 RY   Rushing yards            0.1       72  FUML Fumbles lost         -1
25 RTD  TD rush                  6         77  FG40 FG 40-49              3
26 2PR  2pt rush                 2         80  FG0  FG 0-39               3
86 PAT  PAT made                 1         198 FG50 FG 50-59              4
101 KRTD Kick return TD          6         201 FG60 FG 60+                5
102 PRTD Punt return TD          6
```

Team names/ids for 355398 (for sanity checks): 1 Team Steve, 2 Team Gary,
3 Balls Deep, 4 Team ABIDE, 5 Team Pat/Nick, 6 Love Train, 7 Lawrence of
Duvallia, 8 Oh Henry ..,,The 2nd, 9 Better Luck Next Year, 10 Dangerous
Nights Crew, 11 Team Craig, 12 Bad Company. `Team.owners[0]` carries
`firstName`/`lastName`/`displayName` - use `firstName` as the "manager"
label, `team_name` as the display name.

### 0.3 espn-api surface (verified by source inspection and live calls)

- `League(league_id, year, espn_s2=None, swid=None)`; public leagues need no
  cookies. `league.settings` -> `Settings` with `scoring_format` (list of
  `{abbr,label,id,points}`), `position_slot_counts` (dict keyed by slot label
  e.g. `'RB/WR/TE': 2, 'D/ST': 0, 'BE': 7, 'IR': 2`), `reg_season_count`,
  `playoff_team_count`, `matchup_periods`, `playoff_seed_tie_rule`, `tie_rule`,
  `faab`, `trade_deadline`. Raw settings via
  `league.espn_request.league_get(params={'view':'mSettings'})['settings']`
  give `scheduleSettings.divisions` and `playoffSeedingRule`.
- `league.teams` -> `Team`: `team_id`, `team_abbrev`, `team_name`, `division_id`,
  `wins/losses/ties`, `points_for`, `points_against`, `standing`, `schedule`
  (list of opponent `Team` per matchup period, len 14 = regular season only),
  `outcomes` (`'U'` unplayed / `'W'`/`'L'`/`'T'`), `scores`, `roster`, `owners`.
- `Player` (roster entries): `playerId` (ESPN id, e.g. Puka Nacua 4426515),
  `name`, `position` (`'QB','RB','WR','TE','K','D/ST'`), `proTeam` (ESPN
  abbrevs: `LAR`, `LV`, `JAX`, `WSH`), `lineupSlot`, `eligibleSlots`,
  `injuryStatus` (`'ACTIVE','QUESTIONABLE','DOUBTFUL','OUT','INJURY_RESERVE',
  'SUSPENSION'`...), `injured` (bool), `projected_total_points`,
  `projected_avg_points`, `total_points`, `avg_points`, `stats[week]` with
  `projected_points`/`points` per scoring period (ESPN's own projections are
  available as a reference, NOT used for valuation), `schedule[week] ->
  {'team': opp, 'date': datetime}`, `percent_owned`.
- `league.free_agents(week=None, size=50, position='RB')` -> `BoxPlayer` list
  (adds `pro_opponent`, `on_bye_week`, `slot_position='FA'`). Verified live:
  returns FAs with `injuryStatus`, `percent_owned`. `position='D/ST'` returned
  `[]` in 355398 because that league has no D/ST slot - expected.
- `league.scoreboard(week)` -> `Matchup` objects with `home_team`/`away_team`
  and scores for that matchup period (works for future weeks: scores 0.0).
- ESPN D/ST entities are `Player`s with `position == 'D/ST'` and `proTeam` set;
  map them to NFL teams by `proTeam`, never by name.

### 0.4 nflreadpy surface (verified live)

- `load_schedules(2026)`: 272 REG games, weeks 1-18, 0 played as of 2026-09-09.
  Columns include `game_id, season, game_type, week, gameday, away_team,
  home_team, away_score, home_score`. Team abbrevs are the **nflverse
  canon**: `LA`, `LV`, `JAX`, `WAS` (32 teams). 2026 byes (from the schedule):
  ARI 14, ATL 11, BAL 13, BUF 7, CAR 5, CHI 10, CIN 6, CLE 11, DAL 14, DEN 10,
  DET 6, GB 11, HOU 8, IND 13, JAX 7, KC 5, LA 11, LAC 7, LV 13, MIA 6, MIN 6,
  NE 11, NO 8, NYG 8, NYJ 13, PHI 10, PIT 9, SEA 11, SF 8, TB 10, TEN 9, WAS 7.
- `load_player_stats(season)` (weekly): 150 columns incl. `player_id` (GSIS
  `00-00xxxxx`), `player_display_name`, `position`, `team`, `opponent_team`,
  `week`, `season_type` (`REG`/`POST`), all passing/rushing/receiving stats,
  fumbles (`sack_fumbles_lost`, `rushing_fumbles_lost`, `receiving_fumbles_lost`,
  `fumbles_lost_total`), 2pt conversions, `special_teams_tds`, kicking
  (`fg_made_0_19 ... fg_made_60_`, `fg_missed_*`, `pat_made`, `pat_missed`,
  `fg_blocked`), and `fantasy_points`/`fantasy_points_ppr` (nflverse's own
  standard/PPR totals - **kickers are 0 there**, so never use these columns;
  compute points from raw stats with `engine/scoring.py`).
  Seasons 2022-2025 load fine (~19k rows each, weeks 1-22, ~570 K rows).
  **`load_player_stats(2026)` currently raises `ConnectionError` (404)**
  because the 2026 file does not exist until week 1 completes. The pipeline
  must catch this and treat the current season as "0 weeks played".
- `load_team_stats(season)` (weekly, 570 rows/season): `team`, `opponent_team`,
  `week`, `season_type`, `def_sacks`, `def_interceptions`, `def_tds`,
  `def_safeties`, `def_fumbles_forced`, `fumble_recovery_opp`,
  `fumble_recovery_tds`, `def_punt_blocks`, `def_pat_blocks`, `def_fg_blocks`,
  `special_teams_tds`, plus offensive columns. This is the D/ST stat source
  (points allowed comes from `load_schedules` scores; yards allowed = the
  opponent's row offensive yards - verify column names when building D/ST).
- `load_ff_playerids()`: 12,492 rows, 35 columns incl. `gsis_id`, `espn_id`,
  `fantasypros_id`, `sleeper_id`, `yahoo_id`, `name`, `merge_name`, `position`
  (`PK` for kickers), `team`. Team abbrevs here are DynastyProcess style
  (`LAR`, `LVR`, `JAC`, `GBP`, `KCC`, `NEP`, `NOS`, `SFO`, `TBB`) - normalize.
  Coverage: joining the current FantasyPros redraft lists (774 QB/RB/WR/TE/K
  rows) on `fantasypros_id` leaves 42 unmapped, all 2026 rookies (mostly
  kickers). A name+position fallback is required.
- `load_players()` also carries `gsis_id` and `espn_id` for the same purpose.
- `load_ff_rankings("draft")`: one scrape date (Fridays; currently
  2026-09-04), 25 cols, `page_type` in `redraft-qb/rb/wr/te/k/dst/overall`
  (+dynasty/best-ball), `id` = FantasyPros player id, `ecr`, `sd`, `best`,
  `worst`, `bye`. **The redraft positional pages are FantasyPros' PPR pages**
  (`fp_page` = `/nfl/rankings/ppr-rb-cheatsheets.php` etc.). During the
  season these are FantasyPros' ROS lists (verified via the `"all"` history:
  weekly in-season scrapes through 2024-12-27). `load_ff_rankings("week")`
  (867 rows, scraped 2026-09-08) has `pos_rank`, `player_opponent`,
  `r2p_pts`, `start_sit_grade`, also PPR only. `"all"` is 1.8M rows - never
  load it in the pipeline.

### 0.5 FantasyPros direct scrape (verified live 2026-09-09)

Alex wants format-correct ranks (standard for the main league, half PPR for
the second). FantasyPros rankings pages embed the full table as JSON:

```
var ecrData = {"sport":"NFL","type":"ROS Half PPR","ranking_type_name":"ros",
 "year":"2026","week":"0","position_id":"WR","scoring":"HALF", ...,
 "total_experts":3,"last_updated_ts":1788790315,
 "players":[{"player_id":19788,"player_name":"Ja'Marr Chase",
   "player_team_id":"CIN","player_position_id":"WR","player_bye_week":"6",
   "player_owned_avg":99.9,"rank_ecr":1,"rank_min":"1","rank_max":"2",
   "rank_ave":"1.33","rank_std":"0.47","pos_rank":"WR1","r2p_pts":...}, ...]};
```

Verified URLs (all HTTP 200 with a plain `Mozilla/5.0` User-Agent, one
`ecrData` block each, `player_id` identical to DynastyProcess `fantasypros_id`):

| Scoring | RB / WR / TE ROS | QB / K / DST ROS (no scoring variants) |
|---|---|---|
| Standard | `/nfl/rankings/ros-rb.php` (`scoring: "STD"`, 111 RBs) | `/nfl/rankings/ros-qb.php` (81), `ros-k.php` (32), `ros-dst.php` (32) |
| Half PPR | `/nfl/rankings/ros-half-point-ppr-wr.php` (`scoring: "HALF"`, 150 WRs) | same |
| PPR | `/nfl/rankings/ros-ppr-wr.php` | same |

Weekly pages follow the same pattern without the `ros-` prefix
(`/nfl/rankings/half-point-ppr-wr.php` verified; `qb.php`, `rb.php`, `k.php`,
`dst.php` to verify). Team abbrevs in FantasyPros data: `LAR`, `LV`, `JAC`.
Preseason pages report `"week":"0"` and only 2-3 experts; that is fine.
FantasyPros' `ecrData` is what the old sheet's `IMPORTHTML` was reading, so
this is the same input, fetched properly.

### 0.6 What the sheets actually compute (read from both sheet exports)

Both sheets share one structure; the second adds DST. Facts the rebuild keeps:

1. **Settings block**: `Matchup Dampening: 50%`, `Playoffs Start: 15`,
   `Season End: 17`, `Eligible Weeks: 8`, `PA Basis: L5 | Adjusted`,
   `Opp Adj: 2025` (i.e. which season's points-allowed feeds the index),
   per-position `Rank Include` rows, `FA NMD Basis`, `Trade Partner`.
2. **Rank -> points curve** ("Rank Pts"): per position, rank N's value =
   average PPG of the Nth-ranked player by actual season PPG, built from the
   current season (players with `Games >= Eligible Weeks`) alongside the
   prior season, each expressed both as PPG and as a ratio to a
   starter-pool average (`Avg` row over the top 32 QB / 64 RB / 96 WR /
   48 TE / 28-32 K / 32 DST).
3. **Opp Adj**: each player's week columns hold the opponent's points-allowed
   rank for the player's position; `ROS SOS / Reg SOS / Playoff SOS` average
   them; `Reg Adj / Playoff Adj / ROS Adj` are the resulting multipliers;
   `Reg Proj / Playoff Proj / ROS Proj` are the projections split at
   `Playoffs Start`. Points allowed is trailing-5 ("L5") and
   opponent-strength adjusted.
4. **Current Week Flag**: if a player's ROS positional rank is at or under the
   cutoff (QB 30, RB 50, WR 60, TE 30, K 30) and their FantasyPros *weekly*
   rank is more than the gap (QB/TE/K 15, RB/WR 20) worse than that, the
   player is assumed out this week: this week's projection is zeroed and the
   ROS expectation is redistributed across the remaining weeks. (The first
   number in `Rank Include`, e.g. 50/115/142/72/36, is list depth and is
   obsolete now.)
5. **Teams depth table** (`QB1..QB3, RB1..RB6, WR1..WR6, TE1..TE3, K1..K2,
   DST1..DST2`): each cell is that player's marginal value to their own
   team's lineup - the "next man down" (NMD) delta. This is the value model
   Alex wants (see Part 1 #6).
6. **RV tab**: per team, per position, per week lineup value in three
   scenarios `w/o FLEX`, `w/ FLEX`, `w/ WW` (waiver wire; broken in the
   sheet). The rebuild's team-strength output supersedes it.
7. **Injury grid**: manual `X` per player-week for missed games. Replaced by
   ESPN `injuryStatus` plus the weekly-rank anomaly flag above.

---

## Part 1: Decisions (answered by Alex unless marked "recommendation")

1. **Leagues configured now: both.** Main league public; second league
   needs `ESPN_S2`/`SWID` repo secrets (Alex to add; see Stage 7 for the
   how-to). D/ST is therefore **in scope for v1**.
2. **Consensus rank source: FantasyPros direct scrape, format-aware**
   (standard / half / PPR chosen per league config), ROS pages for the
   valuation input and weekly pages for the injury/anomaly flag. Fallback if
   the scrape fails or the page shape changes: `nflreadpy.load_ff_rankings
   ("draft")` filtered to `page_type == "redraft-<pos>"` (PPR; log a warning
   that ranks are PPR-based). Same FantasyPros ids in both, so the join is
   identical.
3. **Rank -> points curve: seasons 2022-2025, configurable** (`curve_seasons`
   list in each league's YAML so the bounds can change). Rank = end-of-season
   rank by PPG (REG weeks only, `min_games` = 8), curve value = mean PPG at
   that rank across seasons, made monotone non-increasing. **Blend in the
   current season** as it accumulates (Alex's choice): weight of the
   current-season curve = `min(weeks_played / 8, 1) * 0.5` (config
   `curve_current_max_weight`, default 0.5; full weight reached at 8 weeks).
   Alex is curious about the alternative "historical ROS scrape paired with
   subsequent actual PPG" method - note it in README as a future option, do
   not build it.
4. **Matchup dampening = 50%**: `mult = 1 + d * (index - 1)` with `d = 0.5`
   per league config. Confirmed by Alex.
5. **Points allowed: opponent-strength adjusted**, basis configurable
   (`season`, `l5`, `blend`; default `blend`). Early season falls back to the
   prior season's index (the sheet's `Opp Adj: 2025`), phased out over the
   first 6 played weeks (config `pa_prior_season_weeks`).
6. **Value basis for team strength and trades: lineup-delta ("NMD") value.**
   A player's value to a team = the drop in that team's optimal-lineup ROS
   points if the player were removed. Depth at the position lowers it. Two
   variants are computed: roster-only (`value_delta`) and with the best
   available free agent at each position back-filling (`value_delta_ww`).
   *Recommendation:* trade equity and pickup suggestions use `value_delta_ww`
   (it answers "what would I actually do if I lost this player"); the UI
   shows both. Start/sit uses raw weekly projections.
7. **Trade calculator: live, client-side JS**, a twin of `engine/trades.py`
   working from `players.json` + `teams.json` (all inputs are there). A pytest
   fixture pins both implementations to the same numbers.
8. **Refresh cadence: daily** during the season (cron), plus `push` and
   `workflow_dispatch`.
9. **Deployment (recommendation, mirrors numberball):** build in Actions and
   publish `docs/` with `upload-pages-artifact` + `deploy-pages`; generated
   JSON is **not committed** (avoids ~120 bot commits per season). Pages
   source must be set to "GitHub Actions" once, by hand. The prompt's
   "commit the JSON" alternative works too if Alex prefers history; default
   to artifact deploy.
10. **Lineup optimizer: exact assignment** via
    `scipy.optimize.linear_sum_assignment` (players x slot instances,
    ineligible = large negative). Trivial cost, no greedy edge cases with
    overlapping FLEX/`RB/WR`/`WR/TE`/`OP` eligibility.
11. **Playoff simulator: Monte Carlo, 10,000 iterations, seeded.** Weekly
    team score ~ Normal(lineup projection, sqrt(sum of starters' weekly
    variance)), player variance from a rank -> weekly SD table built with the
    curve. Seeding: division winners first when the league has divisions
    (verify in ESPN's league settings page for 355398), then record, tiebreak
    per `playoff_seed_tie_rule` (`TOTAL_POINTS_SCORED`). Ties count 0.5 win
    (`tie_rule=NONE`).
12. **ROS horizon**: current week through the last playoff week
    (`max(matchup_periods) = 17`), split into `reg` (<= `reg_season_count`)
    and `playoff` totals like the sheet. NFL week 18 is never projected.
13. **Injury handling this week**: projection = 0 for `OUT`,
    `INJURY_RESERVE`, `SUSPENSION`, `DOUBTFUL` (config list), or when the
    weekly-rank anomaly flag fires (Part 0.6 #4, cutoffs/gaps in config);
    `QUESTIONABLE` unchanged. ROS total is preserved by rescaling the other
    remaining weeks, exactly like the sheet.
14. **Frontend**: static HTML/CSS/JS, no build step, same shape as
    `irrigation_planner/docs` (`index.html`, `css/styles.css`, `js/*.js`),
    mobile-friendly, light/dark via `prefers-color-scheme`. Views listed in
    Stage 6.

---

## Part 2: Architecture

```
config/leagues/*.yml ─┐
ESPN (espn-api) ──────┼─► ingest/ ─► engine/ ─► docs/data/<slug>/*.json ─► docs/ static site (GitHub Pages)
nflverse (nflreadpy) ─┤
FantasyPros ecrData ──┘        GitHub Actions: daily cron + push + dispatch, artifact deploy
```

Repo layout (final):

```
config/leagues/o-league.yml, any-given-sunday.yml
ingest/
  base.py          LeagueClient Protocol + dataclasses (LeagueSettings, FantasyTeam, RosterPlayer, Matchup)
  espn_client.py   ESPN adapter (only adapter built)
  nfl_data.py      nflreadpy wrappers + team-abbrev normalization + caching
  rankings.py      FantasyPros ecrData scraper (+ nflreadpy fallback)
  ids.py           cross-platform id map (gsis <-> espn <-> fantasypros), name fallback
engine/
  scoring.py       ESPN scoring_format -> points from nflverse stat rows (players and D/ST)
  curve.py         rank -> PPG (and SD) curves per position, history + current-season blend
  matchups.py      points allowed by position, opponent-adjusted, season/L5/blend, prior-season fallback, index + rank
  valuation.py     ROS projections per player (5-step method + injury/anomaly handling)
  lineup.py        exact optimal lineup for a roster/week
  team_strength.py depth (NMD) values, position strength vs league, pickups, trade targets
  trades.py        evaluate a specific trade (both value bases)
  standings.py     Monte Carlo playoff odds
  pipeline.py      orchestrates per league, writes JSON
docs/
  index.html, css/styles.css, js/{app,data,strength,trade,startsit,rankings,standings}.js
  data/leagues.json, data/<slug>/{meta,players,teams,lineups,standings,matchups}.json  (generated, gitignored)
tests/  (pytest, golden fixtures under tests/fixtures/)
.github/workflows/build.yml
requirements.txt, .env.example, README.md
```

Canonical keys used everywhere in `engine/`:
- **Player id**: `gsis_id` when known (`00-00xxxxx`), else `fp:<fantasypros_id>`,
  else `espn:<espn_id>`. DST id: `dst:<TEAM>` (canonical NFL abbrev).
- **NFL team**: nflverse canon (`LA`, `LV`, `JAX`, `WAS`). One
  `normalize_team()` in `ingest/nfl_data.py` maps ESPN (`LAR`,`WSH`),
  FantasyPros (`LAR`,`JAC`), DynastyProcess (`LVR`,`GBP`,`KCC`,`NEP`,`NOS`,
  `SFO`,`TBB`,`JAC`,`LAR`) and legacy (`OAK`,`SD`,`STL`) to canon.
- **Position**: `QB RB WR TE K DST` (ESPN `D/ST` -> `DST`; DP `PK` -> `K`).
- **Week**: NFL week number == ESPN scoring period (verified 1:1 for 355398).

---

## Part 3: Staged implementation

Each stage ends with something runnable. Do not start the frontend before
Stage 5 produces real JSON for both leagues.

### Stage 0 - scaffold

- `requirements.txt`: `nflreadpy`, `polars`, `espn_api`, `pyyaml`, `requests`,
  `numpy`, `scipy`, `pytest`, `python-dotenv`.
- `.env.example` with `ESPN_S2_AGS=` and `SWID_AGS=` (the second league's
  secret names; see config). `.gitignore` already ignores `.env`; add
  `docs/data/` (generated) and `.cache/`.
- `config/leagues/o-league.yml`:

```yaml
slug: o-league
name: The "O" League
platform: espn
league_id: 355398
season: 2026
private: false
credentials: null          # env var names for private leagues, e.g. {espn_s2: ESPN_S2_AGS, swid: SWID_AGS}
scoring_format: standard   # standard | half | ppr  -> FantasyPros page family
valuation:
  curve_seasons: [2022, 2023, 2024, 2025]
  curve_min_games: 8
  curve_current_max_weight: 0.5
  curve_current_full_weight_weeks: 8
  matchup_dampening: 0.5
  index_clamp: [0.5, 1.5]
  pa_basis: blend            # season | l5 | blend
  pa_l5_weight: 0.5          # used when pa_basis == blend
  pa_opponent_adjusted: true
  pa_prior_season_weeks: 6
  zero_this_week_statuses: [OUT, INJURY_RESERVE, SUSPENSION, DOUBTFUL]
  anomaly_rank_cutoff: {QB: 30, RB: 50, WR: 60, TE: 30, K: 30, DST: 32}
  anomaly_rank_gap:    {QB: 15, RB: 20, WR: 20, TE: 15, K: 15, DST: 15}
  starter_pool: {QB: 32, RB: 64, WR: 96, TE: 48, K: 32, DST: 32}   # display-only position averages
strength:
  replacement_from_waivers: true   # trade/pickup math uses value_delta_ww
  max_pickups: 10
  max_trade_targets: 10
sim:
  iterations: 10000
  seed: 2026
  division_winners_first: true
```

  `any-given-sunday.yml`: `league_id: 544449541`, `private: true`,
  `credentials: {espn_s2: ESPN_S2_AGS, swid: SWID_AGS}`,
  `scoring_format: half`, same defaults otherwise.
- `README.md`: replace the scaffold text with the architecture summary,
  local run instructions, and the "methodology" section (the 5 steps,
  dampening, NMD value, curve construction, the future in-season-history
  curve idea).

### Stage 1 - ingestion

**`ingest/nfl_data.py`**
- Thin wrappers returning polars frames: `schedules(season)`,
  `player_stats(season)` (returns an empty frame with the right columns on
  the 404 `ConnectionError`), `team_stats(season)`, `playerids()`, `players()`.
- `normalize_team(abbr) -> str` (table in Part 2). Apply it to every frame's
  team columns on load so nothing downstream sees a non-canonical abbrev.
- `nfl_week_context(season, schedules) -> (weeks_played, bye_weeks: dict[team, week],
  opponent: dict[(team, week), opp|None])` derived from `load_schedules`
  (REG only; a team with no game in a week is on bye). `weeks_played` =
  max week with both scores non-null (0 today).
- File cache under `.cache/nflverse/<name>_<season>.parquet` with a 12h TTL
  for the current season and infinite for past seasons (past-season files
  never change). Keeps local iteration fast; CI runs cold.

**`ingest/rankings.py`**
- `fetch_ros(scoring_format) -> polars frame` with columns
  `fp_id, name, pos, team, ros_pos_rank (rank_ecr), rank_ave, rank_std,
  rank_min, rank_max, bye, owned_pct, scraped_ts, experts` over QB/RB/WR/TE/K/DST.
  Page map: `ros-{prefix}{pos}.php` with `prefix` = `""` / `half-point-ppr-` /
  `ppr-` for rb/wr/te and no prefix for qb/k/dst. Parse with
  `re.search(r"var ecrData = (\{.*?\});", html, re.S)` then `json.loads`.
  Assert `meta["scoring"]` matches the requested format for rb/wr/te
  (`STD`/`HALF`/`PPR`) so a silent redirect is caught.
- `fetch_weekly(scoring_format, week)` same shape from the weekly pages
  (`week_pos_rank`, `r2p_pts`, `opponent`). Verify the standard/half weekly
  URL names for qb/rb/wr/te/k/dst at build time.
- Request hygiene: one `requests.Session`, `User-Agent: Mozilla/5.0`,
  10 s timeout, 3 retries with backoff, ~1 s sleep between pages (12 pages
  total). On any failure of the ROS fetch, fall back to
  `nflreadpy.load_ff_rankings("draft")` filtered to
  `page_type == f"redraft-{pos}"` with `ros_pos_rank` = rank order of `ecr`
  within position, and set `meta.rankings_source = "dynastyprocess-ppr-fallback"`.
  Weekly fallback: `load_ff_rankings("week")`.

**`ingest/ids.py`**
- `IdMap` built from `playerids()` + `players()`: dicts
  `by_fp`, `by_espn`, `by_gsis`, `by_name_pos` (key = `merge_name`
  normalisation: lowercase, strip punctuation and suffixes `jr`, `sr`, `ii`,
  `iii`, `iv`, `v`; note FantasyPros weekly pages spell `JaMarr Chase`
  without the apostrophe and `Amon-Ra St Brown` without the period - the
  normaliser must make these collide).
- `canonical_id(fp_id=None, espn_id=None, gsis_id=None, name=None, pos=None,
  team=None)` -> canonical key per Part 2, with a name+pos fallback that
  requires a unique match (log the ambiguous ones). DST: `dst:<TEAM>`.
- Emit `docs/data/<slug>/unmapped.json` (name, pos, team, source) so gaps
  are visible; the pipeline never fails on an unmapped player, it just
  carries them with `fp:`/`espn:` ids and no stats history.

**`ingest/base.py` + `ingest/espn_client.py`**
- Protocol `LeagueClient`: `get_settings() -> LeagueSettings`,
  `get_teams() -> list[FantasyTeam]` (each with `roster: list[RosterPlayer]`),
  `get_matchups() -> list[Matchup]` (all periods, played and unplayed, with
  scores when played), `get_free_agents(position, size) -> list[RosterPlayer]`.
- `LeagueSettings`: `name, season, current_week, reg_season_count,
  final_week (= max matchup period), playoff_team_count, seed_tie_rule,
  tie_rule, divisions: dict[id, name], slots: dict[slot_label, count]`
  (only non-zero, excluding `BE`/`IR`), `slot_eligibility: dict[slot_label,
  set[pos]]` (e.g. `'RB/WR/TE' -> {RB, WR, TE}`, `'OP' -> {QB, RB, WR, TE}`,
  `'D/ST' -> {DST}`), `scoring_items: list[{id, abbr, points}]`,
  `positions: list[str]` (positions that can fill any starting slot).
- `RosterPlayer`: `espn_id, name, position, nfl_team (canonical),
  fantasy_team_id | None, lineup_slot, eligible_slots, injury_status,
  injured, percent_owned, espn_projected_total, espn_projected_week`.
- ESPN adapter: `League(...)` with cookies from the env var names in config;
  `get_free_agents` calls `league.free_agents(size=..., position=pos)` per
  position in `settings.positions` (size 60 for RB/WR, 40 for QB/TE, 32 K/DST).
  Matchups: `league.scoreboard(week)` for 1..final_week; for played weeks
  fill `home_score/away_score`; also expose `Team.outcomes`/`scores`.

Stage 1 exit check: `python -m ingest.smoke --league o-league` prints team
count, roster sizes, FA counts per position, ROS rank counts per position,
`weeks_played`, and the number of unmapped players (expect only a few rookie
kickers).

### Stage 2 - scoring, curve, matchups (with golden tests)

**`engine/scoring.py`**
- `ScoringRules.from_espn(scoring_items)`; `points_for_row(row) -> float` for a
  nflverse weekly player row, `dst_points_for_row(team_row, opp_row, game)`
  for a team-week.
- Explicit map from ESPN `abbr` -> nflverse expression. Minimum set (main
  league): `PY`->`passing_yards`, `PTD`->`passing_tds`, `INTT`->
  `passing_interceptions`, `2PC`->`passing_2pt_conversions`, `RY`->
  `rushing_yards`, `RTD`->`rushing_tds`, `2PR`->`rushing_2pt_conversions`,
  `REY`->`receiving_yards`, `RETD`->`receiving_tds`, `2PRE`->
  `receiving_2pt_conversions`, `REC`->`receptions`, `FUML`->
  `fumbles_lost_total` (verify this equals sack+rushing+receiving fumbles
  lost; else sum those three), `FTD`->`fumble_recovery_tds`, `KRTD`/`PRTD`
  -> split of `special_teams_tds` (nflverse does not separate them; treat
  `special_teams_tds` as return TDs and score with the KRTD value - document
  it), `PAT`->`pat_made`, `FG0`->`fg_made_0_19+fg_made_20_29+fg_made_30_39`,
  `FG40`->`fg_made_40_49`, `FG50`->`fg_made_50_59`, `FG60`->`fg_made_60_`,
  and the missed/blocked FG and PAT ids if present (`FGM*`, `PATM`), plus the
  D/ST family for the second league (sacks, INT, fumble recoveries, TDs,
  safeties, blocks, points-allowed brackets, yards-allowed brackets). Use
  `espn_api.football.constant.SETTINGS_SCORING_FORMAT_MAP` to see every
  abbr ESPN can emit. **Fail loudly** at pipeline start if a league has a
  non-zero scoring item with no mapping.
- Golden test: hand-computed points for 3 stat lines (a QB, a WR with a
  fumble, a K with a 52-yard FG) under the verified 355398 rules; a half-PPR
  variant; one D/ST line.

**`engine/curve.py`**
- `build_curve(seasons, scoring, min_games, positions) -> Curve` where
  `Curve.ppg[pos][rank]` and `Curve.sd[pos][rank]` (rank 1..N, N = number of
  eligible players; beyond N use the last value) plus `Curve.position_avg[pos]`
  over the configured `starter_pool`.
  Per season: REG rows only, `points = scoring.points_for_row`, PPG = mean
  over that player's rows (a row exists only for games with stats; do not
  divide by 18), weekly SD = std of those rows, eligible if `n_rows >=
  min_games`; rank by PPG desc; then average PPG and SD by rank across
  seasons and apply a monotone non-increasing pass (`np.minimum.accumulate`).
  D/ST curve from `team_stats` the same way.
- `blend_current(curve_hist, curve_cur, weeks_played, cfg)`: weight per
  Part 1 #3; current-season eligibility `min_games = max(1, min(cfg.min_games,
  weeks_played // 2))`; skip entirely when `weeks_played == 0`.
- Test: synthetic two-season frames with known PPGs -> exact expected curve
  and monotone fix-up; `weeks_played=0` returns history untouched.

**`engine/matchups.py`**
- Inputs: player_stats (+ team_stats for DST) for the current season and the
  prior season, scoring, schedules.
- Per (defense team, position, week): `allowed = sum(points of opposing
  players at pos)`; for DST the "defense" is the offense the DST faced (same
  grouping by `opponent_team`; nothing special).
- Opponent-strength adjustment: `offense_avg[team][pos]` = mean per game of
  points scored by that team's `pos` players over the season; for each game
  `ratio = allowed / offense_avg[opponent][pos]`; `index_season[def][pos]` =
  mean of ratios over all games, `index_l5` = mean over the defense's last 5
  games; unadjusted variants = `allowed_avg / league_avg`. Blend per
  `pa_basis`. Prior-season fallback: `w = min(weeks_played /
  pa_prior_season_weeks, 1)`; `index = w * current + (1 - w) * prior`
  (prior = previous full season, season basis). With `weeks_played=0` the
  index is purely last season's - this is what runs today.
- Output `MatchupIndex.index[pos][team]` (clamped to `index_clamp`),
  `rank[pos][team]` (1 = toughest = lowest index, 32 = easiest),
  `allowed_ppg[pos][team]`, `l5_allowed_ppg`, and `season_used`.
- Test: 4 synthetic teams, 3 games each with known points -> known indices
  and ranks; L5 window; prior-season blend weight at weeks 0/3/6.

### Stage 3 - valuation and lineup (with golden tests)

**`engine/valuation.py`**
- `project_player(p, curve, matchups, ctx, cfg) -> Projection`:
  1. `baseline = curve.ppg[pos][ros_pos_rank]` (rank from FantasyPros; if
     unranked: baseline 0, `ranked=False`).
  2. `weeks = [ctx.current_week .. ctx.final_week]`; `active_weeks` =
     those not on bye.
  3. `ros_total = baseline * len(active_weeks)`.
  4. `raw[w] = baseline * (1 + d * (clamp(index[pos][opp_w]) - 1))`, 0 on bye.
  5. Injury/anomaly zeroing for `ctx.current_week` (Part 1 #13); then
     rescale the non-zero weeks so `sum(adjusted) == ros_total` (skip the
     rescale if every remaining week is zero). Store `matchup_index`,
     `opponent`, `matchup_rank`, `projected` per week.
  6. Split: `reg_total` (weeks <= reg_season_count), `playoff_total`,
     `this_week`. Weekly `sd[w] = curve.sd[pos][rank] * mult_w`.
- Test: rank with known baseline, 4 remaining weeks with indices
  `[1.2, 0.8, bye, 1.0]` and `d=0.5` -> raw `[1.1, 0.9, 0, 1.0] * b`, sum
  rescaled to `3b`; OUT this week -> `[0, ...]` still summing to `3b`;
  anomaly flag fires only under the cutoff/gap rule.

**`engine/lineup.py`**
- `optimal_lineup(players: list[(id, pos, pts)], slots, eligibility) ->
  (total, assignment: dict[slot_instance, id])` via
  `linear_sum_assignment` on a `players x slot_instances` matrix
  (`-1e9` where ineligible, `maximize=True`). Slot instance labels
  `QB, RB, WR1, WR2, TE, FLEX1, FLEX2, K` (order stable for the UI).
- Test: random rosters vs brute force over all assignments for a 6-slot
  league; an `OP` (superflex) case; a roster short of a position leaves the
  slot empty (total unaffected).

### Stage 4 - team strength, trades, standings

**`engine/team_strength.py`**
- `roster_projection(roster, week)` -> per-player projected points that week
  (from Stage 3). `lineup_total(roster) = sum over remaining weeks of
  optimal_lineup(...)[0]`.
- `depth_values(team)`: for each player `value_delta = lineup_total(roster)
  - lineup_total(roster - p)`; `value_delta_ww` the same but the reduced
  roster gains the best free agent (by `ros_total`) at `p.position`. Sort
  per position -> the `QB1..`, `RB1..` depth table.
- `position_strength(team)`: per position, sum over remaining weeks of the
  optimal lineup's points contributed by players of that position (flex
  slots attribute to the player's own position), divided by weeks ->
  points/week; `vs_league_avg` = minus the mean over teams; also rank 1-12.
- `pickups(team)`: for the top 30 free agents per position by `ros_total`,
  candidate = add FA and drop the rostered player with the lowest
  `value_delta` (any position, never an IR-slotted player), gain =
  `lineup_total(after) - lineup_total(before)`; keep top `max_pickups`
  with gain > 0, report the suggested drop.
- `trade_targets(team)`: enumerate 1-for-1 swaps with every other team
  (all rostered players both sides) and 2-for-1 swaps limited to each
  side's top 6 by `ros_total`; score `gain_self`, `gain_other` (lineup-delta
  with waiver back-fill); keep swaps where both gains > 0, ranked by
  `gain_self`, top `max_trade_targets`; also summarise per partner team
  "their surplus position / your surplus position". Budget: ~12 teams x
  ~3k swaps x 15 weeks of tiny assignments - measure; if > 90 s total,
  restrict 1-for-1 candidates to each side's top 10 by `ros_total`.

**`engine/trades.py`**
- `evaluate(league_state, team_a, gives_a, team_b, gives_b) -> TradeResult`
  with, per side: `before`, `after`, `gain` (lineup-delta with WW
  back-fill), `raw_given`, `raw_received` (sum of `ros_total`), and
  `favors` (team with the larger gain; "even" if |diff| < 1 pt/week x
  weeks). Pure function over the same dicts that end up in JSON, so the JS
  twin is a line-by-line port.
- Fixture `tests/fixtures/trade_case.json` (a small league state + 3 trades
  + expected results) consumed by the pytest and by a Node-free JS parity
  check: `tests/test_trade_js_parity.py` runs `node docs/js/trade.js
  --fixture` if `node` is on PATH, else skips with a warning (the CI image
  has node).

**`engine/standings.py`**
- State from ESPN: wins/losses/ties/points_for per team, remaining
  matchups (unplayed scoreboard entries), divisions.
- Simulation: for each iteration and each remaining regular-season matchup,
  draw `score = Normal(mean_w, sd_w)` per team where `mean_w` = that week's
  optimal-lineup total and `sd_w = sqrt(sum(starter sd_w^2))`; accumulate
  wins/PF; seed per Part 1 #11; tally `playoff_odds`, `seed_probs[1..6]`,
  `bye_odds` (seeds 1-2), `expected_wins`, `division_win_odds`. Use
  `numpy.random.default_rng(seed)` and vectorise over iterations (one
  `(iterations, teams)` array per week).
- Test: 2-team, 1-week league with identical means -> ~0.5 each; a team
  already clinched -> 1.0.

### Stage 5 - pipeline and data contracts

`python -m engine.pipeline [--league slug] [--out docs/data]` iterates
`config/leagues/*.yml`, runs Stages 1-4 per league, writes JSON, and writes
`docs/data/leagues.json`. One league failing (e.g. bad cookies) logs and
continues; exit code non-zero at the end if any league failed so the
workflow shows red.

Files (all under `docs/data/<slug>/`):

```jsonc
// leagues.json (top level)
[{"slug":"o-league","name":"The \"O\" League","season":2026,"scoring_format":"standard","positions":["QB","RB","WR","TE","K"]}, ...]

// meta.json
{"season":2026,"current_week":3,"weeks_played":2,"final_week":17,"reg_season_count":14,
 "generated_at":"2026-09-23T10:02:11Z","rankings_source":"fantasypros","rankings_scraped_at":"...",
 "curve_seasons":[2022,2023,2024,2025],"curve_current_weight":0.125,"pa_season_used":2025,"pa_prior_weight":0.67,
 "slots":{"QB":1,"RB":1,"WR":2,"TE":1,"RB/WR/TE":2,"K":1},
 "slot_eligibility":{"RB/WR/TE":["RB","WR","TE"], ...},
 "teams":[{"team_id":1,"name":"Team Steve","manager":"Steve","abbrev":"Stev","division":"Strike"}, ...],
 "warnings":["3 players unmapped to nflverse ids", ...]}

// players.json - every rostered player + every fetched free agent
[{"id":"00-0036963","espn_id":4426515,"fp_id":23180,"name":"Puka Nacua","position":"WR","nfl_team":"LA","bye":11,
  "fantasy_team_id":1,                       // null = free agent
  "lineup_slot":"WR","injury_status":"ACTIVE","zeroed_this_week":false,"zero_reason":null,
  "ros_pos_rank":2,"rank_ave":2.36,"rank_std":1.03,"week_pos_rank":2,
  "baseline_ppg":24.8,"ros_total":347.2,"reg_total":272.8,"playoff_total":74.4,"this_week":23.1,
  "value_delta":95.3,"value_delta_ww":61.0,   // null for free agents
  "position_avg_ratio":1.23,                   // baseline / curve.position_avg (display only)
  "weekly":[{"week":3,"opponent":"PHI","home":true,"index":0.91,"rank":9,"projected":23.1,"sd":8.2}, ... {"week":11,"opponent":null,"projected":0}, ...],
  "espn_projected_total":231.4,"percent_owned":99.9}]

// teams.json
[{"team_id":1,"name":"Team Steve","manager":"Steve","wins":2,"losses":0,"points_for":260.1,
  "lineup_total_ros":1650.4,"position_strength":{"QB":{"ppw":21.3,"vs_avg":2.1,"rank":2}, ...},
  "depth":{"QB":[{"id":"...","value_delta":30.1,"value_delta_ww":12.0}], "RB":[...], ...},
  "pickups":[{"add":"id","drop":"id","gain":14.2}],
  "trade_targets":[{"partner_team_id":4,"give":["id"],"get":["id"],"gain_self":11.0,"gain_partner":6.5}],
  "partner_summary":[{"partner_team_id":4,"their_surplus":"RB","your_surplus":"WR"}]}]

// lineups.json  - this week's optimal lineup per team
{"1":{"total":121.4,"slots":{"QB":"id","RB":"id","WR1":"id","WR2":"id","TE":"id","FLEX1":"id","FLEX2":"id","K":"id"},
      "bench":["id", ...],"changes_vs_espn":[{"slot":"FLEX2","espn":"id","optimal":"id","delta":3.1}]}}

// matchups.json - the points-allowed index table for the matchup view
{"QB":{"ARI":{"index":1.08,"rank":24,"allowed_ppg":18.9,"l5_allowed_ppg":20.1}, ...}, "RB":{...}, ...}

// standings.json
[{"team_id":1,"wins":2,"losses":0,"ties":0,"points_for":260.1,"division":"Strike",
  "expected_wins":9.4,"playoff_odds":0.91,"bye_odds":0.41,"division_win_odds":0.62,
  "seed_probs":{"1":0.22,"2":0.19,"3":0.2,"4":0.15,"5":0.1,"6":0.05},"iterations":10000}]

// unmapped.json - see Stage 1
```

Stage 5 exit checks (do these by hand, both leagues):
- Ja'Marr Chase / Puka Nacua / Jahmyr Gibbs land at or near the top of their
  positions with `ros_total` in a plausible range (~15-25 PPG baseline in
  standard scoring; compare against the sheet's Rank Pts curve: QB1 ~27.9,
  RB1 ~23.7, WR1 ~28.1, TE1 ~15.7, K1 ~10.4 in the main sheet's 2025 block).
- A team with two good RBs shows RB1 `value_delta` well above RB2's.
- Every rostered player in ESPN appears once in `players.json`; sum of
  optimal lineups this week is within a sane band of ESPN's projections.
- `weekly` arrays sum to `ros_total` (assert in code, not just by eye).

### Stage 6 - frontend (`docs/`)

Plain HTML/CSS/JS, fetch JSON relative to the page, no framework, no build.
`js/data.js` loads `data/leagues.json`, keeps the selected league in
`localStorage` and in the URL hash (`#league=o-league&view=trade`), then
fetches that league's files once and caches them in memory.

Views (tabs):
1. **Team strength**: pick "your team" (remembered); position strength
   bars vs league average; depth table (the NMD values); pickup suggestions
   with the drop; trade targets; league-wide table of position strength.
2. **Trade calculator**: pick two teams, tick players each side gives;
   shows before/after lineup totals, gain per side, raw ROS given/received,
   "favors" verdict; runs `trade.js` (Python twin) entirely in the browser.
3. **Start/Sit**: your roster this week with opponent, matchup rank badge
   (1 toughest .. 32 easiest, colour scale), projected points, injury
   status, and the optimal lineup with "changes vs your ESPN lineup".
   Toggle to view any team.
4. **Rankings**: sortable table of all players (rostered + FA) by position,
   columns ROS rank, baseline, ROS/reg/playoff totals, this week, owner;
   filter to free agents only.
5. **Standings / odds**: current record, expected wins, playoff/bye odds,
   seed distribution as a small stacked bar.
6. **Matchups**: the points-allowed index heat table per position (from
   `matchups.json`) for the season and L5.

Header shows league name, `current_week`, `generated_at`, and the
`rankings_source` warning if the fallback was used. Follow
`irrigation_planner/docs` for structure and CSS conventions; keep it
responsive (tables scroll inside their own container).

### Stage 7 - automation and deployment

`.github/workflows/build.yml`, modelled on numberball's `key_moments.yml`:
`on: push (paths: config/**, ingest/**, engine/**, docs/**, requirements.txt,
the workflow)`, `schedule: cron '0 10 * * *'` (10:00 UTC daily; GitHub may
delay cron on quiet repos), `workflow_dispatch`. Job: checkout, python 3.11
with pip cache, `pip install -r requirements.txt`, `pytest -q`,
`python -m engine.pipeline` with `ESPN_S2_AGS`/`SWID_AGS` from secrets,
`configure-pages` -> `upload-pages-artifact` (path `docs`,
`include-hidden-files: true`) -> `deploy-pages`. Add `docs/.nojekyll`.
Cache `.cache/nflverse` with `actions/cache` keyed on the past-season files
so the 4 history seasons are not re-downloaded daily.

Manual steps for Alex (put them in README):
1. Repo Settings -> Pages -> Source: **GitHub Actions**.
2. Secrets `ESPN_S2_AGS` and `SWID_AGS`: log into ESPN in a browser, open
   DevTools -> Application -> Cookies -> `espn.com`, copy `espn_s2` (long
   URL-encoded string) and `SWID` (including the braces). They expire
   roughly yearly.
3. Run the workflow once via "Run workflow" and confirm both leagues build.

### Stage 8 - validation against the sheets

Compare, for the main league, against the 2025 sheet's numbers already
extracted in Part 0.6 (curve top ranks, dampening, the depth-table shape)
and, for the second league, against its `Teams` depth table (e.g. Alex:
RB1 13.2, RB2 10.3, RB3 0.0; QB1 1.3; K1 2.7; DST1 3.0 - these are
end-of-2025 weekly-unit values, so only the *pattern* should match: steep
RB1/RB2 drop-off, near-zero third options). Write the comparison into
README under "Validation" with the date.

---

## Part 4: Risks and things to verify en route

1. **Season timing today.** As of 2026-09-09 there are no 2026 stats and
   the ESPN week is 1. Everything must work with `weeks_played=0`: curve =
   history only, matchup index = 2025 only, no weekly anomaly flag until the
   FantasyPros weekly page has week >= 1. Stage 5's first real run is in
   this state; a second validation after week 1 completes (2026-09-15) is
   required before calling the pipeline done.
2. **FantasyPros page changes** (URL names, `ecrData` shape, bot blocking
   from GitHub's IP range). The fallback in Stage 1 covers total failure;
   a partial change (e.g. renamed field) must fail the scrape loudly, not
   produce zeros. Verify the weekly-page URL names for standard scoring
   (`rb.php`, `wr.php`, `te.php`) before relying on them.
3. **ESPN stat-id mapping for D/ST** in the second league cannot be seen
   until the cookies exist. Build the map from `SETTINGS_SCORING_FORMAT_MAP`
   and let the loud-failure check (Stage 2) tell you what is missing.
4. **`fumbles_lost_total` semantics** in nflverse (whether it already sums
   passing/rushing/receiving fumbles lost) - check on one known game.
5. **Return TDs**: nflverse has one `special_teams_tds` column; ESPN
   scores KRTD/PRTD separately (both 6 here). Fine for these leagues;
   document the assumption.
6. **Division-winner seeding**: `playoffSeedingRule` is only the tiebreak.
   Confirm on ESPN's league settings page whether division winners are
   guaranteed top seeds; default `division_winners_first: true`.
7. **Kickers and DSTs in the id map**: many rookie kickers lack
   `fantasypros_id`/`gsis_id`; the name fallback must handle them, and DST
   must be keyed purely by team.
8. **Trade-target enumeration cost** (Stage 4) - measure before widening.
9. **nflreadpy cache**: the library has its own cache settings; if it caches
   the 404 for 2026 stats, the pipeline must not keep believing the season
   has no data after week 1. Bust/ignore its cache for the current season.
10. **ESPN `Team.schedule` is regular season only** (len 14) while
    `scoreboard(week)` covers playoff weeks; use `scoreboard` for matchups.
11. **Standard-scoring WR values look high in the sheet's `Avg` row**
    (WR 20.3 vs QB 19.6) - the sheet's starter pool or eligibility differed
    from what the rebuild does; do not tune to match it, just note it.

---

## Part 5: Open questions for Alex (none block the build)

1. Artifact deploy (no data commits) vs committing JSON to `main` daily -
   plan assumes artifact deploy (Part 1 #9). Say so if you want history.
2. `value_delta_ww` (waiver back-fill) as the default basis for trade
   verdicts and pickups - plan assumes yes (Part 1 #6).
3. Whether to also show ESPN's own weekly projection next to ours in the
   Start/Sit view as a sanity reference - plan includes it (cheap, already
   fetched).
4. Anything specific from the Any Given Sunday sheet beyond D/ST that the
   main plan does not cover (its settings block matched the main sheet's).
