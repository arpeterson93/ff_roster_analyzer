// Declarative weekly-metric registry for the Trends tab (docs/js/trends.js) -
// a thin wrapper around logic that already lives elsewhere (colors.js's
// per-player-week accessors, schedule.js's actual-or-projected FPts blend,
// statcolumns.js's shared stat-column definitions), not a second copy of
// any of it. Every metric exposes the same shape so trends.js can treat
// them uniformly: {key, label, group, unit, positions (Set|null, null =
// every position), get(player, week, currentWeek)}. `group` is the same
// Passing/Rushing/Receiving/Misc/FG Made/PAT/etc. category statcolumns.js
// already groups its own table columns by - shown in the Trends metric
// table's own Category column so a raw stat's block is obvious without
// needing to already know statcolumns.js's own layout.
import { weeklyProjection, snapPct, attPct, tgtPct, airYardPct } from "./colors.js";
import { USAGE_POSITIONS } from "./playermodal.js";
import { STATS_TAB_BLOCKS, blocksForPosition } from "./statcolumns.js";

function statGetter(key) {
  return (p, week) => {
    const w = (p.weekly || []).find((e) => e.week === week);
    const v = w?.actual?.stats?.[key];
    return v === null || v === undefined ? null : v;
  };
}

// null (never 0) for this player's own bye week, checked FIRST and
// regardless of past or future - a past bye has no `actual` stat row
// (same "null not 0" convention colors.js's own pointsWeeksAgo/
// seasonAvgPoints already use), but a FUTURE bye would otherwise fall
// through to weeklyProjection below, which - since ESPN already knows the
// team isn't playing - very often returns a real, legitimate 0 for it,
// not a missing value trends.js's null-check would ever catch. Checking
// player.bye directly, before any actual/projection lookup, is what
// actually guarantees a bye never shows as a real data point regardless
// of which side of "now" it falls on.
//
// A played-but-inactive week (not a bye - no stat row for a week that DID
// happen) is still just "no actual, and it's already in the past", so it
// falls through to the second check below the same way it always did.
//
// Deliberately NOT schedule.js's own pointsForWeek, which falls back to
// the stale pregame projection for a past no-actual week instead - the
// right call for ITS OWN use (some number for a matchup's score), the
// wrong one for a trend line that should visibly stop rather than
// silently show a guess (or a bye's own real 0) as if it happened.
function fptsGetter(p, week, currentWeek) {
  if (p.bye === week) return null;
  const w = (p.weekly || []).find((e) => e.week === week);
  if (w && w.actual) return w.actual.points;
  if (week < currentWeek) return null;
  return weeklyProjection(p, week, currentWeek);
}

function xfpGetter(p, week) {
  const w = (p.weekly || []).find((e) => e.week === week);
  return w?.actual?.xfp_points ?? null;
}

// snapPct/attPct/tgtPct/airYardPct (colors.js) return a 0..1 fraction (same
// convention this file's own fmtUsagePct multiplies by 100 to display) -
// scaled to 0..100 here so the chart's own axis reads as plain percentage
// points without the caller needing to know that convention.
function usageGetter(fn) {
  return (p, week, currentWeek) => {
    const v = fn(p, week, currentWeek);
    return v === null || v === undefined ? null : v * 100;
  };
}

// Flattens a statcolumns.js block list into [group, key, label] triples,
// dropping the passing block's combined ["completions","attempts"] ratio
// column - a single "12/18" display cell, not one chartable number.
// DST_BLOCKS has a couple of ungrouped (group: null) single-column blocks
// (PA, the standalone TD) - falls back to `fallbackGroup` for those rather
// than showing a blank Category cell.
function flatten(blocks, fallbackGroup) {
  return blocks.flatMap(([group, cols]) => cols.filter(([key]) => !Array.isArray(key)).map(([key, label]) => [group || fallbackGroup, key, label]));
}

// Registry `key` is namespaced per source block list (off_/k_/dst_), NOT
// the bare stats-object key `statGetter` actually reads - offense's Misc
// block and DST's own block both have a real "special_teams_tds" column
// (a offense player returning a kick for a TD vs. a defense/special-teams
// unit doing the same), which would otherwise collide into ONE shared
// state.metricState entry (and one checkbox id) for two really-different
// metrics. The registry key just needs to be unique across this whole
// list; statGetter(key) below still reads the original, real stats field.
const OFFENSE_STAT_METRICS = flatten(STATS_TAB_BLOCKS, "Offense").map(([group, key, label]) => ({
  key: `off_${key}`, label, group, unit: "count", positions: USAGE_POSITIONS, get: statGetter(key),
}));
const K_STAT_METRICS = flatten(blocksForPosition("K"), "Kicking").map(([group, key, label]) => ({
  key: `k_${key}`, label, group, unit: "count", positions: new Set(["K"]), get: statGetter(key),
}));
const DST_STAT_METRICS = flatten(blocksForPosition("DST"), "DST").map(([group, key, label]) => ({
  key: `dst_${key}`, label, group, unit: "count", positions: new Set(["DST"]), get: statGetter(key),
}));

export const METRICS = [
  { key: "fpts", label: "FPts", group: "Scoring", unit: "pts", positions: null, get: fptsGetter },
  { key: "xfpts", label: "xFPts", group: "Scoring", unit: "pts", positions: USAGE_POSITIONS, get: xfpGetter },
  { key: "snap_pct", label: "Snap %", group: "Usage", unit: "pct", positions: USAGE_POSITIONS, get: usageGetter(snapPct) },
  { key: "att_pct", label: "Att %", group: "Usage", unit: "pct", positions: USAGE_POSITIONS, get: usageGetter(attPct) },
  { key: "tgt_pct", label: "Tgt %", group: "Usage", unit: "pct", positions: USAGE_POSITIONS, get: usageGetter(tgtPct) },
  { key: "air_yard_pct", label: "AirYd %", group: "Usage", unit: "pct", positions: USAGE_POSITIONS, get: usageGetter(airYardPct) },
  ...OFFENSE_STAT_METRICS,
  ...K_STAT_METRICS,
  ...DST_STAT_METRICS,
];

// Metrics relevant to AT LEAST ONE of the given players - a metric with
// positions:null (FPts) always qualifies; everything else needs a real
// position match, same gating idea as playermodal.js's own USAGE_BLOCK.
export function metricsForPlayers(players) {
  return METRICS.filter((m) => !m.positions || players.some((p) => m.positions.has(p.position)));
}
