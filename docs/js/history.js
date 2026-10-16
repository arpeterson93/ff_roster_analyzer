// History tab - all-time records, win-confidence curves, power rankings,
// every-possible-schedule records, MER and position points, built from
// history.json (engine/history.py). Rank 1 is always the best (most points
// for, fewest points against). A trailing * on a schedule record marks a
// result that includes a substituted week: when the other team's schedule
// had your own team as the opponent, your score was compared against the
// rest of the league's average that week instead.

import { escapeHtml, fmt, getYourTeam } from "./state.js";

const VIEW_LIST = [
  ["alltime", "All-Time"],
  ["wc", "Win Confidence"],
  ["power", "Power Rankings"],
  ["sched", "Schedules"],
  ["mer", "MER"],
  ["pos", "Positions"],
  ["league", "League Scoring"],
];
const SCOPED = new Set(["power", "sched", "mer", "pos"]); // views that can show one season or all seasons
const PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d"];
const ALL = "all";

const pct = (x, d = 1) => (x === null || x === undefined ? "–" : (x * 100).toFixed(d) + "%");
const rec = (w, l, t) => `${w}-${l}${t ? `-${t}` : ""}`;
const sum = (xs) => xs.reduce((a, b) => a + (b || 0), 0);
const avg = (xs) => {
  const v = xs.filter((x) => x !== null && x !== undefined);
  return v.length ? sum(v) / v.length : null;
};

// Green (good) ... red (bad) background for a value within [lo, hi]; invert
// when a LOW value is the good one (ranks, points against).
function heat(v, lo, hi, invert = false) {
  if (v === null || v === undefined || hi === lo) return "";
  let t = (v - lo) / (hi - lo);
  if (invert) t = 1 - t;
  const color = t >= 0.5 ? "var(--green-600)" : "var(--red-600)";
  const strength = Math.round(Math.abs(t - 0.5) * 2 * 38);
  return `style="background:color-mix(in srgb, ${color} ${strength}%, transparent)"`;
}

function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  return { ink: cs.getPropertyValue("--ink-700").trim() || "#555", grid: cs.getPropertyValue("--border").trim() || "#ddd" };
}

export function renderHistory(container, data, slug) {
  const h = data.history;
  if (!h || !h.meta) {
    container.innerHTML = `<div class="card"><h2>History</h2><p class="muted">No league history has been imported for this league yet.</p></div>`;
    return;
  }
  const managers = h.meta.managers;
  const seasonKeys = h.meta.seasons;
  const yourTeamId = getYourTeam(slug);
  const yourTeam = yourTeamId === null ? null : data.teamsById.get(yourTeamId);
  const yourManager = yourTeam && managers.includes(yourTeam.manager) ? yourTeam.manager : null;
  if (!container._historyState || container._historyState.slug !== slug) {
    container._historyState = { slug, view: "alltime", scope: ALL, playoffs: false, focus: yourManager || managers[0], wcPoints: 100, charts: [] };
  }
  const state = container._historyState;

  function destroyCharts() {
    state.charts.forEach((c) => c.destroy());
    state.charts = [];
  }
  function chart(canvasId, config) {
    const canvas = container.querySelector(`#${canvasId}`);
    if (!canvas || !window.Chart) return;
    const { ink, grid } = themeColors();
    config.options = config.options || {};
    config.options.maintainAspectRatio = false;
    config.options.color = ink;
    const scales = config.options.scales || {};
    Object.values(scales).forEach((s) => {
      s.ticks = { color: ink, ...(s.ticks || {}) };
      s.grid = { color: grid, ...(s.grid || {}) };
      if (s.title) s.title.color = ink;
    });
    config.options.scales = scales;
    config.options.plugins = { legend: { labels: { color: ink } }, ...(config.options.plugins || {}) };
    state.charts.push(new window.Chart(canvas.getContext("2d"), config));
  }
  const chartBox = (id, height = 280) => `<div class="history-chart" style="position:relative;height:${height}px"><canvas id="${id}"></canvas></div>`;
  const mgrLabel = (m) => (m === yourManager ? `<strong>${escapeHtml(m)}</strong>` : escapeHtml(m));
  const rowClass = (m) => (m === yourManager ? ` class="your-team-row"` : "");
  const focusSelect = () =>
    `<label class="muted small">Team <select data-focus>${managers.map((m) => `<option ${m === state.focus ? "selected" : ""}>${escapeHtml(m)}</option>`).join("")}</select></label>`;

  // Seasons that actually contain a block for the current scope.
  const scopedSeasons = () => (state.scope === ALL ? seasonKeys : [state.scope]);

  // ------------------------------------------------------------- All-Time
  function viewAllTime() {
    const rows = h.all_time[state.playoffs ? "all" : "reg"];
    const body = (list) =>
      list
        .map((r) => `<tr${rowClass(r.manager)}>
          <td>${mgrLabel(r.manager)}</td><td>${r.games}</td><td>${rec(r.w, r.l, r.t)}</td><td>${pct(r.win_pct)}</td>
          <td>${pct(r.avg_wc_pf)}</td><td>${pct(r.avg_wc_pa)}</td>
          <td>${fmt(r.pf.avg)}</td><td>${fmt(r.pf.med)}</td><td>${fmt(r.pf.sd)}</td><td>${fmt(r.pf.cov, 2)}</td><td>${fmt(r.pf.min)}</td><td>${fmt(r.pf.max)}</td><td>${fmt(r.pf.lowest_win)}</td><td>${fmt(r.pf.highest_loss)}</td>
          ${r.wc_bins.map((b) => `<td ${heat(b, 0, 0.5)}>${pct(b, 0)}</td>`).join("")}
          ${(r.wc_pa_bins || [null, null, null, null]).map((b) => `<td>${b === null ? "–" : pct(b, 0)}</td>`).join("")}
          <td>${r.pa ? fmt(r.pa.avg) : "–"}</td><td>${r.pa ? fmt(r.pa.med) : "–"}</td><td>${r.pa ? fmt(r.pa.sd) : "–"}</td><td>${r.pa ? fmt(r.pa.min) : "–"}</td><td>${r.pa ? fmt(r.pa.max) : "–"}</td>
        </tr>`)
        .join("");
    const head = `<thead>
      <tr class="group-header-row"><th rowspan="2">Team</th><th rowspan="2">Games</th><th rowspan="2">Record</th><th rowspan="2">Win %</th><th colspan="2">Avg Win Conf</th><th colspan="8">Points For</th><th colspan="4">PF Win-Conf bands</th><th colspan="4">PA Win-Conf bands (opp. score)</th><th colspan="5">Points Against</th></tr>
      <tr><th>PF</th><th>PA</th><th>Avg</th><th>Med</th><th>SD</th><th>COV</th><th>Min</th><th>Max</th><th>Lowest Win</th><th>Highest Loss</th><th>0-25%</th><th>25-50%</th><th>50-75%</th><th>75-100%</th><th>0-25%</th><th>25-50%</th><th>50-75%</th><th>75-100%</th><th>Avg</th><th>Med</th><th>SD</th><th>Min</th><th>Max</th></tr></thead>`;
    const active = rows.filter((r) => r.active);
    const inactive = rows.filter((r) => !r.active);
    return `<div class="card">
      <h2>All-Time <label class="muted small"><input type="checkbox" data-playoffs ${state.playoffs ? "checked" : ""}> include playoff/consolation games</label></h2>
      <div class="table-wrap"><table>${head}<tbody>${body(active)}${inactive.length ? `<tr><td colspan="27" class="muted small">Inactive managers</td></tr>${body(inactive)}` : ""}</tbody></table></div>
    </div>
    <div class="card"><h2>Win % vs average win confidence</h2>
      <p class="muted small">Above the line = won more than their scoring "deserved" (lucky schedule); below = unlucky.</p>${chartBox("hc-alltime", 320)}</div>`;
  }
  function afterAllTime() {
    const rows = h.all_time[state.playoffs ? "all" : "reg"].filter((r) => r.games >= 10);
    chart("hc-alltime", {
      type: "scatter",
      data: { datasets: [{ label: "Managers", data: rows.map((r) => ({ x: r.avg_wc_pf * 100, y: r.win_pct * 100, m: r.manager })), backgroundColor: PALETTE[0], pointRadius: 5 }] },
      options: {
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => `${c.raw.m}: WC ${c.raw.x.toFixed(1)}%, win ${c.raw.y.toFixed(1)}%` } } },
        scales: { x: { title: { display: true, text: "Avg PF win confidence (%)" } }, y: { title: { display: true, text: "Win %" } } },
      },
    });
  }

  // ----------------------------------------------------- Win Confidence
  function wcAt(curve, x) {
    const pts = curve.points;
    if (!pts.length) return null;
    if (x <= pts[0][0]) return pts[0][1] * Math.max(0, x / (pts[0][0] || 1));
    for (let i = 1; i < pts.length; i++) {
      if (x <= pts[i][0]) {
        const [x0, y0] = pts[i - 1];
        const [x1, y1] = pts[i];
        return x1 === x0 ? y1 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
      }
    }
    return 1;
  }
  function viewWc() {
    const eras = h.meta.eras.filter((e) => h.wc_curves[e.id] && h.wc_curves[e.id].n);
    return `<div class="card">
      <h2>Win Confidence curves</h2>
      <p class="muted small">How often a score of X has beaten the rest of the league's weekly scores, using this league's own history. Scoring/lineup changes shift the whole distribution, so each scoring era has its own curve.</p>
      <label class="muted small">If my team scores <input type="number" data-wc-points value="${state.wcPoints}" step="1" style="width:5em"> points:</label>
      <ul class="wc-readout">${eras.map((e) => `<li>${escapeHtml(h.wc_curves[e.id].label)}: <strong>${pct(wcAt(h.wc_curves[e.id], state.wcPoints))}</strong> win confidence <span class="muted small">(${h.wc_curves[e.id].n} games)</span></li>`).join("")}</ul>
      ${chartBox("hc-wc", 340)}
    </div>`;
  }
  function afterWc() {
    const eras = h.meta.eras.filter((e) => h.wc_curves[e.id] && h.wc_curves[e.id].n);
    chart("hc-wc", {
      type: "line",
      data: {
        datasets: eras.map((e, i) => ({
          label: h.wc_curves[e.id].label, data: h.wc_curves[e.id].points.map(([x, y]) => ({ x, y: y * 100 })),
          borderColor: PALETTE[i], backgroundColor: PALETTE[i], pointRadius: 0, tension: 0.2,
        })),
      },
      options: {
        parsing: false, interaction: { mode: "nearest", intersect: false },
        scales: { x: { type: "linear", title: { display: true, text: "Points scored" } }, y: { min: 0, max: 100, title: { display: true, text: "Win confidence (%)" } } },
      },
    });
  }

  // -------------------------------------------------------- Power Rankings
  function viewPower() {
    const seasons = scopedSeasons().filter((s) => h.seasons[s].power.weeks.length);
    if (state.scope !== ALL) {
      const p = h.seasons[state.scope].power;
      const teams = Object.entries(p.teams).sort((a, b) => a[1].avg_pf_rank - b[1].avg_pf_rank);
      const maxRank = teams.length;
      const rankCells = (arr) => arr.map((r) => `<td ${heat(r, 1, maxRank, true)}>${r ?? "–"}</td>`).join("");
      return `<div class="card"><h2>Power rankings – ${state.scope}</h2>
        <p class="muted small">Rank within the league each week by points scored (PF) and points allowed (PA); 1 = best (most PF, fewest PA). Exp. wins = weekly share of the league you outscored.</p>
        <div class="table-wrap"><table><thead><tr><th rowspan="2">Team</th><th colspan="${p.weeks.length}">PF rank by week</th><th rowspan="2">Avg PF rk</th><th rowspan="2">Avg PA rk</th><th rowspan="2">Exp. wins</th></tr>
        <tr>${p.weeks.map((w) => `<th>${w}</th>`).join("")}</tr></thead><tbody>
        ${teams.map(([m, t]) => `<tr${rowClass(m)}><td>${mgrLabel(m)}</td>${rankCells(t.pf_rank)}<td>${fmt(t.avg_pf_rank, 1)}</td><td>${fmt(t.avg_pa_rank, 1)}</td><td>${fmt(t.exp_wins, 1)}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Team</th><th colspan="${p.weeks.length}">PA rank by week</th></tr><tr><th></th>${p.weeks.map((w) => `<th>${w}</th>`).join("")}</tr></thead><tbody>
        ${teams.map(([m, t]) => `<tr${rowClass(m)}><td>${mgrLabel(m)}</td>${rankCells(t.pa_rank)}</tr>`).join("")}
        </tbody></table></div></div>
        <div class="card"><h2>Weekly rank – ${escapeHtml(state.focus)} ${focusSelect()}</h2>${chartBox("hc-power", 280)}</div>`;
    }
    // All seasons: one row per manager, one column per season (avg PF rank), plus totals.
    const agg = managers.map((m) => {
      const per = seasons.map((s) => h.seasons[s].power.teams[m] || null);
      const have = per.filter(Boolean);
      return { m, per, seasons: have.length, pfRk: avg(have.map((t) => t.avg_pf_rank)), paRk: avg(have.map((t) => t.avg_pa_rank)), exp: sum(have.map((t) => t.exp_wins)) };
    }).filter((r) => r.seasons).sort((a, b) => a.pfRk - b.pfRk);
    return `<div class="card"><h2>Power rankings – all seasons</h2>
      <p class="muted small">Average weekly PF rank per season (1 = best).</p>
      <div class="table-wrap"><table><thead><tr><th>Team</th>${seasons.map((s) => `<th>${s}</th>`).join("")}<th>Avg PF rk</th><th>Avg PA rk</th><th>Exp. wins</th><th>Seasons</th></tr></thead><tbody>
      ${agg.map((r) => `<tr${rowClass(r.m)}><td>${mgrLabel(r.m)}</td>${r.per.map((t) => `<td ${t ? heat(t.avg_pf_rank, 1, 12, true) : ""}>${t ? fmt(t.avg_pf_rank, 1) : "–"}</td>`).join("")}<td>${fmt(r.pfRk, 2)}</td><td>${fmt(r.paRk, 2)}</td><td>${fmt(r.exp, 1)}</td><td>${r.seasons}</td></tr>`).join("")}
      </tbody></table></div></div>
      <div class="card"><h2>Avg PF rank by season – ${escapeHtml(state.focus)} ${focusSelect()}</h2>${chartBox("hc-power", 280)}</div>`;
  }
  function afterPower() {
    if (state.scope !== ALL) {
      const t = h.seasons[state.scope].power.teams[state.focus];
      if (!t) return;
      const weeks = h.seasons[state.scope].power.weeks;
      chart("hc-power", {
        type: "line",
        data: { labels: weeks, datasets: [
          { label: "PF rank", data: t.pf_rank, borderColor: PALETTE[0], backgroundColor: PALETTE[0], tension: 0.2 },
          { label: "PA rank", data: t.pa_rank, borderColor: PALETTE[1], backgroundColor: PALETTE[1], tension: 0.2 },
        ] },
        options: { scales: { y: { reverse: true, min: 1, title: { display: true, text: "Rank (1 = best)" } }, x: { title: { display: true, text: "Week" } } } },
      });
      return;
    }
    const seasons = seasonKeys.filter((s) => h.seasons[s].power.teams[state.focus]);
    chart("hc-power", {
      type: "line",
      data: { labels: seasons, datasets: [
        { label: "Avg PF rank", data: seasons.map((s) => h.seasons[s].power.teams[state.focus].avg_pf_rank), borderColor: PALETTE[0], backgroundColor: PALETTE[0] },
        { label: "Avg PA rank", data: seasons.map((s) => h.seasons[s].power.teams[state.focus].avg_pa_rank), borderColor: PALETTE[1], backgroundColor: PALETTE[1] },
      ] },
      options: { scales: { y: { reverse: true, title: { display: true, text: "Avg rank (1 = best)" } } } },
    });
  }

  // ------------------------------------------------------------ Schedules
  function schedAggregate() {
    // {team: {actual, avg, luck, cells: {sched: {w,l,t,star}}, best, worst}} across the scoped seasons
    const out = {};
    scopedSeasons().forEach((s) => {
      const sc = h.seasons[s].schedules;
      if (!sc) return;
      Object.entries(sc).forEach(([t, v]) => {
        const o = (out[t] = out[t] || { actual: 0, avg: 0, luck: 0, best: 0, worst: 0, seasons: 0, cells: {} });
        o.actual += v.actual; o.avg += v.avg; o.luck += v.luck; o.best += v.best; o.worst += v.worst; o.seasons += 1;
        Object.entries(v.by_schedule).forEach(([x, c]) => {
          const cell = (o.cells[x] = o.cells[x] || { w: 0, l: 0, t: 0, star: false });
          cell.w += c.w; cell.l += c.l; cell.t += c.t; cell.star = cell.star || c.subbed.length > 0;
        });
      });
    });
    return out;
  }
  function viewSched() {
    const agg = schedAggregate();
    const teams = Object.keys(agg).sort((a, b) => agg[b].luck - agg[a].luck);
    if (!teams.length) return `<div class="card"><h2>Schedules</h2><p class="muted">No opponent data for this scope.</p></div>`;
    const cols = teams;
    const cellHtml = (t, x) => {
      const c = agg[t].cells[x];
      if (!c) return "<td>–</td>";
      const games = c.w + c.l + c.t;
      return `<td ${x === t ? `class="block-end"` : ""} ${heat((c.w + c.t / 2) / games, 0.2, 0.8)}>${rec(c.w, c.l, c.t)}${c.star && x !== t ? "*" : ""}</td>`;
    };
    return `<div class="card"><h2>Record under every other team's schedule – ${state.scope === ALL ? "all seasons" : state.scope}</h2>
      <p class="muted small">Rows: whose scores. Columns: whose schedule they'd have played. * = includes a week where that schedule's opponent was the row team itself, so the rest of the league's average score (excluding the row team) was used. The column with the row team's own name is its actual record. Luck = actual wins − average wins across the other schedules.</p>
      <div class="table-wrap"><table><thead><tr><th>Team</th><th>Actual</th><th>Best</th><th>Worst</th><th>Avg</th><th>Luck</th>${cols.map((c) => `<th title="${escapeHtml(c)}'s schedule">${escapeHtml(c.split(" ")[0])}</th>`).join("")}</tr></thead><tbody>
      ${teams.map((t) => `<tr${rowClass(t)}><td>${mgrLabel(t)}</td><td>${fmt(agg[t].actual, 1)}</td><td>${fmt(agg[t].best, 1)}</td><td>${fmt(agg[t].worst, 1)}</td><td>${fmt(agg[t].avg, 1)}</td><td ${heat(agg[t].luck / agg[t].seasons, -2, 2)}>${agg[t].luck > 0 ? "+" : ""}${fmt(agg[t].luck, 1)}</td>${cols.map((x) => cellHtml(t, x)).join("")}</tr>`).join("")}
      </tbody></table></div></div>
      <div class="card"><h2>Schedule luck (wins above/below average schedule)</h2>${chartBox("hc-sched", 300)}</div>`;
  }
  function afterSched() {
    const agg = schedAggregate();
    const teams = Object.keys(agg).sort((a, b) => agg[b].luck - agg[a].luck);
    chart("hc-sched", {
      type: "bar",
      data: { labels: teams, datasets: [{ label: "Luck (wins)", data: teams.map((t) => Number(agg[t].luck.toFixed(2))), backgroundColor: teams.map((t) => (agg[t].luck >= 0 ? "#2f9e5c" : "#c0392b")) }] },
      options: { plugins: { legend: { display: false } } },
    });
  }

  // ------------------------------------------------------------------ MER
  function merAggregate() {
    const out = {};
    scopedSeasons().forEach((s) => {
      const e = h.seasons[s].effic;
      if (!e) return;
      Object.entries(e.mer).forEach(([m, v]) => {
        const o = (out[m] = out[m] || { actual: 0, optimal: 0, seasons: [] });
        o.actual += v.total_actual; o.optimal += v.total_optimal; o.seasons.push([s, v.mer]);
      });
    });
    return out;
  }
  function viewMer() {
    const agg = merAggregate();
    const teams = Object.keys(agg).sort((a, b) => agg[b].actual / agg[b].optimal - agg[a].actual / agg[a].optimal);
    if (!teams.length) return `<div class="card"><h2>MER</h2><p class="muted">No lineup data for this scope (per-player history needs ESPN box scores, 2019 onward).</p></div>`;
    return `<div class="card"><h2>Manager Efficiency Rating – ${state.scope === ALL ? "all seasons" : state.scope}</h2>
      <p class="muted small">Actual starting-lineup points ÷ the best lineup possible from that week's own roster under the league's lineup structure. 100% = perfect lineups.</p>
      <div class="table-wrap"><table><thead><tr><th>Team</th><th>MER</th><th>Actual pts</th><th>Optimal pts</th><th>Left on bench</th></tr></thead><tbody>
      ${teams.map((t) => { const a = agg[t]; return `<tr${rowClass(t)}><td>${mgrLabel(t)}</td><td ${heat(a.actual / a.optimal, 0.8, 1)}>${pct(a.actual / a.optimal)}</td><td>${fmt(a.actual, 1)}</td><td>${fmt(a.optimal, 1)}</td><td>${fmt(a.optimal - a.actual, 1)}</td></tr>`; }).join("")}
      </tbody></table></div></div>
      <div class="card"><h2>${state.scope === ALL ? "MER by season" : "MER by week"} – ${escapeHtml(state.focus)} ${focusSelect()}</h2>${chartBox("hc-mer", 280)}</div>`;
  }
  function afterMer() {
    let labels, points;
    if (state.scope === ALL) {
      const seasons = merAggregate()[state.focus]?.seasons || [];
      labels = seasons.map((s) => s[0]); points = seasons.map((s) => s[1] * 100);
    } else {
      const e = h.seasons[state.scope].effic;
      if (!e || !e.mer[state.focus]) return;
      labels = e.weeks.slice(0, e.mer[state.focus].weeks.length); points = e.mer[state.focus].weeks.map((x) => x * 100);
    }
    chart("hc-mer", {
      type: "line",
      data: { labels, datasets: [{ label: "MER %", data: points, borderColor: PALETTE[0], backgroundColor: PALETTE[0], tension: 0.2 }] },
      options: { plugins: { legend: { display: false } }, scales: { y: { max: 100, title: { display: true, text: "MER (%)" } } } },
    });
  }

  // ------------------------------------------------------------ Positions
  function posAggregate() {
    const out = {}; // manager -> pos -> {for, against, weeks}
    const positions = new Set();
    scopedSeasons().forEach((s) => {
      const e = h.seasons[s].effic;
      if (!e) return;
      e.positions.forEach((p) => positions.add(p));
      Object.entries(e.pos).forEach(([m, byPos]) => {
        Object.entries(byPos).forEach(([p, v]) => {
          const o = ((out[m] = out[m] || {})[p] = out[m][p] || { for: 0, against: 0, weeks: 0 });
          o.for += sum(v.for); o.against += sum(v.against); o.weeks += v.for.filter((x) => x !== null).length;
        });
      });
    });
    return { out, positions: ["QB", "RB", "WR", "TE", "K", "DST"].filter((p) => positions.has(p)) };
  }
  function viewPos() {
    const { out, positions } = posAggregate();
    const teams = Object.keys(out);
    if (!teams.length) return `<div class="card"><h2>Positions</h2><p class="muted">No lineup data for this scope (per-player history needs ESPN box scores, 2019 onward).</p></div>`;
    const per = (m, p, k) => (out[m][p] && out[m][p].weeks ? out[m][p][k] / out[m][p].weeks : null);
    const range = (p, k) => { const v = teams.map((m) => per(m, p, k)).filter((x) => x !== null); return [Math.min(...v), Math.max(...v)]; };
    const table = (k, title, invert) => `<div class="card"><h2>${title} – ${state.scope === ALL ? "all seasons" : state.scope} <span class="muted small">(per week, starters only)</span></h2>
      <div class="table-wrap"><table><thead><tr><th>Team</th>${positions.map((p) => `<th>${p}</th>`).join("")}<th>Total</th></tr></thead><tbody>
      ${teams.map((m) => `<tr${rowClass(m)}><td>${mgrLabel(m)}</td>${positions.map((p) => { const v = per(m, p, k); const [lo, hi] = range(p, k); return `<td ${heat(v, lo, hi, invert)}>${fmt(v, 1)}</td>`; }).join("")}<td>${fmt(sum(positions.map((p) => per(m, p, k) || 0)), 1)}</td></tr>`).join("")}
      </tbody></table></div></div>`;
    return `${table("for", "Points for by position", false)}${table("against", "Points against by position", true)}
      <div class="card"><h2>${escapeHtml(state.focus)} vs league average ${focusSelect()}</h2>${chartBox("hc-pos", 280)}</div>`;
  }
  function afterPos() {
    const { out, positions } = posAggregate();
    const teams = Object.keys(out);
    if (!out[state.focus]) return;
    const per = (m, p, k) => (out[m][p] && out[m][p].weeks ? out[m][p][k] / out[m][p].weeks : 0);
    const lg = (p, k) => avg(teams.map((m) => per(m, p, k)));
    chart("hc-pos", {
      type: "bar",
      data: { labels: positions, datasets: [
        { label: `${state.focus} PF`, data: positions.map((p) => per(state.focus, p, "for")), backgroundColor: PALETTE[0] },
        { label: "League avg PF", data: positions.map((p) => lg(p, "for")), backgroundColor: "#9aa7bd" },
        { label: `${state.focus} PA`, data: positions.map((p) => per(state.focus, p, "against")), backgroundColor: PALETTE[1] },
        { label: "League avg PA", data: positions.map((p) => lg(p, "against")), backgroundColor: "#c7a3a0" },
      ] },
      options: { scales: { y: { title: { display: true, text: "Points per week" } } } },
    });
  }

  // --------------------------------------------------------- League scoring
  function viewLeague() {
    return `<div class="card"><h2>League scoring over time</h2>${chartBox("hc-league-trend", 260)}</div>
      <div class="card"><h2>Distribution of weekly team scores by era</h2><p class="muted small">The curve of historical points: how many team-weeks landed in each 10-point band.</p>${chartBox("hc-league-hist", 300)}</div>`;
  }
  function afterLeague() {
    const ss = h.league.seasons;
    chart("hc-league-trend", {
      type: "line",
      data: { labels: ss.map((s) => s.season), datasets: [
        { label: "Mean", data: ss.map((s) => s.mean), borderColor: PALETTE[0], backgroundColor: PALETTE[0] },
        { label: "Median", data: ss.map((s) => s.median), borderColor: PALETTE[2], backgroundColor: PALETTE[2], borderDash: [6, 4] },
        { label: "Max", data: ss.map((s) => s.max), borderColor: PALETTE[3], backgroundColor: PALETTE[3], borderDash: [2, 2] },
        { label: "Min", data: ss.map((s) => s.min), borderColor: PALETTE[1], backgroundColor: PALETTE[1], borderDash: [2, 2] },
      ] },
      options: { scales: { y: { title: { display: true, text: "Points per team-week" } } } },
    });
    const eras = h.meta.eras.filter((e) => h.league.hist[e.id]);
    const maxBins = Math.max(...eras.map((e) => h.league.hist[e.id].counts.length));
    chart("hc-league-hist", {
      type: "bar",
      data: { labels: Array.from({ length: maxBins }, (_, i) => `${i * 10}-${i * 10 + 9}`), datasets: eras.map((e, i) => {
        const hist = h.league.hist[e.id];
        return { label: e.label || e.id, data: Array.from({ length: maxBins }, (_, b) => ((hist.counts[b] || 0) / hist.n) * 100), backgroundColor: PALETTE[i] + "99" };
      }) },
      options: { scales: { y: { title: { display: true, text: "% of team-weeks" } } } },
    });
  }

  const VIEW_FNS = {
    alltime: [viewAllTime, afterAllTime], wc: [viewWc, afterWc], power: [viewPower, afterPower],
    sched: [viewSched, afterSched], mer: [viewMer, afterMer], pos: [viewPos, afterPos], league: [viewLeague, afterLeague],
  };

  function render() {
    destroyCharts();
    const [view, after] = VIEW_FNS[state.view];
    const scopeSel = SCOPED.has(state.view)
      ? `<label class="muted small">Season <select data-scope><option value="${ALL}" ${state.scope === ALL ? "selected" : ""}>All seasons</option>${seasonKeys.slice().reverse().map((s) => `<option ${s === state.scope ? "selected" : ""}>${s}</option>`).join("")}</select></label>`
      : "";
    container.innerHTML = `<div class="history-toolbar" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px">
      ${VIEW_LIST.map(([k, label]) => `<button type="button" class="${k === state.view ? "active" : ""}" data-hview="${k}">${label}</button>`).join("")}
      <span style="flex:1"></span>${scopeSel}</div>${view()}`;
    container.querySelectorAll("[data-hview]").forEach((b) => b.addEventListener("click", () => { state.view = b.dataset.hview; render(); }));
    const scope = container.querySelector("[data-scope]");
    if (scope) scope.addEventListener("change", () => { state.scope = scope.value; render(); });
    container.querySelectorAll("[data-focus]").forEach((s) => s.addEventListener("change", () => { state.focus = s.value; render(); }));
    const po = container.querySelector("[data-playoffs]");
    if (po) po.addEventListener("change", () => { state.playoffs = po.checked; render(); });
    const wcp = container.querySelector("[data-wc-points]");
    if (wcp) wcp.addEventListener("change", () => { state.wcPoints = Number(wcp.value) || 0; render(); });
    after();
  }
  render();
}
