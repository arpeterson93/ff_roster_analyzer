// Node runner for tests/test_playoff_odds_js.py's scoring test: prints scoreTrade/pickTop results for fixed cases.
const path = require("path");
const O = require(path.join(__dirname, "..", "..", "docs", "js", "playoffodds.js"));
const side = (b, a) => ({ before: { playoff: b }, after: { playoff: a } });
const mk = (a, b, field) => ({ 1: a, 2: b, field });
const field = (deltas) => ({
  before: Object.fromEntries(Object.keys(deltas).map((k) => [k, { playoff: 0.5 }])),
  after: Object.fromEntries(Object.entries(deltas).map(([k, d]) => [k, { playoff: 0.5 + d }])),
});
const cases = {
  both_gain: O.scoreTrade(mk(side(0.4, 0.46), side(0.5, 0.52), field({ 3: -0.03, 4: 0.01 })), 1, 2, 5, 3),
  one_loses: O.scoreTrade(mk(side(0.4, 0.5), side(0.5, 0.45)), 1, 2, 5, -2),
  within_tolerance: O.scoreTrade(mk(side(0.4, 0.5), side(0.5, 0.498)), 1, 2, 5, -1),
  partner_locked_ok: O.scoreTrade(mk(side(0.4, 0.5), side(0.999, 0.999)), 1, 2, 5, 1),
  partner_locked_loses_points: O.scoreTrade(mk(side(0.4, 0.5), side(0.999, 0.999)), 1, 2, 5, -1),
  both_locked: O.scoreTrade(mk(side(0.999, 0.999), side(0.0, 0.0)), 1, 2, 5, 5),
  no_gain: O.scoreTrade(mk(side(0.4, 0.4), side(0.5, 0.5)), 1, 2, 0, 0),
};
const entries = [
  { rank: 5, giveB: ["x"] }, { rank: 4, giveB: ["x"] }, { rank: 3, giveB: ["x"] }, { rank: 2, giveB: ["y"] },
  { rank: -Infinity, giveB: ["z"] },
];
console.log(JSON.stringify({ cases, picked: O.pickTop(entries, 10, 2).map((e) => e.rank) }));
