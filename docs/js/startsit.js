import { fmt, escapeHtml, getYourTeam, setYourTeam } from "./state.js";
import { POSITION_COLOR, INJURY_BADGE, opponentCellHtml, ratioForRank, colorForRatio, formatKickoff, sortByPositionOrder, teamLabel, playerPhotoHtml } from "./colors.js";
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
// a native app's compact list.
function playerMetaLine(p) {
  const parts = [];
  if (p.nfl_team) parts.push(escapeHtml(p.nfl_team));
  if (p.percent_owned) parts.push(`${fmt(p.percent_owned, 0)}% Rost`);
  if (p.percent_started) parts.push(`${fmt(p.percent_started, 0)}% Start`);
  return parts.join(" · ");
}

// Current week already gets its "our" projection straight from ESPN (see
// rankings.js), so there's no separate proprietary number to show alongside
// it there - only future weeks have our own week-by-week projection
// (p.weekly[].projected, the same field schedule.js reads).
function projValueFor(p, week, currentWeek) {
  if (week === currentWeek) return p.espn_projected_week;
  const weekEntry = (p.weekly || []).find((w) => w.week === week);
  return weekEntry ? weekEntry.projected : null;
}

// isStreamed: this slot's occupant isn't actually on the roster - the real
// starter was on bye, so the optimal-lineup calc pulled in the best
// available free agent for that week instead (see
// engine.team_strength.optimal_lineup_for_week_with_bye_fill). Flagged with
// a visible badge + row tint so it reads as "not really yours yet",
// distinct from every other row on the page.
function playerRow(p, week, currentWeek, slotLabel, isStreamed) {
  const weekEntry = (p.weekly || []).find((w) => w.week === week) || {};
  const slotCell = slotLabel !== undefined ? `<td class="muted small">${slotLabel}</td>` : "";
  const kickoff = formatKickoff(weekEntry.kickoff);
  const oppAttrs = `data-opp-cell data-team="${escapeHtml(weekEntry.opponent || "")}" data-pos="${p.position}"`;
  const proj = projValueFor(p, week, currentWeek);
  const streamBadge = isStreamed ? `<span class="pill small stream-badge" title="Your rostered starter is on bye - this is the best free agent available that week instead">FA</span>` : "";
  return `<tr data-player-id="${p.id}" class="clickable-row ${isStreamed ? "streamed-row" : ""}">
    ${slotCell}
    <td>
      <div class="player-cell">
        ${playerPhotoHtml(p)}
        <div>
          <div>${posTag(p.position)} <strong>${escapeHtml(p.name)}</strong> ${healthBadge(p.injury_status)} ${streamBadge}</div>
          <div class="muted small row-meta">${playerMetaLine(p)}</div>
          <div class="muted small row-meta mobile-line" ${oppAttrs}>${kickoff ? escapeHtml(kickoff) + " " : ""}${opponentCellHtml(weekEntry)}</div>
        </div>
      </div>
    </td>
    <td class="desktop-col" ${oppAttrs}>
      ${kickoff ? `<div class="muted small row-meta">${kickoff}</div>` : ""}
      <div>${opponentCellHtml(weekEntry)}</div>
    </td>
    <td><strong>${fmt(proj, 1)}</strong></td>
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

  return `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Slot</th><th>Player</th><th class="desktop-col">Opp</th><th>Proj</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot>
          <tr class="totals-row">
            <td colspan="2">Starters total</td>
            <td class="desktop-col"></td>
            <td><strong>${fmt(ourTotal, 1)}</strong></td>
          </tr>
        </tfoot>
      </table>
    </div>
    <h3>Bench</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Slot</th><th>Player</th><th class="desktop-col">Opp</th><th>Proj</th></tr></thead>
        <tbody>${benchRows}</tbody>
        <tfoot>
          <tr class="totals-row">
            <td colspan="2">Bench total</td>
            <td class="desktop-col"></td>
            <td><strong>${fmt(benchTotal, 1)}</strong></td>
          </tr>
        </tfoot>
      </table>
    </div>
  `;
}

// Full-cell color fill (not a pill) with the opponent centered above its
// matchup rank, no parentheses - this grid is dense (one column per
// remaining week) so every pixel of cell width matters more here than in a
// single "Opp" column elsewhere.
function rosCellHtml(weekEntry, position) {
  if (!weekEntry || !weekEntry.opponent) return `<td class="heat-cell ros-cell muted">BYE</td>`;
  const label = (weekEntry.home === false ? "@" : "") + weekEntry.opponent;
  const hasRank = weekEntry.rank !== null && weekEntry.rank !== undefined;
  const color = colorForRatio(ratioForRank(weekEntry.rank));
  const rankTitle = hasRank ? `title="Matchup rank ${weekEntry.rank} of 32 (1 = best)"` : "";
  return `<td class="heat-cell ros-cell" style="background:${color}" ${rankTitle} data-opp-cell data-team="${escapeHtml(weekEntry.opponent)}" data-pos="${position}">
    <div class="ros-opp">${label}</div>
    ${hasRank ? `<div class="ros-rank">${weekEntry.rank}</div>` : ""}
  </td>`;
}

function scheduleGrid(roster, currentWeek, finalWeek) {
  const weeks = [];
  for (let w = currentWeek; w <= finalWeek; w++) weeks.push(w);
  const sorted = roster.slice().sort((a, b) => sortByPositionOrder(a, b, (p) => p.position) || b.ros_total - a.ros_total);

  const header = `<tr><th>Player</th>${weeks.map((w) => `<th>Wk ${w}</th>`).join("")}</tr>`;
  const rows = sorted
    .map((p) => {
      const byWeek = new Map((p.weekly || []).map((w) => [w.week, w]));
      const cells = weeks.map((w) => rosCellHtml(byWeek.get(w), p.position)).join("");
      return `<tr data-player-id="${p.id}" class="clickable-row"><td class="ros-name">${posTag(p.position)} ${escapeHtml(p.name)}</td>${cells}</tr>`;
    })
    .join("");
  return `<table class="ros-grid">${header}<tbody>${rows}</tbody></table>`;
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
      <div class="card card-compact">
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
        <div class="table-wrap">${scheduleGrid(roster, data.meta.current_week, data.meta.final_week)}</div>
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
