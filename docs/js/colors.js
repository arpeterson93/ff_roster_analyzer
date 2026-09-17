import { fmt, escapeHtml } from "./state.js";

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

// Win-probability coloring: pure red at/below 25%, pure green at/above 75%,
// a continuous red->amber->green blend in between (50% lands exactly on
// amber) - unlike colorForRatio's own 0-1 span, which only ever reaches
// pure red/green at the extreme ends. Clamped to the 25-75% band and
// remapped onto colorForRatio's own 0-1 scale rather than a separate color
// table, so both stay a single continuous formula instead of two competing
// ones drifting apart later.
export function winProbColor(pct) {
  const clamped = Math.max(0.25, Math.min(0.75, pct));
  return colorForRatio((clamped - 0.25) / 0.5);
}

// Trailing generational suffix, not a surname - "Marvin Harrison Jr." must
// keep "Harrison" as the surname with "Jr." tacked on after, not replaced
// by it (caught live 2026-09-14: was showing "M. Jr." / "A. Sr.").
const _NAME_SUFFIXES = new Set(["jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"]);

// "Christian McCaffrey" -> "C. McCaffrey" - the compact mobile form (see
// styles.css's ".full-name"/".short-name" toggle). Deliberately naive beyond
// the suffix handling above: takes the first token's initial and the last
// non-suffix token as the surname, so a multi-word last name ("Amon-Ra St.
// Brown") still loses some fidelity - fine for a space-constrained mobile
// label, not meant as a full name parser.
export function shortName(name) {
  const parts = (name || "").trim().split(/\s+/);
  if (parts.length < 2) return name || "";
  let end = parts.length - 1;
  let suffix = "";
  if (parts.length > 2 && _NAME_SUFFIXES.has(parts[end].toLowerCase())) {
    suffix = ` ${parts[end]}`;
    end -= 1;
  }
  return `${parts[0][0]}. ${parts[end]}${suffix}`;
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

// The single site-wide rule for "our own proprietary Proj number vs ESPN's"
// - the current week always defers to ESPN (espn_projected_week, which only
// ever covers the current week - see ingest/espn_client.py), every other
// week uses our own proprietary number (p.weekly[].projected). Every view
// that shows a "Proj" column should read from here rather than
// reimplementing this rule locally - that's exactly how the player modal's
// Proj column drifted out of sync with Start/Sit and Schedule once, before
// this existed.
export function weeklyProjection(p, week, currentWeek) {
  if (week === currentWeek) return p.espn_projected_week;
  const w = (p.weekly || []).find((e) => e.week === week);
  return w ? w.projected : null;
}

// Fantasy points from `weeksAgo` weeks before the real current week - null
// (not 0) for a bye or a missed game, same "no stat row" signal
// engine/pipeline.py's _actual_weekly_stats already encodes server-side, so
// a missed week never masquerades as a scoreless one. Always anchored to the
// real currentWeek, not whatever future week a caller might be looking
// ahead to - "3 weeks ago" is a fixed real-world reference point, same as
// Start/Sit's old single-week "Last" column was.
export function pointsWeeksAgo(p, weeksAgo, currentWeek) {
  const entry = (p.weekly || []).find((w) => w.week === currentWeek - weeksAgo);
  const pts = entry?.actual?.points;
  return pts === undefined ? null : pts;
}

// Fantasy points per game ACTUALLY PLAYED so far this season - byes and
// missed games (no stat row, same signal as pointsWeeksAgo above) are
// excluded from both the sum and the denominator, mirroring engine/
// pipeline.py's own recent_form/season_avg_points definition (weeks before
// current_week with a real stat row) rather than a second, drifting
// definition of "season average".
export function seasonAvgPoints(p, currentWeek) {
  const played = (p.weekly || []).filter((w) => w.week < currentWeek && w.actual?.points !== undefined && w.actual?.points !== null);
  if (!played.length) return null;
  return played.reduce((sum, w) => sum + w.actual.points, 0) / played.length;
}

// Total (not averaged) actual fantasy points scored so far this season -
// Rankings' Stats tab uses this for its FPTS column in "season" mode, same
// played-weeks definition as seasonAvgPoints above just summed instead of
// divided. Distinct from ros_total (a rest-of-season PROJECTION) - this is
// real points already scored.
export function seasonTotalPoints(p, currentWeek) {
  const played = (p.weekly || []).filter((w) => w.week < currentWeek && w.actual?.points !== undefined && w.actual?.points !== null);
  if (!played.length) return null;
  return played.reduce((sum, w) => sum + w.actual.points, 0);
}

// ESPN's own CDN, keyed off the espn_id every player already carries - a
// D/ST "player" has no individual headshot, so it gets its team's logo
// instead (keyed off nfl_team; ESPN's logo path accepts both "was" and
// "wsh" for Washington, so no per-team alias table is needed).
export function playerPhotoUrl(p) {
  if (p.position === "DST") return p.nfl_team ? `https://a.espncdn.com/i/teamlogos/nfl/500/${p.nfl_team.toLowerCase()}.png` : null;
  return p.espn_id ? `https://a.espncdn.com/i/headshots/nfl/players/full/${p.espn_id}.png` : null;
}

// sizeClass picks the CSS class (see styles.css) - "player-photo" for
// Start/Sit's compact rows, "player-photo-lg" for the modal's header. A
// handful of deep-roster/practice-squad ids 404 rather than falling back to
// a generic silhouette - onerror just removes the broken <img> instead of
// showing a broken-image icon.
export function playerPhotoHtml(p, sizeClass = "player-photo") {
  const url = playerPhotoUrl(p);
  return url ? `<img class="${sizeClass}" src="${url}" alt="" loading="lazy" onerror="this.remove()" />` : "";
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

// Full-cell color fill (not a pill) with the opponent centered above its
// matchup rank, no parentheses - for a dense weekly grid (one column per
// week) where every pixel of cell width matters more than in a single "Opp"
// column elsewhere. Shared by Start/Sit's Rest-of-season grid and Rankings'
// Schedule tab. Returns a full <td> (with its own background/data
// attributes), not just inner content - callers building a table row string
// directly can splice it in as-is.
export function rosCellHtml(weekEntry, position) {
  if (!weekEntry || !weekEntry.opponent) return `<td class="heat-cell ros-cell muted">BYE</td>`;
  const label = (weekEntry.home === false ? "@" : "") + weekEntry.opponent;
  const hasRank = weekEntry.rank !== null && weekEntry.rank !== undefined;
  const color = colorForRatio(ratioForRank(weekEntry.rank));
  const rankTitle = hasRank ? `title="Matchup rank ${weekEntry.rank} of 32 (1 = best)"` : "";
  return `<td class="heat-cell ros-cell" style="background:${color}" ${rankTitle} data-opp-cell data-team="${escapeHtml(weekEntry.opponent)}" data-pos="${position}">
    <div class="ros-opp">${label}</div>
    ${hasRank ? `<div class="ros-rank">${weekEntry.rank}</div>` : ""}
  </td>`;
}

// Vegas-implied team total for the CURRENT week only (see engine/pipeline.py -
// a future week's line usually isn't posted yet, so weekEntry.implied_total
// is only ever non-null there). For a DST, the number that actually matters
// is the OPPOSING offense's implied total (how many points the team they're
// facing is expected to score), not their own - the caller is expected to
// pass the player's own current-week weekEntry regardless of position, this
// picks the right field internally. Weather (temp/wind/precip, outdoor/
// retractable-roof games only) rides along as a hover tooltip rather than
// its own column - there wasn't room to justify a whole column for it.
export function impliedTotalCellHtml(p, weekEntry) {
  if (!weekEntry) return `<span class="muted">&ndash;</span>`;
  const total = p.position === "DST" ? weekEntry.opponent_implied_total : weekEntry.implied_total;
  if (total === null || total === undefined) return `<span class="muted">&ndash;</span>`;
  const w = weekEntry.weather;
  const title = w ? ` title="${w.temperature_f}&deg;F, wind ${w.wind}, ${w.precip_pct ?? 0}% precip - ${w.short_forecast}"` : "";
  return `<span${title}>${fmt(total, 1)}</span>`;
}

// Same weekEntry.weather data as impliedTotalCellHtml's hover tooltip above,
// rendered as its own visible cell for Start/Sit, which can spare a whole
// column for it. Only ever populated for the CURRENT week (see
// engine/pipeline.py) - a dome game or one beyond NWS's ~7-day forecast
// horizon carries weather: null same as any other week, so this reads as a
// plain "-" rather than a special case.
export function weatherCellHtml(weekEntry) {
  const w = weekEntry && weekEntry.weather;
  if (!w) return `<span class="muted">&ndash;</span>`;
  const title = `${w.temperature_f}&deg;F, wind ${w.wind}, ${w.precip_pct ?? 0}% precip - ${w.short_forecast}`;
  return `<span title="${title}">${w.temperature_f}&deg;F, ${w.wind}</span>`;
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
