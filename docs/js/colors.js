import { fmt } from "./state.js";

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
