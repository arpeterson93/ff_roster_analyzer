import { fmt, escapeHtml } from "./state.js";
import { colorForRatio, ratioForRank, POSITION_COLOR, opponentCellHtml, teamLabel } from "./colors.js";
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

function projectionTable(player) {
  const rows = (player.weekly || [])
    .filter((w) => !w.actual)
    .map((w) => {
      const ratio = ratioForRank(w.rank);
      const badge = w.opponent
        ? `<span class="pill" style="background:${colorForRatio(ratio)}">#${w.rank}</span>`
        : `<span class="muted">-</span>`;
      return `<tr><td>${w.week}</td><td>${opponentCellHtml(w)}</td><td>${badge}</td><td>${fmt(w.projected, 1)}</td><td>${fmt(w.sd, 1)}</td></tr>`;
    })
    .join("");
  // Only a total-points projection is computed for future weeks (not a full
  // stat line), so this can't show the grouped stat columns the game log
  // does - just the scalar projection + uncertainty.
  return `<table><thead><tr><th>Wk</th><th>Opp</th><th>Matchup</th><th>Proj</th><th>SD</th></tr></thead><tbody>${rows}</tbody></table>`;
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
      <h3>FAAB bid estimate <span class="muted small">(on waivers - needs a bid, not an instant add)</span></h3>
      <div class="bar-row"><div class="bar-label">Estimate</div><div class="bar-value">$0</div></div>
      <p class="muted small">Not enough recent usage or production (last week: ${inputs.prior_week_had_stat_row ? `${fmt(inputs.prior_week_actual_points, 1)} pts` : "no stat line"}, ${inputs.snap_pct_prior_week !== null && inputs.snap_pct_prior_week !== undefined ? `${fmt(inputs.snap_pct_prior_week * 100, 0)}% snaps` : "no recent snap data"}) to be worth modeling as a real bid target - defaulting to $0 rather than extrapolating from unrelated comps.</p>
    `;
  }
  const snapPct = inputs.snap_pct_prior_week;
  const inputSummary = [
    `Week ${inputs.week}`,
    inputs.prior_week_had_stat_row ? `${fmt(inputs.prior_week_actual_points, 1)} pts last week` : "no stat line last week",
    snapPct !== null && snapPct !== undefined ? `${fmt(snapPct * 100, 0)}% snap share` : "no recent snap data",
    inputs.own_injury_flag ? "own injury flag" : "no own injury flag",
    inputs.teammate_position_injury_flag ? "a relevant teammate is banged up" : "no relevant teammate injury",
  ].join(" · ");

  const comps = (est.comps || [])
    .map((c) => {
      const rivals = c.competing_bids || [];
      const competition = rivals.length
        ? `<div class="muted small">vs ${rivals.map((b) => `$${fmt(b.bid_dollars, 2)}`).join(", ")}</div>`
        : c.signal === "won"
        ? `<div class="muted small">uncontested</div>`
        : "";
      return `<tr><td>${c.season} wk${c.week}</td><td>${escapeHtml(c.name)}</td><td class="muted small">${c.signal}${competition}</td><td>$${fmt(c.bid_dollars, 2)} <span class="muted small">real</span></td><td>$${fmt(c.fictional_dollars, 0)}</td></tr>`;
    })
    .join("");

  const dist = est.distribution;
  const distHtml = dist
    ? (() => {
        const span = Math.max(1, dist.max - dist.min);
        const pct = (v) => Math.max(0, Math.min(100, ((v - dist.min) / span) * 100));
        return `
          <div class="faab-dist">
            <div class="faab-dist-track">
              <div class="faab-dist-iqr" style="left:${pct(dist.p25)}%; width:${pct(dist.p75) - pct(dist.p25)}%;"></div>
              <div class="faab-dist-marker" style="left:${pct(est.comp_based)}%;" title="Point estimate: $${fmt(est.comp_based, 0)}"></div>
            </div>
            <div class="faab-dist-labels">
              <span>$${fmt(dist.min, 0)}</span>
              <span class="muted">$${fmt(dist.p25, 0)}–$${fmt(dist.p75, 0)} middle half</span>
              <span>$${fmt(dist.max, 0)}</span>
            </div>
          </div>
        `;
      })()
    : "";

  return `
    <h3>FAAB bid estimate <span class="muted small">(on waivers - needs a bid, not an instant add)</span></h3>
    <div class="bar-row"><div class="bar-label">Comp-based</div><div class="bar-value">$${fmt(est.comp_based, 0)}</div></div>
    <div class="bar-row"><div class="bar-label">Similar-usage avg</div><div class="bar-value">$${fmt(est.simple_baseline, 0)}</div></div>
    <div class="bar-row"><div class="bar-label">Regression</div><div class="bar-value">$${fmt(est.regression, 0)}</div></div>
    ${distHtml}
    <p class="muted small">Inputs considered: ${inputSummary}</p>
    <p class="muted small">Most similar historical bids (the comp-based estimate is a weighted average of these, in fictional-$ terms; "real" is what was actually bid at the time):</p>
    <div class="table-wrap"><table><thead><tr><th>When</th><th>Player</th><th>Outcome</th><th>Real bid</th><th>Fictional $</th></tr></thead><tbody>${comps}</tbody></table></div>
  `;
}

export function openPlayerModal(player, data) {
  const color = POSITION_COLOR[player.position] || "#888";
  const team = player.fantasy_team_id !== null ? data.teamsById.get(player.fantasy_team_id) : null;
  const hasGameLog = (player.weekly || []).some((w) => w.actual);

  const html = `
    <h2><span class="pos-tag" style="background:${color}">${player.position}</span> ${escapeHtml(player.name)} <span class="muted small">${escapeHtml(player.nfl_team || "")}</span></h2>
    <p class="muted small">${team ? escapeHtml(teamLabel(team)) : "Free agent"} · ROS rank ${player.ros_pos_rank ?? "–"} · Bye ${player.bye ?? "–"}</p>
    <div class="bar-row"><div class="bar-label">Baseline</div><div class="bar-value">${fmt(player.baseline_ppg, 1)} ppg</div></div>
    <div class="bar-row"><div class="bar-label">ROS total</div><div class="bar-value">${fmt(player.ros_total, 1)}</div></div>
    <div class="bar-row"><div class="bar-label">Reg / Playoff</div><div class="bar-value">${fmt(player.reg_total, 1)} / ${fmt(player.playoff_total, 1)}</div></div>
    <div class="bar-row"><div class="bar-label">Value (w/ waivers)</div><div class="bar-value">${player.value_delta_ww !== null ? fmt(player.value_delta_ww, 1) : "–"}</div></div>
    ${faabEstimateSection(player, data)}
    ${hasGameLog ? `<h3>Game log</h3>${gameLogTable(player)}` : ""}
    <h3>${hasGameLog ? "Remaining schedule" : "Weekly projections"}</h3>
    <div class="table-wrap">${projectionTable(player)}</div>
  `;
  openModal(html);
}
