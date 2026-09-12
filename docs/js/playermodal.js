import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { POSITION_COLOR, opponentCellHtml, teamLabel, playerPhotoHtml, weeklyProjection } from "./colors.js";
import { openModal } from "./modal.js";
import { groupedHeaderHtml, statCellsHtml } from "./statcolumns.js";

// Same grouped stat columns as the points-against modal, so a position's
// actual-results columns read identically in both places.
function gameLogTable(player) {
  const { top, bottom, flatColumns } = groupedHeaderHtml(player.position, ["Wk", "Opp"]);
  const rows = (player.weekly || [])
    .filter((w) => w.actual)
    .slice()
    .reverse()
    .map((w) => {
      const fpts = w.actual.points !== undefined && w.actual.points !== null ? fmt(w.actual.points, 1) : "-";
      return `<tr><td>${w.week}</td><td>${opponentCellHtml(w)}</td>${statCellsHtml(w.actual.stats, flatColumns)}<td><strong>${fpts}</strong></td></tr>`;
    })
    .join("");
  if (!rows) return `<p class="muted small">No games played yet this season.</p>`;
  return `<div class="table-wrap"><table>${top}${bottom}<tbody>${rows}</tbody></table></div>`;
}

// ESPN wk is a second, independent number shown alongside our own Proj/SD -
// never blended into it (see engine/pipeline.py's espn_future_projections).
// The current week's own ESPN number lives on the player record itself
// (espn_projected_week, from get_teams()) rather than in weekly[].
// espn_projected (only fetched for future weeks - see
// EspnClient.get_future_espn_projections), so this falls back to that for
// whichever row is the current week.
function espnWeekProjection(player, w, currentWeek) {
  return w.week === currentWeek ? player.espn_projected_week : w.espn_projected;
}

function projectionTable(player, currentWeek) {
  // opponentCellHtml already colors/labels the Opp cell by matchup rank
  // (see colors.js) - a separate Matchup column repeated that. Proj defers
  // to ESPN for the current week (weeklyProjection, same rule Start/Sit and
  // Schedule use) - SD is only ever ours (ESPN doesn't publish one), so it
  // stays w.sd regardless of week.
  const rows = (player.weekly || [])
    .filter((w) => !w.actual)
    .map((w) => `<tr><td>${w.week}</td><td>${opponentCellHtml(w)}</td><td>${fmt(weeklyProjection(player, w.week, currentWeek), 1)}</td><td>${fmt(w.sd, 1)}</td><td>${fmt(espnWeekProjection(player, w, currentWeek), 1)}</td></tr>`)
    .join("");
  // Only a total-points projection is computed for future weeks (not a full
  // stat line), so this can't show the grouped stat columns the game log
  // does - just the scalar projection + uncertainty.
  return `<table><thead><tr><th>Wk</th><th>Opp</th><th>Proj</th><th>SD</th><th>ESPN wk</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// JS port of engine/faab_estimate.py's weighted_percentile - same "first
// value whose cumulative weight reaches the target" convention, kept in
// sync deliberately rather than shipping a lookup table, so the confidence
// slider computes any percentage live instead of snapping between a few
// values baked in at build time (see faabEstimateSection's price_confidence_samples).
function weightedPercentileJs(samples, pct) {
  if (!samples || !samples.length) return 0;
  const ordered = samples.slice().sort((a, b) => a[0] - b[0]);
  const total = ordered.reduce((s, [, w]) => s + w, 0);
  if (total <= 0) return ordered[ordered.length - 1][0];
  const target = (pct / 100) * total;
  let cum = 0;
  for (const [v, w] of ordered) {
    cum += w;
    if (cum >= target) return v;
  }
  return ordered[ordered.length - 1][0];
}

const SIG_LABEL = { won: "won", outbid: "outbid", other_failure: "failed", no_bid: "no bid" };
function sigChipHtml(signal) {
  return `<span class="sig-chip sig-${signal}">${SIG_LABEL[signal] || signal}</span>`;
}

// Rank/recency-average pairs stacked in one cell (see styles.css's
// .comp-rank-cell) rather than four separate columns - a comp table with
// player/when/signal/bid/%/pts/snap/rank/recent/flags would run to 9-10
// columns, wider than most phone screens can show without scrolling for
// what reads just as well stacked two-to-a-cell.
function rankCellHtml(weeklyRank, rosRank) {
  const wk = weeklyRank !== null && weeklyRank !== undefined ? `wk #${Math.round(weeklyRank)}` : "wk unranked";
  const ros = rosRank !== null && rosRank !== undefined ? `ROS #${Math.round(rosRank)}` : "ROS unranked";
  return `<span class="comp-rank-cell">${wk}<span class="sub">${ros}</span></span>`;
}
function recentCellHtml(pts, trailing, season) {
  const main = pts !== null && pts !== undefined ? `${fmt(pts, 1)} pts` : "–";
  return `<span class="comp-recent-cell">${main}<span class="sub">3wk ${fmt(trailing, 1)} · szn ${fmt(season, 1)}</span></span>`;
}
function flagsCellHtml(ownInjury, teammateInjury) {
  const flags = [ownInjury ? `<span class="comp-flag">inj</span>` : "", teammateInjury ? `<span class="comp-flag tm">tm inj</span>` : ""].join("");
  return flags || `<span class="muted small">–</span>`;
}

const INTEREST_LABEL = { high: "High", medium: "Med", low: "Low" };

// "Which owners in the league are likely to want this guy, and why" - see
// engine/pipeline.py's team_interest_for. Two independent reasons roll up
// into one Low/Med/High level per owner: a real handcuff tie (they own the
// injured starter this player would step in for - always shown at High,
// regardless of the roster-math gain number) and/or a real lineup upgrade
// (fa_values' actual best add/drop point swing, bucketed by percentile of
// this week's own gain spread across the league - the same NMD number
// Rankings shows, just for every owner here instead of only yours).
function teamInterestSection(teamInterest, data) {
  if (!teamInterest || !teamInterest.length) return "";
  const rows = teamInterest
    .map((ti) => {
      const team = data.teamsById.get(ti.team_id);
      const owner = team ? escapeHtml(teamLabel(team)) : `Team ${ti.team_id}`;
      const reason = ti.handcuff_of
        ? `<span class="handcuff-tag">owns ${escapeHtml(ti.handcuff_of)}</span>`
        : ti.nmd_gain !== null && ti.nmd_gain !== undefined
        ? `<span class="muted small">${ti.nmd_gain >= 0 ? "+" : ""}${fmt(ti.nmd_gain, 1)} pts/wk to their lineup</span>`
        : `<span class="muted small">–</span>`;
      return `<tr><td>${owner}</td><td><span class="interest-chip ${ti.level}">${INTEREST_LABEL[ti.level] || ti.level}</span></td><td>${reason}</td></tr>`;
    })
    .join("");
  return `
    <h3>Likely interested owners <span class="muted small">- who else in your league has a real reason to bid</span></h3>
    <div class="table-wrap"><table><thead><tr><th>Owner</th><th>Interest</th><th>Why</th></tr></thead><tbody>${rows}</tbody></table></div>
  `;
}

// Three deliberately-explainable numbers (see engine/faab_estimate.py) -
// the comps table IS the explanation for comp_based, not just supporting
// detail, so it's shown in full rather than collapsed away.
function faabEstimateSection(player, data) {
  const est = (data.faabEstimates || {})[player.id];
  if (!est) return "";
  const inputs = est.inputs || {};
  if (est.below_relevance_threshold) {
    return `
      <div class="bar-row"><div class="bar-label">Estimate</div><div class="bar-value">0%</div></div>
      <p class="muted small">Not enough recent usage or production (last week: ${inputs.prior_week_had_stat_row ? `${fmt(inputs.prior_week_actual_points, 1)} pts` : "no stat line"}, ${inputs.snap_pct_prior_week !== null && inputs.snap_pct_prior_week !== undefined ? `${fmt(inputs.snap_pct_prior_week * 100, 0)}% snaps` : "no recent snap data"}) to be worth modeling as a real bid target - defaulting to 0% rather than extrapolating from unrelated comps.</p>
      ${teamInterestSection(est.team_interest, data)}
    `;
  }
  const snapPct = inputs.snap_pct_prior_week;
  const inputSummary = [
    `Week ${inputs.week}`,
    inputs.prior_week_had_stat_row ? `${fmt(inputs.prior_week_actual_points, 1)} pts last week` : "no stat line last week",
    inputs.trailing_2_3_avg_points !== null && inputs.trailing_2_3_avg_points !== undefined ? `${fmt(inputs.trailing_2_3_avg_points, 1)} pts/wk (2-3 wks ago)` : null,
    inputs.season_avg_points !== null && inputs.season_avg_points !== undefined ? `${fmt(inputs.season_avg_points, 1)} pts/wk season avg` : null,
    snapPct !== null && snapPct !== undefined ? `${fmt(snapPct * 100, 0)}% snap share` : "no recent snap data",
    inputs.own_injury_flag ? "own injury flag" : "no own injury flag",
    inputs.teammate_position_injury_flag ? "a relevant teammate is banged up" : "no relevant teammate injury",
    inputs.had_weekly_rank ? `#${Math.round(inputs.weekly_rank)} weekly rank` : null,
    inputs.had_ros_rank ? `#${Math.round(inputs.ros_rank)} ROS rank` : null,
    // best_position_competitor_ros_rank - see engine/faab_estimate.py's
    // annotate_position_competition: the best (lowest) ROS rank among every
    // OTHER same-position free agent this week, a "hot commodity" crowding-
    // out signal. Shown either way (unlike the other optional chips above,
    // which are silently omitted when missing) - "nobody notable else is
    // on the wire" is itself a meaningful, worth-surfacing read, not an
    // absence of data.
    inputs.had_position_competitor_rank
      ? `best competing FA on wire: #${Math.round(inputs.best_position_competitor_ros_rank)} ROS rank`
      : "no highly-ranked competing FA on the wire",
  ]
    .filter(Boolean)
    .join(" · ");

  // o_league_detail is only set when this comp is a cross-league
  // consolidated event (see engine/faab_estimate.py's
  // consolidate_cross_league_events) that The O League itself was also
  // part of - real specificity from the one league a reader actually
  // knows, on top of the cross-league statistical read.
  const oLeagueTag = (detail) =>
    !detail
      ? ""
      : detail.signal === "won"
      ? `<div class="muted small">The O League: won $${fmt(detail.bid_dollars, 2)}</div>`
      : `<div class="muted small">The O League: ${detail.signal.replace("_", " ")}</div>`;

  const priceComps = (est.comps || [])
    .map((c) => {
      const rivals = c.competing_bids || [];
      const competition = rivals.length
        ? `<div class="muted small">vs ${rivals.map((b) => `$${fmt(b.bid_dollars, 2)}`).join(", ")}</div>`
        : c.signal === "won"
        ? `<div class="muted small">uncontested</div>`
        : "";
      return `<tr>
        <td>${escapeHtml(c.name)}<div class="muted small">${c.season} wk${c.week}</div></td>
        <td>${sigChipHtml(c.signal)}${competition}${oLeagueTag(c.o_league_detail)}</td>
        <td>$${fmt(c.bid_dollars, 2)}</td>
        <td>${fmt(c.pct_of_remaining_budget * 100, 1)}%</td>
        <td>${recentCellHtml(c.prior_week_actual_points, c.trailing_2_3_avg_points, c.season_avg_points)}</td>
        <td>${rankCellHtml(c.weekly_rank, c.ros_rank)}</td>
        <td>${flagsCellHtml(c.own_injury_flag, c.teammate_position_injury_flag)}</td>
      </tr>`;
    })
    .join("");

  const interestComps = (est.interest_comps || [])
    .map(
      (c) => `<tr>
        <td>${escapeHtml(c.name)}<div class="muted small">${c.season} wk${c.week}</div></td>
        <td>${sigChipHtml(c.signal)}${oLeagueTag(c.o_league_detail)}</td>
        <td>${recentCellHtml(c.prior_week_actual_points, c.trailing_2_3_avg_points, c.season_avg_points)}</td>
        <td>${rankCellHtml(c.weekly_rank, c.ros_rank)}</td>
        <td>${flagsCellHtml(c.own_injury_flag, c.teammate_position_injury_flag)}</td>
      </tr>`
    )
    .join("");

  // The K price comps (comp["comps"]) are real WINNING bids only - their
  // spread is the "if this goes to auction" distribution, not the blended
  // headline number (which also folds in P(anyone bids) - see
  // bid_probability/conditional_price below). The marker on this track
  // shows conditional_price, not comp_based, so it actually falls inside
  // the range it's plotted against.
  const dist = est.distribution;
  const condPrice = (est.conditional_price || {}).comp_based;
  const samples = est.price_confidence_samples || [];
  const distHtml = dist
    ? (() => {
        const span = Math.max(0.001, dist.max - dist.min);
        const pct = (v) => Math.max(0, Math.min(100, ((v - dist.min) / span) * 100));
        const defaultConfidence = 80;
        const defaultBid = weightedPercentileJs(samples, defaultConfidence);
        // A losing bid can never exceed its OWN auction's winning price, and
        // every sample here comes from one of the same comps dist.min/max
        // is built from - so this track's existing scale already safely
        // bounds every possible confidence-slider position, no separate
        // axis needed for the second marker.
        return `
          <div class="faab-dist">
            <div class="faab-dist-track">
              <div class="faab-dist-iqr" style="left:${pct(dist.p25)}%; width:${pct(dist.p75) - pct(dist.p25)}%;"></div>
              <div class="faab-dist-marker" style="left:${pct(condPrice)}%;" title="Price if contested: ${fmt(condPrice * 100, 1)}%"></div>
              ${samples.length ? `<div class="faab-dist-marker faab-dist-marker-confidence" data-confidence-marker style="left:${pct(defaultBid)}%;"></div>` : ""}
            </div>
            <div class="faab-dist-labels">
              <span>${fmt(dist.min * 100, 1)}%</span>
              <span class="muted">${fmt(dist.p25 * 100, 1)}%–${fmt(dist.p75 * 100, 1)}% middle half of real winning bids</span>
              <span>${fmt(dist.max * 100, 1)}%</span>
            </div>
          </div>
          ${samples.length
            ? `
            <div class="faab-confidence">
              <div class="faab-confidence-row">
                <span>Bid for <b data-confidence-pct>${defaultConfidence}%</b> confidence</span>
                <span class="faab-confidence-bid" data-confidence-bid>${fmt(defaultBid * 100, 1)}%</span>
              </div>
              <input type="range" min="50" max="99" value="${defaultConfidence}" class="faab-confidence-slider" data-confidence-slider>
              <p class="muted small">The bid that would have beaten about this share of comparable historical bids - <b>every real bid</b> placed in a similar spot, not just the ones that won, pooled from ${samples.length} real bids behind the comps below. Not a guaranteed win chance: this is what similar bidding wars have looked like before, not a forecast of what anyone else bids this specific week.</p>
            </div>
          `
            : ""}
        `;
      })()
    : "";

  // Two-stage model (see engine/faab_estimate.py module docstring), but
  // deliberately NOT shown as one blended P(bid) x price number - that
  // product is an ex-ante expected cost, not "what to bid if you want him".
  // The headline per method is conditional_price (price IF contested);
  // bid_probability is separate context underneath, never multiplied in.
  const bidProb = est.bid_probability || {};
  const condPriceByMethod = est.conditional_price || {};
  const stageBreakdown = (key) =>
    bidProb[key] !== undefined
      ? `<div class="muted small">${fmt(bidProb[key] * 100, 0)}% chance you'll even need to bid</div>`
      : "";

  return `
    <p class="muted small">% of your league's effective starting FAAB budget to bid IF you want to win him - see the % chance below each for how likely a contest even is.</p>
    <div class="bar-row"><div class="bar-label">Comp-based</div><div class="bar-value">${fmt(condPriceByMethod.comp_based * 100, 1)}%</div></div>
    ${stageBreakdown("comp_based")}
    <div class="bar-row"><div class="bar-label">Similar-usage avg</div><div class="bar-value">${fmt(condPriceByMethod.simple_baseline * 100, 1)}%</div></div>
    ${stageBreakdown("simple_baseline")}
    <div class="bar-row"><div class="bar-label">Regression</div><div class="bar-value">${fmt(condPriceByMethod.regression * 100, 1)}%</div></div>
    ${stageBreakdown("regression")}
    ${distHtml}
    <p class="muted small">Inputs considered: ${inputSummary}</p>

    ${teamInterestSection(est.team_interest, data)}

    <h3>Price comps <span class="muted small">- won only, drives the price-if-contested estimate above</span></h3>
    <div class="table-wrap"><table><thead><tr><th>Player</th><th>Outcome</th><th>Real bid</th><th>% of budget</th><th>Recent pts</th><th>Rank</th><th>Flags</th></tr></thead><tbody>${priceComps || `<tr><td colspan="7" class="muted small">No comparable winning bids found.</td></tr>`}</tbody></table></div>

    <h3>Interest comps <span class="muted small">- won + outbid + no-bid, drives the "chance you'll even need to bid" estimate</span></h3>
    <div class="table-wrap"><table><thead><tr><th>Player</th><th>Outcome</th><th>Recent pts</th><th>Rank</th><th>Flags</th></tr></thead><tbody>${interestComps || `<tr><td colspan="5" class="muted small">No comparable situations found.</td></tr>`}</tbody></table></div>
  `;
}

function overviewTabHtml(player, data) {
  const hasGameLog = (player.weekly || []).some((w) => w.actual);
  return `
    ${hasGameLog ? `<h3>Game log</h3>${gameLogTable(player)}` : ""}
    <h3>${hasGameLog ? "Remaining schedule" : "Weekly projections"}</h3>
    <div class="table-wrap">${projectionTable(player, data.meta.current_week)}</div>
  `;
}

// NMD week-by-week (see engine/team_strength.py's depth_values_by_week/
// fa_values) - for a ROSTERED player, the same "Value (w/ waivers)" number
// already shown as a single stat tile above, broken out week by week and
// naming which specific free agent it's computed against (a single pick
// for the whole series, not re-chosen week to week - see that function's
// docstring). For a WAIVER player, the mirror image: your own team's
// specific add/drop swing week by week, and who it would replace - scoped
// to "your team" only (same as the Rankings NMD column and the stat-grid
// tile above), since fa_values_detail.json only carries this level of
// detail for candidates that clear the same real bar (gain > 0) for
// EVERY team, and showing one team's numbers on a click that could be
// anyone's would be misleading rather than just incomplete.
function nmdDetailSection(player, data) {
  if (player.fantasy_team_id !== null) {
    const team = data.teamsById.get(player.fantasy_team_id);
    const entry = (team?.depth?.[player.position] || []).find((d) => d.id === player.id);
    if (!entry) return "";
    const replacement = entry.ww_replacement_id ? data.playersById.get(entry.ww_replacement_id) : null;
    const rows = entry.weekly
      .map((w) => `<tr><td>Wk ${w.week}</td><td>${fmt(w.value_delta, 1)}</td><td>${fmt(w.value_delta_ww, 1)}</td></tr>`)
      .join("");
    return `
      <h3>NMD week-by-week</h3>
      <p class="muted small">"Value" is the lineup points your team loses if he's dropped outright, week by week. "Value (w/ waivers)" is the same drop, but immediately backfilled by ${replacement ? `<b>${escapeHtml(replacement.name)}</b>` : "the best available free agent at his position"} - the same single replacement the whole season through, not re-picked week to week.</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Week</th><th>Value</th><th>Value (w/ waivers)</th></tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr class="totals-row"><td>Total</td><td>${fmt(entry.value_delta, 1)}</td><td>${fmt(entry.value_delta_ww, 1)}</td></tr></tfoot>
        </table>
      </div>
    `;
  }

  const yourTeamId = getYourTeam(data.meta.slug);
  if (yourTeamId === null) return "";
  const detail = (data.faValuesDetail || {})[String(yourTeamId)]?.[player.id];
  if (!detail) return "";
  const dropPlayer = data.playersById.get(detail.drop);
  const weeks = Object.keys(detail.weekly).map(Number).sort((a, b) => a - b);
  const total = weeks.reduce((acc, w) => acc + detail.weekly[w], 0);
  const rows = weeks.map((w) => `<tr><td>Wk ${w}</td><td>${detail.weekly[w] >= 0 ? "+" : ""}${fmt(detail.weekly[w], 1)}</td></tr>`).join("");
  return `
    <h3>NMD week-by-week <span class="muted small">- your team's perspective</span></h3>
    <p class="muted small">Lineup-point swing, week by week, from adding him and dropping ${dropPlayer ? `<b>${escapeHtml(dropPlayer.name)}</b>` : "your weakest same-position player"} - the same add/drop pairing the "NMD" figure elsewhere on the site is built from, not a different trade every week.</p>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Week</th><th>Swing</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr class="totals-row"><td>Total</td><td>${total >= 0 ? "+" : ""}${fmt(total, 1)}</td></tr></tfoot>
      </table>
    </div>
  `;
}

// The single-player modal's full inner HTML (header + Overview/FAAB tabs,
// or just a flat Overview when there's no FAAB tab to show) - factored out
// of openPlayerModal so openComparePlayerModal can render the exact same
// content twice, side by side, rather than reimplementing it.
function playerModalContentHtml(player, data) {
  const color = POSITION_COLOR[player.position] || "#888";
  const team = player.fantasy_team_id !== null ? data.teamsById.get(player.fantasy_team_id) : null;
  // A FAAB tab only exists at all for players on ESPN "WAIVERS" status this
  // week (see engine/pipeline.py's _compute_faab_estimates) - everyone else
  // (the vast majority of players opened from Rankings/rosters) keeps the
  // exact same single flat view this modal always had, no empty tab bar.
  const faabHtml = faabEstimateSection(player, data);

  const header = `
    <div class="player-modal-header">
      ${playerPhotoHtml(player, "player-photo-lg")}
      <div>
        <h2><span class="pos-tag" style="background:${color}">${player.position}</span> ${escapeHtml(player.name)} <span class="muted small">${escapeHtml(player.nfl_team || "")}</span></h2>
        <p class="muted small">${team ? escapeHtml(teamLabel(team)) : "Free agent"} · ROS rank ${player.ros_pos_rank ?? "–"} · Bye ${player.bye ?? "–"}</p>
      </div>
    </div>
    <div class="player-stat-grid">
      <div class="stat-tile"><div class="stat-label">Baseline</div><div class="stat-value">${fmt(player.baseline_ppg, 1)} ppg</div></div>
      <div class="stat-tile"><div class="stat-label">ROS total</div><div class="stat-value">${fmt(player.ros_total, 1)}</div></div>
      <div class="stat-tile"><div class="stat-label">Reg / Playoff</div><div class="stat-value">${fmt(player.reg_total, 1)} / ${fmt(player.playoff_total, 1)}</div></div>
      <div class="stat-tile"><div class="stat-label">Value (w/ waivers)</div><div class="stat-value">${player.value_delta_ww !== null ? fmt(player.value_delta_ww, 1) : "–"}</div></div>
    </div>
    ${nmdDetailSection(player, data)}
  `;

  if (!faabHtml) {
    return `${header}${overviewTabHtml(player, data)}`;
  }

  return `
    ${header}
    <div class="modal-tabs">
      <button class="modal-tab-btn active" data-modal-tab="overview">Overview</button>
      <button class="modal-tab-btn" data-modal-tab="faab">FAAB Bid</button>
    </div>
    <div class="modal-tabpanel active" data-modal-panel="overview">${overviewTabHtml(player, data)}</div>
    <div class="modal-tabpanel" data-modal-panel="faab">${faabHtml}</div>
  `;
}

// Scoped to scopeEl (the whole modal for a single player, or one
// .compare-col for the compare view) rather than the whole document, so two
// independent Overview/FAAB tab groups on screen at once (compare mode)
// don't cross-wire each other's clicks.
function wirePlayerModalTabs(scopeEl) {
  scopeEl.querySelectorAll(".modal-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      scopeEl.querySelectorAll(".modal-tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
      scopeEl.querySelectorAll(".modal-tabpanel").forEach((p) => p.classList.toggle("active", p.dataset.modalPanel === btn.dataset.modalTab));
    });
  });
}

// Scoped the same way wirePlayerModalTabs is (compare mode has two
// independent sliders on screen at once). Looks est back up from
// data.faabEstimates rather than threading it through return values, the
// same lookup faabEstimateSection itself does.
function wireFaabConfidenceSlider(scopeEl, player, data) {
  const slider = scopeEl.querySelector("[data-confidence-slider]");
  if (!slider) return;
  const est = (data.faabEstimates || {})[player.id];
  const samples = est?.price_confidence_samples || [];
  const dist = est?.distribution;
  if (!dist) return;
  const span = Math.max(0.001, dist.max - dist.min);
  const pctPos = (v) => Math.max(0, Math.min(100, ((v - dist.min) / span) * 100));
  slider.addEventListener("input", () => {
    const confidence = Number(slider.value);
    const bid = weightedPercentileJs(samples, confidence);
    scopeEl.querySelectorAll("[data-confidence-pct]").forEach((el) => (el.textContent = `${confidence}%`));
    scopeEl.querySelectorAll("[data-confidence-bid]").forEach((el) => (el.textContent = `${fmt(bid * 100, 1)}%`));
    const marker = scopeEl.querySelector("[data-confidence-marker]");
    if (marker) marker.style.left = `${pctPos(bid)}%`;
  });
}

export function openPlayerModal(player, data) {
  openModal(playerModalContentHtml(player, data));
  const scope = document.querySelector(".modal-content");
  wirePlayerModalTabs(scope);
  wireFaabConfidenceSlider(scope, player, data);
}

// Side-by-side on a wide screen (see .compare-grid/.modal-overlay-wide in
// styles.css); on a narrow one the same markup collapses to one column at a
// time behind the .compare-side-btn toggle instead - same content either
// way, CSS alone decides which layout renders it.
export function openComparePlayerModal(playerA, playerB, data) {
  const html = `
    <div class="compare-toggle">
      <button class="compare-side-btn active" data-compare-side="a">${escapeHtml(playerA.name)}</button>
      <button class="compare-side-btn" data-compare-side="b">${escapeHtml(playerB.name)}</button>
    </div>
    <div class="compare-grid">
      <div class="compare-col active" data-compare-col="a">${playerModalContentHtml(playerA, data)}</div>
      <div class="compare-col" data-compare-col="b">${playerModalContentHtml(playerB, data)}</div>
    </div>
  `;
  openModal(html, { wide: true });

  const modalContent = document.querySelector(".modal-content");
  modalContent.querySelectorAll(".compare-col").forEach((col) => wirePlayerModalTabs(col));
  wireFaabConfidenceSlider(modalContent.querySelector('[data-compare-col="a"]'), playerA, data);
  wireFaabConfidenceSlider(modalContent.querySelector('[data-compare-col="b"]'), playerB, data);
  modalContent.querySelectorAll(".compare-side-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      modalContent.querySelectorAll(".compare-side-btn").forEach((b) => b.classList.toggle("active", b === btn));
      modalContent.querySelectorAll(".compare-col").forEach((c) => c.classList.toggle("active", c.dataset.compareCol === btn.dataset.compareSide));
    });
  });
}
