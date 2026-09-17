import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { POSITION_COLOR, sortByPositionOrder, teamLabel } from "./colors.js";
import { openPlayerModal } from "./playermodal.js";
import { compareCheckboxHtml, wireCompareCheckboxes } from "./compare.js";

function buildPlayersMap(data) {
  const map = {};
  data.players.forEach((p) => {
    const weekly = {};
    (p.weekly || []).forEach((w) => {
      weekly[w.week] = w.projected;
    });
    map[p.id] = { position: p.position, weekly, ros_total: p.ros_total };
  });
  return map;
}

function rosterIds(data, teamId) {
  return data.players.filter((p) => p.fantasy_team_id === teamId).map((p) => p.id);
}

// {position: [{id, position, weekly, ros_total}]} for every unrostered
// player - the same free-agent pool engine/team_strength.py's trade_targets
// already streams from server-side (see evaluate_with_streaming), now also
// fed into the interactive calculator below so a bye-prone K/DST isn't
// scored as a bigger loss/gain than the wire would actually allow. Built
// once per render, not per evaluate() call - data.players doesn't change
// while the calculator is open.
function buildFreeAgentsByPos(data) {
  const byPos = {};
  data.players.forEach((p) => {
    if (p.fantasy_team_id !== null) return;
    const weekly = {};
    (p.weekly || []).forEach((w) => {
      weekly[w.week] = w.projected;
    });
    if (!byPos[p.position]) byPos[p.position] = [];
    byPos[p.position].push({ id: p.id, position: p.position, weekly, ros_total: p.ros_total });
  });
  return byPos;
}

// Value = lineup-delta (next-man-down, already waiver-inclusive - see
// engine/team_strength.py's depth_values_by_week), not raw ROS points - a
// player's value to a trade is what your lineup would actually lose without
// them, not just their season point total.
function nmdValue(p) {
  return p.value_delta ?? 0;
}

function sortedRoster(players, sortMode) {
  const list = players.slice();
  if (sortMode === "position") {
    return list.sort((a, b) => sortByPositionOrder(a, b, (p) => p.position) || nmdValue(b) - nmdValue(a));
  }
  return list.sort((a, b) => nmdValue(b) - nmdValue(a));
}

// Row click opens the player modal (same convention as Rankings/Start-Sit -
// see compareCheckboxHtml's own stopPropagation, which keeps that checkbox
// independent of it) - trade "gives" selection stays a dedicated checkbox
// in its own leading column instead, so the two clickable purposes never
// fight over the same click.
function rosterTable(side, playersList, selected, sortMode) {
  const rows = sortedRoster(playersList, sortMode)
    .map((p) => {
      const isSelected = selected.has(p.id);
      const color = POSITION_COLOR[p.position] || "#888";
      return `<tr class="clickable-row ${isSelected ? "selected-row" : ""}" data-side="${side}" data-id="${p.id}">
        <td><input type="checkbox" ${isSelected ? "checked" : ""} data-side="${side}" data-id="${p.id}" /></td>
        <td>${compareCheckboxHtml(p)}<span class="pos-tag" style="background:${color}">${p.position}</span> ${escapeHtml(p.name)}</td>
        <td class="muted small">#${p.ros_pos_rank ?? "–"}</td>
        <td>${fmt(p.ros_total, 0)}</td>
        <td>${fmt(nmdValue(p), 0)}</td>
      </tr>`;
    })
    .join("");
  return `<table><thead><tr><th></th><th>Player</th><th>Rank</th><th>ROS</th><th>NMD value</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function weeklyImpactTable(weeklyA, weeklyB, teamAName, teamBName, expandedWeek) {
  const rows = weeklyA
    .map((wa, i) => {
      const wb = weeklyB[i];
      const expanded = expandedWeek === wa.week;
      const summary = `<tr class="clickable-row" data-week="${wa.week}">
        <td class="muted small">${expanded ? "▼" : "▶"}</td>
        <td>${wa.week}</td>
        <td>${fmt(wa.before, 1)} → ${fmt(wa.after, 1)} <span class="${wa.delta >= 0 ? "" : "muted"}">(${wa.delta >= 0 ? "+" : ""}${fmt(wa.delta, 1)})</span></td>
        <td>${fmt(wb.before, 1)} → ${fmt(wb.after, 1)} <span class="${wb.delta >= 0 ? "" : "muted"}">(${wb.delta >= 0 ? "+" : ""}${fmt(wb.delta, 1)})</span></td>
      </tr>`;
      const detail = expanded
        ? `<tr><td colspan="4"><div id="trade-week-lineup-${wa.week}" class="trade-result"></div></td></tr>`
        : "";
      return summary + detail;
    })
    .join("");
  return `<table><thead><tr><th></th><th>Week</th><th>${escapeHtml(teamAName)}</th><th>${escapeHtml(teamBName)}</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// A team's post-trade optimal lineup/bench for one specific week, given the
// exact same streaming-aware DP the totals are computed from
// (window.FFTrade.lineupAssignmentForWeekWithStreaming) - so this always
// agrees with the week-by-week totals shown above it, including which
// position (if any) would actually be streamed from the wire that week
// rather than showing a misleadingly empty slot.
function lineupForWeekHtml(teamName, afterRoster, players, freeAgentsByPos, week, slots, eligibility, playersById) {
  const result = window.FFTrade.lineupAssignmentForWeekWithStreaming(afterRoster, players, freeAgentsByPos, week, slots, eligibility);
  const streamedIds = new Set(result.streamedIds);
  const streamBadge = (id) => (streamedIds.has(id) ? `<span class="pill small stream-badge" title="Not on this roster - the best free agent available that week instead">FA</span>` : "");
  const starterRows = result.starters
    .map((s) => {
      const p = playersById.get(s.id);
      if (!p) return "";
      const color = POSITION_COLOR[p.position] || "#888";
      return `<tr class="${streamedIds.has(s.id) ? "streamed-row" : ""}"><td class="muted small">${s.instance.replace(/\d+$/, "")}</td><td><span class="pos-tag" style="background:${color}">${p.position}</span> ${escapeHtml(p.name)} ${streamBadge(s.id)}</td><td>${fmt((p.weekly || []).find((w) => w.week === week)?.projected, 1)}</td></tr>`;
    })
    .join("");
  const benchRows = result.bench
    .map((id) => playersById.get(id))
    .filter(Boolean)
    .map((p) => `<tr class="muted small"><td></td><td><span class="pos-tag" style="background:${POSITION_COLOR[p.position] || "#888"}">${p.position}</span> ${escapeHtml(p.name)}</td><td>${fmt((p.weekly || []).find((w) => w.week === week)?.projected, 1)}</td></tr>`)
    .join("");
  return `<div class="trade-side"><h3 class="small">${escapeHtml(teamName)} - Wk ${week} (post-trade)</h3><table><tbody>${starterRows}${benchRows}</tbody></table></div>`;
}

function renderResult(container, result, teamAName, teamBName, ctx) {
  const verdict = result.favors === "even" ? "Roughly even" : result.favors === "a" ? `Favors ${teamAName}` : `Favors ${teamBName}`;
  container.innerHTML = `
    <div class="trade-verdict">${escapeHtml(verdict)}</div>
    <div class="trade-result">
      <div class="trade-side card">
        <h3>${escapeHtml(teamAName)}</h3>
        <div>Lineup total: ${fmt(result.sideA.before, 1)} → ${fmt(result.sideA.after, 1)} (<strong>${result.sideA.gain >= 0 ? "+" : ""}${fmt(result.sideA.gain, 1)}</strong>)</div>
        <div class="muted small">Gives ${fmt(result.sideA.rawGiven, 1)} ROS pts, gets ${fmt(result.sideA.rawReceived, 1)} ROS pts</div>
      </div>
      <div class="trade-side card">
        <h3>${escapeHtml(teamBName)}</h3>
        <div>Lineup total: ${fmt(result.sideB.before, 1)} → ${fmt(result.sideB.after, 1)} (<strong>${result.sideB.gain >= 0 ? "+" : ""}${fmt(result.sideB.gain, 1)}</strong>)</div>
        <div class="muted small">Gives ${fmt(result.sideB.rawGiven, 1)} ROS pts, gets ${fmt(result.sideB.rawReceived, 1)} ROS pts</div>
      </div>
    </div>
    <details open>
      <summary class="small">Week-by-week lineup impact - click a week to see the resulting lineup</summary>
      <div class="table-wrap">${weeklyImpactTable(result.sideA.weekly, result.sideB.weekly, teamAName, teamBName, ctx.expandedWeek)}</div>
    </details>
  `;

  if (ctx.expandedWeek !== null) {
    const wrap = container.querySelector(`#trade-week-lineup-${ctx.expandedWeek}`);
    if (wrap) {
      wrap.innerHTML =
        lineupForWeekHtml(teamAName, result.sideA.afterRoster, ctx.players, ctx.freeAgentsByPos, ctx.expandedWeek, ctx.slots, ctx.eligibility, ctx.playersById) +
        lineupForWeekHtml(teamBName, result.sideB.afterRoster, ctx.players, ctx.freeAgentsByPos, ctx.expandedWeek, ctx.slots, ctx.eligibility, ctx.playersById);
    }
  }

  container.querySelectorAll("tr.clickable-row[data-week]").forEach((row) => {
    row.addEventListener("click", () => {
      const w = Number(row.dataset.week);
      ctx.expandedWeek = ctx.expandedWeek === w ? null : w;
      renderResult(container, result, teamAName, teamBName, ctx);
    });
  });
}

export function renderTrade(container, data) {
  const teams = data.teams;
  const playersById = data.playersById;
  const freeAgentsByPos = buildFreeAgentsByPos(data);
  // Team A defaults to the viewer's own team (the trade you're actually
  // considering almost always involves you) - falls back to the first team
  // if this browser hasn't picked one for this league yet (see
  // state.js's getYourTeam).
  const yourTeamId = getYourTeam(data.meta.slug);
  const defaultTeamA = teams.find((t) => t.team_id === yourTeamId)?.team_id ?? teams[0].team_id;
  const defaultTeamB = teams.find((t) => t.team_id !== defaultTeamA)?.team_id ?? defaultTeamA;
  const state = {
    teamA: defaultTeamA, teamB: defaultTeamB,
    givesA: new Set(), givesB: new Set(), sortMode: "position", expandedWeek: null,
  };

  function draw() {
    const teamA = data.teamsById.get(state.teamA);
    const teamB = data.teamsById.get(state.teamB);
    const rostA = rosterIds(data, state.teamA).map((id) => playersById.get(id)).filter(Boolean);
    const rostB = rosterIds(data, state.teamB).map((id) => playersById.get(id)).filter(Boolean);

    container.innerHTML = `
      <div class="card">
        <div class="select-row">
          <label>Team A:</label>
          <select id="trade-team-a">${teams.map((t) => `<option value="${t.team_id}" ${t.team_id === state.teamA ? "selected" : ""}>${escapeHtml(teamLabel(t))}</option>`).join("")}</select>
          <label>Team B:</label>
          <select id="trade-team-b">${teams.map((t) => `<option value="${t.team_id}" ${t.team_id === state.teamB ? "selected" : ""}>${escapeHtml(teamLabel(t))}</option>`).join("")}</select>
          <label>Sort:</label>
          <select id="trade-sort-mode">
            <option value="position" ${state.sortMode === "position" ? "selected" : ""}>Position, then value</option>
            <option value="value" ${state.sortMode === "value" ? "selected" : ""}>Value only</option>
          </select>
        </div>
        <div class="trade-result">
          <div class="trade-side">
            <h3>${escapeHtml(teamLabel(teamA))} gives</h3>
            <div class="table-wrap" id="trade-picker-a">${rosterTable("a", rostA, state.givesA, state.sortMode)}</div>
          </div>
          <div class="trade-side">
            <h3>${escapeHtml(teamLabel(teamB))} gives</h3>
            <div class="table-wrap" id="trade-picker-b">${rosterTable("b", rostB, state.givesB, state.sortMode)}</div>
          </div>
        </div>
        <div id="trade-result"></div>
      </div>
    `;

    container.querySelector("#trade-team-a").addEventListener("change", (e) => {
      state.teamA = Number(e.target.value);
      state.givesA.clear();
      draw();
    });
    container.querySelector("#trade-team-b").addEventListener("change", (e) => {
      state.teamB = Number(e.target.value);
      state.givesB.clear();
      draw();
    });
    container.querySelector("#trade-sort-mode").addEventListener("change", (e) => {
      state.sortMode = e.target.value;
      draw();
    });

    wireCompareCheckboxes(container, data);

    function toggle(side, id) {
      const set = side === "a" ? state.givesA : state.givesB;
      set.has(id) ? set.delete(id) : set.add(id);
      draw();
    }
    // Row click opens the player modal (same as Rankings/Start-Sit) - trade
    // "gives" selection is now the dedicated leading checkbox's job only
    // (see rosterTable's comment), and the compare checkbox already stops
    // its own click from bubbling here (see compareCheckboxHtml).
    container.querySelectorAll("#trade-picker-a tr[data-id], #trade-picker-b tr[data-id]").forEach((row) => {
      row.addEventListener("click", (e) => {
        if (e.target.tagName === "INPUT") return; // checkboxes handle their own click below
        const player = playersById.get(row.dataset.id);
        if (player) openPlayerModal(player, data);
      });
    });
    container.querySelectorAll('input[type="checkbox"][data-id]').forEach((cb) => {
      cb.addEventListener("click", (e) => {
        e.stopPropagation();
        toggle(cb.dataset.side, cb.dataset.id);
      });
    });

    evaluate();

    function evaluate() {
      const resultEl = container.querySelector("#trade-result");
      if (!state.givesA.size && !state.givesB.size) {
        resultEl.innerHTML = `<p class="muted small">Pick at least one player from either side.</p>`;
        return;
      }
      const players = buildPlayersMap(data);
      const weeks = [];
      for (let w = data.meta.current_week; w <= data.meta.final_week; w++) weeks.push(w);
      const slots = data.meta.slots;
      const eligibility = data.meta.slot_eligibility;
      const result = window.FFTrade.evaluateTradeWithStreaming({
        givesA: [...state.givesA], givesB: [...state.givesB],
        rosterA: rostA.map((p) => p.id), rosterB: rostB.map((p) => p.id), players, freeAgentsByPos, weeks,
        slots, eligibility,
      });
      const ctx = { expandedWeek: state.expandedWeek, players, freeAgentsByPos, slots, eligibility, playersById: data.playersById };
      renderResult(resultEl, result, teamLabel(teamA), teamLabel(teamB), ctx);
    }
  }

  draw();
}
