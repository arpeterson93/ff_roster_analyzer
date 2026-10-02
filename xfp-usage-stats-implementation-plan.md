# xFP + air yard share integration: implementation plan

Written 2026-10-02 by Sonnet directly in conversation with Alex (no separate
fable-prompt this round — decided explicitly: build the plan here, Sonnet
implements). Verified live against nflreadpy 0.1.5 and the current repo.
Reviewed by Opus 5.5 the same day against the real repo code (not just this
document's prose) — findings cross-checked against the live data/source
before being applied; one was rejected (fumbles — no `_exp` column exists
anywhere, confirmed), the rest fixed in place and marked throughout.

---

## Part 0 — Verified facts

All checked live on 2026-10-02 against nflreadpy 0.1.5 and the current repo
(`main`, commit `8ff5d77`).

### 0.1 ffopportunity data (the xFP source)

1. `nfl.load_ff_opportunity(seasons=..., stat_type="weekly")` returns 159
   columns, one row per player-week, incl. per-component actual AND `_exp`
   pairs (`pass_completions_exp`, `pass_yards_gained_exp`,
   `pass_touchdown_exp`, `pass_interception_exp`, `pass_two_point_conv_exp`,
   `rec_yards_gained_exp`, `receptions_exp`, `rec_touchdown_exp`,
   `rec_two_point_conv_exp`, `rec_first_down_exp`, `rush_yards_gained_exp`,
   `rush_touchdown_exp`, `rush_two_point_conv_exp`, `rush_first_down_exp`),
   plus its own pre-baked `pass_fantasy_points_exp`/`rec_fantasy_points_exp`/
   `rush_fantasy_points_exp`/`total_fantasy_points_exp` (generic scoring
   assumption — **not used**, see Decision D2).
2. **Updates live, same-week as the rest of the pipeline.** Pulled 2026
   (current season) weekly data directly: 1,048 rows through week 4, matching
   today's actual week. No lag.
3. `stat_type="pbp_pass"` / `"pbp_rush"` give the play-level version, keyed by
   `game_id`/`play_id`/`passer_player_id`/`receiver_player_id`/
   `rusher_player_id` (gsis-id format, `00-00xxxxx`) — **confirmed 100% join
   coverage** (18,465/18,465 rows) against `nfl.load_pbp()` on
   `(game_id, play_id)` for 2025.
4. **Per-play expected-yards formula confirmed from ffopportunity's own R
   source** (`github.com/ffverse/ffopportunity`, `R/ep_summarize.R`,
   fetched and read directly 2026-10-02 — no longer a guess):
   `yards_gained_exp = complete_pass_exp * (yards_after_catch_exp + air_yards)`.
   Same quantity serves both the passer's expected passing yards and the
   receiver's expected receiving yards on a given play. **Naming note
   (caught in Opus review, confirmed against the actual live column list):**
   `complete_pass_exp` is `ep_summarize.R`'s own internal rename — the
   column actually published in `pbp_pass` (what nflreadpy downloads) is
   `pass_completion_exp`. Use `pass_completion_exp` when implementing; it's
   the same value, just the pre-rename name. Also null-guard `air_yards`/
   `yards_after_catch_exp` before the multiply — both come back null on
   throwaways/spikes/no-play, where the formula shouldn't run at all
   (those plays contribute 0 expected yards, not a `TypeError`).
5. **The rush "duplicate" columns aren't duplicates** (also confirmed from
   `R/ep_predict.R`): `rushing_yards_exp`/`rushing_td_exp`/`rushing_fd_exp`
   are the raw model outputs; `rush_yards_exp`/`rush_touchdown_exp`/
   `rush_first_down_exp` are the *cleaned* versions (kneel-downs forced to
   -1/0, 2pt-attempt plays zeroed since those are scored separately via
   `two_point_conv_exp`) — these cleaned columns are what's actually
   published in `pbp_rush`, and are what we use.
6. **ffopportunity's own per-play fantasy-points formula is hardcoded to one
   fixed scoring system and isn't even published.** Also found in
   `ep_summarize.R`: it computes a play-level `fantasy_points_exp` using
   1pt/reception, 0.1pt/receiving-yard, 6pt rec TD, 4pt pass TD,
   0.04pt/passing-yard, 2pt two-point, -2pt INT, -2pt fumble lost — baked in
   before the published `pbp_pass`/`pbp_rush` files are written (confirmed:
   not in their actual column list). This is the direct answer to "why not
   just use expected fantasy points for that play": that number doesn't
   exist in the data we have, and even inside ffopportunity's own source it
   assumes one generic scoring system — exactly the problem D2 already
   solved at the weekly level by going through `ScoringRules.points_for_row()`
   instead. The per-play visual needs the same treatment, which is why we
   need the components (expected receptions/yards/TDs/INTs), not a single
   per-play xFP number.
7. No kicker or DST model exists anywhere in ffopportunity — it only covers
   pass/rush/reception opportunity. xFPTS will never exist for K/DST.

### 0.2 Route share / participation (dropped — Decision D1, keeping the proof)

8. `nfl.load_participation()`'s own wrapper caps seasons at
   `get_current_season(roster=True) - 1` (raises `ValueError` for 2026).
9. **Independently confirmed at the source**, bypassing the wrapper: hit
   `https://github.com/nflverse/nflverse-data/releases/download/pbp_participation/pbp_participation_2026.parquet`
   directly → `404`. Queried the GitHub Releases API for the
   `pbp_participation` tag — newest asset in any format (parquet/csv/rds/qs)
   is `pbp_participation_2025`. The data genuinely doesn't exist upstream yet
   for the in-progress season; this is nflverse's well-known one-season lag
   since losing their real-time charting feed, not an nflreadpy restriction.
10. Participation's `route` column (where present) is a **single route-type
   label per play** (`QUICK OUT`, `GO`, `SCREEN`, etc.) describing the
   **targeted receiver's** route on that one play — not a per-player
   route-ran flag for everyone on the field. True route participation would
   still need the `offense_players`/`offense_positions` on-field-for-a-
   dropback proxy, which has the same season-lag problem. Moot given D1.

### 0.3 Existing architecture to reuse

11. `engine/scoring.py`'s `ScoringRules.points_for_row(row: dict) -> float`
   (and `.from_espn(scoring_items, is_dst=...)`) is the single
   league-scoring-aware function that turns a raw stat dict into points,
   already used for both real actual scoring (via `engine/play_log.py`'s
   `_play_stat_row_and_label`) and already parameterized per-league (O
   League = standard, Any Given Sunday = half-PPR — confirmed in memory).
   **This is what xFPTS must run through, not ffopportunity's own generic
   `*_fantasy_points_exp` columns** — see Decision D2.
12. `engine/play_log.py`: per-play actual-points breakdown powering the
   Player Modal's expandable Game Log row. `build_game_play_index` indexes
   one week's pbp rows by `{gsis_id: [rows]}`; `scoring_plays_for_player`/
   `zero_point_plays_for_player`/`incomplete_targets_for_player` each build a
   per-role stat_row dict and call `rules.points_for_row(stat_row)`. This is
   the exact pattern an "expected fantasy points by play" table should mirror.
13. `engine/pipeline.py:151-181` (`_actual_weekly_stats`): builds each
    player-week's `actual` object, shape `{"stats": {...}, "points": float}`
    or `None` for an unplayed/bye/inactive week. This is where
    `xfp_points` needs to be added as a sibling key.
14. `engine/pipeline.py:1080-1102` (`_usage_stats_for_week`): returns RAW
    counts (not pre-divided percentages) — `offense_snaps`,
    `team_offense_snaps`, `team_rb_carries`, `team_targets` — explicitly
    documented as deliberate: the frontend derives both a single-week % and
    a true season split (sum of numerators / sum of denominators, not an
    average of weekly %s) from these raw counts. Air yard share must follow
    the exact same convention: `player_air_yards`/`team_air_yards` raw
    counts, not a pre-computed percentage.
15. `engine/pipeline.py:1286-1315`: builds the Game Log's per-play detail —
    loads `nd.play_by_play(season, current_season=season)` **freshly** here
    (not reused from an earlier pipeline variable; relies on
    `ingest/nfl_data.py`'s own `_cached_load` to avoid re-downloading).
    Writes the result as `result["game_log_plays.json"]` at line 1765 — a
    **separate top-level JSON file**, not nested inside `players.json`.
16. `ingest/nfl_data.py:95-106` (`play_by_play(seasons, current_season=None)`):
    the existing `load_pbp` wrapper. Pattern: `_cached_load` + a
    `try/except ConnectionError` graceful-empty-frame fallback (probes a
    known-good season's schema, returns `.clear()`) +
    `_normalize_team_columns(df, ["posteam", "defteam", "home_team",
    "away_team"])`. No `load_ff_opportunity` wrapper exists yet — a new one
    should follow this exact signature/fallback/normalize shape.
17. `engine/matchups.py:28-38` (`points_by_team_week_pos`): the backing data
    for the Matchups page's "points allowed" matchup-difficulty tool (this
    page is NOT a head-to-head fantasy scoreboard — it's points-allowed by
    position/week, feeding the Points Against modal and matchup index).
    **Correction (Opus's catch, confirmed reading the source directly):**
    the original draft of this fact mis-described the keying. It's actually
    `table[(row["team"], row["week"], row["position"])]` — each team's OWN
    offensive output at that position, NOT pre-keyed by opponent. "Points
    *allowed*" is derived downstream: `allowed_by_team_week_pos` (lines
    79-97) looks up `points_by_team_week_pos.get((opponent_team, week,
    pos))` for defense's own schedule. An expected-stat version needs the
    same two-step shape, not a single reworked function.
18. **The real scope here is much bigger than one new aggregation
    function** (confirmed reading `engine/matchups.py` end to end).
    `points_by_team_week_pos`'s output only becomes the Matchups page's
    actual on-screen numbers after `compute_matchup_index()` (lines
    275-442) — a ~170-line function doing opponent-strength leave-one-out
    adjustment, season/L5/prior-season blending, and ratio-of-averages
    normalization — produces the `MatchupIndex` dataclass (`index`, `rank`,
    `allowed_ppg`, `l5_allowed_ppg`, `adjusted_allowed_ppg`, `pa_factor`).
    The frontend's existing Season/L5 and Raw/Opp-Adjusted selectors read
    different fields off THIS struct, not off `points_by_team_week_pos`
    directly. An "Expected" selector needs a full second
    `compute_matchup_index()` run (fed an `expected_points_by_team_week_pos`
    table) producing a parallel `MatchupIndex`-shaped result — see §2.6.
19. **Two gaps in Matchups' position coverage, confirmed live:**
    `OFFENSE_POSITIONS = ["QB", "RB", "WR", "TE", "K"]` (matchups.py:14)
    includes K, but ffopportunity has no kicker model at all (0.1/#7) — an
    "Expected" view for K has no underlying data. DST is handled by a
    completely separate function (`dst_points_by_team_week_pos`, lines
    41-72, team defensive stats) with no ffopportunity equivalent either.
    Both need explicit handling (likely: no Expected option for K/DST,
    falling back to Actual — needs a decision, see Open Question Q3).
    Also confirmed live: `load_ff_opportunity(stat_type="weekly")` has **no
    `season_type` column at all** (checked directly — not there), and its
    `week` column runs 1-22 (regular season + playoffs together, same
    numbering nflverse uses elsewhere). `points_by_team_week_pos` filters
    `season_type == "REG"` (line 32); the expected version can't filter the
    same way and needs either a week-number cutoff or to skip the filter
    (since `team_weeks`/the schedule-driven lookups upstream already scope
    to real played weeks regardless) — an implementation-time call, not
    fully resolved here.
20. `docs/js/matchups.js:151-234` (`renderMatchups`): already has TWO
    `<select>` dropdowns wired the same way (`#matchups-basis-select` for
    Season/Last-5-weeks, `#matchups-type-select` for Raw/Opp-Adjusted,
    both driving a shared `draw()`). A third "Actual/Expected" selector
    mirrors this exact pattern.
21. `docs/js/rankings.js:329-340` (`statsTrailingColumns`): Rankings Stats
    page's trailing columns — `[Snap%, Att%, Tgt%, FPTS]` for skill
    positions (grouped under a "Usage" header spanning Snap/Att/Tgt, with
    FPTS as its own unlabeled trailing column), or just `[FPTS]` for K/DST.
    `statsFpts(p, sw, cw)` returns either `seasonTotalPoints(...)` or
    `weekEntryFor(p, sw).actual?.points`. AirYd% slots into the Usage group;
    XFPTS slots in immediately after FPTS.
22. `docs/js/playermodal.js:241` (`USAGE_BLOCK`): Game Log tab's
    `["Usage", [["_snap_pct","Snap%"],["_att_pct","Att%"],["_tgt_pct","Tgt%"]]]`
    — same insertion point for AirYd%, gated to the same
    `_USAGE_POSITIONS = {QB,RB,WR,TE}` set (K/DST never get this block, same
    reasoning: neither has a receiving-style usage concept, consistent with
    why xFPTS won't exist for them either).
23. `docs/js/playermodal.js:565` (`usageCellHtml`) + FAAB Lab tab's
    comparable-players/current-week-inputs tables (lines ~753, 771, 980):
    this is the "FAAB Lab UI only" surface Alex named — air yard share
    (and NOT route share, per D1) gets added to `usageCellHtml`'s rendering
    only; `engine/faab_estimate.py`'s K-NN/regression inputs are untouched.
24. `docs/js/startsit.js:99-106`: trend cells are flat, single-value —
    `<td class="stat-col muted">${fmtPts(seasonAvg)}</td>`, same shape for
    3wk/2wk/1wk. No existing nested-value precedent in this file; adding
    xFPTS beneath means new markup (a nested `<div>`/`<span>` inside each
    `<td>`), not an existing pattern to mirror.
25. `docs/js/data.js:35-58`: data.json's shape has no schema-doc comment —
    it's a positional `Promise.all` fetch list, destructured into
    camelCase variables in the same order the fetch array produces them
    (e.g. `gameLogPlays` ← `game_log_plays.json`). Any new top-level file
    (if the per-play xFP data becomes its own file rather than nesting in
    the existing one) needs a new entry in both the fetch array and the
    destructuring line, in matching order.

---

## Part 1 — Decisions

**D1 — Route share: dropped from this pass entirely.** Confirmed at the
GitHub-release level (0.2/#9) that current-season participation data does
not exist upstream yet, not just an nflreadpy wrapper restriction. Air yard
share ships now; route share is not attempted, not even as a frozen
prior-season reference. Revisit once nflverse backfills 2026.

**D2 — xFPTS is computed via `ScoringRules.points_for_row()`, fed
ffopportunity's expected STAT COMPONENTS — not ffopportunity's own baked-in
generic `*_fantasy_points_exp` columns.** This makes xFPTS respect each
league's real scoring (O League standard vs. Any Given Sunday half-PPR)
instead of a fixed assumption baked into the upstream model.

**Explicit weekly column map** (`WEEKLY_EXP_STAT_MAP` — reviewed by Opus
5.5 on 2026-10-02, two corrections applied, see below):

| ffopportunity weekly column | `PLAYER_STAT_EXPR` key |
|---|---|
| `pass_attempt` (actual, not `_exp` — the opportunity itself, not an outcome) | `attempts` |
| `pass_completions_exp` | `completions` |
| `pass_yards_gained_exp` | `passing_yards` |
| `pass_touchdown_exp` | `passing_tds` |
| `pass_interception_exp` | `passing_interceptions` |
| `pass_two_point_conv_exp` | `passing_2pt_conversions` |
| `rush_attempt` (actual) | `carries` |
| `rush_yards_gained_exp` | `rushing_yards` |
| `rush_touchdown_exp` | `rushing_tds` |
| `rush_two_point_conv_exp` | `rushing_2pt_conversions` |
| `rush_first_down_exp` | `rushing_first_downs` |
| `rec_attempt` (actual) | `targets` |
| `receptions_exp` | `receptions` |
| `rec_yards_gained_exp` | `receiving_yards` |
| `rec_touchdown_exp` | `receiving_tds` |
| `rec_two_point_conv_exp` | `receiving_2pt_conversions` |
| `rec_first_down_exp` | `receiving_first_downs` |

**Correction 1 (Opus's catch, confirmed real):** the original draft of this
map only included `_exp` columns and left out `attempts`/`carries`/
`targets` entirely. Without them, `PLAYER_STAT_EXPR["INC"]`
(`attempts - completions`) would silently read as `0 - completions_exp` —
a NEGATIVE incompletion count, which for a league scoring INC as a penalty
would hand out bonus points instead of docking them. Same gap for `PA`
(pass attempts bonus), `RA`/`RA5`/`RA10` (carries), `RET`/`IP10`/`PC10`
(targets/completions brackets). Fixed by mapping the real opportunity
counts (`pass_attempt`/`rush_attempt`/`rec_attempt` — these are observed
facts, not modeled outcomes, so there's no "_exp" version to use) alongside
the modeled `_exp` outcome columns.

**Correction 2 (Opus's catch, confirmed real):** the original wording said
milestone-bonus categories (`P300`, `RY100`, `PY25`, etc.) are "treated as
0." That's not accurate — those categories are computed by
`PLAYER_STAT_EXPR` directly off whatever's in `rushing_yards`/
`passing_yards`/`receiving_yards` (e.g. `"RY100": lambda r: 1.0 if 100 <=
r.get("rushing_yards") < 200 else 0.0`), so once the expected row has
expected yards in those keys, milestone brackets evaluate against expected
yardage automatically — e.g. a QB projected for 310 expected passing yards
genuinely trips `P300`. What's actually true: there is no EXPECTED
analog for `attempts`-independent milestone logic to get wrong, so this
just works as a side effect of the yards mapping above, no special-casing
needed.

**Verified NOT a gap (Opus's claim rejected):** the review flagged "fumbles
are dropped, and the plan contradicts itself" against 0.1/#6's mention of
ffopportunity's own formula including a fumble term. Re-checked directly:
`ep_summarize.R`'s `fantasy_points_exp` formula (the one shown in 0.1/#6)
has NO fumble term at all — the `-2.0 * fumble_lost` line only appears in
the ACTUAL `fantasy_points` formula alongside it, for comparison. Also
re-confirmed live: `load_ff_opportunity(stat_type="weekly")` has
`rec_fumble_lost`/`rush_fumble_lost` (actual counts) but no `_exp` variant
of either, anywhere in the 159 columns. So there genuinely is no fumble
expectation signal anywhere in this data — ffopportunity's own model
doesn't have one either. Fumbles (and return TDs, which also have no
`_exp` column) stay un-mapped, meaning any league scoring those categories
gets a 0 contribution in the expected row — correct given no model exists,
not a bug to fix.

**D3 — xFPTS placement** (all confirmed with Alex):
- Player Modal Game Log tab: next to FPTS, per played week.
- Rankings Stats page: next to the trailing FPTS column.
- Matchups page: new "Actual / Expected" selector, third dropdown alongside
  the existing Season/Last-5 and Raw/Opp-Adjusted selectors — this is
  "expected points allowed by position," computed the same way as today's
  real points-allowed number, just fed expected-stat rows.
- FAAB Lab tab: UI display only (`usageCellHtml`/comparable-players tables),
  never touches `engine/faab_estimate.py`'s K-NN/regression inputs.
- Start/Sit page: beneath each of Szn/3wk/2wk/1wk actual values, smaller
  font, inside the same `<td>`.
- Explicitly NOT yet decided: anywhere else — flagged as Open Question Q1 at
  the end, in case something was missed.

**D4 — Player Modal Game Log row-expansion gets a second, expected-points
visual beneath the existing actual per-play chart/table.** Sourced from
ffopportunity's `pbp_pass`/`pbp_rush`, run through the same per-play
`ScoringRules.points_for_row()` mechanism `engine/play_log.py` already uses
for actual plays. Originally sequenced last pending the per-play yards
formula; that formula is now confirmed from ffopportunity's own R source
(0.1/#4-5), so **this is folded into the main stage sequence, not deferred**
(Alex's call — build it all in this pass).

**D5 — Air yard share** ships as a raw-counts pair
(`player_air_yards`/`team_air_yards`), following the exact same
"raw counts in, frontend derives %, both single-week and season-split"
convention as the existing Snap%/Att%/Tgt% fields. No data-lag issue — it's
live every week. Placement: Usage block in Player Modal Game Log, Usage
group in Rankings Stats page, `usageCellHtml` in FAAB Lab (UI only).

**Simplified source (found during the Opus review, better than the
original `load_pbp`-groupby plan):** nflverse's weekly `player_stats` —
already loaded as `current_stats` in `engine/pipeline.py:911`, the exact
same DataFrame `team_rb_carries`/`team_targets` already come from via
`team_position_totals(current_stats, "RB", "carries")` /
`team_position_totals(current_stats, None, "targets")` — **already has a
`receiving_air_yards` column** (confirmed live against 2026 week 4 data:
Chris Olave, 234 air yards, week 1). That means:
- `team_air_yards = team_position_totals(current_stats, None, "receiving_air_yards")`
  — reuses the existing generic helper, zero new aggregation code.
- `player_air_yards = stats_index[pid][week].get("receiving_air_yards")` —
  reuses the already-built `build_stats_index(current_stats)`
  (`engine/faab_estimate.py:508`), zero new indexing code.

This replaces the original plan's proposal to load `load_pbp` fresh and
group it by `(receiver_player_id, week)`/`(posteam, week)` ourselves —
unnecessary extra work once `player_stats` already has the number.
(nflverse's `player_stats` also already has a pre-divided `air_yards_share`
column — not used here, since it's a single-week ratio and this codebase's
established convention, 0.3/#14, needs raw counts so the frontend can
derive a true season split, not an average of weekly ratios.)

**D6 — Workflow: no separate fable-prompt this round.** Plan built directly
in conversation with Alex; Sonnet implements from it.

---

## Part 2 — Architecture

### 2.1 New backend module: `engine/expected_points.py`

A small, self-contained module mirroring `engine/scoring.py`'s own
relationship to `engine/play_log.py`:

- `WEEKLY_EXP_STAT_MAP: dict[str, str]` — ffopportunity weekly column name →
  `PLAYER_STAT_EXPR` key (the D2 mapping above).
- `expected_stat_row_from_weekly(ff_opp_row: dict) -> dict` — applies the
  map, returns a stat dict shaped exactly like `_play_stat_row_and_label`'s
  output, ready for `ScoringRules.points_for_row()`.
- `weekly_xfp_points(ff_opp_row: dict, rules: ScoringRules) -> float` —
  thin wrapper combining the above with `points_for_row`.
- `expected_stat_row_from_pbp_pass(row: dict, role: Literal["pass","rec"]) -> dict`
  — per-play version for a pass play. Dict keys are the SAME plain
  `PLAYER_STAT_EXPR` names `points_for_row` already looks up (`passing_yards`,
  not `passing_yards_exp` — there is no "_exp" suffix once a value lands in
  the stat row; "expected" is a property of which row we built, not of the
  key names). Using the confirmed formula (0.1/#4):
  `yards = row["pass_completion_exp"] * (row["yards_after_catch_exp"] + row["air_yards"])`
  → `{"passing_yards": yards}` for role="pass" or `{"receiving_yards": yards}`
  for role="rec"; `completions`/`receptions` = `pass_completion_exp`;
  TDs/INTs/2pt/first-downs map straight off `pass_touchdown_exp`/
  `pass_interception_exp`/`two_point_conv_exp`/`pass_first_down_exp` (already
  the "cleaned" published columns — ep_predict.R's spike/2pt/goal-line
  cleanup is already baked into what nflreadpy downloads, no re-cleaning
  needed) into `passing_tds`/`receiving_tds`, `passing_interceptions`,
  `passing_2pt_conversions`/`receiving_2pt_conversions`,
  `rushing_first_downs`-style first-down key for the relevant role.
- `expected_stat_row_from_pbp_rush(row: dict) -> dict` — per-play rush
  version, using the *cleaned* columns directly (0.1/#5):
  `{"rushing_yards": row["rush_yards_exp"], "rushing_tds": row["rush_touchdown_exp"],
  "rushing_first_downs": row["rush_first_down_exp"], "rushing_2pt_conversions": row["two_point_conv_exp"]}`
  — NOT the raw `rushing_yards_exp`/`rushing_td_exp`/`rushing_fd_exp` source
  columns, which still include kneel-downs and double-count 2pt attempts.

Kept separate from `engine/scoring.py` (rather than extending it) because
scoring.py's whole contract is "real raw stat → real points" with
fail-loud behavior on unmapped categories; expected rows are a distinct,
partial-coverage concept (fumbles/return-TDs are never populated) that
shouldn't risk weakening that fail-loud guarantee for actual scoring.

### 2.2 New backend loader: `ingest/nfl_data.py`

`ff_opportunity_weekly(seasons, current_season=None) -> pl.DataFrame` and
`ff_opportunity_pbp(seasons, stat_type: Literal["pbp_pass","pbp_rush"]) -> pl.DataFrame`,
both following `play_by_play()`'s exact shape (0.3/#16): `_cached_load` +
`try/except ConnectionError` graceful-empty-frame fallback +
`_normalize_team_columns(df, ["posteam"])` (ffopportunity's only team
column in either shape).

### 2.3 Pipeline wiring: `engine/pipeline.py`

- Load `ff_opportunity_weekly` once per pipeline run (same place
  `play_by_play` and other nflverse sources already load), index by
  `(player_id, week)`.
  **On the join key (Opus flagged this as Risk R2/finding #5, investigated
  and resolved — no fix needed):** the review worried the pipeline's `pid`
  (the site's own canonical `res.id` from `ingest/ids.py`'s `IdMap.resolve`)
  isn't a gsis id, so joining it against ffopportunity's gsis-keyed
  `player_id` would silently miss. Checked `ingest/ids.py:85-87` directly:
  `Resolution(id=rec_gsis, source=source or "gsis")` — for any player with
  a gsis mapping (essentially every real rostered skill-position player),
  `res.id` IS the literal gsis id string, not a wrapped/prefixed value.
  Non-gsis fallback ids (`fp:...`/`espn:...`/`unmapped:...`, DST's
  `dst:...`) only occur for players with no gsis crosswalk entry — and
  `_usage_stats_for_week` (0.3/#14) already does exactly this same direct
  `gsis_to_pfr.get(pid)` lookup in production today, with no translation
  step, silently returning `None` for those non-gsis edge cases. The new
  `stats_index.get(pid, {})`/ffopportunity-index lookups should follow the
  identical pattern: direct dict `.get(pid, ...)`, same graceful-miss
  behavior as existing code, no new id-translation parameter needed.
- In `_actual_weekly_stats` (0.3/#13): add `xfp_points` as a sibling key to
  `points`/`stats` in the returned dict — `None` for a week with no
  ffopportunity row (bye, DST, K, or a player ffopportunity doesn't carry),
  same "None not 0" convention the rest of this function already uses.
  Also add `xfp_points: None` to the DST early-return branch (0.3/#13's
  function has one — confirm during implementation whether a played-but-
  no-ffopportunity-row week should be `None` or `0.0`; `None` matches this
  function's existing convention better, since it already distinguishes
  "didn't play" from "played for 0").
- In `_usage_stats_for_week` (0.3/#14): add `player_air_yards`/
  `team_air_yards` raw counts — **simplified per D5's finding**: both come
  straight from `current_stats`/`stats_index`/`team_position_totals`
  (already loaded at line 911/923-925), not a fresh `load_pbp` groupby.
  `team_air_yards = team_position_totals(current_stats, None,
  "receiving_air_yards")` computed once alongside `team_rb_carries`/
  `team_targets`; `player_air_yards = stats_index.get(pid, {}).get(week,
  {}).get("receiving_air_yards")` inside the function itself, same
  None-for-unplayed-week gating already there.

### 2.4 Frontend: `docs/js/colors.js`

Add `airYardPct(player, week, currentWeek)`, mirroring `tgtPct`'s exact
season-split formula (sum of numerators over weeks played / sum of
denominators over those same weeks). Add `seasonTotalXfp`/`xfpWeeksAgo`
mirroring `seasonTotalPoints`/`pointsWeeksAgo`, reading the new
`actual.xfp_points` field.

### 2.5 Frontend surfaces

- `playermodal.js`: `USAGE_BLOCK` gains `["_air_yard_pct","AirYd%"]`;
  `gameLogTable` gains an XFPTS cell next to the FPTS `<strong>` cell;
  `usageCellHtml` (FAAB Lab) gains an optional air-yard-share display.
- `rankings.js`: `statsTrailingColumns` gains an XFPTS column after FPTS
  (skipped for K/DST, same gate FPTS-adjacent Usage columns already use);
  AirYd% added into the Usage group.
- `startsit.js`: each of the Szn/3wk/2wk/1wk `<td>` gains a nested smaller
  xFPTS line (new markup — no existing precedent to mirror here, per 0.3/#24).
- `matchups.js`: third `<select>` ("Actual"/"Expected"), wired into the
  existing `draw()` the same way the other two selectors are.
- `engine/matchups.py`: new `expected_points_by_team_week_pos()` mirroring
  `points_by_team_week_pos` (0.3/#17-18) but fed expected-stat rows via
  2.1's `expected_stat_row_from_weekly`, PLUS a second full
  `compute_matchup_index()` run over that table (0.3/#18 — this is NOT a
  one-function change, see that fact for why), producing a parallel
  `MatchupIndex`-shaped result the frontend's existing field-reads
  (`index`/`rank`/`allowed_ppg`/`l5_allowed_ppg`/`adjusted_allowed_ppg`/
  `pa_factor`) can point at when "Expected" is selected. K and DST need
  explicit handling per 0.3/#19 (no ffopportunity model for either) —
  see Open Question Q3.

### 2.6 Per-play expected visual

New `engine/xfp_play_log.py` mirroring `engine/play_log.py`'s
`scoring_plays_for_player`/`zero_point_plays_for_player` shape but over
`pbp_pass`/`pbp_rush` expected rows (via 2.1's
`expected_stat_row_from_pbp_pass`/`_rush`); wired into `game_log_plays.json`
(0.3/#15) as a parallel `expected_plays`/`expected_zero_point_plays`
structure per week; `playermodal.js`'s `playLogDetailHtml` renders a second
chart/table beneath the existing actual one. Unlike the actual version,
every expected play has a nonzero (if small) point value — there's no
real "zero_point" case for a fractional expectation — so this second table
likely shows every play rather than filtering, worth confirming once it's
visually in front of Alex (see Open Question Q2).

---

## Part 3 — Staged implementation

1. **`ingest/nfl_data.py`**: add `ff_opportunity_weekly()` and
   `ff_opportunity_pbp()` wrappers (2.2). Verify output shape/caching
   against a real pipeline-style call.
2. **`engine/expected_points.py`**: weekly stat-map + per-play
   pass/rush stat-row builders + helpers (2.1). Unit-test against a known
   player-week AND a known individual play (e.g. a real 2025 game) comparing
   computed xFPTS to a hand-calculated value under both leagues' scoring
   rules.
3. **`engine/pipeline.py`**: wire `xfp_points` into `_actual_weekly_stats`,
   `player_air_yards`/`team_air_yards` into `_usage_stats_for_week` (2.3),
   AND the per-play expected breakdown into `game_log_plays.json` via the
   new `engine/xfp_play_log.py` (2.6). R2 (join-key format) is resolved
   (2.3) — no translation step needed, just confirm the direct `.get(pid,
   ...)` lookups miss gracefully for the small number of non-gsis ids, same
   as existing code.
4. **`docs/js/colors.js`**: add `airYardPct`, `seasonTotalXfp`,
   `xfpWeeksAgo` (2.4).
5. **`docs/js/playermodal.js`**: USAGE_BLOCK AirYd%, Game Log XFPTS column,
   FAAB Lab air-yard-share display, AND the second expected-plays visual
   under `playLogDetailHtml` (2.5, 2.6).
6. **`docs/js/rankings.js`**: Stats page XFPTS + AirYd% (2.5).
7. **`docs/js/startsit.js`**: nested xFPTS lines (2.5).
8. **`engine/matchups.py` + `docs/js/matchups.js`**: expected points-allowed
   aggregation AND a full second `compute_matchup_index()` run (2.5, 0.3/#18
   — this stage is bigger than it looks, budget accordingly) + third
   selector, with explicit K/DST handling (0.3/#19, Q3).

Each stage should run `pytest -q` and a local pipeline build before moving
to the next; stages 4, 6, 7, 8 are independently shippable in any order once
stage 3 lands (they all just read `xfp_points`/`player_air_yards`/
`team_air_yards`/the new game-log-plays fields off the existing data shape).
Stage 5 depends on both stage 3 (pipeline data) and stage 4 (colors.js
helpers).

---

## Part 4 — Risks

**R1 — RESOLVED.** Per-play expected receiving/passing yards formula is
confirmed from ffopportunity's own R source (0.1/#4), not a guess. No
longer sequenced separately — folded into Stage 3 (0.1/#4-6).

**R2 — RESOLVED (Opus flagged this as finding #5; investigated, no fix
needed).** Confirmed `ingest/ids.py:85-87` directly: the pipeline's
canonical `res.id` IS the literal gsis id for any player with a gsis
crosswalk entry (the normal case), and `_usage_stats_for_week` already does
a direct `gsis_to_pfr.get(pid)` lookup today with no translation step. The
new ffopportunity/`player_stats` lookups should use the same direct
`.get(pid, ...)` pattern — see 2.3.

**R3 — No kicker or DST model.** xFPTS and the expected-points-by-play
visual simply won't exist for K/DST — same "no nflverse stat to source it
from" precedent the codebase already uses for DST's xpr field and the
Usage block. UI should render "—", not attempt an approximation.

**R4 — Extra nflverse pull per pipeline run.** `ff_opportunity_weekly` is
one more live data source alongside `play_by_play`/stats/rosters — follows
the same `CURRENT_SEASON_TTL` caching convention (`ingest/nfl_data.py`), so
it shouldn't meaningfully change build time, but worth a real timing check
after Stage 1 lands (this repo has prior history of caring about build
duration — see `update-scripts-implementation-plan.md`'s Part 0).

**R5 — Matchups is a bigger stage than originally scoped (Opus's findings
#6/#7, confirmed reading `engine/matchups.py` end to end — see 0.3/#18-19).**
Three distinct things, not one: (a) `load_ff_opportunity` IS a full
league-wide weekly dataset (not scoped to rostered players), so player
coverage itself should be fine; (b) the "Expected" selector needs a full
second `compute_matchup_index()` run, not just a new aggregation function,
since that's where the frontend's actual displayed numbers come from; (c)
K has no ffopportunity model despite being in `OFFENSE_POSITIONS`, DST has
a wholly separate non-ffopportunity code path, and ffopportunity weekly has
no `season_type` column to replicate the existing `REG`-only filter — all
three need explicit decisions during Stage 8, not silent fallback
behavior.

---

## Part 5 — Open questions

**Q1 — Other xFPTS placements?** Alex asked to be prompted if any FPTS
surface was missed. Grepped the whole `docs/js/` tree for FPTS/fantasy-
points displays: only `playermodal.js`, `rankings.js`, `matchups.js`
(points-allowed, not a points display per se) turned up — `startsit.js`'s
points trend was the one Alex specifically asked about and confirmed.
`watchlist.js`/`trade.js` have no fantasy-points display to extend. Nothing
else found — but speak up if there's a page/modal this plan missed.

**Q2 — Should the per-play expected visual filter zero-value plays the same
way the actual one does?** `engine/play_log.py`'s actual version drops
genuinely-0-point plays into a separate `zero_point_plays` bucket
(`zero_point_plays_for_player`) so the main chart only bars real scoring
events. The expected version has no true zero case — every play has some
fractional expected value (a 2-yard completion probability still has SOME
chance of a catch) — so either every single play gets a bar (likely a much
noisier chart than the actual one) or we need a different noise-reduction
rule (e.g. only bar plays above some small xFP threshold). Worth deciding
once Stage 5 is visually in front of Alex rather than guessing now.

**Q3 — Matchups "Expected" view for K and DST: hide the option, or fall
back to Actual?** Neither has a ffopportunity model (0.3/#19). Hiding the
selector entirely when K/DST is the active position filter would match the
existing convention (Usage block/xFPTS already disappear for K/DST
elsewhere) — confirm that's the right call here too, rather than showing a
third selector that does nothing or silently repeats Actual's numbers.

**Q4 — Matchups "Expected" view and season/playoff weeks: cut at week 18,
or include everything ffopportunity has?** `points_by_team_week_pos` only
counts `season_type == "REG"` rows; ffopportunity weekly has no
`season_type` column and runs through week 22 (0.3/#19). Since
`compute_matchup_index` is itself only ever fed whatever weeks are in
`team_weeks`/`opponent` (built from the real schedule, already scoped
correctly elsewhere in the pipeline), the simplest fix may be to just not
filter at all and let the schedule-driven lookups naturally ignore
playoff weeks — confirm that's sufficient rather than needing an explicit
week-number cutoff inside the new expected aggregation function.
