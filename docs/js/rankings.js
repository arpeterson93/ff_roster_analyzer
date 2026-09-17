import { fmt, escapeHtml, getYourTeam } from "./state.js";
import {
  POSITION_COLOR, INJURY_BADGE, impliedTotalCellHtml, opponentCellHtml, sortByPositionOrder, teamLabel,
  pointsWeeksAgo, seasonAvgPoints, seasonTotalPoints, weatherCellHtml, rosCellHtml,
} from "./colors.js";
import { openPlayerModal } from "./playermodal.js";
import { openPointsAgainstModal } from "./pointsagainstmodal.js";
import { loadWatchlist, setWatched } from "./watchlist.js";
import { compareCheckboxHtml, wireCompareCheckboxes } from "./compare.js";
import { STATS_TAB_BLOCKS, blocksForPosition, statCellsHtml } from "./statcolumns.js";

const FLEX_POSITIONS = ["RB", "WR", "TE"];

// Overview/Schedule/Stats share one underlying player list, filter row, and
// sort-by-column-header mechanic - only the trailing column set (and, for
// Stats, the header shape) changes per tab. See render() near the bottom
// for how a tab picks its own column set/renderer off this shared base.
const TABS = [
  { key: "overview", label: "Overview" },
  { key: "schedule", label: "Schedule" },
  { key: "stats", label: "Stats" },
];

// Stats tab shows the universal skill-position blocks (Passing/Rushing/
// Receiving/Misc) for every filter EXCEPT when narrowed to exactly K or
// DST - those positions' stat vocabulary (FG/PAT, points-allowed/sacks/
// turnovers) doesn't fit that shape at all, so switching to their OWN
// Game-Log block set (blocksForPosition, same one the player modal already
// uses) only makes sense once they're the only thing in view - mixed in
// with skill positions it would just be a wall of "-" for everyone else.
function statsBlocksFor(position) {
  if (position === "K" || position === "DST") return blocksForPosition(position);
  return STATS_TAB_BLOCKS;
}

function healthBadge(status) {
  const letters = INJURY_BADGE[status];
  return letters ? `<span class="pill small" style="background:var(--red-600)">${letters}</span>` : "";
}

function ownerName(p, teamsById) {
  if (p.fantasy_team_id === null) return "FA";
  return teamLabel(teamsById.get(p.fantasy_team_id));
}

function weekEntryFor(p, week) {
  return (p.weekly || []).find((w) => w.week === week) || {};
}

function thisWeekEntry(p, currentWeek) {
  return weekEntryFor(p, currentWeek);
}

// This league's passing scoring is precise enough (e.g. 0.04/yard) that
// rounding every score to 1 decimal can hide real differences a QB's stat
// line actually earned - but always showing 2 decimals would put a
// pointless trailing zero on every score that doesn't need it. Show 2
// decimals only when the second one is actually nonzero.
function fmtScore(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  const twoDp = Number(n).toFixed(2);
  return twoDp.endsWith("0") ? Number(n).toFixed(1) : twoDp;
}

function fmtOrDash(v, d = 1) {
  return v === null || v === undefined ? "–" : fmt(v, d);
}

function fmtPct(v, d = 1) {
  return v === null || v === undefined ? "–" : `${fmt(v * 100, d)}%`;
}

// Star/Rank/Player/Team - identical leftmost columns on every tab (per the
// conversation this was built from). Kept as one shared definition so all
// three tabs render this part pixel-for-pixel the same way.
function commonColumns(data, watched) {
  const yourTeamId = getYourTeam(data.meta.slug);
  return [
    ...(yourTeamId !== null
      ? [{
          key: "_watch", label: "★", sortable: false,
          fmt: (_v, p) => {
            const isWatched = watched.has(p.id);
            return `<span class="watch-star ${isWatched ? "watched" : ""}" data-watch-toggle data-player-id="${p.id}" title="${isWatched ? "Remove from watch list" : "Add to watch list"}">${isWatched ? "★" : "☆"}</span>`;
          },
        }]
      : []),
    {
      key: "ros_overall_rank", label: "Rank",
      fmt: (v, p) => (v === null ? "–" : `${v} <span class="muted small">(${p.position}${p.ros_pos_rank ?? "–"})</span>`),
    },
    {
      key: "name", label: "Player",
      fmt: (v, p) => `${compareCheckboxHtml(p)}<span class="pos-tag" style="background:${POSITION_COLOR[p.position] || "#888"}">${p.position}</span> ${escapeHtml(v)} ${healthBadge(p.injury_status)}`,
    },
    { key: "nfl_team", label: "Team" },
  ];
}

// ---------- Overview ----------

function overviewColumns(data) {
  const yourTeamId = getYourTeam(data.meta.slug);
  const cw = data.meta.current_week;
  return [
    {
      key: "_opp", label: "Opp", sortable: false,
      fmt: (_v, p) => `<span data-opp-cell data-team="${escapeHtml(thisWeekEntry(p, cw).opponent || "")}" data-pos="${p.position}">${opponentCellHtml(thisWeekEntry(p, cw))}</span>`,
    },
    {
      // actual is only populated once nflverse has that specific player's
      // real stat line for the week - "-" before their game's been
      // played/ingested, the real number once it has.
      key: "_score", label: "Score",
      fmt: (_v, p) => fmtScore(thisWeekEntry(p, cw).actual?.points),
    },
    // Sorted by the underlying NUMBER (week_pos_rank), not the displayed
    // "RB2"-style label - a string sort would put "RB10" before "RB2".
    { key: "fp_week_pos_rank_label", label: "FP RK", fmt: (v) => v ?? "–" },
    { key: "espn_projected_week", label: "ESPN WK", fmt: (v) => fmt(v, 1) },
    { key: "_season_avg", label: "Szn Avg", fmt: (_v, p) => fmtOrDash(seasonAvgPoints(p, cw)) },
    { key: "_wk3", label: "3wk", fmt: (_v, p) => fmtOrDash(pointsWeeksAgo(p, 3, cw)) },
    { key: "_wk2", label: "2wk", fmt: (_v, p) => fmtOrDash(pointsWeeksAgo(p, 2, cw)) },
    { key: "_wk1", label: "1wk", fmt: (_v, p) => fmtOrDash(pointsWeeksAgo(p, 1, cw)) },
    {
      key: "_implied_total", label: "ITT", title: "Implied Team Total", sortable: false,
      fmt: (_v, p) => impliedTotalCellHtml(p, thisWeekEntry(p, cw)),
    },
    {
      key: "_weather", label: "Weather", sortable: false,
      fmt: (_v, p) => weatherCellHtml(thisWeekEntry(p, cw)),
    },
    { key: "percent_owned", label: "Own%", fmt: (v) => fmt(v, 1) },
    {
      key: "percent_owned_delta", label: "Own Δ",
      fmt: (v) => (v === null || v === undefined ? "–" : `${v >= 0 ? "+" : ""}${fmt(v, 1)}`),
    },
    { key: "owner", label: "Owner", fmt: (v) => escapeHtml(v) },
    {
      key: "_faab_est", label: "FAAB Est.",
      fmt: (_v, p) => {
        const est = (data.faabEstimates || {})[p.id];
        if (!est) return "–";
        // below_relevance_threshold means the model never actually ran a
        // search for this player (see engine/pipeline.py's is_relevant) -
        // its 0.0 is a hard gate, not a computed estimate.
        if (est.below_relevance_threshold) return `<span class="muted" title="Not enough recent usage to model - see FAAB Lab tab">–</span>`;
        // % of effective starting budget IF contested (comp-based MEDIAN
        // method) - not blended with P(bid) (that's the separate INT
        // column below), and not a $ amount.
        const pct = (est.conditional_price || {}).comp_based_median;
        return `${fmt(pct * 100, 1)}%`;
      },
    },
    {
      key: "_faab_interest", label: "INT", title: "P(anyone bids) - comp-based method, not multiplied into FAAB Est.",
      fmt: (_v, p) => {
        const est = (data.faabEstimates || {})[p.id];
        if (!est) return "–";
        if (est.below_relevance_threshold) return `<span class="muted" title="Not enough recent usage to model - see FAAB Lab tab">–</span>`;
        const pct = (est.bid_probability || {}).comp_based_median;
        return `${fmt(pct * 100, 0)}%`;
      },
    },
    ...(yourTeamId !== null
      ? [{
          key: "_fa_value", label: "NMD",
          fmt: (_v, p) => {
            if (p.fantasy_team_id !== null) return "–";
            const teamValues = (data.faValues || {})[String(yourTeamId)] || {};
            const gain = teamValues[p.id];
            return gain === undefined ? "–" : `${gain >= 0 ? "+" : ""}${fmt(gain, 1)}`;
          },
        }]
      : []),
  ];
}

// ---------- Schedule ----------

function scheduleColumns(data) {
  const cw = data.meta.current_week;
  const fw = data.meta.final_week;
  const weeks = [];
  for (let w = cw; w <= fw; w++) weeks.push(w);
  return [
    { key: "reg_schedule_rank", label: "Reg Rank", title: "This team's best-remaining-schedule rank at this position, regular season (1 = easiest)", fmt: (v) => v ?? "–" },
    { key: "playoff_schedule_rank", label: "Playoff Rank", title: "Same, over the fantasy playoff weeks", fmt: (v) => v ?? "–" },
    ...weeks.map((w) => ({
      key: `_wk${w}_sched`, label: `Wk ${w}`, rawTd: true,
      fmt: (_v, p) => rosCellHtml(weekEntryFor(p, w), p.position),
    })),
  ];
}

// ---------- Stats ----------

function statsForWeek(p, statsWeek, currentWeek) {
  if (statsWeek !== "season") return weekEntryFor(p, statsWeek).actual?.stats || null;
  // Season = sum of every numeric field across weeks with a real stat row
  // (byes/missed games excluded, same "week < currentWeek with an actual"
  // gate as seasonAvgPoints/seasonTotalPoints) - not position-specific, so
  // a QB's passing totals and a WR's receiving totals both fall out of the
  // exact same loop.
  const played = (p.weekly || []).filter((w) => w.week < currentWeek && w.actual?.stats);
  if (!played.length) return null;
  const sum = {};
  for (const w of played) {
    for (const [k, v] of Object.entries(w.actual.stats)) {
      if (typeof v === "number") sum[k] = (sum[k] || 0) + v;
    }
  }
  return sum;
}

function statsFpts(p, statsWeek, currentWeek) {
  if (statsWeek === "season") return seasonTotalPoints(p, currentWeek);
  return weekEntryFor(p, statsWeek).actual?.points ?? null;
}

// Snap %/Att %/Tgt % share: a true sum(numerator)/sum(denominator) across
// the weeks actually played in "season" mode, not an average of each
// week's own already-divided percentage (see engine/faab_estimate.py's
// build_snap_counts_index for why that distinction matters). getNum/
// getDenom each read one weekly entry; positionGate restricts Att % (RB
// carry share) to RB - meaningless for any other position, same convention
// engine/faab_estimate.py's recent_carry_share already established.
function usageShare(p, statsWeek, currentWeek, getNum, getDenom, positionGate) {
  if (positionGate && p.position !== positionGate) return null;
  const weeks = statsWeek === "season" ? (p.weekly || []).filter((w) => w.week < currentWeek) : [weekEntryFor(p, statsWeek)];
  let num = 0, denom = 0, any = false;
  for (const w of weeks) {
    const n = getNum(w);
    const d = getDenom(w);
    if (n === null || n === undefined || d === null || d === undefined) continue;
    num += n;
    denom += d;
    any = true;
  }
  if (!any || !denom) return null;
  return num / denom;
}

function snapPct(p, statsWeek, currentWeek) {
  return usageShare(p, statsWeek, currentWeek, (w) => w.offense_snaps, (w) => w.team_offense_snaps, null);
}
function attPct(p, statsWeek, currentWeek) {
  return usageShare(p, statsWeek, currentWeek, (w) => w.actual?.stats?.carries, (w) => w.team_rb_carries, "RB");
}
function tgtPct(p, statsWeek, currentWeek) {
  return usageShare(p, statsWeek, currentWeek, (w) => w.actual?.stats?.targets, (w) => w.team_targets, null);
}

// The trailing single-value columns after the grouped stat block (which is
// rendered directly via statCellsHtml, not through this column-def list -
// see renderStatsTable). Snap %/Att %/Tgt % are skill-position usage
// concepts with no K/DST equivalent - dropped entirely (not just blanked)
// once the position filter narrows to one of those, matching the player
// modal's own K/DST game log, which never had a usage row either.
function statsTrailingColumns(data, filters) {
  const cw = data.meta.current_week;
  const sw = filters.statsWeek;
  const fpts = { key: "_fpts", label: "FPTS", fmt: (_v, p) => fmtScore(statsFpts(p, sw, cw)) };
  if (filters.position === "K" || filters.position === "DST") return [fpts];
  return [
    { key: "_snap_pct", label: "Snap%", fmt: (_v, p) => fmtPct(snapPct(p, sw, cw)) },
    { key: "_att_pct", label: "Att%", title: "RB carry share only", fmt: (_v, p) => fmtPct(attPct(p, sw, cw)) },
    { key: "_tgt_pct", label: "Tgt%", fmt: (_v, p) => fmtPct(tgtPct(p, sw, cw)) },
    fpts,
  ];
}

// ---------- sorting ----------

let sortState = { key: "ros_overall_rank", dir: 1 };

function sortValue(p, key, data, filters) {
  const cw = data.meta.current_week;
  if (key === "owner") return ownerName(p, data.teamsById);
  if (key === "_score") {
    const pts = thisWeekEntry(p, cw).actual?.points;
    return pts === undefined || pts === null ? -Infinity : pts;
  }
  if (key === "fp_week_pos_rank_label") return p.week_pos_rank ?? Infinity;
  if (key === "_season_avg") return seasonAvgPoints(p, cw) ?? -Infinity;
  if (key === "_wk3") return pointsWeeksAgo(p, 3, cw) ?? -Infinity;
  if (key === "_wk2") return pointsWeeksAgo(p, 2, cw) ?? -Infinity;
  if (key === "_wk1") return pointsWeeksAgo(p, 1, cw) ?? -Infinity;
  if (key === "_faab_est" || key === "_faab_interest") {
    const est = (data.faabEstimates || {})[p.id];
    const field = key === "_faab_est" ? "conditional_price" : "bid_probability";
    const pct = est ? (est[field] || {}).comp_based_median : undefined;
    // "-" (no estimate at all, or below the relevance threshold) sorts as
    // a literal 0%, same as a real player estimated at 0% would - not
    // pinned to an extreme, so it interleaves naturally with genuinely low
    // real estimates.
    return pct === undefined || pct === null || Number.isNaN(pct) ? 0 : pct;
  }
  if (key === "_fa_value") {
    const yourTeamId = getYourTeam(data.meta.slug);
    if (yourTeamId === null || p.fantasy_team_id !== null) return -Infinity;
    const v = ((data.faValues || {})[String(yourTeamId)] || {})[p.id];
    return v === undefined ? -Infinity : v;
  }
  if (key === "_snap_pct") return snapPct(p, filters.statsWeek, cw) ?? -Infinity;
  if (key === "_att_pct") return attPct(p, filters.statsWeek, cw) ?? -Infinity;
  if (key === "_tgt_pct") return tgtPct(p, filters.statsWeek, cw) ?? -Infinity;
  if (key === "_fpts") return statsFpts(p, filters.statsWeek, cw) ?? -Infinity;
  // Schedule's per-week heat cells ("_wk12_sched", etc.) - same rank-type
  // convention as fp_week_pos_rank_label above (lower rank = better, so a
  // bye/no-data week sorts as Infinity, the worst possible rank, not 0).
  const schedMatch = /^_wk(\d+)_sched$/.exec(key);
  if (schedMatch) return weekEntryFor(p, Number(schedMatch[1])).rank ?? Infinity;
  // Stats tab's individual grouped-block columns (passing_yards, carries,
  // def_sacks, fg_made_20_29, etc.) - not given their own `if` branches
  // since the key set is large and position-dependent; statsForWeek's own
  // stat dict is authoritative for whatever key the active block set uses.
  // A combined column (the C/A key is a 2-element array, not a string)
  // never reaches here - see statsTrailingColumns/renderStatsTable, which
  // never marks it as sortable in the first place.
  if (typeof key === "string") {
    const statVal = statsForWeek(p, filters.statsWeek, cw)?.[key];
    if (statVal !== undefined) return statVal;
  }
  return p[key];
}

function sortableThHtml(c) {
  return `<th data-key="${c.key}" ${c.title ? `title="${escapeHtml(c.title)}"` : ""}>${c.label}${sortState.key === c.key ? (sortState.dir === 1 ? " ▲" : " ▼") : ""}</th>`;
}

// ---------- row filtering (shared by every tab) ----------

function filteredSortedRows(data, filters, watched) {
  const search = filters.search.trim().toLowerCase();
  let rows = data.players.filter((p) => {
    if (search && !p.name.toLowerCase().includes(search)) return false;
    if (filters.watchedOnly && !watched.has(p.id)) return false;
    if (filters.faOnly && p.fantasy_team_id !== null) return false;
    if (filters.team !== "ALL" && p.nfl_team !== filters.team) return false;
    if (filters.position === "ALL") return true;
    if (filters.position === "FLEX") return FLEX_POSITIONS.includes(p.position);
    return p.position === filters.position;
  });

  rows = rows.slice().sort((a, b) => {
    let av = sortValue(a, sortState.key, data, filters);
    let bv = sortValue(b, sortState.key, data, filters);
    if (av === null || av === undefined) av = sortState.dir === 1 ? Infinity : -Infinity;
    if (bv === null || bv === undefined) bv = sortState.dir === 1 ? Infinity : -Infinity;
    if (typeof av === "string") return av.localeCompare(bv) * sortState.dir;
    return (av - bv) * sortState.dir;
  });
  return rows;
}

// ---------- table rendering ----------

function wireTableInteractions(wrap, data, watched, onResort) {
  wrap.querySelectorAll("th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      sortState = { key, dir: sortState.key === key ? -sortState.dir : key === "ros_overall_rank" ? 1 : -1 };
      onResort();
    });
  });
  wrap.querySelectorAll("tr[data-player-id]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("[data-opp-cell]") || e.target.closest("[data-watch-toggle]")) return;
      const p = data.playersById.get(row.dataset.playerId);
      if (p) openPlayerModal(p, data);
    });
  });
  wrap.querySelectorAll("[data-watch-toggle]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const pid = el.dataset.playerId;
      const yourTeamId = getYourTeam(data.meta.slug);
      const nowWatched = !watched.has(pid);
      if (nowWatched) watched.add(pid);
      else watched.delete(pid);
      setWatched(data.meta.slug, yourTeamId, pid, nowWatched);
      onResort();
    });
  });
  wrap.querySelectorAll("[data-opp-cell]").forEach((cell) => {
    cell.addEventListener("click", (e) => {
      e.stopPropagation();
      const team = cell.dataset.team;
      if (team) openPointsAgainstModal(team, cell.dataset.pos, data);
    });
  });
  wireCompareCheckboxes(wrap, data);
}

// Overview/Schedule: one flat column list, one header row. A column with
// `rawTd: true` (Schedule's weekly heat cells) returns its OWN complete
// <td ...> (background color, data attributes) instead of the plain
// wrapper every other column gets - see colors.js's rosCellHtml.
function renderFlatTable(wrap, data, filters, watched, cols) {
  const rows = filteredSortedRows(data, filters, watched);
  const header = cols.map((c) => sortableThHtml(c)).join("");
  const body = rows
    .slice(0, 300)
    .map((p) => {
      const isYours = getYourTeam(data.meta.slug) !== null && p.fantasy_team_id === getYourTeam(data.meta.slug);
      const cells = cols
        .map((c) => {
          const value = c.fmt ? c.fmt(c.key === "owner" ? ownerName(p, data.teamsById) : p[c.key], p) : (p[c.key] ?? "–");
          return c.rawTd ? value : `<td>${value}</td>`;
        })
        .join("");
      return `<tr data-player-id="${p.id}" class="clickable-row ${isYours ? "your-team-row" : ""}">${cells}</tr>`;
    })
    .join("");
  wrap.innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
}

// Stats: needs a genuinely grouped 2-row header (Passing/Rushing/
// Receiving/Misc block labels over their own columns), which doesn't fit
// the single-row generic header above - built directly here instead,
// reusing the exact same sortableThHtml markup for the columns that ARE
// sortable so click-to-sort still behaves identically.
function renderStatsTable(wrap, container, data, filters, watched) {
  const cw = data.meta.current_week;
  const common = commonColumns(data, watched);
  const trailing = statsTrailingColumns(data, filters);
  const blocks = statsBlocksFor(filters.position);
  const flatColumns = blocks.flatMap(([, cols]) => cols);
  const rows = filteredSortedRows(data, filters, watched);

  const topLead = common.map(() => "<th></th>").join("");
  const topGroups = blocks.map(([group, cols]) => `<th colspan="${cols.length}">${escapeHtml(group)}</th>`).join("");
  const topTrail = trailing.map(() => "<th></th>").join("");
  // Every column sorts by its own stat key EXCEPT the combined C/A column
  // (an array key, "completions"+"attempts" - no single sensible sort
  // value), which stays a plain unclickable header.
  const bottomLabels = flatColumns
    .map(([key, label]) => (Array.isArray(key) ? `<th>${escapeHtml(label)}</th>` : sortableThHtml({ key, label })))
    .join("");
  const header = `
    <tr class="group-header-row">${topLead}${topGroups}${topTrail}</tr>
    <tr>${common.map((c) => sortableThHtml(c)).join("")}${bottomLabels}${trailing.map((c) => sortableThHtml(c)).join("")}</tr>
  `;

  const body = rows
    .slice(0, 300)
    .map((p) => {
      const isYours = getYourTeam(data.meta.slug) !== null && p.fantasy_team_id === getYourTeam(data.meta.slug);
      const commonCells = common.map((c) => `<td>${c.fmt ? c.fmt(p[c.key], p) : (p[c.key] ?? "–")}</td>`).join("");
      const statCells = statCellsHtml(statsForWeek(p, filters.statsWeek, cw), flatColumns);
      const trailingCells = trailing.map((c) => `<td>${c.fmt(undefined, p)}</td>`).join("");
      return `<tr data-player-id="${p.id}" class="clickable-row ${isYours ? "your-team-row" : ""}">${commonCells}${statCells}${trailingCells}</tr>`;
    })
    .join("");
  wrap.innerHTML = `<table><thead>${header}</thead><tbody>${body}</tbody></table>`;
  // Row 2 of the sticky header needs to know row 1's real rendered height
  // to sit below it instead of on top of it - see styles.css's
  // "group-header-row + tr th" rule.
  const groupHeaderRow = wrap.querySelector("tr.group-header-row");
  if (groupHeaderRow) container.style.setProperty("--rankings-group-header-h", `${groupHeaderRow.getBoundingClientRect().height}px`);
}

function render(container, data, filters, watched) {
  const wrap = container.querySelector("#rankings-table-wrap");
  const rerender = () => render(container, data, filters, watched);

  if (filters.tab === "overview") {
    renderFlatTable(wrap, data, filters, watched, [...commonColumns(data, watched), ...overviewColumns(data)]);
  } else if (filters.tab === "schedule") {
    renderFlatTable(wrap, data, filters, watched, [...commonColumns(data, watched), ...scheduleColumns(data)]);
  } else {
    renderStatsTable(wrap, container, data, filters, watched);
  }
  wireTableInteractions(wrap, data, watched, rerender);
}

export function renderRankings(container, data, slug) {
  const yourTeamId = getYourTeam(slug);
  const positions = ["ALL", ...[...data.meta.positions].sort((a, b) => sortByPositionOrder(a, b)), "FLEX"];
  const nflTeams = ["ALL", ...new Set(data.players.map((p) => p.nfl_team).filter(Boolean))].sort();
  const weekOptions = [];
  for (let w = 1; w <= data.meta.current_week; w++) weekOptions.push(w);

  const filters = { tab: "overview", position: "ALL", faOnly: false, team: "ALL", watchedOnly: false, search: "", statsWeek: "season" };
  let watched = new Set();

  function draw() {
    container.innerHTML = `
      <div class="card">
        <div class="modal-tabs" id="rankings-tabs">
          ${TABS.map((t) => `<button class="modal-tab-btn${filters.tab === t.key ? " active" : ""}" data-rankings-tab="${t.key}">${t.label}</button>`).join("")}
        </div>
        <div class="select-row rankings-filters" id="rankings-filters">
          <select id="rankings-pos-filter">${positions.map((p) => `<option value="${p}">${p}</option>`).join("")}</select>
          <select id="rankings-team-filter">${nflTeams.map((t) => `<option value="${t}">${t === "ALL" ? "All NFL teams" : t}</option>`).join("")}</select>
          <label><input type="checkbox" id="rankings-fa-only" /> Free agents only</label>
          ${yourTeamId !== null ? `<label><input type="checkbox" id="rankings-watched-only" /> Watch list only</label>` : ""}
          ${filters.tab === "stats" ? `<select id="rankings-stats-week"><option value="season">Season</option>${weekOptions.map((w) => `<option value="${w}">Week ${w}</option>`).join("")}</select>` : ""}
          <input type="search" id="rankings-search" placeholder="Search players..." autocomplete="off" value="${escapeHtml(filters.search)}" />
        </div>
        <div class="table-wrap" id="rankings-table-wrap"></div>
      </div>
    `;

    // Keeps the table header's own sticky offset (--rankings-filters-h, see
    // styles.css) in sync with this row's real height - it wraps onto two
    // lines on narrow screens, so a fixed guess would leave a gap or overlap.
    const filtersEl = container.querySelector("#rankings-filters");
    const setFiltersHeightVar = () => document.documentElement.style.setProperty("--rankings-filters-h", `${filtersEl.offsetHeight}px`);
    setFiltersHeightVar();
    new ResizeObserver(setFiltersHeightVar).observe(filtersEl);

    render(container, data, filters, watched);

    container.querySelectorAll("[data-rankings-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (filters.tab === btn.dataset.rankingsTab) return;
        filters.tab = btn.dataset.rankingsTab;
        sortState = { key: "ros_overall_rank", dir: 1 };
        draw();
      });
    });
    container.querySelector("#rankings-search").addEventListener("input", (e) => {
      filters.search = e.target.value;
      render(container, data, filters, watched);
    });
    container.querySelector("#rankings-pos-filter").value = filters.position;
    container.querySelector("#rankings-pos-filter").addEventListener("change", (e) => {
      filters.position = e.target.value;
      render(container, data, filters, watched);
    });
    container.querySelector("#rankings-team-filter").value = filters.team;
    container.querySelector("#rankings-team-filter").addEventListener("change", (e) => {
      filters.team = e.target.value;
      render(container, data, filters, watched);
    });
    container.querySelector("#rankings-fa-only").checked = filters.faOnly;
    container.querySelector("#rankings-fa-only").addEventListener("change", (e) => {
      filters.faOnly = e.target.checked;
      render(container, data, filters, watched);
    });
    const watchedOnlyEl = container.querySelector("#rankings-watched-only");
    if (watchedOnlyEl) {
      watchedOnlyEl.checked = filters.watchedOnly;
      watchedOnlyEl.addEventListener("change", (e) => {
        filters.watchedOnly = e.target.checked;
        render(container, data, filters, watched);
      });
    }
    const statsWeekEl = container.querySelector("#rankings-stats-week");
    if (statsWeekEl) {
      statsWeekEl.value = filters.statsWeek;
      statsWeekEl.addEventListener("change", (e) => {
        filters.statsWeek = e.target.value === "season" ? "season" : Number(e.target.value);
        render(container, data, filters, watched);
      });
    }
  }

  draw();

  if (yourTeamId !== null) {
    loadWatchlist(slug, yourTeamId).then((set) => {
      watched = set;
      render(container, data, filters, watched);
    });
  }
}
