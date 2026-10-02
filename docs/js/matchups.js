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

// All positions at once: one row per team, one column per position, each
// cell stacking that position's points-allowed figure over its rank - same
// visual language as strength.js's positionValueLeagueTable (total + a
// dimmer sub-line via .heat-sub). Basis (season/last-5) and Type
// (raw/opp-adjusted) both recompute the value AND its rank/color together -
// rank is never the site-wide precomputed blended-index rank, always
// recomputed HERE from whichever value is currently showing, so the numbers
// on screen and the rank/color next to them always agree with each other.
// "Opp-Adjusted" reuses the same season-long pa_factor recentResultsTable's
// own Avg Adjusted column multiplies by (raw * pa_factor) - there's no
// separate last-5-scoped factor computed server-side, so Last 5 + Opp-
// Adjusted applies that same season factor to the l5 raw number, same
// "single season-long factor, whichever average it's scaling" convention
// the rest of the tab already uses. Clicking any cell still opens the full
// points-against detail (including the real blended index) via
// openPointsAgainstModal.
function paByPositionTable(matchups, positions, basis, type, sortState) {
  const teams = new Set();
  positions.forEach((pos) => Object.keys(matchups[pos] || {}).forEach((t) => teams.add(t)));
  const fptsFor = (pos, team) => {
    const m = (matchups[pos] || {})[team];
    if (!m) return null;
    const raw = basis === "l5" ? m.l5_allowed_ppg : m.allowed_ppg;
    if (raw === null || raw === undefined) return null;
    return type === "adj" ? raw * (m.pa_factor ?? 1.0) : raw;
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

function paByWeekTable(byPositionForPos, sortState) {
  const teams = Object.keys(byPositionForPos).sort();
  const anyCurrent = teams.some((t) => Object.keys(byPositionForPos[t].current).length > 0);
  const key = anyCurrent ? "current" : "prior";
  const allWeeks = new Set();
  teams.forEach((t) => Object.keys(byPositionForPos[t][key]).forEach((w) => allWeeks.add(Number(w))));
  const weeks = [...allWeeks].sort((a, b) => a - b);

  if (!weeks.length) {
    return `<p class="muted small">No weekly results available yet.</p>`;
  }

  // Weekly cells always show raw actuals; Raw/Opp/Adjusted are all shown as
  // their own leading columns instead of a toggle - Opp Avg and the factor
  // behind Avg Adjusted are both single season-long numbers, not recomputed
  // per week.
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
    if (k === "opp_avg") return byPositionForPos[t].opp_avg_excl ?? 0;
    if (k === "avg_adj") return adjAvgByTeam[t];
    return byPositionForPos[t][key][String(k)];
  };
  const defaultOrder = teams.slice().sort((a, b) => adjAvgByTeam[b] - adjAvgByTeam[a]);
  const sortedTeams = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;

  const header = `<tr>${sortTh("Team", "team", sortState)}${sortTh("Raw Avg", "avg_raw", sortState)}${sortTh("Opp Avg", "opp_avg", sortState, ` title="What this defense's actual opponents scored on average against everyone ELSE, excluding their own game against this defense - how tough/weak the offenses it actually faced were."`)}${sortTh("Avg Adjusted", "avg_adj", sortState)}${weeks.map((w) => sortTh(`Wk ${w}`, w, sortState)).join("")}</tr>`;
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
      return `<tr data-team="${escapeHtml(t)}" class="clickable-row"><td>${escapeHtml(t)}</td><td class="cell-center">${fmt(rawAvgByTeam[t], 1)}</td><td class="cell-center">${fmt(byPositionForPos[t].opp_avg_excl ?? 0, 1)}</td><td class="cell-center"><strong>${fmt(adjAvgByTeam[t], 1)}</strong>${factorNote}</td>${cells}</tr>`;
    })
    .join("");
  return `<table>${header}<tbody>${rows}</tbody></table>`;
}

export function renderMatchups(container, data) {
  // "_expected" (see engine/pipeline.py's matchups.json build) is a sibling
  // data source, not a real position - excluded from the position list/
  // dropdown the same way lineups.json's "_unrostered_players" is excluded
  // from being treated as a real fantasy team elsewhere on the site.
  const positions = Object.keys(data.matchups).filter((p) => p !== "_expected");
  container.innerHTML = `
    <div class="card">
      <div class="select-row">
        <label>View:</label>
        <select id="matchups-view-select">
          <option value="position">PA by Position</option>
          <option value="week">PA by Week</option>
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
        <span id="matchups-type-wrap"><label>Type:</label>
          <select id="matchups-type-select">
            <option value="raw">Raw</option>
            <option value="adj">Opp-Adjusted</option>
          </select>
        </span>
        <span id="matchups-source-wrap"><label>Data:</label>
          <select id="matchups-source-select">
            <option value="actual">Actual</option>
            <option value="expected">Expected</option>
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
  const typeSelect = container.querySelector("#matchups-type-select");
  const typeWrap = container.querySelector("#matchups-type-wrap");
  const sourceSelect = container.querySelector("#matchups-source-select");
  const sourceWrap = container.querySelector("#matchups-source-wrap");

  // One sort state per view - switching views/position/basis/type resets it
  // (a "Wk 4" sort key from PA by Week means nothing on PA by Position), but
  // re-clicking within the same view keeps toggling as expected.
  let sortState = { key: null, dir: 1, lastKey: null };
  let lastView = viewSelect.value;

  const draw = () => {
    const pos = posSelect.value;
    const view = viewSelect.value;
    if (view !== lastView) {
      sortState = { key: null, dir: 1, lastKey: null };
      lastView = view;
    }
    posWrap.hidden = view !== "week";
    basisWrap.hidden = view !== "position";
    typeWrap.hidden = view !== "position";
    sourceWrap.hidden = view !== "position";

    if (view === "week") {
      help.textContent = '"Opp Avg" is what this defense\'s opponents scored on average against everyone ELSE (excluding their own game against this defense) - "Adjusted" scales Raw Avg by that same strength-of-opponent factor.';
      wrap.innerHTML = paByWeekTable((data.recentResults || { by_position: {} }).by_position[pos] || {}, sortState);
    } else {
      // "Expected" reads matchups.json's "_expected" sibling (see
      // engine/pipeline.py) - built the same way as the real table, just fed
      // ffopportunity's expected-stat rows instead of actual ones. It has no
      // K/DST entries at all (no ffopportunity model for either - see
      // engine/expected_points.py) - those columns simply render as "-" via
      // paByPositionTable's own existing missing-data handling, same as any
      // other position/team with no data this week.
      help.textContent =
        sourceSelect.value === "expected"
          ? "1 = easiest matchup, higher = tougher (expected, based on opponent-faced opportunity - no kicker/DST model exists, so those columns show no data)"
          : "1 = easiest matchup, higher = tougher";
      const source = sourceSelect.value === "expected" ? data.matchups._expected || {} : data.matchups;
      wrap.innerHTML = paByPositionTable(source, positions, basisSelect.value, typeSelect.value, sortState);
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
  typeSelect.addEventListener("change", draw);
  sourceSelect.addEventListener("change", draw);
  draw();
}
