import { fmt, escapeHtml, getYourTeam, setYourTeam } from "./state.js";
import { colorForRatio, teamLabel } from "./colors.js";
import { openPlayerModal } from "./playermodal.js";

function teamSelect(data, slug, selectedId) {
  const options = data.teams
    .map((t) => `<option value="${t.team_id}" ${t.team_id === selectedId ? "selected" : ""}>${escapeHtml(teamLabel(t))}</option>`)
    .join("");
  return `<select id="strength-team-select">${options}</select>`;
}

// Requested order for the top-of-page slot bars specifically: QB, RB, WR1,
// WR2, TE, FLEX1, FLEX2, K, DST.
const SLOT_BAR_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"];

function slotBarSort(a, b) {
  const base = (s) => s.replace(/\d+$/, "");
  const idx = (s) => {
    const i = SLOT_BAR_ORDER.indexOf(base(s));
    return i === -1 ? 99 : i;
  };
  return idx(a) - idx(b) || Number(a.match(/\d+$/) || 0) - Number(b.match(/\d+$/) || 0);
}

function positionBars(strength, total) {
  const positions = Object.keys(strength).sort(slotBarSort);
  const maxAbs = Math.max(1, ...positions.map((p) => Math.abs(strength[p].vs_avg)));
  const bars = positions
    .map((pos) => {
      const s = strength[pos];
      const ratio = 0.5 + (s.vs_avg / maxAbs) * 0.5;
      const widthPct = Math.min(100, Math.abs(s.vs_avg / maxAbs) * 50);
      const side = s.vs_avg >= 0 ? "right" : "left";
      return `<div class="bar-row">
        <div class="bar-label">${pos} <span class="muted">#${s.rank}</span></div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${widthPct}%; background:${colorForRatio(ratio)}; margin-${side === "right" ? "left" : "right"}:auto;"></div>
        </div>
        <div class="bar-value">${fmt(s.ppw, 1)} <span class="muted">(${s.vs_avg >= 0 ? "+" : ""}${fmt(s.vs_avg, 1)})</span></div>
      </div>`;
    })
    .join("");
  if (!total) return bars;
  const totalRatio = 0.5 + Math.max(-1, Math.min(1, total.vs_avg / maxAbs)) * 0.5;
  const totalWidthPct = Math.min(100, Math.abs(total.vs_avg / maxAbs) * 50);
  const totalSide = total.vs_avg >= 0 ? "right" : "left";
  const totalBar = `<div class="bar-row bar-row-total">
    <div class="bar-label"><strong>Total</strong> <span class="muted">#${total.rank}</span></div>
    <div class="bar-track">
      <div class="bar-fill" style="width:${totalWidthPct}%; background:${colorForRatio(totalRatio)}; margin-${totalSide === "right" ? "left" : "right"}:auto;"></div>
    </div>
    <div class="bar-value"><strong>${fmt(total.ppw, 1)}</strong> <span class="muted">(${total.vs_avg >= 0 ? "+" : ""}${fmt(total.vs_avg, 1)})</span></div>
  </div>`;
  return bars + totalBar;
}

// Total starting-lineup strength: the per-position vs_avg values are each
// already (this team's ppw at that position) - (league-average ppw at that
// position), so they sum linearly into one "whole lineup vs. a fully average
// lineup" figure. Ranked against every other team's own sum of the same
// per-position figures (not against slot_strength, which is finer-grained
// than a real position and only computed for the currently selected team).
function computeTotalStrength(data, team) {
  const positions = data.meta.positions;
  const totalFor = (t) => positions.reduce((acc, pos) => acc + (t.position_strength[pos]?.vs_avg || 0), 0);
  const ordered = data.teams.map((t) => ({ team_id: t.team_id, total: totalFor(t) })).sort((a, b) => b.total - a.total);
  const rank = ordered.findIndex((t) => t.team_id === team.team_id) + 1;
  const ppw = positions.reduce((acc, pos) => acc + (team.position_strength[pos]?.ppw || 0), 0);
  return { ppw, vs_avg: totalFor(team), rank };
}

function trendSparkline(weekly) {
  if (!weekly || !weekly.length) return "";
  const values = weekly.map((w) => w.value_delta_ww);
  const maxAbs = Math.max(1, ...values.map((v) => Math.abs(v)));
  const bars = weekly
    .map((w) => {
      const heightPct = Math.max(4, (Math.abs(w.value_delta_ww) / maxAbs) * 100);
      const ratio = 0.5 + (w.value_delta_ww / maxAbs) * 0.5;
      return `<div class="spark-bar" style="height:${heightPct}%; background:${colorForRatio(ratio)}" title="Wk ${w.week}: ${fmt(w.value_delta_ww, 1)}"></div>`;
    })
    .join("");
  return `<div class="sparkline">${bars}</div>`;
}

function depthTable(depth, playersById) {
  const positions = Object.keys(depth);
  return positions
    .map((pos) =>
      depth[pos]
        .map(
          (d, i) =>
            `<tr data-player-id="${d.id}" class="clickable-row"><td>${pos}${i + 1}</td><td>${escapeHtml((playersById.get(d.id) || {}).name || d.id)}</td><td>${fmt(d.value_delta, 1)}</td><td>${fmt(d.value_delta_ww, 1)}</td><td>${trendSparkline(d.weekly)}</td></tr>`
        )
        .join("")
    )
    .join("");
}

function pickupsTable(pickups, playersById) {
  if (!pickups.length) return `<p class="muted small">No pickups projected to improve this roster right now.</p>`;
  return `<table><thead><tr><th>Add</th><th>Drop</th><th>Gain (ROS)</th></tr></thead><tbody>${pickups
    .map((p) => {
      const add = playersById.get(p.add);
      const drop = playersById.get(p.drop);
      return `<tr><td>${escapeHtml(add ? add.name : p.add)}</td><td>${escapeHtml(drop ? drop.name : p.drop)}</td><td>+${fmt(p.gain, 1)}</td></tr>`;
    })
    .join("")}</tbody></table>`;
}

function tradeTargetsTable(targets, playersById, teamsById) {
  if (!targets.length) return `<p class="muted small">No favorable trade targets found.</p>`;
  return `<table><thead><tr><th>Partner</th><th>Give</th><th>Get</th><th>Your gain</th><th>Their gain</th></tr></thead><tbody>${targets
    .map((t) => {
      const partner = teamsById.get(t.partner_team_id);
      const give = t.give.map((id) => (playersById.get(id) || {}).name || id).join(", ");
      const get = t.get.map((id) => (playersById.get(id) || {}).name || id).join(", ");
      return `<tr><td>${escapeHtml(partner ? teamLabel(partner) : t.partner_team_id)}</td><td>${escapeHtml(give)}</td><td>${escapeHtml(get)}</td><td>+${fmt(t.gain_self, 1)}</td><td>+${fmt(t.gain_partner, 1)}</td></tr>`;
    })
    .join("")}</tbody></table>`;
}

function leagueWideTable(data, yourTeamId) {
  const positions = data.meta.positions;
  // Color relative to the SPREAD WITHIN EACH POSITION'S OWN COLUMN (highest
  // ppw in that column = green, lowest = red, middle = yellow) rather than a
  // fixed points/week scale shared across every column - positions with a
  // wide gap between the best and worst team (e.g. RB) and positions with a
  // narrow one (e.g. K) would otherwise all get squeezed onto the same ruler,
  // making two very different RB values look like the same shade.
  const ranges = {};
  positions.forEach((pos) => {
    const vals = data.teams.map((t) => t.position_strength[pos]?.ppw).filter((v) => v !== undefined && v !== null);
    ranges[pos] = { min: Math.min(...vals), max: Math.max(...vals) };
  });
  const totalFor = (t) => positions.reduce((acc, pos) => acc + (t.position_strength[pos]?.ppw || 0), 0);
  const totals = data.teams.map(totalFor);
  const totalRange = { min: Math.min(...totals), max: Math.max(...totals) };

  const header = `<tr><th>Team</th>${positions.map((p) => `<th>${p}</th>`).join("")}<th>Total</th></tr>`;
  const rows = data.teams
    .map((t) => {
      const cells = positions
        .map((pos) => {
          const s = t.position_strength[pos];
          if (!s) return "<td>–</td>";
          const { min, max } = ranges[pos];
          const ratio = max > min ? (s.ppw - min) / (max - min) : 0.5;
          return `<td class="heat-cell" style="background:${colorForRatio(ratio)}">${fmt(s.ppw, 1)}</td>`;
        })
        .join("");
      const total = totalFor(t);
      const totalRatio = totalRange.max > totalRange.min ? (total - totalRange.min) / (totalRange.max - totalRange.min) : 0.5;
      const totalCell = `<td class="heat-cell" style="background:${colorForRatio(totalRatio)}"><strong>${fmt(total, 1)}</strong></td>`;
      const isYours = t.team_id === yourTeamId;
      return `<tr class="${isYours ? "your-team-row" : ""}"><td>${isYours ? "<strong>" : ""}${escapeHtml(teamLabel(t))}${isYours ? "</strong>" : ""}</td>${cells}${totalCell}</tr>`;
    })
    .join("");
  return `<table>${header}${rows}</table>`;
}

function wirePlayerClicks(container, data) {
  container.querySelectorAll("tr[data-player-id]").forEach((row) => {
    row.addEventListener("click", () => {
      const p = data.playersById.get(row.dataset.playerId);
      if (p) openPlayerModal(p, data);
    });
  });
}

export function renderStrength(container, data, slug) {
  const yourTeamId = getYourTeam(slug) || data.teams[0].team_id;
  const team = data.teamsById.get(Number(yourTeamId)) || data.teams[0];

  container.innerHTML = `
    <div class="card card-compact">
      <div class="select-row"><label>Your team:</label> ${teamSelect(data, slug, team.team_id)}</div>
      <h2>Starting Lineup vs. League Avg</h2>
      ${positionBars(team.slot_strength, computeTotalStrength(data, team))}
      <h3>Depth (next-man-down value)</h3>
      <div class="table-wrap"><table><thead><tr><th>Slot</th><th>Player</th><th>Value</th><th>Value (w/ waivers)</th><th>Weekly trend</th></tr></thead><tbody>${depthTable(team.depth, data.playersById)}</tbody></table></div>
      <h3>Suggested pickups</h3>
      <div class="table-wrap">${pickupsTable(team.pickups, data.playersById)}</div>
      <h3>Trade targets</h3>
      <div class="table-wrap">${tradeTargetsTable(team.trade_targets, data.playersById, data.teamsById)}</div>
    </div>
    <div class="card">
      <h2>ROS Projected Points/Week</h2>
      <div class="table-wrap">${leagueWideTable(data, Number(team.team_id))}</div>
    </div>
  `;

  container.querySelector("#strength-team-select").addEventListener("change", (e) => {
    setYourTeam(slug, Number(e.target.value));
    renderStrength(container, data, slug);
  });
  wirePlayerClicks(container, data);
}
