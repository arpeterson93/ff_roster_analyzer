import { fmt, escapeHtml } from "./state.js";
import { colorForRatio, ratioForRank } from "./colors.js";
import { openPointsAgainstModal } from "./pointsagainstmodal.js";

function forwardLookingTable(matchupsForPos, basis) {
  const key = basis === "l5" ? "l5_allowed_ppg" : "allowed_ppg";
  const teams = Object.keys(matchupsForPos).sort((a, b) => matchupsForPos[b][key] - matchupsForPos[a][key]);
  const rows = teams
    .map((team, i) => {
      const m = matchupsForPos[team];
      const rank = i + 1; // 1 = best/easiest matchup, matching the site-wide convention
      const ratio = ratioForRank(rank, teams.length);
      return `<tr data-team="${escapeHtml(team)}" class="clickable-row">
        <td>${escapeHtml(team)}</td>
        <td>${rank}</td>
        <td class="heat-cell" style="background:${colorForRatio(ratio)}">${fmt(m[key], 1)}</td>
        <td>${fmt(m.index, 2)}</td>
      </tr>`;
    })
    .join("");
  return `<table><thead><tr><th>Team</th><th>Rank</th><th>${basis === "l5" ? "Last 5 wks allowed" : "Season allowed"}</th><th>Blended index</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function recentResultsTable(byPositionForPos, season, priorSeason) {
  const teams = Object.keys(byPositionForPos).sort();
  const anyCurrent = teams.some((t) => Object.keys(byPositionForPos[t].current).length > 0);
  const seasonUsed = anyCurrent ? season : priorSeason;
  const key = anyCurrent ? "current" : "prior";
  const allWeeks = new Set();
  teams.forEach((t) => Object.keys(byPositionForPos[t][key]).forEach((w) => allWeeks.add(Number(w))));
  const weeks = [...allWeeks].sort((a, b) => a - b);

  if (!weeks.length) {
    return `<p class="muted small">No weekly results available yet.</p>`;
  }

  const avgByTeam = {};
  teams.forEach((t) => {
    const vals = weeks.map((w) => byPositionForPos[t][key][String(w)] ?? 0);
    avgByTeam[t] = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
  });
  const leagueAverage = Object.values(avgByTeam).reduce((a, b) => a + b, 0) / (teams.length || 1);

  const sortedTeams = teams.slice().sort((a, b) => avgByTeam[b] - avgByTeam[a]);
  const header = `<tr><th>Team</th>${weeks.map((w) => `<th>Wk ${w}</th>`).join("")}<th>Avg</th></tr>`;
  const rows = sortedTeams
    .map((t) => {
      const cells = weeks
        .map((w) => {
          const v = byPositionForPos[t][key][String(w)];
          if (v === undefined) return `<td class="muted">-</td>`;
          const ratio = leagueAverage > 0 ? Math.max(0, Math.min(1, 0.5 + (v - leagueAverage) / leagueAverage)) : 0.5;
          return `<td class="heat-cell" style="background:${colorForRatio(ratio)}">${fmt(v, 1)}</td>`;
        })
        .join("");
      return `<tr data-team="${escapeHtml(t)}" class="clickable-row"><td>${escapeHtml(t)}</td>${cells}<td><strong>${fmt(avgByTeam[t], 1)}</strong></td></tr>`;
    })
    .join("");
  return `<p class="muted small">${seasonUsed} season, actual points allowed per week (not projected).</p><table>${header}<tbody>${rows}</tbody></table>`;
}

function gridTable(matchups, positions) {
  const teams = new Set();
  positions.forEach((pos) => Object.keys(matchups[pos] || {}).forEach((t) => teams.add(t)));

  const totalAllowed = {};
  teams.forEach((t) => {
    totalAllowed[t] = positions.reduce((acc, pos) => acc + ((matchups[pos] || {})[t]?.allowed_ppg || 0), 0);
  });
  const sortedTeams = [...teams].sort((a, b) => totalAllowed[b] - totalAllowed[a]);

  const header = `<tr><th>Team</th>${positions.map((p) => `<th>${p}</th>`).join("")}<th>Total allowed</th></tr>`;
  const rows = sortedTeams
    .map((t) => {
      const cells = positions
        .map((pos) => {
          const m = (matchups[pos] || {})[t];
          if (!m) return `<td class="muted">-</td>`;
          const ratio = Math.max(0, Math.min(1, (33 - m.rank) / 32));
          return `<td data-team="${escapeHtml(t)}" data-pos="${pos}" class="heat-cell clickable-row" style="background:${colorForRatio(ratio)}">${m.rank}</td>`;
        })
        .join("");
      return `<tr>${`<td>${escapeHtml(t)}</td>`}${cells}<td>${fmt(totalAllowed[t], 1)}</td></tr>`;
    })
    .join("");
  return `<table><thead>${header}</thead><tbody>${rows}</tbody></table>`;
}

export function renderMatchups(container, data) {
  const positions = Object.keys(data.matchups);
  container.innerHTML = `
    <div class="card">
      <div class="select-row">
        <label>View:</label>
        <select id="matchups-view-select">
          <option value="grid">All positions grid</option>
          <option value="forward">Forward-looking index (one position)</option>
          <option value="recent">Recent results by week</option>
        </select>
        <span id="matchups-pos-wrap"><label>Position:</label>
          <select id="matchups-pos-select">${positions.map((p) => `<option value="${p}">${p}</option>`).join("")}</select>
        </span>
        <span id="matchups-basis-wrap"><label>Basis:</label>
          <select id="matchups-basis-select">
            <option value="season">Season</option>
            <option value="l5">Last 5 weeks</option>
          </select>
        </span>
      </div>
      <p id="matchups-help" class="muted small"></p>
      <div class="table-wrap" id="matchups-table-wrap"></div>
    </div>
  `;
  const wrap = container.querySelector("#matchups-table-wrap");
  const help = container.querySelector("#matchups-help");
  const posSelect = container.querySelector("#matchups-pos-select");
  const posWrap = container.querySelector("#matchups-pos-wrap");
  const viewSelect = container.querySelector("#matchups-view-select");
  const basisSelect = container.querySelector("#matchups-basis-select");
  const basisWrap = container.querySelector("#matchups-basis-wrap");

  const draw = () => {
    const pos = posSelect.value;
    const view = viewSelect.value;
    posWrap.hidden = view === "grid";
    basisWrap.hidden = view !== "forward";

    if (view === "grid") {
      help.textContent = "Rank per position (1 = best matchup for that position). Sorted by total fantasy points allowed across all positions.";
      wrap.innerHTML = gridTable(data.matchups, positions);
    } else if (view === "recent") {
      help.textContent = "Actual points allowed by position, per week - the historical record behind the forward-looking index.";
      wrap.innerHTML = recentResultsTable((data.recentResults || { by_position: {} }).by_position[pos] || {}, data.recentResults?.current_season, data.recentResults?.prior_season);
    } else {
      help.textContent = 'Rank 1 = best matchup (allows the most points), higher rank = tougher. The "blended index" column is what actually drives projections; the basis toggle here only changes which raw points-allowed column is shown/sorted.';
      wrap.innerHTML = forwardLookingTable(data.matchups[pos], basisSelect.value);
    }

    wrap.querySelectorAll("tr[data-team], td[data-team]").forEach((el) => {
      el.addEventListener("click", () => {
        const team = el.dataset.team;
        const p = el.dataset.pos || pos;
        if (team) openPointsAgainstModal(team, p, data);
      });
    });
  };
  posSelect.addEventListener("change", draw);
  viewSelect.addEventListener("change", draw);
  basisSelect.addEventListener("change", draw);
  draw();
}
