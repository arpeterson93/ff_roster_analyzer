import { fmt, escapeHtml, getYourTeam, setYourTeam } from "./state.js";
import { colorForRatio, teamLabel } from "./colors.js";
import { openPlayerModal } from "./playermodal.js";
import { buildPlayersMap, buildFreeAgentsByPos } from "./tradeui.js";

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

// Starting value specifically, not the blended Total - shows how much this
// player has actually been WINNING his own starting slot by, week to week
// (0 in a week he sat, per position_value_by_player - a real bench week,
// not a modeling gap).
function trendSparkline(weekly) {
  if (!weekly || !weekly.length) return "";
  const values = weekly.map((w) => w.starting_value);
  const maxAbs = Math.max(1, ...values.map((v) => Math.abs(v)));
  const bars = weekly
    .map((w) => {
      const heightPct = Math.max(4, (Math.abs(w.starting_value) / maxAbs) * 100);
      const ratio = 0.5 + (w.starting_value / maxAbs) * 0.5;
      return `<div class="spark-bar" style="height:${heightPct}%; background:${colorForRatio(ratio)}" title="Wk ${w.week}: ${fmt(w.starting_value, 1)}"></div>`;
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
            `<tr data-player-id="${d.id}" class="clickable-row"><td>${pos}${i + 1}</td><td>${escapeHtml((playersById.get(d.id) || {}).name || d.id)}</td><td>${fmt(d.value_delta, 1)}</td><td>${trendSparkline(d.weekly)}</td></tr>`
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

// Same breakdown as the top-of-page bars (positionBars/team.slot_strength) -
// actual starting-lineup SLOTS per the league's own roster settings (QB,
// RB, WR1, WR2, TE, FLEX1, FLEX2, K, DST, ...), not the flatter per-BASE-
// position aggregate (team.position_strength) this table used before.
// slot_strength/slot_labels are computed server-side for every team, not
// just whichever one happens to be selected (see engine/pipeline.py), so
// there's a real per-team column to show here despite only one team's own
// bars being visible above at a time.
function leagueWideTable(data, yourTeamId) {
  const slots = (data.teams[0]?.slot_labels || []).slice().sort(slotBarSort);
  // Color relative to the SPREAD WITHIN EACH SLOT'S OWN COLUMN (highest ppw
  // in that column = green, lowest = red, middle = yellow) rather than a
  // fixed points/week scale shared across every column - slots with a wide
  // gap between the best and worst team (e.g. RB) and slots with a narrow
  // one (e.g. K) would otherwise all get squeezed onto the same ruler,
  // making two very different RB values look like the same shade.
  const ranges = {};
  slots.forEach((slot) => {
    const vals = data.teams.map((t) => t.slot_strength[slot]?.ppw).filter((v) => v !== undefined && v !== null);
    ranges[slot] = { min: Math.min(...vals), max: Math.max(...vals) };
  });
  const totalFor = (t) => slots.reduce((acc, slot) => acc + (t.slot_strength[slot]?.ppw || 0), 0);
  const totals = data.teams.map(totalFor);
  const totalRange = { min: Math.min(...totals), max: Math.max(...totals) };

  const header = `<tr><th>Team</th>${slots.map((s) => `<th>${s}</th>`).join("")}<th>Total</th></tr>`;
  const rows = data.teams
    .slice()
    .sort((a, b) => totalFor(b) - totalFor(a))
    .map((t) => {
      const cells = slots
        .map((slot) => {
          const s = t.slot_strength[slot];
          if (!s) return "<td>–</td>";
          const { min, max } = ranges[slot];
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

// Team x Position heatmap of the NEW replacement-value calc (see
// docs/js/trade.js's positionValueMatrix docstring for the full
// derivation) - the same {startingValue, depthValue, total} the Trade
// Calculator's own "Team value" panel shows for whichever two teams are
// picked there, computed here for EVERY team so a need/excess at a
// position jumps out at a glance across the whole league. That function is
// client-side only (never written into the pipeline JSON - see the
// conversation this was built from), so this loops it once per team on
// render rather than reading a precomputed field.
//
// Colored (like leagueWideTable above) by the spread WITHIN each
// position's own column, not a fixed points scale shared across columns -
// a wide-range position (RB) and a narrow one (K) would otherwise get
// squeezed onto the same ruler. Color follows `total`; starting/depth ride
// along as compact sub-text in the same cell rather than their own
// columns - a separate column per position x {starting,depth,total} would
// triple the column count and defeat the point of a quick-glance table.
function positionValueLeagueTable(data, yourTeamId) {
  const players = buildPlayersMap(data);
  const freeAgentsByPos = buildFreeAgentsByPos(data);
  const weeks = [];
  for (let w = data.meta.current_week; w <= data.meta.final_week; w++) weeks.push(w);
  const slots = data.meta.slots;
  const eligibility = data.meta.slot_eligibility;

  // Same "every base position any slot is eligible for" derivation
  // positionValueMatrix itself uses internally, so this table's own column
  // set always matches whatever position buckets the matrix can return.
  const positions = [];
  Object.keys(eligibility).forEach((base) => {
    (eligibility[base] || []).forEach((pos) => {
      if (positions.indexOf(pos) === -1) positions.push(pos);
    });
  });

  const matrices = new Map();
  data.teams.forEach((t) => {
    const teamPlayerIds = data.players.filter((p) => p.fantasy_team_id === t.team_id).map((p) => p.id);
    matrices.set(t.team_id, window.FFTrade.positionValueMatrix({ teamPlayerIds, players, freeAgentsByPos, weeks, slots, eligibility }));
  });

  const totalFor = (m) => positions.reduce((acc, pos) => acc + (m[pos] ? m[pos].total : 0), 0);
  const ranges = {};
  positions.forEach((pos) => {
    const vals = [...matrices.values()].map((m) => (m[pos] ? m[pos].total : 0));
    ranges[pos] = { min: Math.min(...vals), max: Math.max(...vals) };
  });
  const totals = [...matrices.values()].map(totalFor);
  const totalRange = { min: Math.min(...totals), max: Math.max(...totals) };

  const header = `<tr><th>Team</th>${positions.map((p) => `<th>${p}</th>`).join("")}<th>Total</th></tr>`;
  const rows = data.teams
    .slice()
    .sort((a, b) => totalFor(matrices.get(b.team_id)) - totalFor(matrices.get(a.team_id)))
    .map((t) => {
      const m = matrices.get(t.team_id);
      const cells = positions
        .map((pos) => {
          const v = m[pos];
          if (!v) return "<td>–</td>";
          const { min, max } = ranges[pos];
          const ratio = max > min ? (v.total - min) / (max - min) : 0.5;
          return `<td class="heat-cell" style="background:${colorForRatio(ratio)}">
            <div>${v.total >= 0 ? "+" : ""}${fmt(v.total, 1)}</div>
            <div class="heat-sub" title="Starting / Depth">${v.startingValue >= 0 ? "+" : ""}${fmt(v.startingValue, 1)} / ${fmt(v.depthValue, 1)}</div>
          </td>`;
        })
        .join("");
      const total = totalFor(m);
      const totalRatio = totalRange.max > totalRange.min ? (total - totalRange.min) / (totalRange.max - totalRange.min) : 0.5;
      const totalCell = `<td class="heat-cell" style="background:${colorForRatio(totalRatio)}"><strong>${total >= 0 ? "+" : ""}${fmt(total, 1)}</strong></td>`;
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
      <h3>Depth (points above replacement)</h3>
      <div class="table-wrap"><table><thead><tr><th>Slot</th><th>Player</th><th title="Points above the best available free agent for his slot/position - Starting weeks vs. Depth weeks blended (Depth discounted 50%). See his player card's own NMD week-by-week tab for the full split.">Value</th><th title="Starting value only, week by week - 0 in a week he sat, not a modeling gap.">Weekly trend</th></tr></thead><tbody>${depthTable(team.depth, data.playersById)}</tbody></table></div>
      <h3>Suggested pickups</h3>
      <div class="table-wrap">${pickupsTable(team.pickups, data.playersById)}</div>
      <h3>Trade targets</h3>
      <div class="table-wrap">${tradeTargetsTable(team.trade_targets, data.playersById, data.teamsById)}</div>
    </div>
    <div class="card card-medium">
      <h2>ROS Projected Points/Week</h2>
      <div class="table-wrap">${leagueWideTable(data, Number(team.team_id))}</div>
    </div>
    <div class="card card-medium">
      <h2>Team Value (starting + depth, by position)</h2>
      <p class="muted small">Points above replacement (best currently-available free agent) - same calc as the Trade Calculator's own "Team value" panel, computed here for every team at once. Green = excess value at that position, red = a real need. Each cell: total, with starting/depth split below it.</p>
      <div class="table-wrap">${positionValueLeagueTable(data, Number(team.team_id))}</div>
    </div>
  `;

  container.querySelector("#strength-team-select").addEventListener("change", (e) => {
    setYourTeam(slug, Number(e.target.value));
    renderStrength(container, data, slug);
  });
  wirePlayerClicks(container, data);
}
