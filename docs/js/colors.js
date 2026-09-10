// 3-stop color scale (red -> amber -> green) reused for the matchup heat
// table and position-strength bars. Pattern borrowed from irrigation_planner.

function lerp(a, b, k) {
  return a + (b - a) * k;
}

function lerpColor(c1, c2, k) {
  return [Math.round(lerp(c1[0], c2[0], k)), Math.round(lerp(c1[1], c2[1], k)), Math.round(lerp(c1[2], c2[2], k))];
}

const RED = [192, 57, 43];
const AMBER = [214, 155, 31];
const GREEN = [47, 158, 92];

// Canonical display order for positions app-wide (lineup slots, rankings
// filter, depth tables): QB, RB, WR, TE, FLEX, K, DST.
export const POSITION_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"];

export function sortByPositionOrder(a, b, keyFn = (x) => x) {
  const ai = POSITION_ORDER.indexOf(keyFn(a));
  const bi = POSITION_ORDER.indexOf(keyFn(b));
  return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
}

// ESPN injuryStatus -> short badge letters (verified live values, 2026-09-09:
// ACTIVE, QUESTIONABLE, DOUBTFUL, OUT, INJURY_RESERVE, SUSPENSION, DAY_TO_DAY).
export const INJURY_BADGE = {
  QUESTIONABLE: "Q",
  DOUBTFUL: "D",
  OUT: "O",
  INJURY_RESERVE: "IR",
  SUSPENSION: "SUSP",
  DAY_TO_DAY: "DTD",
};

// Per-position colors, matching the original Google Sheet's palette.
export const POSITION_COLOR = {
  QB: "#c05e85",
  RB: "#73c3a6",
  WR: "#46a2ca",
  TE: "#cc8b4a",
  FLEX: "#8a8a84",
  K: "#9297cf",
  DST: "#995f51",
};

// value in [0,1]: 0 = worst (red), 0.5 = neutral (amber), 1 = best (green).
export function colorForRatio(ratio) {
  const k = Math.max(0, Math.min(1, ratio));
  const [r, g, b] = k < 0.5 ? lerpColor(RED, AMBER, k * 2) : lerpColor(AMBER, GREEN, (k - 0.5) * 2);
  return `rgb(${r},${g},${b})`;
}

// Rank (1 = best, N = worst, within ONE position's own 32 teams) -> ratio.
// This is the color basis used everywhere a matchup rank is shown: it's
// always a full, guaranteed 0-1 spread for every position, unlike a fixed
// index scale.
export function ratioForRank(rank, total = 32) {
  if (rank === null || rank === undefined) return 0.5;
  return (total - rank) / Math.max(1, total - 1);
}

// Manager first name is more stable than a team name (which owners rename
// year to year), so it's the preferred display label everywhere a fantasy
// team shows up - falls back to the team name if a manager isn't set.
export function teamLabel(team) {
  if (!team) return "";
  return team.manager || team.name || "";
}

// Combined "Opp" + "Matchup" cell: @ prefix on the road, colored by the
// matchup RANK (always a full 0-1 spread within that position's own 32
// teams - see ratioForRank).
export function opponentCellHtml(weekEntry) {
  if (!weekEntry || !weekEntry.opponent) return `<span class="muted">BYE</span>`;
  const label = (weekEntry.home === false ? "@" : "") + weekEntry.opponent;
  const hasRank = weekEntry.rank !== null && weekEntry.rank !== undefined;
  const color = colorForRatio(ratioForRank(weekEntry.rank));
  const rankTitle = hasRank ? `title="Matchup rank ${weekEntry.rank} of 32 (1 = best)"` : "";
  return `<span class="pill opp-pill" style="background:${color}" ${rankTitle}>${label}${hasRank ? ` (${weekEntry.rank})` : ""}</span>`;
}

// "Sun 3:25 PM" in the VIEWER's own local time zone - the pipeline only ever
// emits an absolute UTC instant (see ingest/nfl_data.py's
// kickoff_utc_from_schedule), so a plain Date + no explicit timeZone option
// is all that's needed; the browser supplies the rest.
export function formatKickoff(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const weekday = d.toLocaleDateString(undefined, { weekday: "short" });
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${weekday} ${time}`;
}
