import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { colorForRatio, teamLabel } from "./colors.js";
import { scoreOf } from "./schedule.js";
import { renderPaths } from "./paths.js";
import { renderRetro } from "./oddsretro.js";

// Sort indicator + clickable <th> - same tiny local convention matchups.js's
// own sortTh/toggleSort/sortRows twin uses (kept as a self-contained copy
// rather than a shared import, per that file's own comment on why).
function sortIndicator(sortState, key) {
  if (sortState.key !== key) return "";
  return sortState.dir === 1 ? " ▲" : " ▼";
}
function sortTh(label, key, sortState, extraAttrs = "") {
  return `<th data-sort-key="${key}"${extraAttrs}>${escapeHtml(label)}${sortIndicator(sortState, key)}</th>`;
}
function toggleSort(sortState, key) {
  sortState.key = key;
  sortState.dir = sortState.dir === 1 && sortState.lastKey === key ? -1 : 1;
  sortState.lastKey = key;
}
function sortRows(rows, sortState, valueFor) {
  if (!sortState.key) return rows;
  return rows.slice().sort((a, b) => {
    const av = valueFor(a, sortState.key);
    const bv = valueFor(b, sortState.key);
    if (typeof av === "string" || typeof bv === "string") return String(av).localeCompare(String(bv)) * sortState.dir;
    const an = av === null || av === undefined ? -Infinity : av;
    const bn = bv === null || bv === undefined ? -Infinity : bv;
    return (an - bn) * sortState.dir;
  });
}

// Rest-of-season Record/PF/PA for one team - every regular-season matchup
// that hasn't been played yet (playoff weeks aren't real fixed pairings
// until seeding is set, so this stops at reg_season_count, matching the
// playoff-odds sim's own remaining_matchups - see engine/standings.py's
// simulate_playoffs). PF/PA reuse schedule.js's own scoreOf so this always
// agrees with what the Schedule tab itself shows for the same matchup - the
// real score if played, the live-aware in-progress sum for the current
// week, or the optimal-lineup projection for a future week - never a
// simpler re-derivation that could drift out of sync. Record is a plain
// favorite/underdog call from that same matchup's own win probability (not
// a fractional/expected-wins number) - win if favored (>50%), loss if
// underdog, per the conversation this was built from.
function rosStatsFor(teamId, data) {
  const regSeasonCount = data.meta.reg_season_count;
  const remaining = (data.schedule || []).filter(
    (m) => !m.played && m.week <= regSeasonCount && (m.home_team_id === teamId || m.away_team_id === teamId)
  );
  let wins = 0, losses = 0, pf = 0, pa = 0;
  remaining.forEach((m) => {
    const isHome = m.home_team_id === teamId;
    const side = isHome ? "home" : "away";
    const oppSide = isHome ? "away" : "home";
    pf += scoreOf(m, side, data) || 0;
    pa += scoreOf(m, oppSide, data) || 0;
    const winPct = isHome ? m.home_win_pct : m.away_win_pct;
    if (winPct === null || winPct === undefined) return;
    if (winPct > 0.5) wins += 1;
    else losses += 1;
  });
  return { wins, losses, pf, pa };
}

// Win probability for the team's "Thru Wk N" game: the same schedule matchup's own home/away win_pct the ROS
// record and the Schedule tab use. null when there's no game that week or no probability yet.
function thruWinProb(row, data) {
  if (!row.thru || !row.thru.result) return null;
  const m = (data.schedule || []).find((g) => g.week === row.thru.week && (g.home_team_id === row.team_id || g.away_team_id === row.team_id));
  if (!m) return null;
  const p = m.home_team_id === row.team_id ? m.home_win_pct : m.away_win_pct;
  return p === undefined ? null : p;
}

function seedBar(seedProbs) {
  const seeds = Object.keys(seedProbs).sort((a, b) => Number(a) - Number(b));
  const segments = seeds
    .map((s) => {
      const pct = seedProbs[s] * 100;
      if (pct < 0.5) return "";
      const ratio = 1 - (Number(s) - 1) / Math.max(1, seeds.length - 1);
      return `<div style="width:${pct}%; background:${colorForRatio(ratio)};" title="Seed ${s}: ${fmt(pct, 0)}%"></div>`;
    })
    .join("");
  return `<div style="display:flex; height:16px; border-radius:4px; overflow:hidden; width:160px;">${segments}</div>`;
}

// Every sortable column keyed off the same merged {...standings row, ros}
// shape draw() builds - "team" is the only one that sorts as a string
// (localeCompare, via sortRows' own typeof check), everything else numeric.
function valueFor(row, key) {
  if (key === "team") return teamLabel(row.team) || String(row.team_id);
  if (key === "div") return row.division;
  if (key === "seed") return row.seed;
  if (key === "cur_record") return row.wins;
  if (key === "cur_pf") return row.points_for;
  if (key === "cur_pa") return row.points_against;
  if (key === "thru_seed") return row.thru ? row.thru.seed : null;
  if (key === "thru_record") return row.thru ? row.thru.wins : null;
  if (key === "thru_pf") return row.thru ? row.thru.points_for : null;
  if (key === "thru_pa") return row.thru ? row.thru.points_against : null;
  if (key === "ros_record") return row.ros.wins;
  if (key === "ros_pf") return row.ros.pf;
  if (key === "ros_pa") return row.ros.pa;
  if (key === "thru_wp") return row.thru_wp;
  if (key === "fin_record") return row.wins + row.ros.wins;
  if (key === "fin_pf") return row.points_for + row.ros.pf;
  if (key === "fin_pa") return row.points_against + row.ros.pa;
  if (key === "xwins") return row.expected_wins;
  if (key === "playoff") return row.playoff_odds;
  if (key === "bye") return row.bye_odds;
  if (key === "divwin") return row.division_win_odds;
  if (key === "titlegame") return row.title_game_odds;
  if (key === "champ") return row.title_odds;
  return null;
}

// Table <-> Paths toggle, remembered per league across tab switches.
const viewBySlug = new Map();

export function renderStandings(container, data, slug) {
  const view = viewBySlug.get(slug) || "table";
  container.innerHTML = `<div class="seg-toggle" role="tablist">
      <button data-view="table" class="${view === "table" ? "active" : ""}">Table</button>
      <button data-view="paths" class="${view === "paths" ? "active" : ""}">Paths</button>
      <button data-view="retro" class="${view === "retro" ? "active" : ""}">Retrospective</button>
    </div><div id="standings-body"></div>`;
  container.querySelectorAll(".seg-toggle button").forEach((b) =>
    b.addEventListener("click", () => {
      viewBySlug.set(slug, b.dataset.view);
      renderStandings(container, data, slug);
    })
  );
  const body = container.querySelector("#standings-body");
  if (view === "paths") renderPaths(body, data, slug);
  else if (view === "retro") renderRetro(body, data, slug);
  else renderStandingsTable(body, data, slug, (teamId) => {
    viewBySlug.set(slug, "paths");
    renderStandings(container, data, slug);
    renderPaths(container.querySelector("#standings-body"), data, slug, { teamId });
  });
}

function renderStandingsTable(container, data, slug, openPaths) {
  const teamsById = data.teamsById;
  const yourTeamId = getYourTeam(slug);
  const allRows = data.standings.map((s) => ({ ...s, team: teamsById.get(s.team_id), ros: rosStatsFor(s.team_id, data), thru_wp: thruWinProb(s, data) }));

  // Real current seed (see engine.standings.compute_current_seeds), which
  // now covers every team - non-playoff teams are seeded past
  // playoff_team_count using the last playoff seed's own criteria.
  const defaultOrder = allRows.slice().sort((a, b) => a.seed - b.seed);

  // "Thru Wk N": the standings one week out (engine/standings_stage.py's
  // _thru_week_standings) - absent from older builds and once the regular
  // season is over, in which case the whole block is simply not drawn.
  const thruWeek = allRows.find((r) => r.thru)?.thru.week ?? null;
  // Championship odds come from the playoff-bracket sim; older builds don't have them.
  const hasTitle = allRows.some((r) => r.title_odds !== null && r.title_odds !== undefined);
  const titleCell = (v) => (v === null || v === undefined ? "–" : `${fmt(v * 100, 0)}%`);

  // Seed one week out, plus how far it moved from today's seed (lower seed
  // number = better, so a smaller number is an improvement).
  const thruSeedCell = (s) => {
    if (!s.thru || s.thru.seed === null || s.thru.seed === undefined) return "–";
    const delta = s.seed === null || s.seed === undefined ? 0 : s.seed - s.thru.seed;
    const mark = delta > 0 ? ` <span class="seed-up small">▲${delta}</span>` : delta < 0 ? ` <span class="seed-down small">▼${-delta}</span>` : "";
    return `${s.thru.seed}${mark}`;
  };
  const thruResultTitle = (s) => {
    if (!s.thru || !s.thru.result) return "No projected game this week";
    const opp = data.teamsById.get(s.thru.opponent_id);
    return `Wk ${s.thru.week}: projected ${s.thru.result} vs ${opp ? teamLabel(opp) : s.thru.opponent_id} (${fmt(s.thru.projected_score, 1)}-${fmt(s.thru.opponent_score, 1)})`;
  };

  let sortState = { key: null, dir: 1, lastKey: null };

  const wrap = () => {
    const sortedRows = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;
    const rows = sortedRows
      .map((s) => {
        const isYours = s.team_id === yourTeamId;
        return `<tr class="${isYours ? "your-team-row" : ""}">
          <td class="sticky-seed">${s.seed ?? "–"}</td>
          <td class="sticky-team${data.simPaths && data.simPaths.games ? " path-link" : ""}" data-team="${s.team_id}" title="Open this team's playoff paths">${isYours ? "<strong>" : ""}${escapeHtml(s.team ? teamLabel(s.team) : s.team_id)}${isYours ? "</strong>" : ""}</td>
          <td>${escapeHtml(s.division)}</td>
          <td>${s.wins}-${s.losses}${s.ties ? "-" + s.ties : ""}</td>
          <td>${fmt(s.points_for, 1)}</td>
          <td class="block-end">${fmt(s.points_against, 1)}</td>
          ${thruWeek === null ? "" : `<td>${thruSeedCell(s)}</td>
          <td title="${escapeHtml(thruResultTitle(s))}">${s.thru ? `${s.thru.wins}-${s.thru.losses}${s.thru.ties ? "-" + s.thru.ties : ""}` : "–"}${s.thru && s.thru.result ? ` <span class="muted small">${s.thru.result}</span>` : ""}</td>
          <td>${s.thru_wp === null ? "–" : `${fmt(s.thru_wp * 100, 0)}%`}</td>
          <td>${s.thru ? fmt(s.thru.points_for, 1) : "–"}</td>
          <td class="block-end">${s.thru ? fmt(s.thru.points_against, 1) : "–"}</td>`}
          <td>${s.ros.wins}-${s.ros.losses}</td>
          <td>${fmt(s.ros.pf, 1)}</td>
          <td class="block-end">${fmt(s.ros.pa, 1)}</td>
          <td>${s.wins + s.ros.wins}-${s.losses + s.ros.losses}${s.ties ? "-" + s.ties : ""}</td>
          <td>${fmt(s.points_for + s.ros.pf, 1)}</td>
          <td class="block-end">${fmt(s.points_against + s.ros.pa, 1)}</td>
          <td>${fmt(s.expected_wins, 1)}</td>
          <td>${fmt(s.playoff_odds * 100, 0)}%</td>
          <td>${fmt(s.bye_odds * 100, 0)}%</td>
          <td>${fmt(s.division_win_odds * 100, 0)}%</td>
          ${hasTitle ? `<td>${titleCell(s.title_game_odds)}</td><td>${titleCell(s.title_odds)}</td>` : ""}
          <td>${seedBar(s.seed_probs)}</td>
        </tr>`;
      })
      .join("");

    container.innerHTML = `
      <div class="card">
        <h2>Standings &amp; playoff odds <span class="muted small">(${data.standings[0] ? data.standings[0].iterations.toLocaleString() : 0} simulations)</span></h2>
        <div class="table-wrap">
          <table class="standings-table">
            <thead>
              <tr class="group-header-row">
                ${sortTh("Seed", "seed", sortState, ` class="sticky-seed" rowspan="2"`)}
                ${sortTh("Team", "team", sortState, ` class="sticky-team" rowspan="2"`)}
                ${sortTh("Div", "div", sortState, ` rowspan="2"`)}
                <th colspan="3" class="block-end">Current</th>
                ${thruWeek === null ? "" : `<th colspan="5" class="block-end" title="Current record plus this week's matchups played out at their projected scores (live-blended once the week has started), seeded with the same tiebreakers.">Thru Wk ${thruWeek}</th>`}
                <th colspan="3" class="block-end">ROS</th>
                <th colspan="3" class="block-end" title="Projected regular-season finish: Current plus ROS (record from each remaining game's favorite/underdog call; PF and PA are the sums).">Finish</th>
                ${sortTh("xWins", "xwins", sortState, ` rowspan="2"`)}
                ${sortTh("Playoff%", "playoff", sortState, ` rowspan="2"`)}
                ${sortTh("Bye%", "bye", sortState, ` rowspan="2"`)}
                ${sortTh("Div win%", "divwin", sortState, ` rowspan="2"`)}
                ${hasTitle ? `${sortTh("Title game%", "titlegame", sortState, ` rowspan="2" title="Chance to reach the championship game (simulated through the playoff bracket)"`)}${sortTh("Champ%", "champ", sortState, ` rowspan="2" title="Chance to win the championship (simulated through the playoff bracket; set the reseed rule in Settings)"`)}` : ""}
                <th rowspan="2">Seed dist.</th>
              </tr>
              <tr>
                ${sortTh("Record", "cur_record", sortState)}${sortTh("PF", "cur_pf", sortState)}${sortTh("PA", "cur_pa", sortState, ` class="block-end"`)}
                ${thruWeek === null ? "" : `${sortTh("Seed", "thru_seed", sortState)}${sortTh("Record", "thru_record", sortState)}${sortTh("WP", "thru_wp", sortState, ` title="Win probability for this week's game"`)}${sortTh("PF", "thru_pf", sortState)}${sortTh("PA", "thru_pa", sortState, ` class="block-end"`)}`}
                ${sortTh("Record", "ros_record", sortState)}${sortTh("PF", "ros_pf", sortState)}${sortTh("PA", "ros_pa", sortState, ` class="block-end"`)}
                ${sortTh("Record", "fin_record", sortState)}${sortTh("PF", "fin_pf", sortState)}${sortTh("PA", "fin_pa", sortState, ` class="block-end"`)}
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;

    container.querySelectorAll("td.path-link").forEach((td) => {
      td.addEventListener("click", () => openPaths(Number(td.dataset.team)));
    });
    container.querySelectorAll("th[data-sort-key]").forEach((th) => {
      th.addEventListener("click", () => {
        toggleSort(sortState, th.dataset.sortKey);
        wrap();
      });
    });
  };
  wrap();
}
