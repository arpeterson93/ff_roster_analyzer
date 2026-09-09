// Client-side twin of engine/trades.py + engine/team_strength.py's lineup_total.
// Loaded as a plain (non-module) script so it also runs standalone under Node
// for tests/test_trade_js_parity.py (`node docs/js/trade.js --fixture <path>`).
// Keep this file a line-by-line port of the Python: no framework, no imports.

var EVEN_THRESHOLD_PER_WEEK = 1.0;

function expandSlots(slots) {
  // slots: {label: count} -> [{instance, base}], numbered when count > 1.
  var DISPLAY_LABEL = { "D/ST": "DST", "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX" };
  var instances = [];
  Object.keys(slots).forEach(function (label) {
    var count = slots[label];
    var display = DISPLAY_LABEL[label] || label;
    if (count === 1) {
      instances.push({ instance: display, base: label });
    } else {
      for (var i = 1; i <= count; i++) instances.push({ instance: display + i, base: label });
    }
  });
  return instances;
}

// Exact optimal *total* (no assignment reconstruction needed for trade
// evaluation) via a bitmask DP over which players are used - equivalent to
// engine/lineup.py's Hungarian-algorithm solution, just specialized to
// return only the total. Fine for roster-sized inputs (<= ~20 players).
function optimalLineupTotal(players, slots, eligibility) {
  var slotList = expandSlots(slots);
  var n = players.length;
  var size = 1 << n;
  var dp = new Float64Array(size).fill(-1);
  dp[0] = 0;

  for (var s = 0; s < slotList.length; s++) {
    var base = slotList[s].base;
    var eligibleSet = eligibility[base] || [];
    var next = new Float64Array(size).fill(-1);
    for (var mask = 0; mask < size; mask++) {
      if (dp[mask] < 0) continue;
      // skip this slot
      if (next[mask] < dp[mask]) next[mask] = dp[mask];
      for (var p = 0; p < n; p++) {
        if (mask & (1 << p)) continue;
        if (eligibleSet.indexOf(players[p].pos) === -1) continue;
        var newMask = mask | (1 << p);
        var candidate = dp[mask] + players[p].pts;
        if (candidate > next[newMask]) next[newMask] = candidate;
      }
    }
    dp = next;
  }

  var best = 0;
  for (var m2 = 0; m2 < size; m2++) if (dp[m2] > best) best = dp[m2];
  return best;
}

// Same DP as optimalLineupTotal, but keeps every intermediate dp array so the
// optimal assignment can be recovered by backtracking from the best final
// mask - which slot (if any) each player fills, and who's left on the bench.
function optimalLineupAssignment(players, slots, eligibility) {
  var slotList = expandSlots(slots);
  var n = players.length;
  var size = 1 << n;
  var L = slotList.length;
  var EPS = 1e-6;

  var dpHistory = [];
  var dp = new Float64Array(size).fill(-1);
  dp[0] = 0;
  dpHistory.push(dp);

  for (var s = 0; s < L; s++) {
    var base = slotList[s].base;
    var eligibleSet = eligibility[base] || [];
    var next = new Float64Array(size).fill(-1);
    for (var mask = 0; mask < size; mask++) {
      if (dp[mask] < 0) continue;
      if (next[mask] < dp[mask]) next[mask] = dp[mask];
      for (var p = 0; p < n; p++) {
        if (mask & (1 << p)) continue;
        if (eligibleSet.indexOf(players[p].pos) === -1) continue;
        var newMask = mask | (1 << p);
        var candidate = dp[mask] + players[p].pts;
        if (candidate > next[newMask]) next[newMask] = candidate;
      }
    }
    dp = next;
    dpHistory.push(dp);
  }

  var bestMask = 0, bestVal = 0;
  for (var m2 = 0; m2 < size; m2++) if (dp[m2] > bestVal) { bestVal = dp[m2]; bestMask = m2; }

  var mask = bestMask;
  var slotPlayerIndex = new Array(L).fill(null);
  for (var i = L - 1; i >= 0; i--) {
    var target = dpHistory[i + 1][mask];
    var skipVal = dpHistory[i][mask];
    if (skipVal >= 0 && Math.abs(skipVal - target) < EPS) continue; // slot i left empty
    var base2 = slotList[i].base;
    var eligibleSet2 = eligibility[base2] || [];
    for (var p2 = 0; p2 < n; p2++) {
      if (!(mask & (1 << p2))) continue;
      if (eligibleSet2.indexOf(players[p2].pos) === -1) continue;
      var prevMask = mask ^ (1 << p2);
      if (dpHistory[i][prevMask] >= 0 && Math.abs(dpHistory[i][prevMask] + players[p2].pts - target) < EPS) {
        slotPlayerIndex[i] = p2;
        mask = prevMask;
        break;
      }
    }
  }

  var benchIndices = [];
  for (var b = 0; b < n; b++) if (!(bestMask & (1 << b))) benchIndices.push(b);

  return {
    total: bestVal,
    slots: slotList.map(function (sl, idx) { return { instance: sl.instance, base: sl.base, playerIndex: slotPlayerIndex[idx] }; }),
    benchIndices: benchIndices,
  };
}

// Same as optimalLineupAssignment, but for one specific week of a roster of
// player IDs (as opposed to a pre-built {pos, pts} entries array), returning
// player IDs rather than array indices - what the trade calculator's
// click-to-expand week view needs directly.
function lineupAssignmentForWeek(playerIds, players, week, slots, eligibility) {
  var entries = [];
  var idByIndex = [];
  for (var j = 0; j < playerIds.length; j++) {
    var pid = playerIds[j];
    var p = players[pid];
    if (!p) continue;
    entries.push({ pos: p.position, pts: (p.weekly && p.weekly[week] !== undefined) ? p.weekly[week] : 0 });
    idByIndex.push(pid);
  }
  var result = optimalLineupAssignment(entries, slots, eligibility);
  return {
    total: result.total,
    starters: result.slots
      .filter(function (sl) { return sl.playerIndex !== null; })
      .map(function (sl) { return { instance: sl.instance, base: sl.base, id: idByIndex[sl.playerIndex] }; }),
    bench: result.benchIndices.map(function (idx) { return idByIndex[idx]; }),
  };
}

function lineupTotalByWeek(playerIds, players, weeks, slots, eligibility) {
  var byWeek = {};
  for (var i = 0; i < weeks.length; i++) {
    var w = weeks[i];
    var entries = [];
    for (var j = 0; j < playerIds.length; j++) {
      var p = players[playerIds[j]];
      if (!p) continue;
      entries.push({ pos: p.position, pts: (p.weekly && p.weekly[w] !== undefined) ? p.weekly[w] : 0 });
    }
    byWeek[w] = optimalLineupTotal(entries, slots, eligibility);
  }
  return byWeek;
}

function lineupTotal(playerIds, players, weeks, slots, eligibility) {
  var byWeek = lineupTotalByWeek(playerIds, players, weeks, slots, eligibility);
  var total = 0;
  for (var w in byWeek) total += byWeek[w];
  return total;
}

function afterRosters(givesA, givesB, rosterA, rosterB) {
  return {
    afterRosterA: rosterA.filter(function (p) { return givesA.indexOf(p) === -1; }).concat(givesB),
    afterRosterB: rosterB.filter(function (p) { return givesB.indexOf(p) === -1; }).concat(givesA),
  };
}

function evaluateTrade(opts) {
  var givesA = opts.givesA, givesB = opts.givesB, rosterA = opts.rosterA, rosterB = opts.rosterB;
  var players = opts.players, weeks = opts.weeks, slots = opts.slots, eligibility = opts.eligibility;

  var beforeAWk = lineupTotalByWeek(rosterA, players, weeks, slots, eligibility);
  var beforeBWk = lineupTotalByWeek(rosterB, players, weeks, slots, eligibility);

  var rosters = afterRosters(givesA, givesB, rosterA, rosterB);
  var afterRosterA = rosters.afterRosterA, afterRosterB = rosters.afterRosterB;

  var afterAWk = lineupTotalByWeek(afterRosterA, players, weeks, slots, eligibility);
  var afterBWk = lineupTotalByWeek(afterRosterB, players, weeks, slots, eligibility);

  var beforeA = 0, beforeB = 0, afterA = 0, afterB = 0;
  var weeklyA = [], weeklyB = [];
  weeks.forEach(function (w) {
    beforeA += beforeAWk[w]; afterA += afterAWk[w];
    beforeB += beforeBWk[w]; afterB += afterBWk[w];
    weeklyA.push({ week: w, before: beforeAWk[w], after: afterAWk[w], delta: afterAWk[w] - beforeAWk[w] });
    weeklyB.push({ week: w, before: beforeBWk[w], after: afterBWk[w], delta: afterBWk[w] - beforeBWk[w] });
  });

  var rawGivenA = givesA.reduce(function (acc, p) { return acc + players[p].ros_total; }, 0);
  var rawGivenB = givesB.reduce(function (acc, p) { return acc + players[p].ros_total; }, 0);

  var sideA = { before: beforeA, after: afterA, gain: afterA - beforeA, rawGiven: rawGivenA, rawReceived: rawGivenB, weekly: weeklyA, afterRoster: afterRosterA };
  var sideB = { before: beforeB, after: afterB, gain: afterB - beforeB, rawGiven: rawGivenB, rawReceived: rawGivenA, weekly: weeklyB, afterRoster: afterRosterB };

  var diff = sideA.gain - sideB.gain;
  var threshold = EVEN_THRESHOLD_PER_WEEK * weeks.length;
  var favors = Math.abs(diff) < threshold ? "even" : (diff > 0 ? "a" : "b");

  return { sideA: sideA, sideB: sideB, favors: favors };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    evaluateTrade: evaluateTrade, lineupTotal: lineupTotal, lineupTotalByWeek: lineupTotalByWeek,
    optimalLineupTotal: optimalLineupTotal, optimalLineupAssignment: optimalLineupAssignment,
    lineupAssignmentForWeek: lineupAssignmentForWeek,
  };

  if (require.main === module) {
    var fs = require("fs");
    var args = process.argv.slice(2);
    var fixtureIdx = args.indexOf("--fixture");
    if (fixtureIdx === -1) {
      console.error("usage: node trade.js --fixture <path>");
      process.exit(2);
    }
    var fixture = JSON.parse(fs.readFileSync(args[fixtureIdx + 1], "utf-8"));

    // Build the {id: {position, weekly, ros_total}} map trade.js expects from
    // the fixture's flat {position, ppw, ros_total} shape.
    var players = {};
    Object.keys(fixture.players).forEach(function (id) {
      var p = fixture.players[id];
      var weekly = {};
      fixture.weeks.forEach(function (w) { weekly[w] = p.ppw; });
      players[id] = { position: p.position, weekly: weekly, ros_total: p.ros_total };
    });

    var results = fixture.cases.map(function (c) {
      var result = evaluateTrade({
        givesA: c.gives_a, givesB: c.gives_b, rosterA: fixture.roster_a, rosterB: fixture.roster_b,
        players: players, weeks: fixture.weeks, slots: fixture.slots, eligibility: fixture.eligibility,
      });
      return { name: c.name, gain_a: result.sideA.gain, gain_b: result.sideB.gain, favors: result.favors };
    });
    console.log(JSON.stringify(results));
  }
} else if (typeof window !== "undefined") {
  window.FFTrade = {
    evaluateTrade: evaluateTrade, lineupTotal: lineupTotal, lineupTotalByWeek: lineupTotalByWeek,
    optimalLineupTotal: optimalLineupTotal, optimalLineupAssignment: optimalLineupAssignment,
    lineupAssignmentForWeek: lineupAssignmentForWeek,
  };
}
