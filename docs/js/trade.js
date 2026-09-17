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

// Same idea as lineupTotalWithStreamingByWeek, but for ONE week's real
// assignment (what the trade calculator's click-to-expand week view shows) -
// so that view always agrees with the streaming-aware totals above it
// instead of silently showing an empty slot the totals already credited a
// free agent for filling. Only ever adds ONE synthetic candidate per
// zeroed-out position (the single best free agent that week), same as the
// totals-only version - not the whole free-agent pool - so the bitmask DP
// stays small. Returns the same shape as lineupAssignmentForWeek, plus
// `streamedIds`: the subset of playerIds in the result that are free-agent
// fill-ins, not actually on the roster, for the frontend to flag distinctly
// (see docs/js/tradeui.js's stream badge).
function lineupAssignmentForWeekWithStreaming(playerIds, players, freeAgentsByPos, week, slots, eligibility) {
  var entries = [];
  var idByIndex = [];
  var rosteredByPos = {};
  for (var j = 0; j < playerIds.length; j++) {
    var pid = playerIds[j];
    var p = players[pid];
    if (!p) continue;
    var pts = (p.weekly && p.weekly[week] !== undefined) ? p.weekly[week] : 0;
    entries.push({ pos: p.position, pts: pts });
    idByIndex.push(pid);
    rosteredByPos[p.position] = Math.max(rosteredByPos[p.position] || 0, pts);
  }
  var streamedIds = [];
  Object.keys(freeAgentsByPos || {}).forEach(function (pos) {
    if ((rosteredByPos[pos] || 0) > 0) return;
    var best = null, bestPts = 0;
    (freeAgentsByPos[pos] || []).forEach(function (fa) {
      var pts = (fa.weekly && fa.weekly[week] !== undefined) ? fa.weekly[week] : 0;
      if (pts > bestPts) { bestPts = pts; best = fa; }
    });
    if (best) {
      entries.push({ pos: pos, pts: bestPts });
      idByIndex.push(best.id);
      streamedIds.push(best.id);
    }
  });
  var result = optimalLineupAssignment(entries, slots, eligibility);
  var starters = result.slots
    .filter(function (sl) { return sl.playerIndex !== null; })
    .map(function (sl) { return { instance: sl.instance, base: sl.base, id: idByIndex[sl.playerIndex] }; });
  // A streamed candidate the optimizer didn't actually pick for a starting
  // slot (rare - only when a real roster player was eligible for the same
  // slot at an equal-or-better points-per-slot tradeoff elsewhere) isn't a
  // real bench player, so it's dropped rather than shown as one.
  var bench = result.benchIndices.map(function (idx) { return idByIndex[idx]; }).filter(function (id) { return streamedIds.indexOf(id) === -1; });
  var usedStreamedIds = streamedIds.filter(function (id) { return starters.some(function (s) { return s.id === id; }); });
  return { total: result.total, starters: starters, bench: bench, streamedIds: usedStreamedIds };
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

// Port of engine/team_strength.py's lineup_total_with_streaming_by_week: for
// any week where every ROSTERED player at a position projects to 0 (most
// commonly a lone K/DST on bye), the best available free agent's OWN
// projection for that specific week is assumed to start instead - picked
// fresh per week (not by season-long ros_total), same "whoever's got the
// matchup that week" logic a real manager streams with. Without this, a
// trade calculator would give a rostered player full credit for "filling"
// a bye week the wire would have trivially filled anyway.
function lineupTotalWithStreamingByWeek(playerIds, players, freeAgentsByPos, weeks, slots, eligibility) {
  var byWeek = {};
  for (var i = 0; i < weeks.length; i++) {
    var w = weeks[i];
    var entries = [];
    var rosteredByPos = {};
    for (var j = 0; j < playerIds.length; j++) {
      var p = players[playerIds[j]];
      if (!p) continue;
      var pts = (p.weekly && p.weekly[w] !== undefined) ? p.weekly[w] : 0;
      entries.push({ pos: p.position, pts: pts });
      rosteredByPos[p.position] = Math.max(rosteredByPos[p.position] || 0, pts);
    }
    Object.keys(freeAgentsByPos || {}).forEach(function (pos) {
      if ((rosteredByPos[pos] || 0) > 0) return;
      var bestFaWeekPts = 0;
      (freeAgentsByPos[pos] || []).forEach(function (fa) {
        var pts = (fa.weekly && fa.weekly[w] !== undefined) ? fa.weekly[w] : 0;
        if (pts > bestFaWeekPts) bestFaWeekPts = pts;
      });
      if (bestFaWeekPts > 0) entries.push({ pos: pos, pts: bestFaWeekPts });
    });
    byWeek[w] = optimalLineupTotal(entries, slots, eligibility);
  }
  return byWeek;
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

// Port of engine/trades.py's evaluate_with_streaming - identical to
// evaluateTrade above except every lineup total goes through
// lineupTotalWithStreamingByWeek instead of lineupTotalByWeek, so a trade
// isn't scored as a bigger swing than it really is when the position it
// touches (typically K/DST) has a decent replacement sitting on the wire
// anyway. opts additionally takes freeAgentsByPos: {position: [{id,
// weekly}]}.
function evaluateTradeWithStreaming(opts) {
  var givesA = opts.givesA, givesB = opts.givesB, rosterA = opts.rosterA, rosterB = opts.rosterB;
  var players = opts.players, freeAgentsByPos = opts.freeAgentsByPos;
  var weeks = opts.weeks, slots = opts.slots, eligibility = opts.eligibility;

  var beforeAWk = lineupTotalWithStreamingByWeek(rosterA, players, freeAgentsByPos, weeks, slots, eligibility);
  var beforeBWk = lineupTotalWithStreamingByWeek(rosterB, players, freeAgentsByPos, weeks, slots, eligibility);

  var rosters = afterRosters(givesA, givesB, rosterA, rosterB);
  var afterRosterA = rosters.afterRosterA, afterRosterB = rosters.afterRosterB;

  var afterAWk = lineupTotalWithStreamingByWeek(afterRosterA, players, freeAgentsByPos, weeks, slots, eligibility);
  var afterBWk = lineupTotalWithStreamingByWeek(afterRosterB, players, freeAgentsByPos, weeks, slots, eligibility);

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
    evaluateTrade: evaluateTrade, evaluateTradeWithStreaming: evaluateTradeWithStreaming,
    lineupTotal: lineupTotal, lineupTotalByWeek: lineupTotalByWeek,
    lineupTotalWithStreamingByWeek: lineupTotalWithStreamingByWeek,
    optimalLineupTotal: optimalLineupTotal, optimalLineupAssignment: optimalLineupAssignment,
    lineupAssignmentForWeek: lineupAssignmentForWeek,
    lineupAssignmentForWeekWithStreaming: lineupAssignmentForWeekWithStreaming,
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

    // streaming_cases are fully self-contained (own players/free_agents/
    // slots/weeks per case, unlike cases above which share one fixed
    // roster) since each one is testing a specific bye/streaming scenario -
    // see tests/fixtures/trade_case.json's _comment on this key.
    var streamingResults = (fixture.streaming_cases || []).map(function (c) {
      var casePlayers = {};
      Object.keys(c.players).forEach(function (id) {
        var p = c.players[id];
        var weekly = {};
        Object.keys(p.weekly).forEach(function (w) { weekly[w] = p.weekly[w]; });
        casePlayers[id] = { position: p.position, weekly: weekly, ros_total: p.ros_total };
      });
      var freeAgentsByPos = {};
      Object.keys(c.free_agents || {}).forEach(function (pos) {
        freeAgentsByPos[pos] = c.free_agents[pos].map(function (fa) {
          var weekly = {};
          Object.keys(fa.weekly).forEach(function (w) { weekly[w] = fa.weekly[w]; });
          return { id: fa.id, position: fa.position, weekly: weekly, ros_total: fa.ros_total };
        });
      });
      var result = evaluateTradeWithStreaming({
        givesA: c.gives_a, givesB: c.gives_b, rosterA: c.roster_a, rosterB: c.roster_b,
        players: casePlayers, freeAgentsByPos: freeAgentsByPos, weeks: c.weeks, slots: c.slots, eligibility: c.eligibility,
      });
      return { name: c.name, gain_a: result.sideA.gain, gain_b: result.sideB.gain, favors: result.favors };
    });

    console.log(JSON.stringify({ cases: results, streaming_cases: streamingResults }));
  }
} else if (typeof window !== "undefined") {
  window.FFTrade = {
    evaluateTrade: evaluateTrade, evaluateTradeWithStreaming: evaluateTradeWithStreaming,
    lineupTotal: lineupTotal, lineupTotalByWeek: lineupTotalByWeek,
    lineupTotalWithStreamingByWeek: lineupTotalWithStreamingByWeek,
    optimalLineupTotal: optimalLineupTotal, optimalLineupAssignment: optimalLineupAssignment,
    lineupAssignmentForWeek: lineupAssignmentForWeek,
    lineupAssignmentForWeekWithStreaming: lineupAssignmentForWeekWithStreaming,
  };
}
