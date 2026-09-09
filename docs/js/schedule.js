import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { POSITION_COLOR, sortByPositionOrder, colorForRatio, teamLabel } from "./colors.js";

function posTag(pos) {
  return `<span class="pos-tag" style="background:${POSITION_COLOR[pos] || "#888"}">${pos}</span>`;
}

// A player's `this_week` field is always the CURRENT week's number, not the
// week being displayed here (schedule rows cover every past/future week) -
// look up that specific week's own projection/actual from p.weekly instead.
function pointsForWeek(p, week) {
  const w = (p.weekly || []).find((e) => e.week === week);
  if (!w) return null;
  return w.actual ? w.actual.points : w.projected;
}

function miniLineup(teamId, week, data) {
  const lineupTeam = data.lineups[String(teamId)];
  const lineupWeek = lineupTeam ? lineupTeam.weeks[String(week)] : null;
  if (!lineupWeek) return `<p class="muted small">No projection for this week.</p>`;

  const bySlot = Object.entries(lineupWeek.slots || {}).sort(([a], [b]) => sortByPositionOrder(a, b, (s) => s.replace(/\d+$/, "")));
  const rows = bySlot
    .map(([slot, pid]) => {
      const p = data.playersById.get(pid);
      if (!p) return "";
      return `<tr><td class="muted small">${slot.replace(/\d+$/, "")}</td><td>${posTag(p.position)} ${escapeHtml(p.name)}</td><td>${fmt(pointsForWeek(p, week), 1)}</td></tr>`;
    })
    .join("");
  const bench = (lineupWeek.bench || [])
    .map((pid) => data.playersById.get(pid))
    .filter(Boolean)
    .map((p) => `<tr class="muted small"><td></td><td>${posTag(p.position)} ${escapeHtml(p.name)}</td><td>${fmt(pointsForWeek(p, week), 1)}</td></tr>`)
    .join("");

  return `<table><tbody>${rows}${bench}</tbody></table>`;
}

function scoreOf(m, side, data) {
  if (m.played) return side === "home" ? m.home_score : m.away_score;
  const teamId = side === "home" ? m.home_team_id : m.away_team_id;
  return (data.lineups[String(teamId)]?.weeks[String(m.week)] || {}).total ?? null;
}

function matchupRow(m, data, expandedKey, yourTeamId, avg, spread) {
  const home = data.teamsById.get(m.home_team_id);
  const away = data.teamsById.get(m.away_team_id);
  const key = `${m.week}-${m.home_team_id}-${m.away_team_id}`;
  const isYours = m.home_team_id === yourTeamId || m.away_team_id === yourTeamId;

  const homeScore = scoreOf(m, "home", data);
  const awayScore = scoreOf(m, "away", data);
  const scoreCell = (v) => {
    if (v === null || v === undefined) return `<span class="muted">–</span>`;
    const ratio = spread > 0 ? Math.max(0, Math.min(1, 0.5 + (v - avg) / spread)) : 0.5;
    return `<span class="heat-cell" style="background:${colorForRatio(ratio)}; display:inline-block; width:100%;">${fmt(v, 1)}</span>`;
  };

  const expanded = expandedKey === key;
  return `
    <tr class="clickable-row schedule-row ${isYours ? "your-team-row" : ""}" data-key="${key}">
      <td class="schedule-cell">${escapeHtml(teamLabel(home) || m.home_team_id)}</td>
      <td class="schedule-cell small">${scoreCell(homeScore)}</td>
      <td class="schedule-cell small">${scoreCell(awayScore)}</td>
      <td class="schedule-cell">${escapeHtml(teamLabel(away) || m.away_team_id)}</td>
    </tr>
    ${expanded
      ? `<tr><td colspan="4">
          <div class="trade-result">
            <div class="trade-side"><h3 class="small">${escapeHtml(teamLabel(home))}</h3>${miniLineup(m.home_team_id, m.week, data)}</div>
            <div class="trade-side"><h3 class="small">${escapeHtml(teamLabel(away))}</h3>${miniLineup(m.away_team_id, m.week, data)}</div>
          </div>
        </td></tr>`
      : ""}
  `;
}

export function renderSchedule(container, data, slug) {
  const state = { expandedKey: null };
  const weeks = [...new Set((data.schedule || []).map((m) => m.week))].sort((a, b) => a - b);
  const yourTeamId = getYourTeam(slug);

  // Normalize the red/yellow/green score coloring against the spread of
  // every score shown on the page (actual + projected), not a fixed scale.
  const allScores = [];
  (data.schedule || []).forEach((m) => {
    const h = scoreOf(m, "home", data);
    const a = scoreOf(m, "away", data);
    if (h !== null) allScores.push(h);
    if (a !== null) allScores.push(a);
  });
  const avg = allScores.length ? allScores.reduce((s, v) => s + v, 0) / allScores.length : 0;
  const spread = allScores.length ? Math.max(...allScores.map((v) => Math.abs(v - avg)), 1) : 1;

  function draw() {
    // One shared table for every week (not a separate table per week) with
    // fixed column widths, so the home/score/away columns land in the same
    // horizontal position throughout - team-name length can't stagger them.
    const rows = weeks
      .map((w) => {
        const weekMatchups = data.schedule.filter((m) => m.week === w);
        const allProjected = weekMatchups.length > 0 && weekMatchups.every((m) => !m.played);
        const weekHeader = `<tr class="week-divider"><td colspan="4">Week ${w}${w === data.meta.current_week ? " (current)" : ""}${allProjected ? " - Projected" : ""}</td></tr>`;
        const matchups = weekMatchups.map((m) => matchupRow(m, data, state.expandedKey, yourTeamId, avg, spread)).join("");
        return weekHeader + matchups;
      })
      .join("");

    container.innerHTML = `
      <div class="card">
        <h2>Schedule</h2>
        <p class="muted small">Click a matchup to see each team's optimal lineup that week.</p>
        <div class="table-wrap">
          <table class="schedule-table">
            <colgroup><col style="width:32%"><col style="width:18%"><col style="width:18%"><col style="width:32%"></colgroup>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;

    container.querySelectorAll("tr.schedule-row").forEach((row) => {
      row.addEventListener("click", () => {
        state.expandedKey = state.expandedKey === row.dataset.key ? null : row.dataset.key;
        draw();
      });
    });
  }
  draw();
}
