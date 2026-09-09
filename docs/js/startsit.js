import { fmt, escapeHtml, getYourTeam, setYourTeam } from "./state.js";
import { POSITION_COLOR, INJURY_BADGE, opponentCellHtml, formatKickoff, sortByPositionOrder, teamLabel } from "./colors.js";
import { openPlayerModal } from "./playermodal.js";
import { openPointsAgainstModal } from "./pointsagainstmodal.js";

function posTag(pos) {
  const color = POSITION_COLOR[pos] || "#888";
  return `<span class="pos-tag" style="background:${color}">${pos}</span>`;
}

function healthBadge(status) {
  const letters = INJURY_BADGE[status];
  return letters ? `<span class="pill" style="background:var(--red-600)">${letters}</span>` : "";
}

// Data columns: [Slot], Player (name + health badge, with a second smaller
// meta line for team/ownership), Opp/Matchup (combined, with kickoff time on
// its own line above it), Proj, ESPN proj. Kept in sync with the <tfoot>
// colspan below so totals line up under the right columns.
function playerMetaLine(p) {
  const parts = [];
  if (p.nfl_team) parts.push(escapeHtml(p.nfl_team));
  if (p.percent_owned) parts.push(`${fmt(p.percent_owned, 0)}% Rost`);
  if (p.percent_started) parts.push(`${fmt(p.percent_started, 0)}% Start`);
  return parts.join(" · ");
}

function playerRow(p, week, slotLabel) {
  const weekEntry = (p.weekly || []).find((w) => w.week === week) || {};
  const slotCell = slotLabel !== undefined ? `<td class="muted small">${slotLabel}</td>` : "";
  const kickoff = formatKickoff(weekEntry.kickoff);
  return `<tr data-player-id="${p.id}" class="clickable-row">
    ${slotCell}
    <td>
      <div>${posTag(p.position)} <strong>${escapeHtml(p.name)}</strong> ${healthBadge(p.injury_status)}</div>
      <div class="muted small row-meta">${playerMetaLine(p)}</div>
    </td>
    <td data-opp-cell data-team="${escapeHtml(weekEntry.opponent || "")}" data-pos="${p.position}">
      ${kickoff ? `<div class="muted small row-meta">${kickoff}</div>` : ""}
      <div>${opponentCellHtml(weekEntry)}</div>
    </td>
    <td>${fmt(p.this_week, 1)}</td>
    <td>${fmt(p.espn_projected_week, 1)}</td>
  </tr>`;
}

function lineupSection(roster, week, lineupWeek) {
  const slots = lineupWeek.slots || {};
  const bySlot = Object.entries(slots).sort(([a], [b]) => sortByPositionOrder(a, b, (s) => s.replace(/\d+$/, "")));
  const playersById = new Map(roster.map((p) => [p.id, p]));
  const rows = bySlot
    .map(([slotLabel, pid]) => {
      const p = playersById.get(pid);
      return p ? playerRow(p, week, slotLabel.replace(/\d+$/, "")) : "";
    })
    .join("");
  const startedIds = new Set(Object.values(slots));
  const ourTotal = bySlot.reduce((acc, [, pid]) => acc + ((playersById.get(pid) || {}).this_week || 0), 0);
  const espnTotal = bySlot.reduce((acc, [, pid]) => acc + ((playersById.get(pid) || {}).espn_projected_week || 0), 0);

  const bench = (lineupWeek.bench || []).map((id) => playersById.get(id)).filter(Boolean);
  const benchRows = bench.map((p) => playerRow(p, week)).join("");
  const benchOurTotal = bench.reduce((acc, p) => acc + (p.this_week || 0), 0);
  const benchEspnTotal = bench.reduce((acc, p) => acc + (p.espn_projected_week || 0), 0);

  return `
    <table>
      <thead><tr><th>Slot</th><th>Player</th><th>Opp</th><th>Proj</th><th>ESPN proj</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot>
        <tr class="totals-row"><td colspan="2">Starters total</td><td></td><td><strong>${fmt(ourTotal, 1)}</strong></td><td><strong>${fmt(espnTotal, 1)}</strong></td></tr>
      </tfoot>
    </table>
    <h3>Bench <span class="muted small">(sorted by ESPN proj)</span></h3>
    <table>
      <thead><tr><th>Player</th><th>Opp</th><th>Proj</th><th>ESPN proj</th></tr></thead>
      <tbody>${benchRows}</tbody>
      <tfoot>
        <tr class="totals-row"><td>Bench total</td><td></td><td><strong>${fmt(benchOurTotal, 1)}</strong></td><td><strong>${fmt(benchEspnTotal, 1)}</strong></td></tr>
      </tfoot>
    </table>
  `;
}

function scheduleGrid(roster, currentWeek, finalWeek) {
  const weeks = [];
  for (let w = currentWeek; w <= finalWeek; w++) weeks.push(w);
  const sorted = roster.slice().sort((a, b) => sortByPositionOrder(a, b, (p) => p.position) || b.ros_total - a.ros_total);

  const header = `<tr><th>Player</th>${weeks.map((w) => `<th>Wk ${w}</th>`).join("")}</tr>`;
  const rows = sorted
    .map((p) => {
      const byWeek = new Map((p.weekly || []).map((w) => [w.week, w]));
      const cells = weeks.map((w) => `<td>${opponentCellHtml(byWeek.get(w))}</td>`).join("");
      return `<tr data-player-id="${p.id}" class="clickable-row"><td>${posTag(p.position)} ${escapeHtml(p.name)}</td>${cells}</tr>`;
    })
    .join("");
  return `<table>${header}<tbody>${rows}</tbody></table>`;
}

function changesTable(changes, playersById) {
  if (!changes.length) return `<p class="muted small">Your ESPN lineup already matches the optimal lineup.</p>`;
  return `<table><thead><tr><th>Start instead</th><th>Bench instead</th><th>Gain</th></tr></thead><tbody>${changes
    .map((c) => {
      const optimal = playersById.get(c.optimal);
      const espn = playersById.get(c.espn);
      return `<tr><td>${escapeHtml(optimal ? optimal.name : c.optimal)}</td><td>${escapeHtml(espn ? espn.name : c.espn)}</td><td>+${fmt(c.delta, 1)}</td></tr>`;
    })
    .join("")}</tbody></table>`;
}

function wireRowClicks(container, data) {
  container.querySelectorAll("tr[data-player-id]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("[data-opp-cell]")) return; // handled separately below
      const p = data.playersById.get(row.dataset.playerId);
      if (p) openPlayerModal(p, data);
    });
  });
  container.querySelectorAll("[data-opp-cell]").forEach((cell) => {
    cell.addEventListener("click", (e) => {
      e.stopPropagation();
      const team = cell.dataset.team;
      if (team) openPointsAgainstModal(team, cell.dataset.pos, data);
    });
  });
}

export function renderStartSit(container, data, slug) {
  const yourTeamId = getYourTeam(slug) || data.teams[0].team_id;
  const team = data.teamsById.get(Number(yourTeamId)) || data.teams[0];
  const roster = data.players.filter((p) => p.fantasy_team_id === team.team_id);
  const lineupTeam = data.lineups[String(team.team_id)] || { weeks: {}, changes_vs_espn: [] };

  const weekOptions = Object.keys(lineupTeam.weeks || {})
    .map(Number)
    .sort((a, b) => a - b);
  let selectedWeek = data.meta.current_week;

  const teamOptions = data.teams.map((t) => `<option value="${t.team_id}" ${t.team_id === team.team_id ? "selected" : ""}>${escapeHtml(teamLabel(t))}</option>`).join("");

  function draw() {
    const lineupWeek = (lineupTeam.weeks || {})[String(selectedWeek)] || { total: 0, slots: {}, bench: [] };
    container.innerHTML = `
      <div class="card">
        <div class="select-row">
          <label>Team:</label><select id="startsit-team-select">${teamOptions}</select>
          <label>Week:</label><select id="startsit-week-select">${weekOptions.map((w) => `<option value="${w}" ${w === selectedWeek ? "selected" : ""}>${w}${w === data.meta.current_week ? " (current)" : ""}</option>`).join("")}</select>
        </div>
        ${lineupSection(roster, selectedWeek, lineupWeek)}
        ${selectedWeek === data.meta.current_week
          ? `<h3>Changes vs. your ESPN lineup</h3><div class="table-wrap">${changesTable(lineupTeam.changes_vs_espn, data.playersById)}</div>`
          : ""}
        <details>
          <summary class="small">Rest-of-season opponent schedule</summary>
          <div class="table-wrap">${scheduleGrid(roster, data.meta.current_week, data.meta.final_week)}</div>
        </details>
      </div>
    `;
    container.querySelector("#startsit-team-select").addEventListener("change", (e) => {
      setYourTeam(slug, Number(e.target.value));
      renderStartSit(container, data, slug);
    });
    container.querySelector("#startsit-week-select").addEventListener("change", (e) => {
      selectedWeek = Number(e.target.value);
      draw();
    });
    wireRowClicks(container, data);
  }

  draw();
}
