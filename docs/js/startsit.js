import { fmt, escapeHtml, getYourTeam, setYourTeam } from "./state.js";
import { POSITION_COLOR, INJURY_BADGE, impliedTotalCellHtml, opponentCellHtml, formatKickoff, shortName, sortByPositionOrder, teamLabel, playerPhotoHtml, weeklyProjection, pointsWeeksAgo, seasonAvgPoints, weatherCellHtml, rosCellHtml } from "./colors.js";
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

// Desktop keeps a separate Opp column (marked ".desktop-col"). On mobile (see
// the ".desktop-col"/".mobile-line" media query in styles.css) that column
// collapses away and its info folds into the Player cell instead - matching
// a native app's compact list. Every OTHER column (ITT, Proj, the recent-
// points trend, Szn Avg, FP Rank, Weather) stays a real column on mobile too
// - the table just grows wider than the screen and scrolls sideways via
// .table-wrap's overflow-x, same pattern Rankings' own wide table already
// uses, rather than hiding data Opp doesn't need hiding.
function playerMetaLine(p) {
  const parts = [];
  if (p.nfl_team) parts.push(escapeHtml(p.nfl_team));
  if (p.percent_owned) parts.push(`${fmt(p.percent_owned, 0)}% Rost`);
  if (p.percent_started) parts.push(`${fmt(p.percent_started, 0)}% Start`);
  return parts.join(" · ");
}

const projValueFor = weeklyProjection;

function fmtPts(v) {
  return v === null || v === undefined ? "–" : fmt(v, 1);
}

// FP Rank and Weather are both only ever real for the CURRENT week (FP Rank:
// engine/pipeline.py's weekly_lookup is FantasyPros' current-week positional
// rankings page, there's no future-week equivalent to show; Weather: only
// ever fetched for current_week - see colors.js's weatherCellHtml) - shown
// only when viewing that week's lineup rather than as a column of dashes for
// every other week.
const headerRow = (isCurrentWeek) => `<tr>
    <th class="slot-col">Slot</th><th class="photo-col"></th><th class="player-td">Player</th><th class="desktop-col">Opp</th>
    ${isCurrentWeek ? `<th class="stat-col" title="FantasyPros weekly positional rank">FP Rank</th>` : ""}
    <th class="stat-col">Proj</th>
    <th class="stat-col" title="Fantasy points per game played this season (byes/missed games excluded)">Szn</th>
    <th class="stat-col" title="Fantasy points 3 weeks ago (byes/missed games excluded)">3wk</th>
    <th class="stat-col" title="Fantasy points 2 weeks ago (byes/missed games excluded)">2wk</th>
    <th class="stat-col" title="Fantasy points 1 week ago (byes/missed games excluded)">1wk</th>
    <th class="stat-col" title="Implied Team Total">ITT</th>
    ${isCurrentWeek ? `<th class="stat-col">Weather</th>` : ""}
  </tr>`;

function totalsRow(label, total, isCurrentWeek) {
  return `<tr class="totals-row">
    <td colspan="3" class="slot-col">${label}</td>
    <td class="desktop-col"></td>
    ${isCurrentWeek ? `<td class="stat-col"></td>` : ""}
    <td class="stat-col"><strong>${fmt(total, 1)}</strong></td>
    <td class="stat-col"></td><td class="stat-col"></td><td class="stat-col"></td><td class="stat-col"></td>
    <td class="stat-col"></td>
    ${isCurrentWeek ? `<td class="stat-col"></td>` : ""}
  </tr>`;
}

// isStreamed: this slot's occupant isn't actually on the roster - the real
// starter was on bye, so the optimal-lineup calc pulled in the best
// available free agent for that week instead (see
// engine.team_strength.optimal_lineup_for_week_with_bye_fill). Flagged with
// a visible badge + row tint so it reads as "not really yours yet",
// distinct from every other row on the page.
function playerRow(p, week, currentWeek, slotLabel, isStreamed) {
  const weekEntry = (p.weekly || []).find((w) => w.week === week) || {};
  const slotCell = slotLabel !== undefined ? `<td class="muted small slot-col">${slotLabel}</td>` : "";
  const kickoff = formatKickoff(weekEntry.kickoff);
  const oppAttrs = `data-opp-cell data-team="${escapeHtml(weekEntry.opponent || "")}" data-pos="${p.position}"`;
  const proj = projValueFor(p, week, currentWeek);
  const isCurrentWeek = week === currentWeek;
  // The recent-points trend and season average are always anchored to the
  // real currentWeek, not whatever future week `week` is looking ahead to -
  // "3 weeks ago" is a fixed real-world reference point, same as the old
  // single-week "Last" column was.
  const seasonAvg = seasonAvgPoints(p, currentWeek);
  const streamBadge = isStreamed ? `<span class="pill small stream-badge" title="Your rostered starter is on bye - this is the best free agent available that week instead">FA</span>` : "";
  return `<tr data-player-id="${p.id}" class="clickable-row ${isStreamed ? "streamed-row" : ""}">
    ${slotCell}
    <td class="photo-col">${playerPhotoHtml(p)}</td>
    <td class="player-td">
      <div>${posTag(p.position)} <strong>${escapeHtml(p.name)}</strong> ${healthBadge(p.injury_status)} ${streamBadge}</div>
      <div class="muted small row-meta">${playerMetaLine(p)}</div>
      <div class="muted small row-meta mobile-line" ${oppAttrs}>${kickoff ? escapeHtml(kickoff) + " " : ""}${opponentCellHtml(weekEntry)}</div>
    </td>
    <td class="desktop-col" ${oppAttrs}>
      ${kickoff ? `<div class="muted small row-meta">${kickoff}</div>` : ""}
      <div>${opponentCellHtml(weekEntry)}</div>
    </td>
    ${isCurrentWeek ? `<td class="stat-col">${p.fp_week_pos_rank_label ?? "–"}</td>` : ""}
    <td class="stat-col"><strong>${fmt(proj, 1)}</strong></td>
    <td class="stat-col muted">${fmtPts(seasonAvg)}</td>
    <td class="stat-col muted">${fmtPts(pointsWeeksAgo(p, 3, currentWeek))}</td>
    <td class="stat-col muted">${fmtPts(pointsWeeksAgo(p, 2, currentWeek))}</td>
    <td class="stat-col muted">${fmtPts(pointsWeeksAgo(p, 1, currentWeek))}</td>
    <td class="stat-col">${impliedTotalCellHtml(p, weekEntry)}</td>
    ${isCurrentWeek ? `<td class="stat-col">${weatherCellHtml(weekEntry)}</td>` : ""}
  </tr>`;
}

function lineupSection(roster, week, lineupWeek, currentWeek, allPlayersById) {
  const slots = lineupWeek.slots || {};
  const streamed = new Set(lineupWeek.streamed || []);
  const bySlot = Object.entries(slots).sort(([a], [b]) => sortByPositionOrder(a, b, (s) => s.replace(/\d+$/, "")));
  const rosterById = new Map(roster.map((p) => [p.id, p]));
  // A streamed slot's occupant is a free agent, not on `roster` - fall back
  // to the full player map (every fetched free agent is in there too).
  const resolve = (pid) => rosterById.get(pid) || allPlayersById.get(pid);
  const rows = bySlot
    .map(([slotLabel, pid]) => {
      const p = resolve(pid);
      return p ? playerRow(p, week, currentWeek, slotLabel.replace(/\d+$/, ""), streamed.has(pid)) : "";
    })
    .join("");
  const ourTotal = bySlot.reduce((acc, [, pid]) => {
    const p = resolve(pid);
    return acc + (p ? projValueFor(p, week, currentWeek) || 0 : 0);
  }, 0);

  // Sorted by THIS week's own displayed Proj value (not the backend's fixed
  // espn_projected_week ordering, which doesn't vary week to week and can
  // disagree with what's actually shown in the Proj column here).
  const bench = (lineupWeek.bench || [])
    .map((id) => rosterById.get(id))
    .filter(Boolean)
    .sort((a, b) => (projValueFor(b, week, currentWeek) || 0) - (projValueFor(a, week, currentWeek) || 0));
  const benchRows = bench.map((p) => playerRow(p, week, currentWeek, p.lineup_slot === "IR" ? "IR" : "Bench")).join("");
  const benchTotal = bench.reduce((acc, p) => acc + (projValueFor(p, week, currentWeek) || 0), 0);

  const isCurrentWeek = week === currentWeek;
  const thead = `<thead>${headerRow(isCurrentWeek)}</thead>`;
  return `
    <div class="table-wrap lineup-scroll">
      <table class="lineup-table">
        ${thead}
        <tbody>${rows}</tbody>
        <tfoot>${totalsRow("Starters total", ourTotal, isCurrentWeek)}</tfoot>
      </table>
    </div>
    <h3>Bench</h3>
    <div class="table-wrap lineup-scroll">
      <table class="lineup-table">
        ${thead}
        <tbody>${benchRows}</tbody>
        <tfoot>${totalsRow("Bench total", benchTotal, isCurrentWeek)}</tfoot>
      </table>
    </div>
  `;
}

// Lets the Starters and Bench tables' independent horizontal scrollbars
// (".lineup-scroll", each its own .table-wrap) track each other, so
// scrolling either one to see the stat columns scrolls both - otherwise
// comparing a starter against a bench player means re-scrolling twice.
// Self-terminating without a re-entrancy guard: setting scrollLeft to a
// value it's already at doesn't fire another native "scroll" event, so the
// mirrored update on the other element doesn't bounce back.
function syncHorizontalScroll(elements) {
  elements.forEach((el) => {
    el.addEventListener("scroll", () => {
      elements.forEach((other) => {
        if (other !== el && other.scrollLeft !== el.scrollLeft) other.scrollLeft = el.scrollLeft;
      });
    });
  });
}

// Starters and Bench are two separate <table>s (same thead markup, but
// auto-layout sizes each table's columns off only ITS OWN body content) - a
// long name/value in one but not the other left their columns drifting out
// of alignment despite scrolling in lockstep (see syncHorizontalScroll).
// Measures both tables' real rendered header-cell widths (same "read the
// DOM, then apply" approach as app.js's --sticky-top/rankings.js's
// --rankings-filters-h), takes the max per column, then locks both tables to
// those widths via table-layout:fixed - CSS 2.1's fixed-layout algorithm
// reads column widths off the FIRST row's own cells, which is exactly the
// (identical) header row every one of these tables has.
function alignLineupColumnWidths(tables) {
  if (tables.length < 2) return;
  const headerRows = tables.map((t) => t.querySelector("thead tr")).filter(Boolean);
  if (headerRows.length < 2) return;
  const colCount = headerRows[0].children.length;
  const widths = [];
  for (let i = 0; i < colCount; i++) {
    widths.push(Math.max(...headerRows.map((row) => row.children[i]?.getBoundingClientRect().width || 0)));
  }
  tables.forEach((t) => {
    t.style.tableLayout = "fixed";
    const row = t.querySelector("thead tr");
    Array.from(row.children).forEach((th, i) => {
      th.style.width = `${widths[i]}px`;
    });
  });
}

function scheduleGrid(roster, currentWeek, finalWeek) {
  const weeks = [];
  for (let w = currentWeek; w <= finalWeek; w++) weeks.push(w);
  const sorted = roster.slice().sort((a, b) => sortByPositionOrder(a, b, (p) => p.position) || b.ros_total - a.ros_total);
  const colCount = weeks.length + 1;

  const header = `<tr><th>Player</th>${weeks.map((w) => `<th>Wk ${w}</th>`).join("")}</tr>`;
  // A divider row per position group instead of a chip in the name cell -
  // this grid is dense enough that a per-row chip was one more thing
  // competing for a sliver of column width; one label above each group
  // says the same thing once instead of on every row.
  let lastPos = null;
  const rows = sorted
    .map((p) => {
      const byWeek = new Map((p.weekly || []).map((w) => [w.week, w]));
      const cells = weeks.map((w) => rosCellHtml(byWeek.get(w), p.position)).join("");
      const divider = p.position !== lastPos ? `<tr class="week-divider"><td colspan="${colCount}"><div class="ros-divider-label">${p.position}</div></td></tr>` : "";
      lastPos = p.position;
      // Mobile shows "F. Last" (see styles.css's ".full-name"/".short-name"
      // toggle, same pattern as Schedule's symmetric lineup).
      const nameHtml = `<span class="full-name">${escapeHtml(p.name)}</span><span class="short-name">${escapeHtml(shortName(p.name))}</span>`;
      return `${divider}<tr data-player-id="${p.id}" class="clickable-row"><td class="ros-name">${nameHtml}</td>${cells}</tr>`;
    })
    .join("");
  return `<table class="ros-grid"><thead>${header}</thead><tbody>${rows}</tbody></table>`;
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
      <div class="card lineup-card">
        <div class="select-row">
          <label>Team:</label><select id="startsit-team-select">${teamOptions}</select>
          <label>Week:</label><select id="startsit-week-select">${weekOptions.map((w) => `<option value="${w}" ${w === selectedWeek ? "selected" : ""}>${w}${w === data.meta.current_week ? " (cur)" : ""}</option>`).join("")}</select>
        </div>
        ${lineupSection(roster, selectedWeek, lineupWeek, data.meta.current_week, data.playersById)}
        ${selectedWeek === data.meta.current_week
          ? `<h3>Changes vs. your ESPN lineup</h3><div class="table-wrap">${changesTable(lineupTeam.changes_vs_espn, data.playersById)}</div>`
          : ""}
      </div>
      <div class="card">
        <h2>Rest-of-season opponent schedule</h2>
        <div class="table-wrap" id="ros-grid-wrap">${scheduleGrid(roster, data.meta.current_week, data.meta.final_week)}</div>
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
    syncHorizontalScroll(Array.from(container.querySelectorAll(".lineup-scroll")));
    alignLineupColumnWidths(Array.from(container.querySelectorAll(".lineup-table")));
  }

  draw();
}
