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

// ESPN's raw lineupSlot strings -> the same base labels engine/lineup.py's
// display_label uses for its own computed-lineup slot keys (D/ST -> DST,
// the three flex-eligible composites -> FLEX) - so a real ESPN slot and a
// computed-optimal slot instance line up under one label space below.
function slotBase(rawSlot) {
  if (rawSlot === "D/ST") return "DST";
  if (rawSlot === "RB/WR/TE" || rawSlot === "RB/WR" || rawSlot === "WR/TE") return "FLEX";
  if (rawSlot === "TQB") return "QB";
  return rawSlot;
}

function actualStarters(teamId, data) {
  return data.players.filter((p) => p.fantasy_team_id === teamId && p.lineup_slot && p.lineup_slot !== "BE" && p.lineup_slot !== "IR");
}

// The real, ESPN-set lineup for the CURRENT week - not our optimal computed
// one - since that's the lineup that's actually live for a week already
// underway. Past/future weeks have no such "actual" lineup to pull (ESPN
// doesn't retain history here and obviously can't know a future week's
// lineup), so those keep using the computed optimal lineup below. Shaped
// like a computed week's {slots, bench} so both paths render identically.
function actualLineupWeek(teamId, data) {
  const roster = data.players.filter((p) => p.fantasy_team_id === teamId);
  const byBase = new Map();
  actualStarters(teamId, data)
    .slice()
    .sort((a, b) => (b.espn_projected_week || 0) - (a.espn_projected_week || 0))
    .forEach((p) => {
      const base = slotBase(p.lineup_slot);
      if (!byBase.has(base)) byBase.set(base, []);
      byBase.get(base).push(p.id);
    });
  const slots = {};
  byBase.forEach((ids, base) => {
    ids.forEach((id, i) => {
      slots[ids.length > 1 ? `${base}${i + 1}` : base] = id;
    });
  });
  const bench = roster.filter((p) => !p.lineup_slot || p.lineup_slot === "BE" || p.lineup_slot === "IR").map((p) => p.id);
  return { slots, bench };
}

function lineupWeekFor(teamId, week, data) {
  if (week === data.meta.current_week) return actualLineupWeek(teamId, data);
  const lineupTeam = data.lineups[String(teamId)];
  return lineupTeam ? lineupTeam.weeks[String(week)] : null;
}

function scoreOf(m, side, data) {
  if (m.played) return side === "home" ? m.home_score : m.away_score;
  const teamId = side === "home" ? m.home_team_id : m.away_team_id;
  if (m.week === data.meta.current_week) {
    return actualStarters(teamId, data).reduce((acc, p) => acc + (pointsForWeek(p, m.week) || 0), 0);
  }
  return (data.lineups[String(teamId)]?.weeks[String(m.week)] || {}).total ?? null;
}

// One shared table, home team's players/scores on the outside-left and away
// team's on the outside-right, mirrored around a single center "slot" column
// - rather than two separate side-by-side tables - so the two lineups read
// as one symmetric comparison instead of two unrelated lists.
function symmetricLineupHtml(homeTeamId, awayTeamId, week, data) {
  const home = lineupWeekFor(homeTeamId, week, data);
  const away = lineupWeekFor(awayTeamId, week, data);
  if (!home || !away) return `<p class="muted small">No projection for this week.</p>`;

  const streamed = new Set([...(home.streamed || []), ...(away.streamed || [])]);
  const keys = [...new Set([...Object.keys(home.slots || {}), ...Object.keys(away.slots || {})])].sort(
    (a, b) => sortByPositionOrder(a, b, (s) => s.replace(/\d+$/, "")) || a.localeCompare(b)
  );

  const playerCell = (pid) => {
    if (!pid) return "";
    const p = data.playersById.get(pid);
    if (!p) return "";
    const badge = streamed.has(pid) ? ` <span class="pill small stream-badge" title="Free-agent bye-week fill-in, not on your roster">FA</span>` : "";
    return `${posTag(p.position)} ${escapeHtml(p.name)}${badge}`;
  };
  const scoreCell = (pid) => {
    const p = pid && data.playersById.get(pid);
    return p ? fmt(pointsForWeek(p, week), 1) : "–";
  };

  const rows = keys
    .map((key) => {
      const hPid = (home.slots || {})[key];
      const aPid = (away.slots || {})[key];
      const rowClass = streamed.has(hPid) || streamed.has(aPid) ? "streamed-row" : "";
      return `<tr class="${rowClass}">
        <td class="lineup-player lineup-player-home">${playerCell(hPid)}</td>
        <td class="lineup-score">${scoreCell(hPid)}</td>
        <td class="lineup-slot muted small">${key.replace(/\d+$/, "")}</td>
        <td class="lineup-score">${scoreCell(aPid)}</td>
        <td class="lineup-player lineup-player-away">${playerCell(aPid)}</td>
      </tr>`;
    })
    .join("");

  // Bench sizes/order between the two teams have no natural row-for-row
  // pairing the way starting slots do, so these stay two independent lists
  // rather than forced into the symmetric grid above.
  const benchList = (ids) =>
    (ids || [])
      .map((id) => data.playersById.get(id))
      .filter(Boolean)
      .map((p) => `<div>${posTag(p.position)} ${escapeHtml(p.name)}${p.lineup_slot === "IR" ? ` <span class="muted small">(IR)</span>` : ""} <span class="value-readout">${fmt(pointsForWeek(p, week), 1)}</span></div>`)
      .join("");

  return `
    <table class="lineup-symmetric">
      <colgroup><col style="width:36%"><col style="width:9%"><col style="width:10%"><col style="width:9%"><col style="width:36%"></colgroup>
      <tbody>${rows}</tbody>
    </table>
    <div class="lineup-bench-cols muted small">
      <div>${benchList(home.bench)}</div>
      <div>${benchList(away.bench)}</div>
    </div>
  `;
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
          <div class="lineup-symmetric-heading">
            <h3 class="small">${escapeHtml(teamLabel(home))}</h3>
            <h3 class="small">${escapeHtml(teamLabel(away))}</h3>
          </div>
          ${symmetricLineupHtml(m.home_team_id, m.away_team_id, m.week, data)}
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
        <p class="muted small">Click a matchup to see each team's lineup that week - your actual ESPN-set starters for the current week, optimal projected lineups otherwise.</p>
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
