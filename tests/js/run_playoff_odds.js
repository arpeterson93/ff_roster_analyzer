// Node runner for tests/test_playoff_odds_js.py: node run_playoff_odds.js <case.json>
const fs = require("fs");
const path = require("path");
const FFOdds = require(path.join(__dirname, "..", "..", "docs", "js", "playoffodds.js"));
const c = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const inp = FFOdds.buildInputs(c.parts);
const opts = { iterations: c.iterations, seed: 7 };
const before = FFOdds.simulate(inp, null, opts);
const adj = {};
Object.keys(c.adjustments || {}).forEach((t) => { adj[t] = {}; Object.keys(c.adjustments[t]).forEach((w) => { adj[t][Number(w)] = c.adjustments[t][w]; }); });
const after = FFOdds.simulate(inp, adj, opts);
console.log(JSON.stringify({ before, after }));
