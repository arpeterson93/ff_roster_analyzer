# FF Roster Analyzer

Automated fantasy football analysis for two ESPN leagues: team-strength /
trade-equity evaluation and weekly start/sit matchup guidance. Replaces a
hand-fed Google Sheet with a daily Python pipeline (ESPN + nflverse +
FantasyPros) and a static site on GitHub Pages.

## Architecture

```
config/leagues/*.yml ─┐
ESPN (espn-api) ──────┼─► ingest/ ─► engine/ ─► docs/data/<slug>/*.json ─► docs/ static site (GitHub Pages)
nflverse (nflreadpy) ─┤
FantasyPros ecrData ──┘        GitHub Actions: daily cron + push + dispatch, artifact deploy
```

- `config/leagues/*.yml` — one file per configured league (id, season,
  scoring format, valuation/strength/sim tuning). Two leagues configured:
  `o-league` (public, standard scoring) and `any-given-sunday` (private,
  half PPR, needs `ESPN_S2_AGS`/`SWID_AGS`).
- `ingest/` — thin adapters over ESPN (`espn_client.py`), nflverse
  (`nfl_data.py`), FantasyPros (`rankings.py`), and cross-platform player id
  resolution (`ids.py`).
- `engine/` — pure computation: scoring rules, the rank→points curve,
  opponent-adjusted matchup difficulty, ROS valuation, lineup optimization,
  team strength / trades / pickups, playoff simulation, and the pipeline
  that orchestrates all of it into JSON.
- `docs/` — static HTML/CSS/JS frontend, no build step, no framework.
  Deployed via GitHub Pages (Actions artifact deploy — generated JSON in
  `docs/data/` is not committed to `main`).
- `.github/workflows/build.yml` — runs the pipeline daily (cron), on
  relevant pushes, and on manual dispatch; publishes `docs/` to Pages.

## Methodology

The valuation method mirrors the logic from the original Google Sheet, with
every input now sourced automatically instead of copy-pasted:

1. Start from each player's **consensus rest-of-season positional rank**
   (FantasyPros direct scrape, format-aware: standard for `o-league`, half
   PPR for `any-given-sunday`; falls back to `nflreadpy`'s DynastyProcess
   rankings if the scrape fails).
2. Map that rank to an **expected points-per-week baseline**, from an
   empirical rank→PPG curve built from actual weekly fantasy output in
   2022–2025 (computed with this league's real scoring rules), blended with
   the current season's own curve as it accumulates games.
3. Sum the baseline across all remaining weeks (through the last playoff
   week) for an **expected ROS total**.
4. **Adjust week-to-week** by the opponent's points-allowed-by-position
   index (season/trailing-5/blend, opponent-strength adjusted, damped 50%
   toward the baseline) - except the **current week**, which instead maps
   FantasyPros' *weekly* consensus rank directly to the curve baseline,
   since their weekly rank already reflects that week's specific matchup
   (layering our own opponent adjustment on top would double-count it).
   Falls back to the baseline*matchup method if no weekly rank is available.
   An `OUT`/`INJURY_RESERVE`/`SUSPENSION`/`DOUBTFUL` status still zeroes the
   current week regardless of either method.
5. **Reconcile**: rescale the weekly-adjusted values so they still sum to
   the ROS total from step 3 — matchup strength redistributes value across
   weeks, it doesn't change the season total.

Player value for trades and team strength is **lineup-delta ("next man
down")**: the drop in a team's optimal-lineup ROS points if a player were
removed, computed both roster-only (`value_delta`) and with the best
available free agent backfilling the position (`value_delta_ww`, used as
the primary basis for trade verdicts and pickup suggestions).

**Future option, not built:** an alternative curve-construction method
pairing historical ROS rankings with the players' subsequent actual PPG,
rather than the current end-of-season-rank approach.

## Settings sheet (live-editable league settings)

Some tuning knobs (matchup dampening, points-allowed basis, seeding) can be
changed from the site's **Settings** tab instead of editing
`config/leagues/*.yml`, synced through a Google Sheet - the same pattern
`irrigation_planner` uses. This is optional; leave `settings_sheet_id: null`
in a league's YAML to skip it entirely and just use the YAML values.

**One-time setup per league that wants this:**

1. Create a Google Sheet with a tab named exactly `Settings` and header row
   `league_slug | key | value`. One row per setting per league, e.g.:
   ```
   league_slug | key                    | value
   o-league    | matchup_dampening      | 0.5
   o-league    | pa_basis               | blend
   o-league    | pa_l5_weight           | 0.5
   o-league    | division_winners_first | true
   ```
   Recognized keys are listed in `ingest/settings_sheet.py`'s
   `_SETTINGS_SCHEMA`. An unrecognized key is ignored with a warning, not a
   pipeline failure.
2. Share it as "Anyone with the link can view" (the pipeline reads it
   unauthenticated via the public CSV export endpoint).
3. Put the sheet's id (the long string in its URL between `/d/` and `/edit`)
   into `settings_sheet_id` in that league's `config/leagues/<slug>.yml`.
4. To make the Settings **tab in the site itself** able to write back: open
   the sheet's Extensions → Apps Script, paste in
   `tools/apps-script/settings_sync.gs`, then Deploy → New deployment → type
   "Web app" → Execute as "Me" → Who has access "Anyone" → Deploy. Copy the
   resulting Web App URL into `SETTINGS_WEBAPP_URL` in
   `docs/js/settingsConfig.js`.

Without step 4, the Settings tab still shows the sheet's current values
read-only (or the YAML defaults if `settings_sheet_id` isn't set at all) -
step 4 is only needed to edit values from the site itself rather than
editing the sheet directly. Either way, a change takes effect on the next
pipeline run (daily cron, or trigger the `build` workflow manually), not
instantly - the site doesn't recompute live in the browser.

## Watch list sheet (synced Rankings watch list)

The Rankings tab's ★ watch-list column works out of the box with no setup -
it just saves to `localStorage` in that one browser. To make it follow you
across devices/browsers, sync it through a Google Sheet the same way the
Settings sheet works, with one difference: the site reads this sheet
**live** from the browser (not once a day at pipeline build time), since a
watch-list toggle should show up elsewhere right away.

**One-time setup (optional):**

1. Create a Google Sheet with a tab named exactly `Watchlist` and header row
   `league_slug | team_id | player_id`. One row per watched player per team;
   leave it empty otherwise, the site creates rows as you click ★.
2. Share it as "Anyone with the link can view" (read unauthenticated via the
   public CSV export endpoint, same as the Settings sheet).
3. Put the sheet's id (the long string in its URL between `/d/` and `/edit`)
   into `WATCHLIST_SHEET_ID` in `docs/js/watchlistConfig.js`.
4. To let the site write back: open the sheet's Extensions → Apps Script,
   paste in `tools/apps-script/watchlist_sync.gs`, then Deploy → New
   deployment → type "Web app" → Execute as "Me" → Who has access "Anyone" →
   Deploy. Copy the resulting Web App URL into `WATCHLIST_WEBAPP_URL` in
   `docs/js/watchlistConfig.js`.

There's no login, so "your team" (the same per-browser pick used everywhere
else on the site) is what a watch list is keyed on - pick the same team on
each device/browser to see the same list. Without steps 3-4, the ★ column
still works, just local to that one browser.

**Not yet built:** custom tiebreaker chains (head-to-head, points against,
etc.) beyond ESPN's own `playoff_seed_tie_rule` - `engine/standings.py`
currently only supports the single tiebreak ESPN reports
(`TOTAL_POINTS_SCORED` for both leagues, verified). `division_winners_first`
is the only seeding behavior exposed as a setting today.

## Local development

```
pip install -r requirements.txt
cp .env.example .env   # fill in ESPN_S2_AGS / SWID_AGS for any-given-sunday
python -m ingest.smoke --league o-league
python -m engine.pipeline --league o-league
python -m http.server --directory docs
pytest -q
```

Run with `PYTHONIOENCODING=utf-8` on Windows if player names with accented
characters fail to print.

## Deployment (manual steps, one-time)

1. Repo Settings → Pages → Source: **GitHub Actions**.
2. Add repo secrets `ESPN_S2_AGS` and `SWID_AGS` (see `.env.example` for how
   to obtain them).
3. Run the `build` workflow once via "Run workflow" and confirm both
   leagues build successfully.

## Validation

**2026-09-09, o-league, pre-season (weeks_played=0):**

- Top-of-position baselines land where expected and are close to the original
  sheet's 2025 Rank Pts curve where the methodology is comparable: Josh Allen
  QB1 27.35 PPG (sheet ~27.9), Ja'Marr Chase WR1 15.5 PPG, Jahmyr Gibbs RB1
  19.08 PPG (sheet ~23.7 - lower here, plausibly because the curve blends
  four seasons of history rather than one), Brandon Aubrey K1 10.42 PPG
  (sheet ~10.4, close match). Per Fable's Part 4 risk #11, standard-scoring
  WR baselines are *not* tuned to match the sheet's unusually high WR average
  - noted, not treated as a bug.
- Depth table shows the expected shape: a team with two good RBs shows a
  steep RB1 → RB2 → RB3 value_delta drop-off (e.g. 68.7 / 14.2 / 6.9 / 0.0 /
  0.0 for one roster), matching the "next man down" pattern from the AGS
  sheet's Teams tab.
- 180/180 rostered players resolved to a canonical id with zero unmapped
  players in the ROS rankings join; every `weekly` array sums to its
  player's `ros_total` (asserted in `engine/pipeline.py`, no warnings fired).
- Pickups and trade targets only surfaced positive-gain candidates; simulated
  `seed_probs` sum exactly to each team's `playoff_odds`.
- `any-given-sunday` cannot be validated yet - it needs `ESPN_S2_AGS`/
  `SWID_AGS` secrets Alex has not added (Stage 7's manual step), which also
  means the D/ST scoring map is unverified against real ESPN scoring items.

**Outstanding per Fable's Part 4 risk #1:** today is pre-week-1, so the curve
is history-only and the matchup index is 2025-only. A second validation pass
after 2026-09-15 (once week 1 has real stats) is required before treating
the pipeline as done - re-run the checks above and confirm `weeks_played`,
the curve blend weight, and the matchup index all update as expected.

**Not yet verified:** the frontend (`docs/`) was built and served locally
successfully (all assets return 200, JSON shapes were manually cross-checked
against every JS module's field access), but this session had no working
browser automation tool available to click through the six views
interactively. Do a manual pass in a browser before trusting the UI, and
run `pytest tests/test_trade_js_parity.py` on a machine with `node` on PATH
(this session had none) to confirm the JS trade calculator matches
`engine/trades.py` beyond the hand-traced example already checked.

## Status

Stages 0-7 built and passing `pytest -q` (50 passed, 1 skipped locally - the
JS trade-parity test needs `node` on PATH, which this session lacked; CI has
it). Stage 5 validated by
hand for `o-league` per above; `any-given-sunday` blocked on Alex adding the
two ESPN cookie secrets. Frontend needs a manual browser click-through.
