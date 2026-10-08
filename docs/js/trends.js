import { escapeHtml, getYourTeam } from "./state.js";
import { POSITION_COLOR } from "./colors.js";
import { loadWatchlist } from "./watchlist.js";
import { metricsForPlayers } from "./metrics.js";

// Fixed categorical palette - for the ADDED PLAYERS when more than one is
// on the chart (two players can share a position, so POSITION_COLOR can't
// tell them apart), or for the METRICS themselves when there's only one
// player (see colorFor below - with a single player, player-color has
// nothing left to distinguish, so color flips to marking metrics instead).
// Cycles if more than 8 are ever on screen at once.
const PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d"];

// Line/bar style variation so two ENABLED metrics sharing the same chart
// type (two lines, or two bars) are still visually distinct even when
// color is busy encoding the player instead (multi-player mode) - assigned
// by each metric's own position among currently-enabled metrics of that
// SAME type, so the mapping is per-metric, not per (player, metric) pair:
// every player's "Snap %" line gets the same dash, whatever it is. `css` is
// the nearest plain CSS border-style, used for the metric table's own
// small sample swatch (see styleSwatchHtml) so the mapping is readable
// without decoding Chart.js's raw dash arrays.
const PATTERNS = [
  { dash: [], css: "solid" },
  { dash: [6, 4], css: "dashed" },
  { dash: [2, 2], css: "dotted" },
  { dash: [8, 4, 2, 4], css: "dashed" },
];

function posTag(pos) {
  return `<span class="pos-tag" style="background:${POSITION_COLOR[pos] || "#888"}">${pos}</span>`;
}

// FPts on by default (bar, primary axis) so the chart isn't blank the
// moment a first player's added - every other metric starts off, left to
// the user to turn on, matching the "nothing renders until you ask for it"
// flexibility this tab is for.
function defaultMetricState(metric) {
  return metric.key === "fpts" ? { enabled: true, chartType: "bar", axis: "y" } : { enabled: false, chartType: "line", axis: "y" };
}

// A metric's own pattern, by its position among currently-ENABLED metrics
// of the SAME chart type (recomputed fresh from the live enabled set every
// render, not persisted - toggling one metric off can reassign another's
// pattern, which is fine since nothing refers to a pattern by its OLD
// assignment across redraws).
function patternForMetrics(enabledMetrics, metricState) {
  const seen = { line: 0, bar: 0 };
  const byKey = {};
  enabledMetrics.forEach((m) => {
    const type = metricState[m.key].chartType;
    byKey[m.key] = PATTERNS[seen[type] % PATTERNS.length];
    seen[type] += 1;
  });
  return byKey;
}

function styleSwatchHtml(pattern, color) {
  return `<span class="trend-style-swatch" style="border-bottom-style:${pattern.css}; border-bottom-color:${color}"></span>`;
}

export function renderTrends(container, data) {
  const yourTeamId = getYourTeam(data.meta.slug);
  const state = { playerIds: [], metricState: {}, chart: null, watchSet: new Set(), targetSet: new Set(), configOpen: true };

  // Loaded once, up front - a toggle on the Rankings/player-modal star
  // while this tab is also open won't be reflected until the next load of
  // this tab, same "loaded once per render of the view" tradeoff Rankings'
  // own watch-star already accepts.
  if (yourTeamId !== null) {
    Promise.all([loadWatchlist(data.meta.slug, yourTeamId, "watch"), loadWatchlist(data.meta.slug, yourTeamId, "target")]).then(([watch, target]) => {
      state.watchSet = watch;
      state.targetSet = target;
      const panel = container.querySelector("[data-trend-results]");
      if (panel && !panel.hidden) renderPickerPanel();
    });
  }

  function currentPlayers() {
    return state.playerIds.map((id) => data.playersById.get(id)).filter(Boolean);
  }

  // Backfills default config for any metric that's newly relevant (a just-
  // added player's position unlocks it) - never overwrites a metric the
  // user already configured, so toggling players around doesn't reset
  // choices already made for metrics that stay relevant throughout.
  function ensureMetricDefaults(players) {
    metricsForPlayers(players).forEach((m) => {
      if (!state.metricState[m.key]) state.metricState[m.key] = defaultMetricState(m);
    });
  }

  function addPlayer(id) {
    if (state.playerIds.includes(id)) return;
    state.playerIds.push(id);
    ensureMetricDefaults(currentPlayers());
    draw();
  }

  function removePlayer(id) {
    state.playerIds = state.playerIds.filter((pid) => pid !== id);
    draw();
  }

  // Single player on the chart: player-color has nothing left to
  // distinguish (there's only one), so color flips to marking METRICS
  // instead - every enabled metric gets its own PALETTE color, same one
  // across the whole chart. Multiple players: color goes back to marking
  // the PLAYER (same color across all of that player's own lines/bars),
  // and metrics instead lean on the dash pattern below to stay tellable
  // apart.
  function colorFor(playerIndex, metricIndex, multi) {
    return multi ? PALETTE[playerIndex % PALETTE.length] : PALETTE[metricIndex % PALETTE.length];
  }

  function playerPillsHtml(players, multi) {
    return players
      .map((p, i) => {
        // No swatch at all in single-player mode - that lone pill's color
        // would claim to mean something on the chart when color is
        // actually encoding metrics there instead (see colorFor).
        const swatch = multi ? `<span class="trend-player-swatch" style="background:${colorFor(i, 0, true)}"></span>` : "";
        return `<span class="trend-player-pill" ${multi ? `style="border-color:${colorFor(i, 0, true)}"` : ""}>
          ${swatch}${posTag(p.position)} ${escapeHtml(p.name)}
          <button type="button" class="trend-player-remove" data-remove-player="${p.id}" aria-label="Remove ${escapeHtml(p.name)}">&times;</button>
        </span>`;
      })
      .join("");
  }

  // One row per relevant metric - checkbox, label, its Passing/Rushing/
  // Receiving/Misc/etc. category (statcolumns.js's own grouping, threaded
  // through by metrics.js), chart-type/axis selects, and a Style sample
  // that doubles as this metric's own little legend entry: the color
  // swatch only appears here (not a second time per player) when color is
  // currently metric-keyed (single-player mode); in multi-player mode the
  // player pills above already carry the color meaning, so this swatch is
  // just the dash/border sample on its own.
  function metricRowsHtml(metrics, enabledMetrics, patterns, multi) {
    return metrics
      .map((m) => {
        const ms = state.metricState[m.key] || defaultMetricState(m);
        // Only single-player mode gives this swatch a real color (color is
        // metric-keyed there) - in multi-player mode color is player-keyed
        // instead, so a metric row has no ONE color of its own to show;
        // the sample stays neutral and the Style column is purely a dash
        // reference there.
        const color = ms.enabled && !multi ? colorFor(0, enabledMetrics.indexOf(m), false) : "var(--ink-500)";
        const pattern = ms.enabled ? patterns[m.key] : PATTERNS[0];
        return `<tr>
          <td><input type="checkbox" data-metric-toggle="${m.key}" ${ms.enabled ? "checked" : ""} /></td>
          <td>${escapeHtml(m.label)}</td>
          <td class="muted small">${escapeHtml(m.group)}</td>
          <td><select data-metric-type="${m.key}">
            <option value="line" ${ms.chartType === "line" ? "selected" : ""}>Line</option>
            <option value="bar" ${ms.chartType === "bar" ? "selected" : ""}>Bar</option>
          </select></td>
          <td><select data-metric-axis="${m.key}">
            <option value="y" ${ms.axis === "y" ? "selected" : ""}>Primary</option>
            <option value="y1" ${ms.axis === "y1" ? "selected" : ""}>Secondary</option>
          </select></td>
          <td>${styleSwatchHtml(pattern, color)}</td>
        </tr>`;
      })
      .join("");
  }

  // {leftover id -> true} grows as each higher-priority section claims its
  // players, so the same player never shows twice across My Team/Watch
  // List/Targets - "dedupe" is really just "earlier section wins."
  function pickerSections() {
    const claimed = new Set(state.playerIds);
    const sections = [];
    const addSection = (label, players) => {
      const rows = players.filter((p) => !claimed.has(p.id));
      rows.forEach((p) => claimed.add(p.id));
      if (rows.length) sections.push({ label, players: rows });
    };
    if (yourTeamId !== null) {
      addSection("My Team", data.players.filter((p) => p.fantasy_team_id === yourTeamId));
      addSection("Watch List", data.players.filter((p) => state.watchSet.has(p.id)));
      addSection("Targets", data.players.filter((p) => state.targetSet.has(p.id)));
    }
    return sections;
  }

  function pickerRowHtml(p) {
    return `<div class="compare-search-result" data-pick-player="${p.id}">${posTag(p.position)} ${escapeHtml(p.name)} <span class="muted small">${escapeHtml(p.nfl_team || "")}</span></div>`;
  }

  // Default (empty search box) view: My Team/Watch List/Targets, each a
  // labeled section of clickable rows - the "prominent, no typing needed"
  // shortcut. Typing anything switches to a plain flat name-filter over
  // every player, same as before.
  function renderPickerPanel() {
    const panel = container.querySelector("[data-trend-results]");
    const input = container.querySelector("#trend-player-search");
    if (!panel || !input) return;
    const q = input.value.trim().toLowerCase();
    if (q) {
      const matches = data.players.filter((p) => !state.playerIds.includes(p.id) && p.name.toLowerCase().includes(q)).slice(0, 8);
      panel.innerHTML = matches.length ? matches.map(pickerRowHtml).join("") : `<p class="muted small">No players found.</p>`;
    } else {
      const sections = pickerSections();
      panel.innerHTML = sections.length
        ? sections.map((s) => `<div class="trend-picker-section-label muted small">${escapeHtml(s.label)}</div>${s.players.map(pickerRowHtml).join("")}`).join("")
        : `<p class="muted small">Type a name to search.</p>`;
    }
    panel.querySelectorAll("[data-pick-player]").forEach((row) => {
      row.addEventListener("click", () => {
        input.value = "";
        panel.hidden = true;
        addPlayer(row.dataset.pickPlayer);
      });
    });
  }

  // Full season (week 1 through final_week), not just the remaining
  // schedule - the whole point of a trend view is seeing played weeks'
  // real history alongside the rest-of-season projection, not just the
  // latter. A bye week or a played-but-inactive week is a real GAP, not a
  // zero and not bridged over: every metric getter already returns null
  // for exactly those weeks (see metrics.js's own fptsGetter/statGetter),
  // and spanGaps:false below means a null in the middle of a line dataset
  // genuinely breaks it - the line stops at the last real value, no line
  // at all through the gap, then starts fresh at the next real one -
  // rather than either drawing a misleading zero or silently connecting
  // straight across the gap as if nothing happened.
  function buildChart(players) {
    if (state.chart) {
      state.chart.destroy();
      state.chart = null;
    }
    const canvas = container.querySelector("#trend-chart");
    if (!canvas || !players.length) return;
    const weeks = [];
    for (let w = 1; w <= data.meta.final_week; w++) weeks.push(w);
    const metrics = metricsForPlayers(players);
    const enabledMetrics = metrics.filter((m) => state.metricState[m.key]?.enabled);
    const patterns = patternForMetrics(enabledMetrics, state.metricState);
    const multi = players.length > 1;
    const datasets = [];
    players.forEach((p, pi) => {
      enabledMetrics.forEach((m, mi) => {
        if (m.positions && !m.positions.has(p.position)) return;
        const ms = state.metricState[m.key];
        const color = colorFor(pi, mi, multi);
        const pattern = patterns[m.key];
        datasets.push({
          type: ms.chartType,
          label: `${p.name} - ${m.label}`,
          data: weeks.map((w) => m.get(p, w, data.meta.current_week)),
          yAxisID: ms.axis,
          borderColor: color,
          backgroundColor: ms.chartType === "bar" ? `${color}99` : color,
          borderDash: pattern.dash,
          spanGaps: false,
          tension: 0.2,
        });
      });
    });
    state.chart = new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels: weeks, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        // Chart.js's own per-dataset legend is off - it has no concept of
        // "the same player across several series," which is exactly the
        // one-entry-per-player de-dup this tab needs (see playerPillsHtml,
        // the real legend for color; the metric table's own Style column
        // is the legend for pattern/metric-color).
        plugins: { legend: { display: false } },
        scales: {
          x: { title: { display: true, text: "Week" } },
          y: { type: "linear", position: "left" },
          y1: { type: "linear", position: "right", grid: { drawOnChartArea: false } },
        },
      },
    });
  }

  function draw() {
    const players = currentPlayers();
    const metrics = metricsForPlayers(players);
    const enabledMetrics = metrics.filter((m) => state.metricState[m.key]?.enabled);
    const patterns = patternForMetrics(enabledMetrics, state.metricState);
    const multi = players.length > 1;

    // Player row (the added-player pills, doubling as the chart's own
    // legend - see colorFor) sits directly ABOVE the chart, not above the
    // metric-customization table - the config is something you set up
    // once and then mostly want out of the way, while the pills are the
    // one thing worth reading alongside the chart itself every time. The
    // config table is wrapped in a native <details> for exactly that: open
    // by default so it's discoverable, but collapsible down to just its
    // <summary> line once you've picked your metrics, and state.configOpen
    // (set from the element's own "toggle" event below) carries whichever
    // way you last left it across every redraw - draw() rebuilds this
    // whole card from scratch on every change, so without that it would
    // otherwise snap back open every time.
    const configHtml =
      players.length === 0
        ? ""
        : `
      <details class="trend-config" data-trend-config ${state.configOpen ? "open" : ""}>
        <summary>Customize metrics</summary>
        <div class="table-wrap">
          <table class="trend-metric-table">
            <thead><tr><th></th><th>Metric</th><th>Category</th><th>Chart</th><th>Axis</th><th>Style</th></tr></thead>
            <tbody>${metricRowsHtml(metrics, enabledMetrics, patterns, multi)}</tbody>
          </table>
        </div>
      </details>
    `;
    // Search box on its OWN row, above the pills, rather than trailing
    // after them in one shared flex row - with the two combined, adding a
    // 4th/5th pill pushed the search input further along (or down to a
    // new wrapped line) every time, so its position kept moving right
    // under the user's cursor. Fixed above the pills, it stays exactly
    // where it was regardless of how many players are already added.
    const playerRowHtml = `
      <div class="trend-player-search">
        <input type="search" id="trend-player-search" placeholder="Add a player..." autocomplete="off" />
        <div class="compare-search-results" data-trend-results hidden></div>
      </div>
      ${players.length ? `<div class="trend-player-row">${playerPillsHtml(players, multi)}</div>` : ""}
    `;

    container.innerHTML = `
      <div class="card">
        <h2>Trends</h2>
        ${configHtml}
        ${playerRowHtml}
        ${
          players.length === 0
            ? `<p class="muted small">Add a player above to get started.</p>`
            : `<div class="trend-chart-wrap"><canvas id="trend-chart"></canvas></div>`
        }
      </div>
    `;

    const configEl = container.querySelector("[data-trend-config]");
    if (configEl) configEl.addEventListener("toggle", () => { state.configOpen = configEl.open; });

    container.querySelectorAll("[data-remove-player]").forEach((btn) => {
      btn.addEventListener("click", () => removePlayer(btn.dataset.removePlayer));
    });

    const searchInput = container.querySelector("#trend-player-search");
    const panel = container.querySelector("[data-trend-results]");
    let suppressBlur = false;
    panel.addEventListener("mousedown", () => {
      suppressBlur = true;
    });
    searchInput.addEventListener("focus", () => {
      panel.hidden = false;
      renderPickerPanel();
    });
    searchInput.addEventListener("blur", () => {
      if (suppressBlur) {
        suppressBlur = false;
        return;
      }
      panel.hidden = true;
    });
    searchInput.addEventListener("input", renderPickerPanel);

    container.querySelectorAll("[data-metric-toggle]").forEach((cb) => {
      cb.addEventListener("change", () => {
        state.metricState[cb.dataset.metricToggle].enabled = cb.checked;
        draw();
      });
    });
    container.querySelectorAll("[data-metric-type]").forEach((sel) => {
      sel.addEventListener("change", () => {
        state.metricState[sel.dataset.metricType].chartType = sel.value;
        draw();
      });
    });
    container.querySelectorAll("[data-metric-axis]").forEach((sel) => {
      sel.addEventListener("change", () => {
        state.metricState[sel.dataset.metricAxis].axis = sel.value;
        draw();
      });
    });

    buildChart(players);
  }

  draw();
}
