// Node runner for tests/test_playoff_odds_js.py: node run_odds_retro.js <dataDir>
const fs = require("fs");
const path = require("path");
const FFOdds = require(path.join(__dirname, "..", "..", "docs", "js", "playoffodds.js"));
const d = process.argv[2];
const load = (f) => JSON.parse(fs.readFileSync(path.join(d, f), "utf8"));
const parts = { meta: load("meta.json"), standings: load("standings.json"), schedule: load("schedule.json"), lineups: load("lineups.json") };
const skipArg = process.argv[3] ? JSON.parse(process.argv[3]) : null;
const r = FFOdds.retrospective(parts, { iterations: 2000, skip: skipArg });
for (let i = 0; i < r.total; i++) r.step(i);
const weeks = Object.keys(r.byWeek).map(Number).sort((a, b) => a - b);
const sums = weeks.map((w) => Object.keys(r.byWeek[w]).reduce((t, id) => t + r.byWeek[w][id].playoff, 0));
const first = Object.keys(r.byWeek[0])[0];
console.log(JSON.stringify({ weeks, sums, playoffCount: parts.meta.playoff_team_count, lastWeek: r.lastWeek, sample: weeks.map((w) => +r.byWeek[w][first].playoff.toFixed(3)) }));
