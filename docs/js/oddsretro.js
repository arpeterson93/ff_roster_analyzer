// Standings > Retrospective: each team's playoff (or bye) odds after every
// completed week, as a line chart plus a ranked "after week N" table that
// follows the pointer. Each week's point is the odds the site actually
// published right after that week finished (odds_history.json, recorded by
// engine/odds_history.py: real rosters and projections as of then). Weeks that
// predate the recording fall back to FFOdds.retrospective's results-only
// re-simulation and are drawn dashed; the last point ("Now") is the current
// Standings table.

import { escapeHtml, getYourTeam } from "./state.js";
import { teamLabel } from "./colors.js";

// 12 distinguishable hues that read on both light and dark cards. Your own
// team is drawn thicker and always on top, so it never depends on its hue.
const PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#16a5a5", "#d4a017", "#f032e6", "#7a9a01", "#9a6324", "#808080", "#0b6e99"];

const cache = new Map(); // slug -> { stamp, result } so tab switches don't re-simulate
const metricBySlug = new Map();

const pct = (v) => `${(v * 100).toFixed(0)}%`;

function load(data, slug) {
  const stamp = `${data.meta.generated_at}:${data.meta.weeks_complete}`;
  const hit = cache.get(slug);
  if (hit && hit.stamp === stamp) return hit;
  const snaps = {};
  ((data.oddsHistory && data.oddsHistory.snapshots) || []).forEach((s) => { snaps[s.week] = s; });
  const skip = {};
  Object.keys(snaps).forEach((w) => { skip[w] = true; });
  const result = window.FFOdds
    ? window.FFOdds.retrospective({ meta: data.meta, standings: data.standings, schedule: data.schedule, lineups: data.lineups }, { skip })
    : null;
  const entry = { stamp, result, snaps, done: false, running: false };
  cache.set(slug, entry);
  return entry;
}

// One simulated week per timer tick so the page stays responsive.
function compute(entry, onProgress) {
  entry.cb = onProgress; // the latest render wants the updates (tab may have been re-opened mid-run)
  if (entry.done || entry.running || !entry.result) return;
  entry.running = true;
  let i = 0;
  const tick = () => {
    entry.result.step(i++);
    entry.cb(i, entry.result.total);
    if (i < entry.result.total) setTimeout(tick, 0);
    else { entry.done = true; entry.running = false; }
  };
  setTimeout(tick, 0);
}

export function renderRetro(container, data, slug) {
  const entry = load(data, slug);
  if (!entry.result) {
    container.innerHTML = `<div class="card"><p class="muted">The retrospective needs at least one completed regular-season week.</p></div>`;
    return;
  }
  const teams = data.standings.map((s) => ({ id: s.team_id, team: data.teamsById.get(s.team_id) }));
  const yourId = getYourTeam(slug);
  const colorOf = {};
  teams.forEach((t, i) => { colorOf[t.id] = PALETTE[i % PALETTE.length]; });
  const nameOf = (id) => { const t = data.teamsById.get(id); return t ? teamLabel(t) : String(id); };

  const state = { metric: metricBySlug.get(slug) || "playoff", hover: null, focus: null, hidden: new Set(), sel: null };

  container.innerHTML = `<div class="card retro">
      <h2>Playoff odds after each week <span class="muted small" id="retro-status"></span></h2>
      <div class="retro-controls">
        <div class="seg-toggle" id="retro-metric"><button data-m="playoff">Make playoffs</button><button data-m="bye">Get a bye</button></div>
      </div>
      <div id="retro-chart"></div>
      <div id="retro-legend" class="retro-legend"></div>
      <div id="retro-table"></div>
      <p class="muted small">Each solid point is the odds this site published right after that week finished, using the rosters and projections it had then. <span id="retro-approx-note"></span><strong>Now</strong> is the current Standings table.</p>
    </div>`;
  const statusEl = container.querySelector("#retro-status");
  const chartEl = container.querySelector("#retro-chart");
  const legendEl = container.querySelector("#retro-legend");
  const tableEl = container.querySelector("#retro-table");

  const R = entry.result;
  const weekList = [];
  const lastW = Math.max(R.lastWeek, ...Object.keys(entry.snaps).map(Number));
  for (let w = 0; w <= lastW; w++) weekList.push(w);
  // Real snapshot when we have one, else the results-only estimate; the final slot is "Now".
  const rowFor = (i, id) => {
    if (i >= weekList.length) return R.now[id];
    const w = weekList[i];
    const snap = entry.snaps[w];
    if (snap) { const t = snap.teams[String(id)]; return t ? t : null; }
    return R.byWeek[w] ? R.byWeek[w][id] : null;
  };
  const isApprox = (i) => i < weekList.length && !entry.snaps[weekList[i]];
  const valueAt = (id, i) => {
    const row = rowFor(i, id);
    return row && row[state.metric] != null ? row[state.metric] : 0;
  };
  const labels = () => weekList.map((w) => (w === 0 ? "Pre" : `Wk ${w}`)).concat(["Now"]);

  function drawChart() {
    const n = weekList.length + 1; // weeks + Now
    const W = 760, H = 340, L = 40, Rr = 14, T = 12, B = 30;
    const x = (i) => L + (n === 1 ? 0 : (i * (W - L - Rr)) / (n - 1));
    const y = (v) => T + (1 - v) * (H - T - B);
    let svg = `<svg viewBox="0 0 ${W} ${H}" class="retro-svg" role="img" aria-label="Playoff odds by week for each team">`;
    [0, 0.25, 0.5, 0.75, 1].forEach((g) => {
      svg += `<line x1="${L}" x2="${W - Rr}" y1="${y(g)}" y2="${y(g)}" class="retro-grid"/><text x="${L - 6}" y="${y(g) + 4}" text-anchor="end" class="retro-tick">${pct(g)}</text>`;
    });
    labels().forEach((lb, i) => { svg += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" class="retro-tick">${lb}</text>`; });
    if (state.sel !== null) svg += `<line x1="${x(state.sel)}" x2="${x(state.sel)}" y1="${T}" y2="${H - B}" class="retro-guide"/>`;
    const order = teams.map((t) => t.id).filter((id) => !state.hidden.has(id)).sort((a, b) => (a === yourId) - (b === yourId));
    order.forEach((id) => {
      const focus = state.focus;
      const dim = focus !== null && focus !== id;
      const w = id === yourId || focus === id ? 3.4 : 1.8;
      // Segments that end on an estimated week are dashed; recorded ones are solid.
      for (let i = 1; i < n; i++) {
        const dash = isApprox(i) || isApprox(i - 1);
        svg += `<line x1="${x(i - 1).toFixed(1)}" y1="${y(valueAt(id, i - 1)).toFixed(1)}" x2="${x(i).toFixed(1)}" y2="${y(valueAt(id, i)).toFixed(1)}" stroke="${colorOf[id]}" stroke-width="${w}" stroke-linecap="round"${dash ? ' stroke-dasharray="5 4"' : ""} opacity="${dim ? 0.12 : 1}" data-team="${id}" class="retro-line"/>`;
      }
      const lastI = n - 1;
      svg += `<circle cx="${x(lastI)}" cy="${y(valueAt(id, lastI))}" r="${id === yourId || focus === id ? 4.5 : 3}" fill="${colorOf[id]}" opacity="${dim ? 0.12 : 1}"/>`;
    });
    svg += `<rect x="${L}" y="${T}" width="${W - L - Rr}" height="${H - T - B}" fill="transparent" id="retro-hit"/></svg>`;
    chartEl.innerHTML = svg;
    const hit = chartEl.querySelector("#retro-hit");
    const pick = (ev) => {
      const r = hit.getBoundingClientRect();
      const frac = Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width));
      return Math.round(frac * (n - 1));
    };
    hit.addEventListener("mousemove", (ev) => { const i = pick(ev); if (i !== state.sel) { state.sel = i; drawChart(); drawTable(); } });
    hit.addEventListener("click", (ev) => { state.sel = pick(ev); drawChart(); drawTable(); });
  }

  function drawLegend() {
    legendEl.innerHTML = teams
      .map((t) => {
        const off = state.hidden.has(t.id);
        return `<button class="retro-chip${off ? " off" : ""}${t.id === yourId ? " you" : ""}" data-team="${t.id}" title="Click to hide or show; hover to highlight"><span class="sw" style="background:${colorOf[t.id]}"></span>${escapeHtml(nameOf(t.id))}</button>`;
      })
      .join("");
    legendEl.querySelectorAll(".retro-chip").forEach((b) => {
      const id = Number(b.dataset.team);
      b.addEventListener("mouseenter", () => { state.focus = id; drawChart(); });
      b.addEventListener("mouseleave", () => { state.focus = null; drawChart(); });
      b.addEventListener("click", () => { if (state.hidden.has(id)) state.hidden.delete(id); else state.hidden.add(id); drawLegend(); drawChart(); });
    });
  }

  function drawTable() {
    const n = weekList.length + 1;
    const i = state.sel === null ? n - 1 : state.sel;
    const label = labels()[i];
    const rows = teams
      .map((t) => ({ id: t.id, v: valueAt(t.id, i), prev: i > 0 ? valueAt(t.id, i - 1) : null }))
      .sort((a, b) => b.v - a.v);
    tableEl.innerHTML = `<h3 class="retro-h">${label === "Pre" ? "Before week 1" : label === "Now" ? "Now" : `After week ${weekList[i]}${isApprox(i) ? " (estimated)" : ""}`} <span class="muted small">(hover or tap the chart to change week)</span></h3>
      <div class="table-wrap"><table class="retro-table"><thead><tr><th>Team</th><th class="r">${state.metric === "bye" ? "Bye" : "Playoff"}%</th><th class="r">Change</th></tr></thead><tbody>${rows
        .map((r) => {
          const d = r.prev === null ? null : r.v - r.prev;
          const dTxt = d === null ? "–" : `${d >= 0.0005 ? "+" : d <= -0.0005 ? "−" : ""}${Math.abs(d * 100).toFixed(0)}`;
          const cls = d === null ? "" : d >= 0.005 ? "seed-up" : d <= -0.005 ? "seed-down" : "muted";
          return `<tr class="${r.id === yourId ? "your-team-row" : ""}"><td><span class="sw" style="background:${colorOf[r.id]}"></span> ${escapeHtml(nameOf(r.id))}</td><td class="r">${pct(r.v)}</td><td class="r ${cls}">${dTxt}</td></tr>`;
        })
        .join("")}</tbody></table></div>`;
  }

  const approxWeeks = weekList.filter((w) => !entry.snaps[w]);
  const noteEl = container.querySelector("#retro-approx-note");
  if (noteEl && approxWeeks.length) noteEl.textContent = `Dashed ${approxWeeks.length === 1 ? "point" : "points"} (${approxWeeks.map((w) => (w === 0 ? "pre-season" : "week " + w)).join(", ")}) predate the recording and are estimated from results alone: the real record so far, with each team's strength taken from its scoring average, pulled toward the league average. `;

  function drawAll() { drawLegend(); drawChart(); drawTable(); }

  container.querySelectorAll("#retro-metric button").forEach((b) => {
    b.classList.toggle("active", b.dataset.m === state.metric);
    b.addEventListener("click", () => {
      state.metric = b.dataset.m;
      metricBySlug.set(slug, state.metric);
      container.querySelectorAll("#retro-metric button").forEach((x) => x.classList.toggle("active", x === b));
      drawAll();
    });
  });

  if (entry.done) { drawAll(); return; }
  chartEl.innerHTML = `<p class="muted">Simulating each week…</p>`;
  compute(entry, (done, total) => {
    if (!container.isConnected) return;
    statusEl.textContent = done < total ? `(simulating ${done}/${total})` : "";
    if (done === total) drawAll();
  });
}
