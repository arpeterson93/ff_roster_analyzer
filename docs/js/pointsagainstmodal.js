import { fmt, escapeHtml } from "./state.js";
import { openModal } from "./modal.js";
import { groupedHeaderHtml, statCellsHtml } from "./statcolumns.js";

function opponentLabel(wk) {
  if (!wk.opponent) return `<span class="muted">-</span>`;
  return (wk.home === false ? "@" : "") + escapeHtml(wk.opponent);
}

function render(position, weekDetail, expandedWeek) {
  const { top, bottom, flatColumns } = groupedHeaderHtml(position, ["", "Wk", "Opp"]);
  const weeks = Object.keys(weekDetail)
    .map(Number)
    .sort((a, b) => b - a);

  const rows = weeks
    .map((w) => {
      const wk = weekDetail[w];
      const players = wk.players || [];
      const expandable = players.length > 0;
      const expanded = expandable && expandedWeek === w;
      const arrow = expandable ? (expanded ? "▼" : "▶") : "";
      const rowClass = expandable ? "clickable-row" : "";
      const summaryRow = `<tr class="${rowClass}" data-week="${w}"><td class="muted small">${arrow}</td><td>${w}</td><td>${opponentLabel(wk)}</td>${statCellsHtml(wk.stats, flatColumns)}<td><strong>${fmt(wk.points, 1)}</strong></td></tr>`;
      if (!expanded) return summaryRow;
      const playerRows = players
        .map((p) => `<tr class="muted small"><td></td><td>${escapeHtml(p.name)}</td><td></td>${statCellsHtml(p.stats, flatColumns)}<td>${fmt(p.points, 1)}</td></tr>`)
        .join("");
      return summaryRow + playerRows;
    })
    .join("");

  return `<div class="table-wrap"><table>${top}${bottom}<tbody>${rows || '<tr><td colspan="99" class="muted">No data yet.</td></tr>'}</tbody></table></div>`;
}

export function openPointsAgainstModal(team, position, data) {
  const detailForPos = (data.pointsAgainst || {})[position] || { current: {}, prior: {} };
  const currentDetail = detailForPos.current?.[team];
  const useCurrentSeason = currentDetail && Object.keys(currentDetail).length > 0;
  const weekDetail = useCurrentSeason ? currentDetail : detailForPos.prior?.[team] || {};
  const season = useCurrentSeason ? data.recentResults?.current_season : data.recentResults?.prior_season;

  const state = { expandedWeek: null };

  function draw() {
    openModal(`
      <h2>${escapeHtml(team)} vs. ${position} <span class="muted small">(${season})</span></h2>
      ${position === "DST" ? '<p class="muted small">One row per week - a defense is a single unit, not many players.</p>' : ""}
      ${render(position, weekDetail, state.expandedWeek)}
    `);
    document.querySelectorAll(".modal-overlay .modal-content tr.clickable-row[data-week]").forEach((row) => {
      row.addEventListener("click", () => {
        const w = Number(row.dataset.week);
        state.expandedWeek = state.expandedWeek === w ? null : w;
        draw();
      });
    });
  }
  draw();
}
