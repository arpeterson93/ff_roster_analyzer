// Client-side twin of engine/standings.py's simulate_playoffs, trimmed to what
// the Trade Calculator needs: "what would each team's playoff/bye odds be if
// this trade happened?". Loaded as a plain (non-module) script, like trade.js,
// so it also runs standalone under Node for tests (tests/test_playoff_odds_js.py).
//
// How a trade enters the sim: the nightly pipeline already publishes every
// team's per-week lineup mean + SD (lineups.json), the real records
// (standings.json) and the remaining schedule (schedule.json) - exactly the
// inputs simulate_playoffs ran on. A trade just shifts the two teams' weekly
// means by the same per-week deltas the points comparison shows
// (evaluateTradeWithStreaming's weekly[].delta); SDs are left alone.
//
// Noise control: the "before" and "after" runs use the SAME random draws
// (common random numbers), so a 1-2 point change in odds is a real effect of
// the trade, not Monte Carlo jitter - absolute odds can wobble a little vs the
// Standings page (different RNG, fewer iterations), the DIFFERENCE is stable.

var ODDS_DEFAULT_ITERATIONS = 10000;
var ODDS_DEFAULT_SEED = 1234;

function oddsBracketByeCount(playoffTeamCount) {
  if (playoffTeamCount <= 1) return 0;
  var size = 1;
  while (size < playoffTeamCount) size *= 2;
  return size - playoffTeamCount;
}

// Port of ingest/settings_sheet.py's parse_seeding_config. Returns
// { divisionOrder: [..], seedConfigs: {n: {divisionPriority, tiebreakOrder}} }.
function oddsParseSeedingConfig(raw) {
  raw = raw || {};
  var divisionOrder = [];
  for (var i = 1; i <= 4; i++) if (raw["division_tiebreak_" + i]) divisionOrder.push(raw["division_tiebreak_" + i]);
  var seedNumbers = {};
  Object.keys(raw).forEach(function (k) {
    var m = /^seed_(\d+)_(division_priority|tiebreak_[1-4])$/.exec(k);
    if (m) seedNumbers[Number(m[1])] = true;
  });
  var seedConfigs = {};
  Object.keys(seedNumbers).forEach(function (nStr) {
    var n = Number(nStr);
    var dp = String(raw["seed_" + n + "_division_priority"] == null ? "" : raw["seed_" + n + "_division_priority"]).trim().toLowerCase();
    var order = [];
    for (var j = 1; j <= 4; j++) if (raw["seed_" + n + "_tiebreak_" + j]) order.push(raw["seed_" + n + "_tiebreak_" + j]);
    seedConfigs[n] = { divisionPriority: dp === "true" || dp === "1" || dp === "yes", tiebreakOrder: order };
  });
  return { divisionOrder: divisionOrder, seedConfigs: seedConfigs };
}

// Assemble the sim's inputs from the already-loaded site data (the same
// objects data.js returns). Returns null when there's nothing to simulate
// (offseason, regular season over, or the files aren't there).
function oddsBuildInputs(parts) {
  var meta = parts.meta, standings = parts.standings, schedule = parts.schedule, lineups = parts.lineups, live = parts.live;
  if (!meta || !standings || !standings.length || !schedule || !lineups) return null;
  var regCount = meta.reg_season_count;
  var currentWeek = meta.current_week;
  var settings = meta.settings || {};

  var teams = standings.map(function (s) {
    return {
      id: s.team_id, division: s.division, wins: (s.wins || 0) + 0.5 * (s.ties || 0),
      pf: s.points_for || 0, pa: s.points_against || 0,
    };
  });
  var teamIdx = {};
  teams.forEach(function (t, i) { teamIdx[t.id] = i; });

  var games = [];
  schedule.forEach(function (m) {
    if (m.played || m.week > regCount) return;
    if (teamIdx[m.home_team_id] === undefined || teamIdx[m.away_team_id] === undefined) return;
    games.push({ week: m.week, home: m.home_team_id, away: m.away_team_id });
  });
  if (!games.length) return null;
  games.sort(function (a, b) { return a.week - b.week; });

  // mean[teamId][week], sd[teamId][week] - from lineups.json, with the
  // in-progress current week replaced by the live blend (live.json) exactly
  // like engine/standings_stage.py's sim_team_week_mean override.
  var mean = {}, sd = {};
  teams.forEach(function (t) {
    mean[t.id] = {}; sd[t.id] = {};
    var entry = lineups[String(t.id)];
    if (!entry || !entry.weeks) return;
    Object.keys(entry.weeks).forEach(function (w) {
      var we = entry.weeks[w];
      if (we && typeof we.total === "number") { mean[t.id][Number(w)] = we.total; sd[t.id][Number(w)] = we.sd || 0; }
    });
  });
  var liveWeek = null;
  if (live && live.week === currentWeek && live.teams) {
    liveWeek = currentWeek;
    teams.forEach(function (t) {
      var lt = live.teams[String(t.id)];
      if (lt && typeof lt.mean === "number") { mean[t.id][currentWeek] = lt.mean; sd[t.id][currentWeek] = lt.sd || 0; }
    });
  }

  var parsed = oddsParseSeedingConfig(settings.seeding_raw);
  var divisionOrder = parsed.divisionOrder.length ? parsed.divisionOrder : ["wins", "points_for"];
  var playoffTeamCount = meta.playoff_team_count;
  var divisionNames = {};
  teams.forEach(function (t) { divisionNames[t.division] = true; });
  var seedConfigs = parsed.seedConfigs;
  if (!Object.keys(seedConfigs).length) {
    var divisionCount = Object.keys(divisionNames).length;
    seedConfigs = {};
    for (var n = 1; n <= playoffTeamCount; n++) {
      seedConfigs[n] = { divisionPriority: settings.division_winners_first !== false && n <= divisionCount, tiebreakOrder: ["wins", "points_for"] };
    }
  }
  return {
    teams: teams, games: games, mean: mean, sd: sd, liveWeek: liveWeek,
    playoffTeamCount: playoffTeamCount, byeSeeds: oddsBracketByeCount(playoffTeamCount),
    divisionOrder: divisionOrder.filter(function (c) { return c !== "head_to_head"; }),
    seedConfigs: seedConfigs,
  };
}

// --- seeded standard-normal stream (mulberry32 + Box-Muller) ---
function oddsRng(seed) {
  var a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) >>> 0;
    var t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

var _oddsZCache = { key: null, z: null };
function oddsNormals(count, seed) {
  var key = count + ":" + seed;
  if (_oddsZCache.key === key) return _oddsZCache.z;
  var rng = oddsRng(seed);
  var z = new Float32Array(count);
  for (var i = 0; i < count; i += 2) {
    var u1 = rng() || 1e-12, u2 = rng();
    var r = Math.sqrt(-2 * Math.log(u1));
    z[i] = r * Math.cos(2 * Math.PI * u2);
    if (i + 1 < count) z[i + 1] = r * Math.sin(2 * Math.PI * u2);
  }
  _oddsZCache = { key: key, z: z };
  return z;
}

// Is team i ahead of team j under `order` (higher wins/PF/PA first, lower
// index on a full tie - same deterministic fallback as the Python key)?
function oddsAhead(i, j, order, w, p, a) {
  for (var k = 0; k < order.length; k++) {
    var c = order[k], vi, vj;
    if (c === "wins") { vi = w[i]; vj = w[j]; }
    else if (c === "points_for") { vi = p[i]; vj = p[j]; }
    else if (c === "points_against") { vi = a[i]; vj = a[j]; }
    else continue;
    if (vi !== vj) return vi > vj;
  }
  return i < j;
}

function oddsBest(candidates, order, w, p, a) {
  var best = candidates[0];
  for (var k = 1; k < candidates.length; k++) if (oddsAhead(candidates[k], best, order, w, p, a)) best = candidates[k];
  return best;
}

// One simulated season's seeds (team indexes, seed 1 first) - port of
// simulate_playoffs' seed_one_season (seed_configs branch).
function oddsSeedSeason(inp, divMembers, w, p, a) {
  var n = inp.teams.length;
  var divWinners = divMembers.map(function (members) { return oddsBest(members, inp.divisionOrder, w, p, a); });
  var dwQueue = divWinners.slice().sort(function (x, y) { return oddsAhead(x, y, inp.divisionOrder, w, p, a) ? -1 : 1; });
  var assigned = {};
  var seeds = [];
  for (var seedN = 1; seedN <= inp.playoffTeamCount; seedN++) {
    var cfg = inp.seedConfigs[seedN];
    while (dwQueue.length && assigned[dwQueue[0]]) dwQueue.shift();
    var chosen;
    if (cfg && cfg.divisionPriority && dwQueue.length) {
      chosen = dwQueue.shift();
    } else {
      var pool = [];
      for (var i = 0; i < n; i++) if (!assigned[i]) pool.push(i);
      if (!pool.length) break;
      var order = (cfg && cfg.tiebreakOrder.length ? cfg.tiebreakOrder : inp.divisionOrder).filter(function (c) { return c !== "head_to_head"; });
      if (!order.length) order = inp.divisionOrder;
      chosen = oddsBest(pool, order, w, p, a);
    }
    seeds.push(chosen);
    assigned[chosen] = true;
  }
  return seeds;
}

// adjustments: {teamId: {week: deltaMean}} (optional). Returns
// {teamId: {playoff, bye, expectedWins}}.
function oddsSimulate(inp, adjustments, opts) {
  opts = opts || {};
  var iterations = opts.iterations || ODDS_DEFAULT_ITERATIONS;
  var seed = opts.seed === undefined ? ODDS_DEFAULT_SEED : opts.seed;
  adjustments = adjustments || {};
  var teams = inp.teams, n = teams.length, games = inp.games;
  var idx = {};
  teams.forEach(function (t, i) { idx[t.id] = i; });
  var z = oddsNormals(games.length * 2 * iterations, seed);

  var divKeys = [], divMembers = [];
  teams.forEach(function (t, i) {
    var d = divKeys.indexOf(t.division);
    if (d < 0) { divKeys.push(t.division); divMembers.push([]); d = divKeys.length - 1; }
    divMembers[d].push(i);
  });

  function meanFor(teamId, week) {
    var base = inp.mean[teamId][week];
    if (base === undefined) return 0;
    var adj = adjustments[teamId];
    return base + (adj && adj[week] ? adj[week] : 0);
  }
  var gm = games.map(function (g) {
    return {
      h: idx[g.home], a: idx[g.away],
      hMean: meanFor(g.home, g.week), aMean: meanFor(g.away, g.week),
      hSd: inp.sd[g.home][g.week] || 0, aSd: inp.sd[g.away][g.week] || 0,
    };
  });

  var playoffCount = new Float64Array(n), byeCount = new Float64Array(n), winSum = new Float64Array(n);
  var w = new Float64Array(n), p = new Float64Array(n), a = new Float64Array(n);
  for (var it = 0; it < iterations; it++) {
    for (var i = 0; i < n; i++) { w[i] = teams[i].wins; p[i] = teams[i].pf; a[i] = teams[i].pa; }
    for (var g = 0; g < gm.length; g++) {
      var G = gm[g];
      var hs = G.hMean + G.hSd * z[(2 * g) * iterations + it];
      var as = G.aMean + G.aSd * z[(2 * g + 1) * iterations + it];
      p[G.h] += hs; p[G.a] += as; a[G.h] += as; a[G.a] += hs;
      if (hs > as) w[G.h] += 1; else if (as > hs) w[G.a] += 1; else { w[G.h] += 0.5; w[G.a] += 0.5; }
    }
    var seeds = oddsSeedSeason(inp, divMembers, w, p, a);
    for (var s = 0; s < seeds.length; s++) {
      playoffCount[seeds[s]] += 1;
      if (s < inp.byeSeeds) byeCount[seeds[s]] += 1;
    }
    for (var k = 0; k < n; k++) winSum[k] += w[k];
  }
  var out = {};
  teams.forEach(function (t, i) {
    out[t.id] = { playoff: playoffCount[i] / iterations, bye: byeCount[i] / iterations, expectedWins: winSum[i] / iterations };
  });
  return out;
}

// Per-week mean shifts for the two teams in a trade, from evaluateTrade*'s
// weekly deltas. The in-progress current week is NOT shifted - its lineups are
// already locked, a trade can't change it.
function oddsAdjustmentsFromTrade(inp, teamAId, teamBId, result) {
  function toMap(weekly) {
    var m = {};
    weekly.forEach(function (row) { if (row.week !== inp.liveWeek) m[row.week] = row.delta; });
    return m;
  }
  var adj = {};
  adj[teamAId] = toMap(result.sideA.weekly);
  adj[teamBId] = toMap(result.sideB.weekly);
  return adj;
}

// {teamId: {before, after}} for both trade teams (each {playoff, bye}).
// `baseline` may be passed to reuse a cached no-trade run.
function oddsEvaluateTrade(inp, teamAId, teamBId, result, opts, baseline) {
  var before = baseline || oddsSimulate(inp, null, opts);
  var after = oddsSimulate(inp, oddsAdjustmentsFromTrade(inp, teamAId, teamBId, result), opts);
  var out = {};
  [teamAId, teamBId].forEach(function (id) { out[id] = { before: before[id], after: after[id] }; });
  return out;
}

var FFOdds = {
  buildInputs: oddsBuildInputs, simulate: oddsSimulate, evaluateTrade: oddsEvaluateTrade,
  adjustmentsFromTrade: oddsAdjustmentsFromTrade, parseSeedingConfig: oddsParseSeedingConfig,
  bracketByeCount: oddsBracketByeCount,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = FFOdds;
} else if (typeof window !== "undefined") {
  window.FFOdds = FFOdds;
}
