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

  var setup = oddsSeedingSetup(meta, teams);
  return {
    teams: teams, games: games, mean: mean, sd: sd, liveWeek: liveWeek,
    playoffTeamCount: setup.playoffTeamCount, byeSeeds: setup.byeSeeds,
    divisionOrder: setup.divisionOrder, seedConfigs: setup.seedConfigs,
  };
}

// Seeding rules shared by every sim input: how many make the playoffs, byes,
// division tiebreak order and per-seed configs (head_to_head skipped).
function oddsSeedingSetup(meta, teams) {
  var settings = meta.settings || {};
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

// --- retrospective: "what were each team's odds after week N?" ---
// The nightly pipeline only keeps today's projections, so past weeks can't be
// replayed with the lineup model. Instead each past week is re-simulated from
// what was knowable THEN: the real record/PF/PA after that week, and every
// team's strength = its average score so far (shrunk toward the league
// average; the prior fades as games accumulate), with one pooled week-to-week
// spread. Used only for weeks before odds_history.json's recorded snapshots
// begin; week 0 is the pre-season (everyone equal).
var ODDS_RETRO_SHRINK_GAMES = 12; // ~ (weekly SD 18 / true team-talent SD 5)^2: one week of scoring says little about strength
var ODDS_RETRO_SD_PRIOR_GAMES = 12;

function oddsRetrospective(parts, opts) {
  opts = opts || {};
  var meta = parts.meta, standings = parts.standings, schedule = parts.schedule, lineups = parts.lineups || {};
  if (!meta || !standings || !standings.length || !schedule) return null;
  var regCount = meta.reg_season_count;
  var playedWeeks = {};
  schedule.forEach(function (m) { if (m.played && m.week <= regCount) playedWeeks[m.week] = true; });
  var lastWeek = 0;
  while (playedWeeks[lastWeek + 1]) lastWeek++;
  if (lastWeek < 1) return null;

  var teams0 = standings.map(function (s) { return { id: s.team_id, division: s.division }; });
  var setup = oddsSeedingSetup(meta, teams0);
  // Priors for week 0 come from today's lineup model (league-average mean / sd).
  var priorSum = 0, priorN = 0, sdSum = 0, sdN = 0;
  Object.keys(lineups).forEach(function (id) {
    var e = lineups[id];
    if (!e || !e.weeks) return;
    Object.keys(e.weeks).forEach(function (w) {
      var we = e.weeks[w];
      if (we && typeof we.total === "number") { priorSum += we.total; priorN++; if (we.sd) { sdSum += we.sd; sdN++; } }
    });
  });
  var priorMeanFallback = priorN ? priorSum / priorN : 100;
  var priorSd = sdN ? sdSum / sdN : 20;

  var weeks = [];
  for (var w = 0; w <= lastWeek; w++) {
    var recs = {}, scores = {};
    teams0.forEach(function (t) { recs[t.id] = { wins: 0, pf: 0, pa: 0 }; scores[t.id] = []; });
    var allScores = [];
    schedule.forEach(function (m) {
      if (!m.played || m.week > w || m.week > regCount || !recs[m.home_team_id] || !recs[m.away_team_id]) return;
      var h = recs[m.home_team_id], a = recs[m.away_team_id];
      h.pf += m.home_score; h.pa += m.away_score; a.pf += m.away_score; a.pa += m.home_score;
      if (m.home_score > m.away_score) h.wins += 1; else if (m.away_score > m.home_score) a.wins += 1; else { h.wins += 0.5; a.wins += 0.5; }
      scores[m.home_team_id].push(m.home_score); scores[m.away_team_id].push(m.away_score);
      allScores.push(m.home_score, m.away_score);
    });
    var leagueMean = allScores.length ? allScores.reduce(function (x, y) { return x + y; }, 0) / allScores.length : priorMeanFallback;
    var teamMean = {}, resid = 0, dof = 0;
    teams0.forEach(function (t) {
      var sc = scores[t.id], sum = sc.reduce(function (x, y) { return x + y; }, 0);
      teamMean[t.id] = (sum + ODDS_RETRO_SHRINK_GAMES * leagueMean) / (sc.length + ODDS_RETRO_SHRINK_GAMES);
      if (sc.length > 1) {
        var m = sum / sc.length;
        sc.forEach(function (x) { resid += (x - m) * (x - m); });
        dof += sc.length - 1;
      }
    });
    var sd = Math.sqrt((resid + ODDS_RETRO_SD_PRIOR_GAMES * priorSd * priorSd) / (dof + ODDS_RETRO_SD_PRIOR_GAMES));
    var inp = {
      teams: teams0.map(function (t) { return { id: t.id, division: t.division, wins: recs[t.id].wins, pf: recs[t.id].pf, pa: recs[t.id].pa }; }),
      games: [], mean: {}, sd: {}, liveWeek: null,
      playoffTeamCount: setup.playoffTeamCount, byeSeeds: setup.byeSeeds, divisionOrder: setup.divisionOrder, seedConfigs: setup.seedConfigs,
    };
    teams0.forEach(function (t) { inp.mean[t.id] = {}; inp.sd[t.id] = {}; });
    schedule.forEach(function (m) {
      if (m.week <= w || m.week > regCount) return;
      inp.games.push({ week: m.week, home: m.home_team_id, away: m.away_team_id });
      [m.home_team_id, m.away_team_id].forEach(function (id) { inp.mean[id][m.week] = teamMean[id]; inp.sd[id][m.week] = sd; });
    });
    weeks.push({ week: w, inp: inp });
  }
  var iterations = opts.iterations || 5000;
  // opts.skip: weeks already covered by recorded snapshots (no need to rebuild them).
  var todo = weeks.filter(function (x) { return !(opts.skip && opts.skip[x.week]); });
  var out = { byWeek: {}, now: {}, lastWeek: lastWeek, total: todo.length };
  // One week per call so the UI can interleave them with paints.
  out.step = function (i) {
    var res = oddsSimulate(todo[i].inp, null, { iterations: iterations, seed: opts.seed || ODDS_DEFAULT_SEED });
    var row = {};
    teams0.forEach(function (t) { row[t.id] = { playoff: res[t.id].playoff, bye: res[t.id].bye }; });
    out.byWeek[todo[i].week] = row;
  };
  standings.forEach(function (s) { out.now[s.team_id] = { playoff: s.playoff_odds, bye: s.bye_odds }; });
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
  // The whole field's before/after, for "who else does this trade help/hurt".
  out.field = { before: before, after: after };
  return out;
}

// --- ranking trades by playoff-odds change ---
// A trade is "mutual" when neither side's odds fall by more than ODDS_TOLERANCE
// (a probability, 0.005 = half a point) and at least one side gains at least
// ODDS_MIN_GAIN. A side that can't move (odds ~0% or ~100% before AND after)
// has no meaningful odds delta, so it's skipped for the score and instead must
// not lose on points (pointsGain >= 0). Both sides immovable -> odds can't
// rank this trade at all (score -Infinity).
var ODDS_TOLERANCE = 0.005;
var ODDS_MIN_GAIN = 0.005;
var ODDS_IMMOVABLE_LOW = 0.01;
var ODDS_IMMOVABLE_HIGH = 0.99;

function oddsIsImmovable(side) {
  var b = side.before.playoff, a = side.after.playoff;
  return (b < ODDS_IMMOVABLE_LOW && a < ODDS_IMMOVABLE_LOW) || (b > ODDS_IMMOVABLE_HIGH && a > ODDS_IMMOVABLE_HIGH);
}

// odds: oddsEvaluateTrade's return; pointsGainA/B: evaluateTrade*'s side
// gains. Returns {deltaA, deltaB, score, mutual, rank, rival}. score = the
// smaller of the movable sides' deltas (so balanced trades beat lopsided
// ones); rank puts every mutual trade above every non-mutual one.
function oddsScoreTrade(odds, teamAId, teamBId, pointsGainA, pointsGainB) {
  var sa = odds[teamAId], sb = odds[teamBId];
  var deltaA = sa.after.playoff - sa.before.playoff;
  var deltaB = sb.after.playoff - sb.before.playoff;
  var immA = oddsIsImmovable(sa), immB = oddsIsImmovable(sb);
  var movable = [];
  if (!immA) movable.push(deltaA);
  if (!immB) movable.push(deltaB);

  var rival = null;
  if (odds.field) {
    Object.keys(odds.field.after).forEach(function (id) {
      if (Number(id) === teamAId || Number(id) === teamBId) return;
      var d = odds.field.after[id].playoff - odds.field.before[id].playoff;
      if (rival === null || d < rival.delta) rival = { teamId: Number(id), delta: d };
    });
  }
  if (!movable.length) return { deltaA: deltaA, deltaB: deltaB, score: -Infinity, mutual: false, rank: -Infinity, rival: rival };

  var score = Math.min.apply(null, movable);
  var best = Math.max.apply(null, movable);
  var immovableOk = (!immA || pointsGainA >= 0) && (!immB || pointsGainB >= 0);
  var mutual = score >= -ODDS_TOLERANCE && best >= ODDS_MIN_GAIN && immovableOk;
  var rank = (mutual ? 10 : 0) + score + 0.01 * (deltaA + deltaB) - (immovableOk ? 0 : 0.5);
  return { deltaA: deltaA, deltaB: deltaB, score: score, mutual: mutual, rank: rank, rival: rival };
}

// Best-ranked entries first, but no incoming player (giveB) used more than
// `cap` times so one standout can't flood the list with near-copies - same
// idea as trade.js's pickDiverse, keyed on the odds rank instead.
function oddsPickTop(entries, n, cap) {
  var sorted = entries.filter(function (e) { return e.rank > -Infinity; }).sort(function (x, y) { return y.rank - x.rank; });
  var counts = {}, out = [];
  for (var i = 0; i < sorted.length && out.length < n; i++) {
    var e = sorted[i];
    if (e.giveB.every(function (id) { return (counts[id] || 0) < cap; })) {
      out.push(e);
      e.giveB.forEach(function (id) { counts[id] = (counts[id] || 0) + 1; });
    }
  }
  return out;
}

var FFOdds = {
  buildInputs: oddsBuildInputs, simulate: oddsSimulate, evaluateTrade: oddsEvaluateTrade,
  adjustmentsFromTrade: oddsAdjustmentsFromTrade, scoreTrade: oddsScoreTrade, pickTop: oddsPickTop, parseSeedingConfig: oddsParseSeedingConfig,
  bracketByeCount: oddsBracketByeCount,
  retrospective: oddsRetrospective,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = FFOdds;
} else if (typeof window !== "undefined") {
  window.FFOdds = FFOdds;
}
