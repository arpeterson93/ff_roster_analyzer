// Node runner for tests/test_playoff_odds_js.py: node run_odds_retro.js <dataDir>
const fs = require("fs");
const path = require("path");
const FFOdds = require(path.join(__dirname, "..", "..", "docs", "js", "playoffodds.js"));
const d = process.argv[2];
const load = (f) => JSON.parse(fs.readFileSync(path.join(d, f), "utf8"));
const parts = { meta: load("meta.json"), standings: load("standings.json"), schedule: load("schedule.json"), lineups: load("lineups.json") };
const r = FFOdds.retrospective(parts, { iterations: 2000 });
for (let i = 0; i < r.total; i++) r.step(i);
const sums = r.weeks.map((w, i) => Object.keys(r.series).reduce((t, id) => t + r.series[id][i].playoff, 0));
console.log(JSON.stringify({ weeks: r.weeks, sums, playoffCount: parts.meta.playoff_team_count, lastWeek: r.lastWeek, sample: r.series[Object.keys(r.series)[0]].map((x) => +x.playoff.toFixed(3)) }));
