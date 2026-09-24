import { fmt, escapeHtml } from "./state.js";
import { colorForRatio, ratioForRank } from "./colors.js";
import { openPointsAgainstModal } from "./pointsagainstmodal.js";

// Sort indicator + clickable <th>, same convention rankings.js's own
// sortableThHtml uses - kept as a tiny local twin rather than sharing an
// import since these tables build their header cells inline as part of a
// single big template string, not from a column-definition list.
function sortIndicator(sortState, key) {
  if (sortState.key !== key) return "";
  return sortState.dir === 1 ? " ▲" : " ▼";
}
function sortTh(label, key, sortState, extraAttrs = "") {
  return `<th data-sort-key="${key}"${extraAttrs}>${escapeHtml(label)}${sortIndicator(sortState, key)}</th>`;
}
// First click on a column sorts ascending; clicking the SAME column again
// flips direction - "team" (name) is the only column defaulting to alpha via
// localeCompare, everything else is numeric.
function toggleSort(sortState, key) {
  sortState.key = key;
  sortState.dir = sortState.dir === 1 && sortState.lastKey === key ? -1 : 1;
  sortState.lastKey = key;
}
function sortRows(rows, sortState, valueFor) {
  if (!sortState.key) return rows;
  return rows.slice().sort((a, b) => {
    const av = valueFor(a, sortState.key);
    const bv = valueFor(b, sortState.key);
    if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * sortState.dir;
    const an = av === null || av === undefined ? -Infinity : av;
    const bn = bv === null || bv === undefined ? -Infinity : bv;
    return (an - bn) * sortState.dir;
  });
}

// All positions at once (see the conversation this was built from - this
// used to require picking one position first): one row per team, one column
// per position, each cell stacking that position's points-allowed figure
// over its rank - same visual language as strength.js's positionValueLeagueTable
// (total + a dimmer sub-line via .heat-sub). No separate Raw/Adjusted toggle
// here - always the raw points-allowed number (same convention the grid view
// already uses), NOT the strength-of-schedule-adjusted one: pa_factor can
// swing wildly on a small early-season sample (confirmed live - one real
// team's adjusted TE number came back over 10x its raw one), which would
// make this quick-glance view actively misleading by default. "Blended
// index" itself is dropped from this view entirely - it doesn't change with
// Basis (see the old help text this view used to show), and clicking any
// cell still opens the full points-against detail (including the real
// blended index, and the adjusted number for anyone who wants it) via
// openPointsAgainstModal.
function forwardLookingAllPositionsTable(matchups, positions, basis, sortState) {
  const teams = new Set();
  positions.forEach((pos) => Object.keys(matchups[pos] || {}).forEach((t) => teams.add(t)));
  const fptsFor = (pos, team) => {
    const m = (matchups[pos] || {})[team];
    if (!m) return null;
    return basis === "l5" ? m.l5_allowed_ppg : m.allowed_ppg;
  };
  const rankByPos = {};
  positions.forEach((pos) => {
    const ranked = [...teams]
      .map((t) => ({ t, v: fptsFor(pos, t) }))
      .filter((x) => x.v !== null && x.v !== undefined)
      .sort((a, b) => b.v - a.v);
    rankByPos[pos] = new Map(ranked.map((x, i) => [x.t, i + 1]));
  });
  const totalFor = (t) => positions.reduce((acc, pos) => acc + (fptsFor(pos, t) || 0), 0);

  const valueFor = (t, key) => (key === "team" ? t : fptsFor(key, t));
  const defaultOrder = [...teams].sort((a, b) => totalFor(b) - totalFor(a));
  const sortedTeams = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;

  const header = `<tr>${sortTh("Team", "team", sortState)}${positions.map((p) => sortTh(p, p, sortState)).join("")}</tr>`;
  const rows = sortedTeams
    .map((t) => {
      const cells = positions
        .map((pos) => {
          const v = fptsFor(pos, t);
          const rank = rankByPos[pos].get(t);
          if (v === null || v === undefined || rank === undefined) return `<td class="muted">-</td>`;
          const ratio = ratioForRank(rank, teams.size);
          return `<td data-team="${escapeHtml(t)}" data-pos="${pos}" class="heat-cell clickable-row" style="background:${colorForRatio(ratio)}">
            <div>${fmt(v, 1)}</div>
            <div class="heat-sub">#${rank}</div>
          </td>`;
        })
        .join("");
      return `<tr><td>${escapeHtml(t)}</td>${cells}</tr>`;
    })
    .join("");
  return `<table><thead>${header}</thead><tbody>${rows}</tbody></table>`;
}

function recentResultsTable(byPositionForPos, season, priorSeason, sortState) {
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

  // Weekly cells always show raw actuals; Raw/Adjusted are both shown as
  // their own trailing columns instead of a toggle - the factor is a single
  // season-long number, not recomputed per week.
  const rawAvgByTeam = {};
  const adjAvgByTeam = {};
  teams.forEach((t) => {
    const vals = weeks.map((w) => byPositionForPos[t][key][String(w)] ?? 0);
    const rawAvg = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
    rawAvgByTeam[t] = rawAvg;
    adjAvgByTeam[t] = rawAvg * (byPositionForPos[t].pa_factor ?? 1.0);
  });
  const leagueAverage = Object.values(rawAvgByTeam).reduce((a, b) => a + b, 0) / (teams.length || 1);

  const valueFor = (t, k) => {
    if (k === "team") return t;
    if (k === "avg_raw") return rawAvgByTeam[t];
    if (k === "avg_adj") return adjAvgByTeam[t];
    return byPositionForPos[t][key][String(k)];
  };
  const defaultOrder = teams.slice().sort((a, b) => adjAvgByTeam[b] - adjAvgByTeam[a]);
  const sortedTeams = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;

  const header = `<tr>${sortTh("Team", "team", sortState)}${weeks.map((w) => sortTh(`Wk ${w}`, w, sortState)).join("")}${sortTh("Avg (raw)", "avg_raw", sortState)}${sortTh("Avg (adjusted)", "avg_adj", sortState)}</tr>`;
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
      const factorNote = ` <span class="muted small">(&times;${fmt(byPositionForPos[t].pa_factor ?? 1.0, 2)})</span>`;
      return `<tr data-team="${escapeHtml(t)}" class="clickable-row"><td>${escapeHtml(t)}</td>${cells}<td class="cell-center">${fmt(rawAvgByTeam[t], 1)}</td><td class="cell-center"><strong>${fmt(adjAvgByTeam[t], 1)}</strong>${factorNote}</td></tr>`;
    })
    .join("");
  return `<p class="muted small">${seasonUsed} season, actual points allowed per week (not projected).</p><table>${header}<tbody>${rows}</tbody></table>`;
}

function gridTable(matchups, positions, sortState) {
  const teams = new Set();
  positions.forEach((pos) => Object.keys(matchups[pos] || {}).forEach((t) => teams.add(t)));

  const totalAllowed = {};
  teams.forEach((t) => {
    totalAllowed[t] = positions.reduce((acc, pos) => acc + ((matchups[pos] || {})[t]?.allowed_ppg || 0), 0);
  });
  const valueFor = (t, key) => {
    if (key === "team") return t;
    if (key === "total") return totalAllowed[t];
    return (matchups[key] || {})[t]?.rank;
  };
  const defaultOrder = [...teams].sort((a, b) => totalAllowed[b] - totalAllowed[a]);
  const sortedTeams = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;

  const header = `<tr>${sortTh("Team", "team", sortState)}${positions.map((p) => sortTh(p, p, sortState)).join("")}${sortTh("AVG FPTS", "total", sortState)}</tr>`;
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
      return `<tr>${`<td>${escapeHtml(t)}</td>`}${cells}<td class="cell-center">${fmt(totalAllowed[t], 1)}</td></tr>`;
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
          <option value="forward">Forward-looking index (all positions)</option>
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

  // One sort state per view - switching views/position/basis resets it
  // (a "Wk 4" sort key from Recent Results means nothing on the Grid table),
  // but re-clicking within the same view keeps toggling as expected.
  let sortState = { key: null, dir: 1, lastKey: null };
  let lastView = viewSelect.value;

  const draw = () => {
    const pos = posSelect.value;
    const view = viewSelect.value;
    if (view !== lastView) {
      sortState = { key: null, dir: 1, lastKey: null };
      lastView = view;
    }
    posWrap.hidden = view !== "recent";
    basisWrap.hidden = view !== "forward";

    if (view === "grid") {
      help.textContent = "Rank per position (1 = best matchup for that position). Sorted by total fantasy points allowed across all positions - click any column header to sort by it instead.";
      wrap.innerHTML = gridTable(data.matchups, positions, sortState);
    } else if (view === "recent") {
      help.textContent = 'Actual points allowed by position, per week - the historical record behind the forward-looking index. "Adjusted" scales the season Avg by a single season-long factor for the strength of offenses that defense has faced (excluding its own game against them); weekly cells always stay raw. Click any column header to sort by it.';
      wrap.innerHTML = recentResultsTable((data.recentResults || { by_position: {} }).by_position[pos] || {}, data.recentResults?.current_season, data.recentResults?.prior_season, sortState);
    } else {
      help.textContent = "Every position at once - each cell stacks raw points allowed over rank (1 = best/easiest matchup, higher = tougher). Click a cell for that position's full detail, including its blended index and strength-of-schedule-adjusted number; click a column header to sort by it.";
      wrap.innerHTML = forwardLookingAllPositionsTable(data.matchups, positions, basisSelect.value, sortState);
    }

    wrap.querySelectorAll("tr[data-team], td[data-team]").forEach((el) => {
      el.addEventListener("click", () => {
        const team = el.dataset.team;
        const p = el.dataset.pos || pos;
        if (team) openPointsAgainstModal(team, p, data);
      });
    });
    wrap.querySelectorAll("th[data-sort-key]").forEach((th) => {
      th.addEventListener("click", () => {
        toggleSort(sortState, th.dataset.sortKey);
        draw();
      });
    });
  };
  posSelect.addEventListener("change", draw);
  viewSelect.addEventListener("change", draw);
  basisSelect.addEventListener("change", draw);
  draw();
}
