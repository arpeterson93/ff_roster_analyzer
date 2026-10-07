// Per-team player lists for the Rankings tab (Watch List, Target List -
// see playermodal.js's star/bullseye toggles). "Per user" has no login to
// hang off of here, so it reuses the same identity the rest of the site
// already has: the per-browser "your team" pick (state.js's getYourTeam).
// Unlike the Settings sheet (read once per day by the pipeline, at build
// time), this reads the sheet LIVE from the browser via the public CSV
// export endpoint, so a toggle on one device shows up on another the next
// time Rankings loads there - not just after the next pipeline run.
// localStorage is always kept in sync too, so the feature still works with
// nothing configured (see watchlistConfig.js), just without cross-device
// sync.
//
// Both lists share ONE sheet tab/row shape (league_slug | team_id |
// player_id | list) rather than a second sheet/deployment - a 4th column
// distinguishes which list a row belongs to. A legacy row with no `list`
// value (every row synced before this column existed) is treated as
// "watch", so an already-deployed sheet needs no backfill.
import { WATCHLIST_SHEET_ID, WATCHLIST_WEBAPP_URL } from "./watchlistConfig.js";

const SHEET_TAB_NAME = "Watchlist";
const DEFAULT_LIST = "watch";
// Only "watch" keeps the original, unprefixed localStorage key - a browser
// with watch-list entries saved before Target existed must keep reading
// them from the exact same place, no migration step. Any OTHER list name
// (just "target" today) gets its own separate, prefixed key instead of
// sharing one blob, so the two lists' local caches can't collide or leak
// into each other.
const LOCAL_KEY = "ffRosterAnalyzerWatchlist_v1";
const LOCAL_KEY_PREFIX = "ffRosterAnalyzerList_";

function localStorageKeyFor(listName) {
  return listName === DEFAULT_LIST ? LOCAL_KEY : `${LOCAL_KEY_PREFIX}${listName}_v1`;
}

function loadLocal(listName) {
  try {
    const raw = localStorage.getItem(localStorageKeyFor(listName));
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

function saveLocal(listName, state) {
  try {
    localStorage.setItem(localStorageKeyFor(listName), JSON.stringify(state));
  } catch (e) {
    /* private mode / storage disabled - ignore */
  }
}

function teamKey(leagueSlug, teamId) {
  return `${leagueSlug}:${teamId}`;
}

function csvExportUrl(sheetId) {
  return `https://docs.google.com/spreadsheets/d/${sheetId}/gviz/tq?tqx=out:csv&sheet=${SHEET_TAB_NAME}`;
}

// These columns are always plain ids/names (no commas/quotes), so a naive
// split is enough - no need for a full CSV parser here. listIdx === -1
// (the sheet hasn't had the `list` column added yet) means every row is
// implicitly "watch" - same as a present-but-blank cell on a legacy row.
function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (!lines.length) return [];
  const header = lines[0].split(",").map((h) => h.trim().replace(/^"|"$/g, ""));
  const slugIdx = header.indexOf("league_slug");
  const teamIdx = header.indexOf("team_id");
  const playerIdx = header.indexOf("player_id");
  const listIdx = header.indexOf("list");
  if (slugIdx === -1 || teamIdx === -1 || playerIdx === -1) return [];
  return lines.slice(1).map((line) => {
    const cols = line.split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
    const list = (listIdx === -1 ? "" : cols[listIdx]) || DEFAULT_LIST;
    return { league_slug: cols[slugIdx], team_id: cols[teamIdx], player_id: cols[playerIdx], list };
  });
}

let remoteByList = null; // Map<listName, Map<"slug:teamId", Set<player_id>>>, fetched once per page load (one CSV fetch covers every list - they're all rows in the same sheet)

async function fetchRemote() {
  if (!WATCHLIST_SHEET_ID) return null;
  if (remoteByList) return remoteByList;
  try {
    const resp = await fetch(csvExportUrl(WATCHLIST_SHEET_ID));
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const byList = new Map();
    parseCsv(await resp.text()).forEach((r) => {
      if (!r.league_slug || !r.team_id || !r.player_id) return;
      if (!byList.has(r.list)) byList.set(r.list, new Map());
      const byTeam = byList.get(r.list);
      const key = teamKey(r.league_slug, r.team_id);
      if (!byTeam.has(key)) byTeam.set(key, new Set());
      byTeam.get(key).add(r.player_id);
    });
    remoteByList = byList;
  } catch (e) {
    console.warn("watchlist: failed to fetch remote sheet", e);
    remoteByList = new Map(); // don't keep retrying every render this page load
  }
  return remoteByList;
}

export const isSyncConfigured = () => !!(WATCHLIST_SHEET_ID && WATCHLIST_WEBAPP_URL);

// Set<player_id> for this league+team+list - remote sheet if configured,
// else whatever's saved locally in this browser. teamId may be null (no
// "your team" picked yet), in which case there's no identity to key a list
// on. listName defaults to "watch" so every pre-existing call site (the
// Rankings star) keeps working unchanged.
export async function loadWatchlist(leagueSlug, teamId, listName = DEFAULT_LIST) {
  if (teamId === null || teamId === undefined) return new Set();
  const key = teamKey(leagueSlug, teamId);
  const remote = await fetchRemote();
  if (remote) return new Set((remote.get(listName) || new Map()).get(key) || []);
  return new Set(loadLocal(listName)[key] || []);
}

// Fire-and-forget: caller already has its own Set it's updating optimistically
// for the current render; this just persists that same change.
export function setWatched(leagueSlug, teamId, playerId, watched, listName = DEFAULT_LIST) {
  const key = teamKey(leagueSlug, teamId);

  const local = loadLocal(listName);
  const localSet = new Set(local[key] || []);
  if (watched) localSet.add(playerId);
  else localSet.delete(playerId);
  local[key] = [...localSet];
  saveLocal(listName, local);

  if (remoteByList) {
    if (!remoteByList.has(listName)) remoteByList.set(listName, new Map());
    const byTeam = remoteByList.get(listName);
    if (!byTeam.has(key)) byTeam.set(key, new Set());
    if (watched) byTeam.get(key).add(playerId);
    else byTeam.get(key).delete(playerId);
  }

  if (!WATCHLIST_WEBAPP_URL) return;
  const entry = { league_slug: leagueSlug, team_id: teamId, player_id: playerId, list: listName };
  const body = watched ? { adds: [entry], removes: [] } : { adds: [], removes: [entry] };
  // text/plain avoids a CORS preflight (OPTIONS) - Apps Script Web Apps
  // don't handle those - same trick settings.js uses; the .gs handler still
  // JSON.parses the body itself.
  fetch(WATCHLIST_WEBAPP_URL, { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify(body) }).catch((e) =>
    console.warn("watchlist: failed to sync toggle to sheet", e)
  );
}
