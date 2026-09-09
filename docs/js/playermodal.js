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
    ${hasGameLog ? `<h3>Game log</h3>${gameLogTable(player)}` : ""}
    <h3>${hasGameLog ? "Remaining schedule" : "Weekly projections"}</h3>
    <div class="table-wrap">${projectionTable(player)}</div>
  `;
  openModal(html);
}
