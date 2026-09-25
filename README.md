# FF Roster Analyzer

Automated fantasy football analysis for two ESPN leagues: team-strength /
trade-equity evaluation and weekly start/sit matchup guidance. Replaces a
hand-fed Google Sheet with a Python pipeline (ESPN + nflverse + FantasyPros)
that runs at cadence-appropriate stages throughout the week and a static
site on GitHub Pages.

## Architecture

```
config/leagues/*.yml ─┐
ESPN (espn-api) ──────┼─► ingest/ ─► engine/ ─► docs/data/<slug>/*.json ─► docs/ static site (GitHub Pages)
nflverse (nflreadpy) ─┤
FantasyPros ecrData ──┘        GitHub Actions: cron-job.org dispatch + push, artifact deploy
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
  that orchestrates all of it into JSON. `engine/live.py` is the gameday-only
  live tier - see "Update cadence" below.
- `docs/` — static HTML/CSS/JS frontend, no build step, no framework.
  Deployed via GitHub Pages (Actions artifact deploy — generated JSON in
  `docs/data/` is not committed to `main`, though it IS committed to the
  separate `site-data` branch - see below).
- `.github/workflows/update.yml` — runs one of three stages (`full` /
  `refresh` / `live`) on `workflow_dispatch`, or `full` on a relevant push;
  publishes `docs/` to Pages. See "Update cadence" below.

## Update cadence

The pipeline runs as three differently-scoped stages instead of one daily
job, dispatched by [cron-job.org](https://cron-job.org) rather than GitHub's
own `schedule:` trigger (measured firing ~4h late on this repo - a public
repo with low activity gets deprioritized; cron-job.org fires on time).

| Stage | What it recomputes | Cadence |
|-------|--------------------|---------|
| `full` | Everything - the whole pipeline, including the FAAB bid estimator (its ~2.7GB nflverse release-data download/parse is the single most expensive step) | Daily 8:00am CT |
| `refresh` | Everything except FAAB estimates (skips the 2.7GB download) | Daily 12:15am CT + Sun 4:15pm/10:45pm CT, each waiting up to an hour for nflverse to actually publish newer data |
| `live` | Live scores/win%/playoff odds for the current week only, ESPN-only (no nflverse) | Every 30 min during game windows (Thu/Sun/Mon + holiday one-offs) |

A `push` to `main` (touching `config/`, `ingest/`, `engine/`, `docs/`, etc.)
still triggers a `full` run, same as before.

**The `site-data` branch.** Since `docs/data/` isn't committed to `main`, a
`refresh` or `live` run - which only recomputes a few of the ~14 files per
league - needs SOME prior output to start from and overlay onto. An orphan
branch, `site-data`, holds exactly the current contents of `docs/data/` and
is never merged to `main`. Every run checks it out, copies it into
`docs/data/`, runs its stage (which only overwrites the files that stage
actually computes), copies the result back, and commits/pushes - retrying
with a fetch+reset+reapply-only-this-run's-files dance
(`tools/ci/push_site_data.sh`) if another run pushed in the meantime. A
`refresh`/`live` run with no `site-data` branch yet (or missing a league's
`meta.json`) is a no-op - a `full` run has to go first.

**cron-job.org jobs** (all: timezone America/Chicago, POST to
`https://api.github.com/repos/arpeterson93/ff_roster_analyzer/actions/workflows/update.yml/dispatches`,
headers `Authorization: Bearer <PAT>`, `Accept: application/vnd.github+json`,
`X-GitHub-Api-Version: 2022-11-28`; treat HTTP 204 as success):

| Job | Schedule (CT) | Body |
|-----|---------------|------|
| nightly-full | daily 08:00 | `{"ref":"main","inputs":{"stage":"full"}}` |
| refresh-postgame | daily 00:15 | `{"ref":"main","inputs":{"stage":"refresh","wait_minutes":"60"}}` |
| refresh-sun-early | Sun 16:15 | same as above |
| refresh-sun-late | Sun 22:45 | same as above |
| live-thu | Thu 19:00–23:00, minutes 0,30 | `{"ref":"main","inputs":{"stage":"live"}}` |
| live-sun | Sun 12:00–23:30, minutes 0,30 | same |
| live-mon | Mon 19:00–23:00, minutes 0,30 | same |
| live-intl-oct | Oct 4, 11, 18, 25 · 08:30–11:30, minutes 0,30 | same |
| live-intl-nov | Nov 8, 15 · 08:30–11:30, minutes 0,30 | same |
| live-thanksgiving | Nov 25–27 · 12:00–23:30, minutes 0,30 | same |
| live-sat-dec19 | Dec 19 · 16:00–23:30, minutes 0,30 | same |
| live-xmas | Dec 25 · 12:00–23:30, minutes 0,30 | same |

The live windows deliberately over-cover (cheap: `engine/live.py --gate`
exits in ~30s if nothing's live or recently finished).

**One more cron-job.org job, on a DIFFERENT workflow** (`faab_weekly.yml`,
not `update.yml` - see "FAAB model data refresh" below for what it does and
why it's separate): `faab-weekly`, Wed 08:00 CT, POST to
`https://api.github.com/repos/arpeterson93/ff_roster_analyzer/actions/workflows/faab_weekly.yml/dispatches`
with the same headers as above and body `{"ref":"main"}` (no `inputs` - this
workflow doesn't take any).

**The dispatch PAT.** A fine-grained GitHub PAT scoped to this repo with
**Actions: Read and write**, stored wherever cron-job.org's job config holds
it. It expires - put a renewal reminder on the calendar for whatever expiry
was chosen, and point cron-job.org's own failure notifications (401s once it
expires) at an email that gets checked.

## Methodology

**Weekly projections come from ESPN directly**, taken outright with no
adjustment on top - `EspnClient.get_future_espn_projections` (one
`mRoster?scoringPeriodId=<week>` request per remaining week for rostered
players, one no-position-filter `free_agents(week=...)` request per week for
everyone else) plus the current week's own live fetch
(`espn_projected_week`, from `get_teams()`). An ROS total is a plain sum of
those weekly numbers.

Our own proprietary method only fills in a week ESPN hasn't published a
projection for yet (their own coverage doesn't reliably reach the full rest
of season) - it's the same rank→curve→baseline×matchup approach this site
used exclusively before, now demoted to a gap-filler and a reference-only
column (`our_projected` / the player modal's "Our proj"):

1. Start from each player's **consensus rest-of-season positional rank**
   (FantasyPros direct scrape, format-aware: standard for `o-league`, half
   PPR for `any-given-sunday`; falls back to `nflreadpy`'s DynastyProcess
   rankings if the scrape fails).
2. Map that rank to an **expected points-per-week baseline**, from an
   empirical rank→PPG curve built from actual weekly fantasy output in
   2022–2025 (computed with this league's real scoring rules), blended with
   the current season's own curve as it accumulates games.
3. **Adjust week-to-week** by the opponent's points-allowed-by-position
   index (season/trailing-5/blend, opponent-strength adjusted, damped 50%
   toward the baseline).

An `OUT`/`INJURY_RESERVE`/`SUSPENSION`/`DOUBTFUL` status (or a known IR
return week) zeroes the affected week(s) outright - no redistribution onto
the remaining weeks, so the season total visibly reflects the missed time.

We switched to ESPN-primary because our own curve has real signal only down
to however many players at a position get enough games to qualify each
season (~33 for kickers) - every free agent ranked deeper than that clamped
to the exact same floor value, making several "different" waiver
suggestions secretly identical. ESPN's own per-player number doesn't have
that ceiling. Stacking our own opponent adjustment on top of ESPN's number
would also double-count it, since their projection is presumably already
matchup-aware.

Player value for trades and team strength is **lineup-delta ("next man
down")**: the drop in a team's optimal-lineup ROS points if a player were
removed, computed both roster-only (`value_delta`) and with the best
available free agent backfilling the position (`value_delta_ww`, used as
the primary basis for trade verdicts and pickup suggestions). A recommended
trade must also clear a **fairness ratio** (`trade_fairness_ratio`,
`min(gain_self, gain_partner) / max(...)`) - both sides being merely
positive isn't enough, since that alone lets through wildly lopsided offers
a real partner would never accept.

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
   o-league    | pa_prior_season_weeks  | 6
   o-league    | division_winners_first | true
   ```
   Recognized flat keys are listed in `ingest/settings_sheet.py`'s
   `_SETTINGS_SCHEMA`. An unrecognized key is ignored with a warning, not a
   pipeline failure.

   Playoff-seeding tiebreakers are a second, per-seed group of keys instead
   of one flat schema entry - `division_tiebreak_<1-4>` (ranks each
   division's own members to pick its #1 team, then ranks the division
   winners against each other for the top seeds) and, per playoff seed n,
   `seed_<n>_division_priority` (fill this seed from the next-best
   division winner) plus `seed_<n>_tiebreak_<1-4>` (that seed's own
   wildcard tiebreak chain, used when it isn't filled from the
   division-winner queue). Each criterion is one of `wins`, `points_for`,
   `points_against`, `head_to_head`. e.g., to give divisions 1-3 the top
   3 seeds by Wins→PF→PA and make seed 6 go to the best remaining team by
   Points For alone (a real O-League rule):
   ```
   league_slug | key                       | value
   o-league    | division_tiebreak_1       | wins
   o-league    | division_tiebreak_2       | points_for
   o-league    | division_tiebreak_3       | points_against
   o-league    | seed_1_division_priority  | true
   o-league    | seed_2_division_priority  | true
   o-league    | seed_3_division_priority  | true
   o-league    | seed_6_tiebreak_1         | points_for
   ```
   This is all editable from the site's Settings tab (see "Playoff seeding"
   there) rather than the sheet directly. Unconfigured seeds/leagues fall
   back to today's behavior - `division_winners_first` for the top seeds,
   Wins→Points For for the rest.
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
`full` or `refresh` pipeline run (see "Update cadence" above, or trigger the
`update` workflow manually), not instantly - the site doesn't recompute live
in the browser.

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

**Real current seed vs. simulated odds:** the Standings page's Seed column
is `engine.standings.compute_current_seeds`' deterministic read of the real
current standings under the settings-sheet tiebreaker config above; the Seed
dist./Playoff%/Bye% columns are `simulate_playoffs`' 10,000-iteration Monte
Carlo forecast of the *rest of the season*, which still resolves in-sim ties
with a fixed Wins→PF rule (not the configurable chain) since re-deriving
head-to-head/PA results inside every simulated season isn't worth the added
cost for a probabilistic forecast.

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

## FAAB model data refresh (occasional, manual - not in `update.yml`)

The FAAB bid estimator (`engine/faab_estimate.py`, the "FAAB Bid" player-modal
tab and the Waiver Bid Backtest artifact) trains on a pooled multi-league
dataset that lives outside git entirely - see "FAAB training data storage"
below. Nothing about refreshing or RETRAINING it is scheduled or automatic:
only the `full` stage even touches it, and only ever to *reuse* whatever's
currently published, never to rebuild it. Pulling ~30 leagues' worth of ESPN
history is slow, has
real WAF/soft-block risk (see `ingest/espn_injuries.py`'s comments), and
retraining changes what the model actually believes - all good reasons for
this to stay a deliberate action you run by hand, not a cron job.

The one exception is the same-week cross-league signal
(`FaabModel.same_week_signal`, `tools/faab_history/pull_current_week_bids.py`)
- unlike the pooled training dataset, this is genuinely time-sensitive (it's
only useful for the week it's pulled) and only ever needs PUBLIC-league ESPN
access, so it IS automated: `.github/workflows/faab_weekly.yml`, dispatched
by cron-job.org Wednesday mornings (see "Update cadence" above), commits
`tools/faab_history/current-week-bids.json` straight to `main`.

**To expand the pooled dataset with more leagues:**

1. `python -m tools.faab_history.discover_public_leagues --start <id> --end <id>`
   - scans an ESPN league-id range for leagues that are public *right now*
   and currently running real FAAB bidding. `--start`/`--end` are required
   (no default range - you choose which id block to scan). `--year`
   (default 2026) is the season whose settings count as "current" for that
   check.
2. `python -m tools.faab_history.vet_candidates` - filters those candidates
   against The O League's own settings (team count, PPR format, not IDP,
   looks like a normal points league). Any newly-encountered scoring
   category needs a human judgment call in `scoring_ledger.json` (repo
   root) before a league can pass this step - see `league_profile.py`'s
   `load_scoring_ledger`/`check_scoring_ledger`. Resumable by default -
   skips league ids already in `vetted_candidates.json`. Qualifiers:
   - `--year` (default 2026) - the season every candidate is checked as
     public in.
   - `--reverify` - re-fetches settings and re-runs the compatibility check
     (including the scoring-ledger check) for *every* candidate, not just
     ones not yet vetted. Needed once after `scoring_ledger.json` is
     created or edited - existing compatible=true/false verdicts were
     decided before the ledger existed, so a normal resumable run would
     never revisit them.
3. `python -m tools.faab_history.check_candidate_history` - checks which of
   each vetted candidate's *past* seasons are actually readable without
   ESPN login (being public today doesn't mean history is - The O League
   itself needs credentials for 2019-2025 despite 2026 being open), and
   separately confirms each of those accessible seasons was actually
   running FAAB that year (a league can switch onto FAAB partway through
   its ESPN history). Resumable by default - skips league ids already in
   `history_availability.json`. Qualifiers:
   - `--start-year`/`--end-year` (default 2019/2025) - the fixed window
     checked by default, matching when The O League itself started FAAB
     bidding.
   - `--extend-earlier` - after the fixed window, keeps walking backward
     one year at a time (`--start-year` - 1, - 2, ...) for as long as each
     earlier season stays accessible without credentials *and* confirmed
     FAAB, stopping at the first season that comes back private, doesn't
     exist, or was real-but-not-FAAB (plain waiver priority) - whichever
     comes first. Off by default: it adds real unbounded per-league request
     volume to a rate-limited public API, worth opting into deliberately
     for a candidate whose real history likely predates 2019 rather than on
     every routine run.
   - `--reverify` - re-checks *every* compatible candidate's years, not
     just ones not yet in `history_availability.json`. Needed once after
     the per-season FAAB-enabled check was added (2026-09-16) - leagues
     checked before then were only ever verified for transaction
     accessibility, never for whether each accessible season was actually
     running FAAB, and a normal resumable run would never revisit them.
4. `python -m tools.faab_history.pull_public_league_bids` and
   `python -m tools.faab_history.pull_public_league_rosters` - pull the
   accessible (league, year) pairs from step 3. Both default to every
   league found accessible; pass `--league-id <id>` to pull just one.
5. `python -m tools.faab_history.validate_league_seasons` - a second, softer
   pass beyond step 2's structural compatibility check: flags any (league,
   season) whose real bid activity sits in the extreme tails of the pooled
   distribution (near-zero team participation, a reported budget that looks
   wrong, bidding far quieter or more frantic than everywhere else) for
   *human* review - it's a report, not an automatic filter, since "quiet but
   real" and "broken" both look like a low bid rate from the outside. Prints
   a table and writes `league_season_validity_report.json`; hand-exclude any
   confirmed problem the same way as `KNOWN_BAD_BID_TRANSACTION_IDS` (drop
   the league from `vetted_candidates.json`'s compatible set, or a specific
   transaction id) before the next step. No command-line qualifiers.
6. `python -m tools.faab_history.build_training_table` - joins The O
   League's own bids plus everything pulled above against nflverse data and
   one shared baseline scoring standard (`baseline_scoring.json`, repo root -
   every pooled row's points-based features are computed under this ONE
   standard regardless of which league placed the bid, so a 28-point PPR
   game and a 20-point Standard game for the same real box score train as
   the same situation; only the bid *dollars* stay denominated in each
   league's own budget), writing both `o-league-training-table.json` (O
   League only - what `evaluate_model.py` backtests against by default) and
   `combined-training-table.parquet` (pooled - what the live model at
   `engine.faab_estimate.POOLED_TRAINING_TABLE_PATH` actually trains on;
   `.parquet`, not `.json` - the pooled table's row count made a plain
   `json.loads` OOM a CI runner outright, see `engine/faab_estimate.py`'s
   `load_pools`). A
   league-season with zero real transactions never contributes synthetic
   no-bid rows (see `build_no_bid_rows`'s own docstring) even if it has a
   roster pull. No command-line qualifiers.
7. Sanity-check before publishing - `python -m tools.faab_history.evaluate_model`
   (backtest) and a look at the Waiver Bid Backtest artifact are the two
   established ways to confirm a change didn't quietly make things worse
   (see the "does pooling help" section on that artifact for the shape of
   this check). Qualifiers:
   - `--table <path>` (default `o-league-training-table.json`) - which
     training table to evaluate against; point at
     `combined-training-table.parquet` to include the other pooled leagues.
   - `--holdout-league-id <id>` (default none) - restricts the *test*
     holdout to just this one league's events; every other league's events
     fold into training unconditionally regardless of the normal train/test
     split. Use `--table combined-training-table.parquet --holdout-league-id
     355398` (The O League) to see whether pooling other public leagues'
     bids actually improves prediction of The O League's own held-out bids,
     rather than assuming it does.
   - `--out <path>` (default `eval_results.json`) - where results are
     written.
8. `python -m tools.faab_history.publish_release_data combined-training-table.parquet`
   (and any of the three raw files that changed) - uploads to the
   `faab-data` release and updates the pinned checksum in
   `fetch_release_data.py` in the same step. Commit that updated file. Takes
   one or more filenames as plain positional arguments (not flags) - not
   resumable/incremental, just re-uploads and re-hashes whatever you name;
   requires the `gh` CLI installed and authenticated locally, and is meant
   to be run by hand, never from CI.

Pastable in Command Prompt (uses the venv's own python explicitly, not
whatever bare `python` resolves to on PATH - a stale system Python with an
ancient polars silently crashed build_training_table.py with an
unrelated-looking `AttributeError` the one time this pointed at the wrong
interpreter; several of these steps run long enough that it's easy to end up
running one from a fresh terminal that never had the venv activated):
.venv\Scripts\python.exe -m tools.faab_history.discover_public_leagues --start <id> --end <id>
.venv\Scripts\python.exe -m tools.faab_history.vet_candidates
.venv\Scripts\python.exe -m tools.faab_history.check_candidate_history --extend-earlier --reverify
.venv\Scripts\python.exe -m tools.faab_history.pull_public_league_bids
.venv\Scripts\python.exe -m tools.faab_history.pull_public_league_rosters
.venv\Scripts\python.exe -m tools.faab_history.validate_league_seasons
.venv\Scripts\python.exe -m tools.faab_history.build_training_table
.venv\Scripts\python.exe -m tools.faab_history.evaluate_model
.venv\Scripts\python.exe -m tools.faab_history.publish_release_data combined-training-table.parquet other-leagues-bids-raw.json other-leagues-bids.json other-leagues-rostered-by-week.json

**FAAB training data storage:** `combined-training-table.parquet` (115MB
and growing as more leagues get pooled in - well over GitHub's 100MB
per-file push limit; this used to be JSON, 2.7GB raw and climbing, until it
grew enough to OOM-kill a CI runner loading it - see `engine/
faab_estimate.py`'s `load_pools`) and the three raw pulled-data files it's
built from aren't committed to git - they're assets on this repo's
`faab-data` GitHub Release instead (a release's file attachments live
outside git's own object store entirely, so they're exempt from both the
100MB limit and git's repo-size concerns; unlike Git LFS, a public repo's
release-asset bandwidth isn't metered the way LFS's stingy free tier is,
which matters given the `full` stage re-downloads it every day).
`publish_release_data.py` gzip-compresses each file before uploading it
regardless of format (a uniform step, not load-bearing for the Parquet file
specifically - its own columnar compression already keeps it well under
GitHub's 2GB single-asset cap on its own); `fetch_release_data.py` downloads
the compressed asset and decompresses it locally, transparently to every
other script that just expects the plain file to be there.
`.github/workflows/update.yml` runs `python -m tools.faab_history.fetch_release_data`
before the pipeline on `full` runs only (see "Update cadence" above) to pull
the one file it needs; `--all` also fetches the three raw inputs, only
needed to rebuild the training table from scratch per the steps above.

## Deployment (manual steps, one-time)

1. Repo Settings → Pages → Source: **GitHub Actions**.
2. Add repo secrets `ESPN_S2_AGS` and `SWID_AGS` (see `.env.example` for how
   to obtain them).
3. Run the `update` workflow once via "Run workflow" (stage `full`) and
   confirm both leagues build successfully, then confirm the `site-data`
   branch was created with both leagues' files (see "Update cadence" above).
4. Set up the cron-job.org jobs from the "Update cadence" table above,
   using a fine-grained PAT scoped to this repo with **Actions: Read and
   write**. Test each job once with "Execute now" and watch the run appear
   under Actions with the right `stage`.

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
