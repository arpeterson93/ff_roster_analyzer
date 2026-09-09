# FF Roster Analyzer — Python rebuild

## Context

The current tool is a single, hand-built Google Sheet ("Fantasy Football Start
Sit 2025 - TOL", 23 tabs) for a 12-team ESPN league. It computes custom player
value metrics, opponent-adjusted matchup difficulty, optimal lineups, and
playoff-seeding odds — but the data feeding it is pulled manually (copy-paste
CSV blobs into `Import QB-TE`/`Import K`, plus Sheets-only `IMPORTHTML`/`QUERY`
calls that only work inside Sheets), the formulas are undocumented, and it
can't be reused for another league.

Goal: replace it with a Python pipeline + static site that:
1. **Identifies team strengths/weaknesses by position** — to guide who to
   pick up, drop, or target in a trade (including evaluating specific
   proposed trades).
2. **Shows upcoming defensive matchup difficulty per player** — to guide
   weekly start/sit decisions.

Both are automated from real NFL data (`nflreadpy`) and this league's actual
ESPN data (`espn-api`) — no more manual pulls, no dependency on Sheets-only
functions.

**Scope note:** this plan folds what would otherwise be "phase 2" (multi-league
config, a full trade-equity calculator) directly into this build so the first
version is comprehensive and viable on its own, not a stripped-down MVP with
a long deferred list. The only things intentionally left as forward-compatible
but *not* implemented now are non-ESPN platforms (Yahoo/Sleeper) — the
ingestion layer is designed so those can be added as adapters later without
a redesign, but only the ESPN adapter is built now.

## Handoff note

This plan is written to be picked up cold by another model (Fable) for
detailed implementation planning, and then by a different model for the
actual build. It therefore includes the source-system context and data
contracts below so neither downstream step needs to re-inspect the original
Google Sheet.

## Source system reference (for context only — not being ported as-is)

Original file: "Fantasy Football Start Sit 2025 - TOL" Google Sheet, 23 tabs,
12-team ESPN league. Roster slots per team: QB, RB, WR, WR, TE, FLEX×5, K.
Tabs directly informing this rebuild's scope:

- **League** (~415 player rows): central player database — Manager, Player,
  Team, POS, ROS Rank, Value, NMD ("next man down" depth value), Trade value,
  Relative Value, weekly stat/rank columns. Replaced by `engine/valuation.py`,
  which **keeps the user's existing valuation methodology** (see below) but
  automates its inputs and makes the mechanics explicit/documented.
- **Lineups**: per-manager weekly best-eligible-player-per-slot, pulled via
  array formulas from `League`. Replaced by `engine/lineup.py`.
- **PA** / **PA 5 Wk**: NFL-team stats/points allowed by opposing position
  (QB/RB/WR/TE/K), season and trailing-5-week. Fed by a *manual* copy-paste
  staging tab (`Import QB-TE`, `Import K`) — this manual step is exactly what
  the user wants automated away. Replaced by `engine/matchups.py`.
- **Opp Adj** / **NFL Sch**: maps each defense's points-allowed-by-position
  into a relative "expected offensive output" adjustment factor. This is the
  week-to-week adjustment mechanism the user explicitly wants to keep — see
  "Valuation engine" below. Folded into `engine/matchups.py`.
- **PF** / **PF 5 Wk**: NFL teams' own offensive output. Not carried forward
  as a separate table — not directly actionable once points-allowed is
  computed directly; superseded by the Opp Adj mechanism above.
- **Standings** / **FF Sch**: current standings + playoff seeding/tiebreaker
  simulation. Replaced by `engine/standings.py`.
- **Teams**: manual per-manager roster tracking. Replaced entirely by live
  ESPN roster data via `ingest/espn_client.py`.
- **Tables**: 500+ row manual FantasyPros-vs-ESPN player name reconciliation.
  Replaced by `nflreadpy.load_ff_playerids()` (DynastyProcess cross-platform
  ID map) — join on IDs, not fuzzy name matching.
- **ROS Ovr / ROS Pos / Wk Pos**: consensus rest-of-season and weekly
  positional rankings, currently pulled via Sheets-only `IMPORTHTML`/`QUERY`
  against FantasyPros (can't run outside Sheets). This is the **consensus
  rank input** the valuation engine needs — see "Sourcing consensus ranks"
  below for the automated replacement.
- **Rank Pts / Player Stats**: weekly actual stat lines + manual
  injury/status annotation. Actual stats are replaced by
  `nflreadpy.load_player_stats()`; manual injury annotation is replaced by
  ESPN's own injury-status field on rostered/available players (available via
  `espn-api`), removing the manual "who checked this player" workflow.
- **RV / SV**: parallel valuation scenario tables ("w/o FLEX" / "w/ FLEX" /
  "w/ WW"); the "w/ WW" (waiver-wire) block was broken (`#N/A` on every row)
  in the source — the old system's waiver-value logic was never finished.
  This build's `engine/team_strength.py` pickup/trade-target logic replaces
  it with a working version built on the same valuation output.

## Valuation engine — kept methodology, automated inputs

The user's current valuation logic is intentionally being **kept**, not
replaced, because it is simple, explainable, and transparent — the priority
over a more "sophisticated" but opaque redesign. The method (as described by
the user):

1. Start from a **consensus rest-of-season (ROS) rank per position** for each
   player (e.g. "WR12").
2. **Map that rank to an average weekly fantasy-point output** for players
   who typically hold that rank at that position — giving an expected
   points-per-week baseline.
3. Sum that baseline across all remaining weeks to get an **expected ROS
   total**.
4. **Adjust week-to-week** using that week's opponent: each defense's
   points-allowed-by-position has been mapped to a relative "expected
   offensive output" index (the `Opp Adj` mechanism), and the player's
   weekly baseline is scaled by their opponent's index for that week.
5. **Reconcile**: after applying all the weekly adjustments, rescale them so
   they still sum to the expected ROS total from step 3 — matchup strength
   redistributes value across weeks, it doesn't change the season total.

This whole mechanism carries forward as-is. What changes is *only* how the
inputs are sourced:

### Sourcing consensus ranks (replaces the Sheets-only IMPORT pull)
Use `nflreadpy.load_ff_rankings()` (sourced from DynastyProcess.com) as the
automated consensus-rank input, refreshed on the same cadence as the rest of
the pipeline. This directly replaces the `IMPORTHTML`/`QUERY`-against-
FantasyPros mechanism that only worked inside Sheets. *(Open decision: which
`load_ff_rankings()` mode — "draft"/"week"/"all" — best represents a
rest-of-season view; confirm during implementation planning.)*

### Building the rank → weekly-output curve
Self-sourced from `nflreadpy.load_player_stats()` history: for each position,
build an empirical table of "a player ranked Nth at this position typically
scores X fantasy points/week" using multiple past seasons of actual
performance (fantasy points computed via this league's real scoring rules,
`engine/scoring.py`), so the curve itself doesn't depend on any external
provider even though the *live* rank input (above) does.

### Opponent adjustment index (Opp Adj replacement)
`engine/matchups.py` computes each of the 32 NFL teams' points allowed by
position (season-to-date and trailing-5-week, from `nflreadpy`) and expresses
it as a relative index around league average (e.g. 1.15 = allows 15% more
than average to that position) — this is the multiplier applied in step 4
above.

## Architecture

```
ESPN (espn-api, per league config)  ──┐
                                        ├──►  ingest/  ──►  engine/  ──►  docs/data/<league>/*.json  ──►  docs/ (static site)
nflreadpy / DynastyProcess rankings ──┘           │              │                                            ▲
                                                    │              │                                            │
                                   GitHub Actions cron (.github/workflows/sync.yml) runs the whole pipeline weekly per configured league, commits refreshed JSON
```

- **No database.** Each pipeline run recomputes everything from ESPN +
  nflverse/DynastyProcess source data and overwrites plain JSON files under
  `docs/data/`, committed to the repo. The frontend fetches those JSON files
  directly — same "static site" shape as `irrigation_planner`'s `docs/`, but
  with the data-refresh mechanism from `numberball`'s `sync.yml` (scheduled
  workflow running a Python script) instead of a live DB write.
- **No backend server at runtime.** All computation happens in the cron job;
  the site is pure static HTML/CSS/JS on GitHub Pages.
- **Multi-league from the start**: the pipeline iterates over a list of
  league configs (`config/leagues/*.yml` — league ID, season, display name,
  and which secret holds its ESPN credentials if private) and writes each
  league's output under its own `docs/data/<league-slug>/` namespace. The
  user's own league is just the first entry. The frontend has a league
  switcher reading `docs/data/leagues.json` (the list of configured leagues).

## Data ingestion (`ingest/`)

**`ingest/espn_client.py`** — wraps `espn-api`'s `League` object, parameterized
by league ID + season (from a league config, not hardcoded) with
`ESPN_S2`/`SWID` credentials as secrets for private leagues:
- League settings: scoring rules, roster slots, current week.
- Teams & rosters, including each rostered/available player's ESPN injury
  status (replaces the old manual injury annotation).
- Matchup schedule: who plays whom each week (feeds the playoff simulator).
- Free agents / waiver pool (feeds pickup suggestions).
- Structured as a thin adapter conforming to a small internal interface
  (`LeagueClient`: `get_settings()`, `get_rosters()`, `get_matchups()`,
  `get_free_agents()`) so a Yahoo or Sleeper adapter could be added later
  without touching `engine/`. Only the ESPN adapter is implemented now.

**`ingest/nfl_data.py`** — wraps `nflreadpy`:
- `load_schedules(season)` — game results, each team's real-NFL opponent per
  week (including bye weeks).
- `load_player_stats(season)` — weekly per-player raw stat lines (passing/
  rushing/receiving/kicking), and multiple past seasons for the rank→output
  curve (see Valuation engine above).
- `load_ff_playerids()` — cross-platform player ID map (DynastyProcess),
  replacing the old `Tables` tab's manual name reconciliation.
- `load_ff_rankings()` — consensus rank input for `engine/valuation.py`.

## Calc engine (`engine/`)

**`engine/scoring.py`** — apply a league's actual ESPN scoring rules to a raw
stat line → fantasy points. Single source of truth used everywhere else.

**`engine/valuation.py`** — implements the 5-step methodology described
above (rank → weekly baseline → ROS total → opponent-adjusted weekly values →
reconciled to ROS total). Output per player: expected ROS total, and a
per-remaining-week adjusted projection array that sums to it.

**`engine/matchups.py`** — per-position, per-NFL-team points-allowed (season
+ trailing-5-week) expressed as a relative index vs. league average. Feeds
both the opponent-adjustment step in valuation and the start/sit matchup
view directly.

**`engine/team_strength.py`** — per fantasy team, aggregate rostered players'
expected ROS value by position vs. league average and vs. every other team,
surfacing surplus/deficit per position. Cross-references other teams'
surplus/deficit to surface plausible **trade targets**, and feeds pickup/drop
suggestions from the free-agent pool.

**`engine/trades.py`** — full trade-equity calculator: given a specific
proposed set of players exchanged between two teams, sum each side's
expected-ROS-value given up vs. received (using `engine/valuation.py`'s
output) and report the net value differential per team, so a specific trade
offer can be evaluated, not just plausible targets surfaced.

**`engine/lineup.py`** — given a roster, a league's roster-slot rules, and
each player's opponent-adjusted weekly projection, pick the best lineup per
slot (replaces `Lineups`). Simple greedy assignment is sufficient given
league roster sizes — no need for a full ILP solver.

**`engine/standings.py`** — playoff seeding/odds. Current wins/points-for
(from ESPN) + remaining ESPN matchup schedule + each team's weekly projection
distribution → Monte Carlo simulation for seeding probabilities (replaces
`Standings`/`FF Sch`).

**`engine/pipeline.py`** — orchestrates all of the above per configured
league and writes `docs/data/<league-slug>/*.json` plus the top-level
`docs/data/leagues.json` index.

## Data contracts (`docs/data/`)

```jsonc
// docs/data/leagues.json — index of configured leagues, for the frontend switcher
[ { "slug": "brillo-league", "name": "Fantasy Football Start Sit", "season": 2026 } ]

// docs/data/<league>/meta.json — run metadata
{ "season": 2026, "week": 3, "generated_at": "2026-09-14T12:00:00Z" }

// docs/data/<league>/players.json — one row per rostered/available player
[
  {
    "player_id": "nflverse-or-espn-id",
    "name": "Ja'Marr Chase",
    "position": "WR",
    "nfl_team": "CIN",
    "fantasy_team": "Brillo",       // null if a free agent
    "consensus_ros_rank": 3,
    "expected_ros_total": 187.4,
    "weekly_projections": [ { "week": 4, "opponent": "PIT", "matchup_index": 0.88, "projected_pts": 14.2 }, "..." ],
    "this_week_matchup_rank": 4      // 1 = toughest matchup for this position, 32 = easiest
  }
]

// docs/data/<league>/teams.json — one row per fantasy team
[
  {
    "manager": "Brillo",
    "position_strength": { "QB": 1.2, "RB": -0.8, "WR": 2.1, "TE": 0.1, "K": 0.0 }, // vs. league avg
    "suggested_trade_targets": [ { "manager": "Chris", "their_surplus_pos": "RB", "your_surplus_pos": "WR" } ],
    "suggested_pickups": [ "player_id", "..." ]
  }
]

// docs/data/<league>/trade_evaluations.json — on-demand results if the frontend posts a proposed trade
{ "team_a": { "manager": "Brillo", "gives": ["player_id"], "value_given": 42.1 },
  "team_b": { "manager": "Chris", "gives": ["player_id"], "value_given": 38.7 },
  "net_favors": "Brillo" }

// docs/data/<league>/lineups.json — this week's optimal lineup per team
{ "Brillo": { "QB": "player_id", "RB1": "player_id", "...": "..." } }

// docs/data/<league>/standings.json
[
  { "manager": "Brillo", "wins": 8, "losses": 2, "points_for": 1180.4,
    "playoff_odds": 0.94, "seed_probabilities": { "1": 0.2, "2": 0.35, "...": "..." } }
]
```

Note on `trade_evaluations.json`: a *specific* proposed trade can't be
precomputed for every possible combination. `engine/trades.py` should be
callable both from the pipeline (for nothing in particular) and, if the
frontend needs live "what if" trade evaluation rather than just precomputed
suggestions, that implies a small piece of the trade calculator may need to
run client-side (all inputs — player values — are already in `players.json`,
so a client-side JS reimplementation of the simple sum/compare math is
feasible without a backend). Flagged as an implementation-planning decision.

## Frontend (`docs/`)

Static HTML/CSS/JS, no build step, no framework — matching
`irrigation_planner`'s pattern. Views:
- **League switcher** (if more than one league is configured).
- **Team strength dashboard**: per-position surplus/deficit for your team
  (and league-wide comparison), with suggested pickup/drop/trade targets.
- **Trade calculator**: pick players from two teams, see value given/received
  and net favor — live in the browser against `players.json`.
- **Start/Sit matchup view**: your current roster this week, each player's
  opponent and matchup difficulty rank, suggested optimal lineup.
- **Player rankings**: sortable expected-ROS-value leaderboard by position.
- **Standings / playoff odds**: current standings + simulated seeding
  probabilities.

Deployed via GitHub Pages (`Settings -> Pages -> Deploy from branch -> main
/docs`), same as `irrigation_planner`.

## Automation (`.github/workflows/sync.yml`)

Scheduled GitHub Actions workflow (weekly cadence — NFL data doesn't need
5-minute polling like live baseball plays) that runs `engine/pipeline.py`
across every configured league, regenerates `docs/data/**/*.json`, and
commits the changes. Plus `workflow_dispatch` for manual runs, matching
`numberball`'s `sync.yml` pattern. `ESPN_S2` / `SWID` (per private league)
stored as repo secrets, referenced by name from each league's config.

## Repo layout

```
docs/                  static frontend (index.html, css/, js/) + data/<league-slug>/ (generated JSON, committed)
config/leagues/*.yml   one file per configured league: id, season, display name, credential secret names
ingest/                espn_client.py (LeagueClient interface + ESPN adapter), nfl_data.py
engine/                scoring.py, valuation.py, matchups.py, team_strength.py, trades.py, lineup.py, standings.py, pipeline.py
tests/                 pytest golden tests for scoring/valuation/matchup math
.github/workflows/sync.yml
requirements.txt
README.md
.env.example            ESPN_S2 / SWID placeholders for local dev (actual .env gitignored)
```

## Verification

- `pytest tests/` — golden-value tests for `engine/scoring.py` (known stat
  line → known fantasy points for a league's scoring settings),
  `engine/matchups.py` (known games → known points-allowed index), and
  `engine/valuation.py` (known rank + known opponent sequence → known
  reconciled weekly projections summing to the expected ROS total), same
  pattern as `irrigation_planner/tests/`.
- Run `python -m engine.pipeline` locally against the real league config and
  manually sanity-check `docs/data/<league>/*.json` against a couple of known
  players/matchups.
- Serve `docs/` locally (`python -m http.server`) and click through each view
  against the generated JSON before wiring up the GitHub Actions schedule.

## Open decisions for the next planning pass

Judgment calls this plan makes a reasonable default on, which the next
planning step (Fable) should confirm or revisit rather than assume final:

- **`load_ff_rankings()` mode**: confirm which option ("draft"/"week"/"all")
  best represents a live rest-of-season consensus rank, and its refresh
  cadence relative to the pipeline's weekly run.
- **Rank→weekly-output curve construction**: how many past seasons to use,
  whether rank is by season-long PPG finish or a rolling in-season rank, and
  how to handle bye weeks / missed games in the historical sample.
- **Reconciliation edge cases**: how the "rescale to ROS total" step in
  `engine/valuation.py` behaves when a bye week falls within the remaining
  weeks, or when an opponent-adjustment would push a projection negative.
- **Playoff simulator method**: Monte Carlo proposed; confirm iteration count
  and how per-team weekly score variance is modeled.
- **Lineup optimizer**: greedy assignment proposed as sufficient for this
  roster size (11 slots incl. 5 FLEX); confirm this can't produce a
  suboptimal assignment vs. a proper matching approach given FLEX
  eligibility overlaps.
- **Trade calculator execution location**: precomputed suggestions only vs.
  a live client-side "what if" calculator (see note under Data contracts).
- **League configs**: the user's actual ESPN league ID/season, and whether
  any additional leagues should be configured now or just the one to start
  (the multi-league *architecture* is built either way).
- **Update cadence**: weekly proposed; confirm whether daily (for
  injury/roster-move freshness between games) is wanted during the season.
