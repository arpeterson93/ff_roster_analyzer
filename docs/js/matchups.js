import { fmt, escapeHtml } from "./state.js";
import { colorForRatio, ratioForRank, chartTeamColor } from "./colors.js";
import { openPointsAgainstModal } from "./pointsagainstmodal.js";

// Sort indicator + clickable <th>, same convention rankings.js's own
// sortableThHtml uses - kept as a tiny local twin rather than sharing an
// import since these tables build their header cells inline as part of a
// single big template string, not from a column-definition list.
function sortIndicator(sortState, key) {
  if (sortState.key !== key) return "";
  return sortState.dir === 1 ? " ▲" : " ▼";
}
function sortTh(label, key, sortState, extraAttrs = "") {
  return `<th data-sort-key="${key}"${extraAttrs}>${escapeHtml(label)}${sortIndicator(sortState, key)}</th>`;
}
// First click on a column sorts ascending; clicking the SAME column again
// flips direction - "team" (name) is the only column defaulting to alpha via
// localeCompare, everything else is numeric.
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

// "ALL" is not a real position: it is the TOTAL of every offensive position
// (QB+RB+WR+TE+K - whichever the league has), so a team's overall fantasy
// points allowed. DST is excluded: its data is keyed by the OFFENSE (points
// the opposing D/ST scored against it), a different side of the matchup that
// can't be added to what a defense allows. Because every position's number is
// per-game over the same games, the total of per-game averages (and of the
// +/- differences) is exactly the average of the per-game totals.
const ALL_POS = "ALL";
const offensePositionsOf = (data) => Object.keys(data.matchups).filter((p) => p !== "_expected" && p !== "DST" && p !== ALL_POS);

// points_against.json-shaped {current, prior} for ALL: per defense/week, the
// sum of every offensive position's points (null if ANY position still has no
// points that week - i.e. a future week), with that week's opponent.
function allPositionsDetail(data) {
  const out = { current: {}, prior: {} };
  const positions = offensePositionsOf(data);
  ["current", "prior"].forEach((key) => {
    const teams = new Set();
    positions.forEach((p) => Object.keys(((data.pointsAgainst || {})[p] || {})[key] || {}).forEach((t) => teams.add(t)));
    teams.forEach((t) => {
      const weeks = new Set();
      positions.forEach((p) => Object.keys((((data.pointsAgainst || {})[p] || {})[key] || {})[t] || {}).forEach((w) => weeks.add(w)));
      const byWeek = {};
      weeks.forEach((w) => {
        let total = 0;
        let complete = true;
        let opponent = null;
        let home = null;
        positions.forEach((p) => {
          const e = ((((data.pointsAgainst || {})[p] || {})[key] || {})[t] || {})[w];
          if (!e) return;
          if (e.points === null || e.points === undefined) complete = false;
          else total += e.points;
          if (opponent === null && e.opponent) {
            opponent = e.opponent;
            home = e.home;
          }
        });
        byWeek[w] = { points: complete ? Math.round(total * 10) / 10 : null, opponent, home, stats: {}, players: [] };
      });
      out[key][t] = byWeek;
    });
  });
  return out;
}

// recent_results.by_position-shaped {team: {current, prior, pa_factor,
// opp_avg_excl}} for ALL (feeds the PA by Week table). pa_factor is chosen so
// that rawAvg * pa_factor equals the SUM of each position's own adjusted
// average - the existing "Avg Adjusted" formula then needs no special case.
function allPositionsByWeek(data) {
  const byPos = (data.recentResults || { by_position: {} }).by_position;
  const positions = offensePositionsOf(data);
  const teams = new Set();
  positions.forEach((p) => Object.keys(byPos[p] || {}).forEach((t) => teams.add(t)));
  const mean = (obj) => {
    const v = Object.values(obj);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : 0;
  };
  const out = {};
  teams.forEach((t) => {
    const entry = { current: {}, prior: {}, pa_factor: 1.0, opp_avg_excl: 0 };
    ["current", "prior"].forEach((key) => {
      const weeks = new Set();
      positions.forEach((p) => Object.keys((((byPos[p] || {})[t] || {})[key]) || {}).forEach((w) => weeks.add(w)));
      weeks.forEach((w) => {
        entry[key][w] = positions.reduce((a, p) => a + (((((byPos[p] || {})[t] || {})[key]) || {})[w] ?? 0), 0);
      });
    });
    const useKey = Object.keys(entry.current).length ? "current" : "prior";
    let raw = 0;
    let adj = 0;
    positions.forEach((p) => {
      const pt = (byPos[p] || {})[t];
      if (!pt) return;
      const avg = mean(pt[useKey] || {});
      raw += avg;
      adj += avg * (pt.pa_factor ?? 1.0);
      entry.opp_avg_excl += pt.opp_avg_excl ?? 0;
    });
    entry.pa_factor = raw > 0 ? adj / raw : 1.0;
    out[t] = entry;
  });
  return out;
}

// Small line-graph icon next to a team name: opens the PA Chart view for that
// team at the position currently selected (see renderMatchups' wiring).
function chartIconHtml(team) {
  return `<span class="chart-icon" data-chart-team="${escapeHtml(team)}" role="button" tabindex="0" title="Weekly points-against chart" aria-label="Weekly points-against chart for ${escapeHtml(team)}"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 2v12h12"/><path d="M4.5 10.5l3-3.5 2.5 2 3.5-5"/></svg></span>`;
}

// Expected-source twin of recent_results' opp_avg_excl, which only exists for ACTUAL points.
// matchups.json's pa_factor is league_avg / opp_avg_excl for whichever points it was built from
// (engine/matchups.py), and the league average is just the mean of every team's allowed_ppg - so
// the opponents' average is recoverable as league_avg / pa_factor (verified against the actual
// data's opp_avg_excl to within 0.002). Lets Opp-Adjusted (+/-) work on the Expected source too.
function expectedOppAvg(source, pos, team) {
  const byTeam = source[pos] || {};
  const m = byTeam[team];
  if (!m || !m.pa_factor) return null;
  const vals = Object.values(byTeam).map((x) => x.allowed_ppg).filter((v) => v !== null && v !== undefined);
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length / m.pa_factor;
}

// All positions at once: one row per team, one column per position, each
// cell stacking that position's points-allowed figure over its rank - same
// visual language as strength.js's positionValueLeagueTable (total + a
// dimmer sub-line via .heat-sub). Basis (season/last-5) and Type
// (raw/opp-adjusted) both recompute the value AND its rank/color together -
// rank is never the site-wide precomputed blended-index rank, always
// recomputed HERE from whichever value is currently showing, so the numbers
// on screen and the rank/color next to them always agree with each other.
// "Opp-Adjusted" reuses the same season-long pa_factor recentResultsTable's
// own Avg Adjusted column multiplies by (raw * pa_factor) - there's no
// separate last-5-scoped factor computed server-side, so Last 5 + Opp-
// Adjusted applies that same season factor to the l5 raw number, same
// "single season-long factor, whichever average it's scaling" convention
// the rest of the tab already uses. Clicking any cell still opens the full
// points-against detail (including the real blended index) via
// openPointsAgainstModal.
function paByPositionTable(matchups, positions, basis, type, sortState, oppAvgFor = () => null) {
  const teams = new Set();
  positions.forEach((pos) => Object.keys(matchups[pos] || {}).forEach((t) => teams.add(t)));
  const fptsFor = (pos, team) => {
    const m = (matchups[pos] || {})[team];
    if (!m) return null;
    const raw = basis === "l5" ? m.l5_allowed_ppg : m.allowed_ppg;
    if (raw === null || raw === undefined) return null;
    if (type === "adj") return raw * (m.pa_factor ?? 1.0);
    // "add": points allowed minus the points those opponents normally score
    // at the position (their other games) - "+3.2" = gives up 3.2 more than
    // the offenses it faced usually score. Same season-long Opp Avg the
    // multiplicative factor is built from, applied to whichever basis is showing.
    if (type === "add") {
      const opp = oppAvgFor(pos, team);
      return opp === null || opp === undefined ? null : raw - opp;
    }
    return raw;
  };
  const showValue = (v) => (type === "add" ? `${v > 0 ? "+" : ""}${fmt(v, 1)}` : fmt(v, 1));
  // The ALL column (right of the positions): the sum of every offensive
  // position's value for the basis/type showing - needs EVERY offensive
  // position the table has data for, so a team missing one isn't under-counted.
  const fptsForPos = fptsFor;
  const offense = positions.filter((p) => p !== "DST" && p !== ALL_POS);
  const offenseWithData = offense.filter((p) => [...teams].some((t) => fptsForPos(p, t) !== null));
  const fptsAll = (team) => {
    if (!offenseWithData.length) return null;
    let total = 0;
    for (const p of offenseWithData) {
      const v = fptsForPos(p, team);
      if (v === null || v === undefined) return null;
      total += v;
    }
    return total;
  };
  const cols = offenseWithData.length ? [...positions, ALL_POS] : positions;
  const fpts = (pos, team) => (pos === ALL_POS ? fptsAll(team) : fptsForPos(pos, team));
  const rankByPos = {};
  cols.forEach((pos) => {
    const ranked = [...teams]
      .map((t) => ({ t, v: fpts(pos, t) }))
      .filter((x) => x.v !== null && x.v !== undefined)
      .sort((a, b) => b.v - a.v);
    rankByPos[pos] = new Map(ranked.map((x, i) => [x.t, i + 1]));
  });
  const totalFor = (t) => positions.reduce((acc, pos) => acc + (fptsFor(pos, t) || 0), 0);

  const valueFor = (t, key) => (key === "team" ? t : fpts(key, t));
  const defaultOrder = [...teams].sort((a, b) => totalFor(b) - totalFor(a));
  const sortedTeams = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;

  const header = `<tr>${sortTh("Team", "team", sortState)}${cols.map((p) => sortTh(p, p, sortState, p === ALL_POS ? ` title="Total across QB+RB+WR+TE+K (D/ST excluded)"` : "")).join("")}</tr>`;
  const rows = sortedTeams
    .map((t) => {
      const cells = cols
        .map((pos) => {
          const v = fpts(pos, t);
          const rank = rankByPos[pos].get(t);
          if (v === null || v === undefined || rank === undefined) return `<td class="muted cell-center">-</td>`;
          const ratio = ratioForRank(rank, teams.size);
          // ALL has no points-against detail to open, so its cells aren't clickable.
          const clickAttrs = pos === ALL_POS ? 'class="heat-cell"' : `data-team="${escapeHtml(t)}" data-pos="${pos}" class="heat-cell clickable-row"`;
          return `<td ${clickAttrs} style="background:${colorForRatio(ratio)}">
            <div>${showValue(v)}</div>
            <div class="heat-sub">#${rank}</div>
          </td>`;
        })
        .join("");
      return `<tr><td>${escapeHtml(t)} ${chartIconHtml(t)}</td>${cells}</tr>`;
    })
    .join("");
  return `<table><thead>${header}</thead><tbody>${rows}</tbody></table>`;
}

function paByWeekTable(byPositionForPos, sortState, clickable = true) {
  const teams = Object.keys(byPositionForPos).sort();
  const anyCurrent = teams.some((t) => Object.keys(byPositionForPos[t].current).length > 0);
  const key = anyCurrent ? "current" : "prior";
  const allWeeks = new Set();
  teams.forEach((t) => Object.keys(byPositionForPos[t][key]).forEach((w) => allWeeks.add(Number(w))));
  const weeks = [...allWeeks].sort((a, b) => a - b);

  if (!weeks.length) {
    return `<p class="muted small">No weekly results available yet.</p>`;
  }

  // Weekly cells always show raw actuals; Raw/Opp/Adjusted are all shown as
  // their own leading columns instead of a toggle - Opp Avg and the factor
  // behind Avg Adjusted are both single season-long numbers, not recomputed
  // per week.
  const rawAvgByTeam = {};
  const adjAvgByTeam = {};
  const addAvgByTeam = {};
  teams.forEach((t) => {
    const vals = weeks.map((w) => byPositionForPos[t][key][String(w)] ?? 0);
    const rawAvg = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
    rawAvgByTeam[t] = rawAvg;
    adjAvgByTeam[t] = rawAvg * (byPositionForPos[t].pa_factor ?? 1.0);
    addAvgByTeam[t] = rawAvg - (byPositionForPos[t].opp_avg_excl ?? 0);
  });
  const leagueAverage = Object.values(rawAvgByTeam).reduce((a, b) => a + b, 0) / (teams.length || 1);

  const valueFor = (t, k) => {
    if (k === "team") return t;
    if (k === "avg_raw") return rawAvgByTeam[t];
    if (k === "opp_avg") return byPositionForPos[t].opp_avg_excl ?? 0;
    if (k === "avg_adj") return adjAvgByTeam[t];
    if (k === "avg_add") return addAvgByTeam[t];
    return byPositionForPos[t][key][String(k)];
  };
  const defaultOrder = teams.slice().sort((a, b) => adjAvgByTeam[b] - adjAvgByTeam[a]);
  const sortedTeams = sortState.key ? sortRows(defaultOrder, sortState, valueFor) : defaultOrder;

  // Rank = a team's place in the order the table is CURRENTLY sorted by (the default order - Avg
  // Adjusted, highest first - until a header is clicked). Teams tied on the sorted value, compared at
  // the precision the table shows, share the rank of the topmost one: it is printed on that row and the
  // rows below it in the tie are left blank. Bye weeks (no value) tie with each other.
  const rankKey = sortState.key || "avg_adj";
  const rankValue = (t) => {
    const v = valueFor(t, rankKey);
    return typeof v === "number" && !Number.isNaN(v) ? Math.round(v * 10) / 10 : v === undefined ? null : v;
  };
  const rankByTeam = new Map();
  sortedTeams.forEach((t, i) => {
    const tied = i > 0 && rankValue(t) === rankValue(sortedTeams[i - 1]);
    rankByTeam.set(t, tied ? "" : i + 1);
  });

  const header = `<tr><th title="Place in the current sort - ties share the topmost team's rank, with the rows below left blank">Rank</th>${sortTh("Team", "team", sortState)}${sortTh("Raw Avg", "avg_raw", sortState)}${sortTh("Opp Avg", "opp_avg", sortState, ` title="What this defense's actual opponents scored on average against everyone ELSE, excluding their own game against this defense - how tough/weak the offenses it actually faced were."`)}${sortTh("Avg Adjusted", "avg_adj", sortState)}${sortTh("vs Opp Avg (+/-)", "avg_add", sortState, ` title="Raw Avg minus Opp Avg: how many more (+) or fewer (-) points per game this defense allows than the offenses it faced normally score at this position."`)}${weeks.map((w) => sortTh(`Wk ${w}`, w, sortState)).join("")}</tr>`;
  const rows = sortedTeams
    .map((t) => {
      const cells = weeks
        .map((w) => {
          const v = byPositionForPos[t][key][String(w)];
          if (v === undefined) return `<td class="muted cell-center">-</td>`;
          const ratio = leagueAverage > 0 ? Math.max(0, Math.min(1, 0.5 + (v - leagueAverage) / leagueAverage)) : 0.5;
          return `<td class="heat-cell" style="background:${colorForRatio(ratio)}">${fmt(v, 1)}</td>`;
        })
        .join("");
      const factorNote = ` <span class="muted small" title="Opponent-adjustment factor: league average / Opp Avg - Raw Avg &times; this = Avg Adjusted">(&times;${fmt(byPositionForPos[t].pa_factor ?? 1.0, 2)})</span>`;
      // ALL has no per-position detail to open, so its rows aren't clickable (the chart icon still works).
      return `<tr ${clickable ? `data-team="${escapeHtml(t)}" class="clickable-row"` : ""}><td class="cell-center">${rankByTeam.get(t)}</td><td>${escapeHtml(t)} ${chartIconHtml(t)}</td><td class="cell-center">${fmt(rawAvgByTeam[t], 1)}</td><td class="cell-center">${fmt(byPositionForPos[t].opp_avg_excl ?? 0, 1)}${factorNote}</td><td class="cell-center"><strong>${fmt(adjAvgByTeam[t], 1)}</strong></td><td class="cell-center"><strong>${addAvgByTeam[t] > 0 ? "+" : ""}${fmt(addAvgByTeam[t], 1)}</strong></td>${cells}</tr>`;
    })
    .join("");
  return `<table>${header}<tbody>${rows}</tbody></table>`;
}

// Per-week chart data for one defense at one position: points it allowed
// (from points_against.json, the same weekly rows the points-against modal
// shows) vs. the average of the offense it faced that week in that offense's
// OTHER games at this position (leave-one-out, same definition as the PA by
// Week table's "Opp Avg" - a defense never gets graded against its own game).
// A unit's weekly output at the position is read off the same rows: it is
// every entry across all defenses whose `opponent` is that unit.
function chartSeriesFor(team, position, data, mode = "defense") {
  const detail = position === ALL_POS ? allPositionsDetail(data) : (data.pointsAgainst || {})[position] || { current: {}, prior: {} };
  const hasCurrent = Object.values(detail.current || {}).some((weeks) => Object.keys(weeks).length > 0);
  const bySeason = hasCurrent ? detail.current : detail.prior || {};
  const season = hasCurrent ? data.recentResults?.current_season : data.recentResults?.prior_season;
  const unitWeeks = {};
  Object.values(bySeason).forEach((weeks) => {
    Object.entries(weeks).forEach(([w, e]) => {
      if (!e.opponent || e.points === null || e.points === undefined) return;  // future weeks carry no points yet
      (unitWeeks[e.opponent] = unitWeeks[e.opponent] || {})[w] = e.points;
    });
  });
  if (mode === "offense") {
    // The same rows from the OTHER side: what this team's position group
    // scored each week (every defense's entry whose opponent is this team)
    // vs. the average that week's opposing defense allows at the position in
    // its other games (leave-one-out, same convention as the defense view).
    const rows = [];
    Object.entries(bySeason).forEach(([defense, weeksMap]) => {
      Object.entries(weeksMap).forEach(([w, e]) => {
        if (e.opponent === team) rows.push({ week: Number(w), defense, points: e.points });
      });
    });
    rows.sort((a, b) => a.week - b.week);
    const defAvg = rows.map((r) => {
      const others = Object.entries(bySeason[r.defense] || {})
        .filter(([w, e]) => Number(w) !== r.week && e.points !== null && e.points !== undefined)
        .map(([, e]) => e.points);
      return others.length ? others.reduce((a, b) => a + b, 0) / others.length : null;
    });
    return {
      weeks: rows.map((r) => r.week), points: rows.map((r) => r.points), opponents: rows.map((r) => r.defense),
      oppAvg: defAvg, season, teams: Object.keys(bySeason).filter((t) => Object.keys(bySeason[t]).length).sort(),
    };
  }
  const mine = bySeason[team] || {};
  const weeks = Object.keys(mine).map(Number).sort((a, b) => a - b);
  const points = weeks.map((w) => mine[w].points);
  const opponents = weeks.map((w) => mine[w].opponent);
  const oppAvg = weeks.map((w, i) => {
    const others = Object.entries(unitWeeks[opponents[i]] || {}).filter(([k]) => Number(k) !== w).map(([, v]) => v);
    return others.length ? others.reduce((a, b) => a + b, 0) / others.length : null;
  });
  return { weeks, points, opponents, oppAvg, season, teams: Object.keys(bySeason).filter((t) => Object.keys(bySeason[t]).length).sort() };
}

// Schedule strength from the OFFENSE's side: for each team's position group,
// the average of the opposing defenses' season-long numbers over a window of
// weeks - already faced, the rest of the regular fantasy season, or the
// fantasy playoffs. Per defense the number is its raw points allowed per game
// at the position, or that minus what the offenses it faced normally score
// (the PA tab's +/- type). Rank 1 = softest schedule. For DST (keyed by
// offense in the data) the row is the team's defense, facing offenses.
// Weeks covered by a PA Schedule window: already completed NFL weeks, the rest of the regular fantasy
// season, or the fantasy playoffs.
function scheduleWindow(meta, windowKey) {
  return {
    faced: [1, meta.weeks_complete ?? 0],
    reg: [(meta.weeks_complete ?? 0) + 1, meta.reg_season_count],
    playoffs: [meta.reg_season_count + 1, meta.final_week],
  }[windowKey];
}

// A defense's number for the PA Schedule views: its raw points allowed per game at a position, or that
// minus what the offenses it faced normally score ("+/-"). ALL = the sum over every offensive position.
function scheduleMetric(data, metric) {
  const byPos = (data.recentResults || { by_position: {} }).by_position;
  const valueFor = (pos, rated) => {
    const m = (data.matchups[pos] || {})[rated];
    if (!m || m.allowed_ppg === null || m.allowed_ppg === undefined) return null;
    if (metric === "raw") return m.allowed_ppg;
    const opp = ((byPos[pos] || {})[rated] || {}).opp_avg_excl;
    return opp === null || opp === undefined ? null : m.allowed_ppg - opp;
  };
  const offense = offensePositionsOf(data);
  const valueForAll = (rated) => {
    let total = 0;
    for (const p of offense) {
      const v = valueFor(p, rated);
      if (v === null) return null;
      total += v;
    }
    return offense.length ? total : null;
  };
  return { valueFor, valueForAll, offense };
}

function scheduleStrengthRows(data, positions, windowKey, metric) {
  const [lo, hi] = scheduleWindow(data.meta, windowKey);
  const { valueFor, valueForAll, offense } = scheduleMetric(data, metric);
  const columns = offense.length ? [...positions, ALL_POS] : positions;
  const table = {}; // team -> {games, avg: {col: value}, rank: {col: rank}}
  columns.forEach((col) => {
    const detail = col === ALL_POS ? allPositionsDetail(data) : (data.pointsAgainst || {})[col] || { current: {}, prior: {} };
    const hasCurrent = Object.values(detail.current || {}).some((w) => Object.keys(w).length > 0);
    const bySeason = hasCurrent ? detail.current : detail.prior || {};
    const vals = {}; // unit team -> [value per week in window]
    Object.entries(bySeason).forEach(([rated, weeks]) => {
      Object.entries(weeks).forEach(([w, e]) => {
        const wk = Number(w);
        if (!e.opponent || wk < lo || wk > hi) return;
        const v = col === ALL_POS ? valueForAll(rated) : valueFor(col, rated);
        if (v === null) return;
        (vals[e.opponent] = vals[e.opponent] || []).push(v);
      });
    });
    Object.entries(vals).forEach(([team, list]) => {
      const row = (table[team] = table[team] || { team, games: 0, avg: {}, rank: {} });
      row.avg[col] = list.reduce((a, b) => a + b, 0) / list.length;
      row.games = Math.max(row.games, list.length);
    });
    Object.values(table)
      .filter((r) => r.avg[col] !== undefined)
      .sort((a, b) => b.avg[col] - a.avg[col])
      .forEach((r, i) => (r.rank[col] = i + 1));
  });
  return { rows: Object.values(table), columns, lo, hi };
}

// One position's schedule week by week - the Rankings Schedule tab's opponent grid, but for every team's
// position group: each cell is the opponent (@ = away) with that defense's number (+/- or raw points
// allowed per game, per the Metric) and its rank among the defenses at the position (1 = softest, i.e.
// allows the most), colored by that rank. A bye is a muted BYE. Avg = the window's average number.
function scheduleWeeklyGrid(data, pos, windowKey, metric, sortState) {
  const { valueFor, valueForAll } = scheduleMetric(data, metric);
  const [lo, hi] = scheduleWindow(data.meta, windowKey);
  if (hi < lo) return `<p class="muted small">No games in this window.</p>`;
  const detail = pos === ALL_POS ? allPositionsDetail(data) : (data.pointsAgainst || {})[pos] || { current: {}, prior: {} };
  const hasCurrent = Object.values(detail.current || {}).some((w) => Object.keys(w).length > 0);
  const bySeason = hasCurrent ? detail.current : detail.prior || {};
  const metricOf = (rated) => (pos === ALL_POS ? valueForAll(rated) : valueFor(pos, rated));

  // Every defense ranked at this position by the number: 1 = highest = softest matchup.
  const ranked = Object.keys(bySeason)
    .map((t) => ({ t, v: metricOf(t) }))
    .filter((x) => x.v !== null)
    .sort((a, b) => b.v - a.v);
  const rankOf = new Map(ranked.map((x, i) => [x.t, i + 1]));
  const valueOfDef = new Map(ranked.map((x) => [x.t, x.v]));
  const n = ranked.length;

  // Each team's own schedule (it is the "unit" in a defense's entry: entry.opponent). `home` on an entry is the
  // DEFENSE's home flag, so the unit is the road team exactly when it is true.
  const sched = {};
  Object.entries(bySeason).forEach(([rated, weeks]) => {
    if (!rankOf.has(rated)) return;
    Object.entries(weeks).forEach(([w, e]) => {
      const wk = Number(w);
      if (!e.opponent || wk < lo || wk > hi) return;
      (sched[e.opponent] = sched[e.opponent] || {})[wk] = { opp: rated, away: e.home === true, value: valueOfDef.get(rated), rank: rankOf.get(rated) };
    });
  });
  const weeks = [];
  for (let w = lo; w <= hi; w++) weeks.push(w);
  const rows = Object.entries(sched).map(([team, byWeek]) => {
    const vals = Object.values(byWeek).map((c) => c.value);
    return { team, byWeek, avg: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null };
  });
  rows.filter((r) => r.avg !== null).sort((a, b) => b.avg - a.avg).forEach((r, i) => (r.avgRank = i + 1));

  const valueFor2 = (r, key) => (key === "team" ? r.team : key === "avg" ? r.avg : r.byWeek[key]?.value ?? null);
  const ordered = rows.slice().sort((a, b) => (a.avgRank ?? 99) - (b.avgRank ?? 99));
  const sorted = sortState.key ? sortRows(ordered, sortState, valueFor2) : ordered;
  const fmtV = (v) => (metric === "diff" ? `${v > 0 ? "+" : ""}${fmt(v, 1)}` : fmt(v, 1));
  const header = `<tr>${sortTh("Team", "team", sortState)}${sortTh("Avg", "avg", sortState, ` title="Average over the window's games"`)}${weeks.map((w) => sortTh(`Wk ${w}`, w, sortState)).join("")}</tr>`;
  const body = sorted
    .map((r) => {
      const avgCell = r.avg === null ? `<td class="muted cell-center">-</td>` : `<td class="heat-cell" style="background:${colorForRatio(ratioForRank(r.avgRank, rows.length))}"><div>${fmtV(r.avg)}</div><div class="heat-sub">#${r.avgRank}</div></td>`;
      const cells = weeks
        .map((w) => {
          const c = r.byWeek[w];
          if (!c) return `<td class="muted cell-center">BYE</td>`;
          const tip = `Wk ${w}: ${c.away ? "at" : "vs"} ${c.opp} - allows ${fmtV(c.value)} (${metric === "diff" ? "+/- vs expected" : "pts/g"}), #${c.rank} of ${n}`;
          // Click the opponent's cell -> that defense's points-against modal for this position (none exists for ALL).
          const clickAttrs = pos === ALL_POS ? 'class="heat-cell ros-cell"' : `class="heat-cell ros-cell clickable-row" data-team="${escapeHtml(c.opp)}" data-pos="${pos}"`;
          return `<td ${clickAttrs} style="background:${colorForRatio(ratioForRank(c.rank, n))}" title="${escapeHtml(tip)}"><div class="ros-opp">${c.away ? "@" : ""}${escapeHtml(c.opp)}</div><div class="heat-sub">${fmtV(c.value)} &middot; #${c.rank}</div></td>`;
        })
        .join("");
      return `<tr><td>${escapeHtml(r.team)}</td>${avgCell}${cells}</tr>`;
    })
    .join("");
  return `<table><thead>${header}</thead><tbody>${body}</tbody></table>`;
}

function scheduleStrengthTable(data, positions, windowKey, metric, sortState) {
  const { rows, columns, lo, hi } = scheduleStrengthRows(data, positions, windowKey, metric);
  if (!rows.length || hi < lo) return `<p class="muted small">No games in this window.</p>`;
  const valueFor = (r, key) => (key === "team" ? r.team : key === "games" ? r.games : r.avg[key] ?? null);
  // Default order: the ALL rank (softest total schedule first); without ALL, the first position's rank.
  const orderCol = columns.includes(ALL_POS) ? ALL_POS : columns[0];
  const ordered = rows.slice().sort((a, b) => (a.rank[orderCol] ?? 99) - (b.rank[orderCol] ?? 99));
  const sorted = sortState.key ? sortRows(ordered, sortState, valueFor) : ordered;
  const n = rows.length;
  const fmtV = (v) => (metric === "diff" ? `${v > 0 ? "+" : ""}${fmt(v, 1)}` : fmt(v, 1));
  const header = `<tr>${sortTh("Team", "team", sortState)}${sortTh("G", "games", sortState)}${columns.map((p) => sortTh(p, p, sortState, p === ALL_POS ? ` title="Total across QB+RB+WR+TE+K (D/ST excluded)"` : "")).join("")}</tr>`;
  const body = sorted
    .map((r) => {
      const cells = columns
        .map((col) => {
          if (r.avg[col] === undefined) return `<td class="muted cell-center">-</td>`;
          return `<td class="heat-cell" style="background:${colorForRatio(ratioForRank(r.rank[col], n))}"><div>${fmtV(r.avg[col])}</div><div class="heat-sub">#${r.rank[col]}</div></td>`;
        })
        .join("");
      return `<tr><td>${escapeHtml(r.team)}</td><td class="cell-center">${r.games}</td>${cells}</tr>`;
    })
    .join("");
  return `<table><thead>${header}</thead><tbody>${body}</tbody></table>`;
}

export function renderMatchups(container, data) {
  // "_expected" (see engine/pipeline.py's matchups.json build) is a sibling
  // data source, not a real position - excluded from the position list/
  // dropdown the same way lineups.json's "_unrostered_players" is excluded
  // from being treated as a real fantasy team elsewhere on the site.
  const positions = Object.keys(data.matchups).filter((p) => p !== "_expected");
  container.innerHTML = `
    <div class="card">
      <div class="select-row">
        <label>View:</label>
        <select id="matchups-view-select">
          <option value="position">PA by Position</option>
          <option value="week">PA by Week</option>
          <option value="chart">PA Chart</option>
          <option value="schedule">PA Schedule</option>
        </select>
        <span id="matchups-pos-wrap"><label>Position:</label>
          <select id="matchups-pos-select">${[...positions, ...(offensePositionsOf(data).length ? [ALL_POS] : [])].map((p) => `<option value="${p}">${p}</option>`).join("")}</select>
        </span>
        <span id="matchups-team-wrap" hidden><label>Team:</label>
          <select id="matchups-team-select"></select>
          <select id="matchups-chart-mode" title="Which side of the matchup to chart">
            <option value="defense">Defense: points allowed</option>
            <option value="offense">Offense: points scored</option>
          </select>
        </span>
        <span id="matchups-sched-wrap" hidden>
          <label>Position:</label>
          <select id="matchups-sched-pos-select">${[["summary", "All positions"], ...positions.map((p) => [p, p]), ...(offensePositionsOf(data).length ? [[ALL_POS, ALL_POS]] : [])].map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
          <label>Window:</label>
          <select id="matchups-window-select">
            <option value="faced">Faced to date</option>
            <option value="reg">Rest of regular season</option>
            <option value="playoffs">Fantasy playoffs</option>
          </select>
          <label>Metric:</label>
          <select id="matchups-metric-select">
            <option value="diff">+/- vs expected</option>
            <option value="raw">Raw pts allowed/g</option>
          </select>
        </span>
        <span id="matchups-basis-wrap"><label>Basis:</label>
          <select id="matchups-basis-select">
            <option value="season">Season</option>
            <option value="l5">Last 5 weeks</option>
          </select>
        </span>
        <span id="matchups-type-wrap"><label>Type:</label>
          <select id="matchups-type-select">
            <option value="raw">Raw</option>
            <option value="adj">Opp-Adjusted (&times;)</option>
            <option value="add">Opp-Adjusted (+/-)</option>
          </select>
        </span>
        <span id="matchups-source-wrap"><label>Data:</label>
          <select id="matchups-source-select">
            <option value="actual">Actual</option>
            <option value="expected">Expected</option>
          </select>
        </span>
      </div>
      <p id="matchups-help" class="muted small"></p>
      <div class="table-wrap" id="matchups-table-wrap"></div>
    </div>
  `;
  const wrap = container.querySelector("#matchups-table-wrap");
  const help = container.querySelector("#matchups-help");
  const posSelect = container.querySelector("#matchups-pos-select");
  const posWrap = container.querySelector("#matchups-pos-wrap");
  const viewSelect = container.querySelector("#matchups-view-select");
  const basisSelect = container.querySelector("#matchups-basis-select");
  const basisWrap = container.querySelector("#matchups-basis-wrap");
  const typeSelect = container.querySelector("#matchups-type-select");
  const typeWrap = container.querySelector("#matchups-type-wrap");
  const sourceSelect = container.querySelector("#matchups-source-select");
  const sourceWrap = container.querySelector("#matchups-source-wrap");
  const teamSelect = container.querySelector("#matchups-team-select");
  const teamWrap = container.querySelector("#matchups-team-wrap");
  const chartModeSelect = container.querySelector("#matchups-chart-mode");
  const schedWrap = container.querySelector("#matchups-sched-wrap");
  const windowSelect = container.querySelector("#matchups-window-select");
  const schedPosSelect = container.querySelector("#matchups-sched-pos-select");
  let lastSchedKey = null; // position|window - a different one resets the sort (a "Wk 9" key means nothing elsewhere)
  const metricSelect = container.querySelector("#matchups-metric-select");
  let chartInstance = null;
  let pendingChartTeam = null; // set by a table row's chart icon, consumed by the next chart draw

  // One sort state per view - switching views/position/basis/type resets it
  // (a "Wk 4" sort key from PA by Week means nothing on PA by Position), but
  // re-clicking within the same view keeps toggling as expected.
  let sortState = { key: null, dir: 1, lastKey: null };
  let lastView = viewSelect.value;

  const draw = () => {
    const pos = posSelect.value;
    const view = viewSelect.value;
    if (view !== lastView) {
      sortState = { key: null, dir: 1, lastKey: null };
      lastView = view;
    }
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
    posWrap.hidden = view === "position" || view === "schedule";
    teamWrap.hidden = view !== "chart";
    schedWrap.hidden = view !== "schedule";
    basisWrap.hidden = view !== "position";
    typeWrap.hidden = view !== "position";
    sourceWrap.hidden = view !== "position";

    if (view === "chart") {
      const mode = chartModeSelect.value;
      const probe = chartSeriesFor("", pos, data, mode);
      const prevTeam = teamSelect.value;
      teamSelect.innerHTML = probe.teams.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
      if (probe.teams.includes(prevTeam)) teamSelect.value = prevTeam;
      if (pendingChartTeam && probe.teams.includes(pendingChartTeam)) teamSelect.value = pendingChartTeam;
      pendingChartTeam = null;
      const series = chartSeriesFor(teamSelect.value, pos, data, mode);
      help.textContent =
        mode === "offense"
          ? "Points this team's position group scored each week, vs. the average that week's opposing defense allows at the position in its other games - above the dashed line means the offense beat what that defense usually gives up. " +
            (pos === "DST" ? "For DST the team shown is the defense." : "")
          : "Points this defense allowed each week at the position, vs. the average that week's opponent scores at the position in its other games - above the dashed line means the defense gave up more than that offense usually does. " +
            (pos === "DST" ? "For DST the team shown is the offense." : "");
      wrap.innerHTML = series.weeks.length
        ? `<div class="matchups-chart" style="position:relative;height:320px"><canvas id="matchups-chart-canvas"></canvas></div>`
        : `<p class="muted small">No weekly results available yet.</p>`;
      const canvas = wrap.querySelector("#matchups-chart-canvas");
      if (canvas && window.Chart) {
        const cs = getComputedStyle(document.documentElement);
        const ink = cs.getPropertyValue("--ink-700").trim() || "#555";
        const grid = cs.getPropertyValue("--border").trim() || "#ddd";
        // The charted team's own color for its line; the opponent-average line is a neutral gray.
        const teamColor = chartTeamColor(teamSelect.value, cs.getPropertyValue("--card"));
        const avgColor = cs.getPropertyValue("--ink-500").trim() || "#7a7a73";
        chartInstance = new window.Chart(canvas.getContext("2d"), {
          type: "line",
          data: {
            labels: series.weeks.map((w, i) => `Wk ${w} ${series.opponents[i] || ""}`),
            datasets: [
              { label: `${teamSelect.value} ${pos} points ${mode === "offense" ? "scored" : "allowed"}`, data: series.points, borderColor: teamColor, backgroundColor: teamColor, tension: 0.2 },
              {
                label: mode === "offense" ? "Opponent defense's avg allowed (their other games)" : "Opponent's season avg (their other games)",
                data: series.oppAvg, borderColor: avgColor, backgroundColor: avgColor, borderDash: [6, 4], tension: 0.2,
              },
            ],
          },
          options: {
            maintainAspectRatio: false,
            color: ink,
            plugins: { legend: { labels: { color: ink } } },
            scales: {
              x: { ticks: { color: ink }, grid: { color: grid } },
              y: { ticks: { color: ink }, grid: { color: grid }, title: { display: true, text: "Fantasy points", color: ink } },
            },
          },
        });
      }
      return;
    }

    if (view === "schedule") {
      help.textContent =
        "Each team's position group's schedule: the average of the opposing defenses' season numbers (+/- vs. what the offenses they faced normally score, or raw points allowed per game) over the window. Rank 1 = softest schedule. For DST the row is the team's defense, facing offenses. Fantasy playoff weeks come from this league's settings.";
      const schedKey = `${schedPosSelect.value}|${windowSelect.value}`;
      if (schedKey !== lastSchedKey) {
        sortState = { key: null, dir: 1, lastKey: null };
        lastSchedKey = schedKey;
      }
      if (schedPosSelect.value === "summary") {
        wrap.innerHTML = scheduleStrengthTable(data, positions, windowSelect.value, metricSelect.value, sortState);
      } else {
        help.textContent =
          "Each team's position group, week by week: the opponent (@ = away), that defense's number (+/- vs. what the offenses it faces normally score, or raw points allowed per game) and its rank among defenses at the position - 1 = softest matchup. Avg is the window's average. BYE = no game. Click a cell for that defense's points-against detail. Pick All positions for the summary table.";
        wrap.innerHTML = scheduleWeeklyGrid(data, schedPosSelect.value, windowSelect.value, metricSelect.value, sortState);
      }
    } else if (view === "week") {
      help.textContent = '"Opp Avg" is what this defense\'s opponents scored on average against everyone ELSE (excluding their own game against this defense) - "Adjusted" scales Raw Avg by that same strength-of-opponent factor.';
      wrap.innerHTML =
        pos === ALL_POS
          ? paByWeekTable(allPositionsByWeek(data), sortState, false)
          : paByWeekTable((data.recentResults || { by_position: {} }).by_position[pos] || {}, sortState);
    } else {
      // "Expected" reads matchups.json's "_expected" sibling (see
      // engine/pipeline.py) - built the same way as the real table, just fed
      // ffopportunity's expected-stat rows instead of actual ones. It has no
      // K/DST entries at all (no ffopportunity model for either - see
      // engine/expected_points.py) - those columns simply render as "-" via
      // paByPositionTable's own existing missing-data handling, same as any
      // other position/team with no data this week.
      help.textContent =
        sourceSelect.value === "expected"
          ? "1 = easiest matchup, higher = tougher (expected, based on opponent-faced opportunity - no kicker/DST model exists, so those columns show no data)"
          : "1 = easiest matchup, higher = tougher" +
            (typeSelect.value === "add" ? ". Opp-Adjusted (+/-) = points allowed minus what the offenses faced normally score at the position (their other games); +3.2 means this defense gives up 3.2 more per game than expected." : "");
      const source = sourceSelect.value === "expected" ? data.matchups._expected || {} : data.matchups;
      // The opponents' average each team's +/- is measured against: the actual Opp Avg for the Actual
      // source; for Expected, recovered from that source's own pa_factor (see expectedOppAvg).
      const oppAvgFor = (p, t) =>
        sourceSelect.value === "expected"
          ? expectedOppAvg(source, p, t)
          : (((data.recentResults || { by_position: {} }).by_position[p] || {})[t] || {}).opp_avg_excl ?? null;
      wrap.innerHTML = paByPositionTable(source, positions, basisSelect.value, typeSelect.value, sortState, oppAvgFor);
    }

    wrap.querySelectorAll("tr[data-team], td[data-team]").forEach((el) => {
      el.addEventListener("click", () => {
        const team = el.dataset.team;
        const p = el.dataset.pos || pos;
        if (team) openPointsAgainstModal(team, p, data);
      });
    });
    // Chart icon: jump to the PA Chart for this team at the position now selected.
    wrap.querySelectorAll("[data-chart-team]").forEach((icon) => {
      const go = (e) => {
        e.stopPropagation();
        pendingChartTeam = icon.dataset.chartTeam;
        viewSelect.value = "chart";
        draw();
      };
      icon.addEventListener("click", go);
      icon.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          go(e);
        }
      });
    });
    wrap.querySelectorAll("th[data-sort-key]").forEach((th) => {
      th.addEventListener("click", () => {
        toggleSort(sortState, th.dataset.sortKey);
        draw();
      });
    });
  };
  posSelect.addEventListener("change", draw);
  viewSelect.addEventListener("change", draw);
  basisSelect.addEventListener("change", draw);
  typeSelect.addEventListener("change", draw);
  sourceSelect.addEventListener("change", draw);
  teamSelect.addEventListener("change", draw);
  chartModeSelect.addEventListener("change", draw);
  windowSelect.addEventListener("change", draw);
  schedPosSelect.addEventListener("change", draw);
  metricSelect.addEventListener("change", draw);
  draw();
}
