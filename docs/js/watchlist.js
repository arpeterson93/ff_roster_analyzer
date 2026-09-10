// Per-team watch list for the Rankings tab. "Per user" has no login to hang
// off of here, so it reuses the same identity the rest of the site already
// has: the per-browser "your team" pick (state.js's getYourTeam). Unlike the
// Settings sheet (read once per day by the pipeline, at build time), this
// reads the sheet LIVE from the browser via the public CSV export endpoint,
// so a toggle on one device shows up on another the next time Rankings
// loads there - not just after the next pipeline run. localStorage is
// always kept in sync too, so the feature still works with nothing
// configured (see watchlistConfig.js), just without cross-device sync.
import { WATCHLIST_SHEET_ID, WATCHLIST_WEBAPP_URL } from "./watchlistConfig.js";

const SHEET_TAB_NAME = "Watchlist";
const LOCAL_KEY = "ffRosterAnalyzerWatchlist_v1";

function loadLocal() {
  try {
    const raw = localStorage.getItem(LOCAL_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

function saveLocal(state) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(state));
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

// These three columns are always plain ids (no commas/quotes), so a naive
// split is enough - no need for a full CSV parser here.
function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  if (!lines.length) return [];
  const header = lines[0].split(",").map((h) => h.trim().replace(/^"|"$/g, ""));
  const slugIdx = header.indexOf("league_slug");
  const teamIdx = header.indexOf("team_id");
  const playerIdx = header.indexOf("player_id");
  if (slugIdx === -1 || teamIdx === -1 || playerIdx === -1) return [];
  return lines.slice(1).map((line) => {
    const cols = line.split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
    return { league_slug: cols[slugIdx], team_id: cols[teamIdx], player_id: cols[playerIdx] };
  });
}

let remoteByTeam = null; // Map<"slug:teamId", Set<player_id>>, fetched once per page load

async function fetchRemote() {
  if (!WATCHLIST_SHEET_ID) return null;
  if (remoteByTeam) return remoteByTeam;
  try {
    const resp = await fetch(csvExportUrl(WATCHLIST_SHEET_ID));
    if (!resp.ok) throw new Error(`status ${resp.status}`);
    const map = new Map();
    parseCsv(await resp.text()).forEach((r) => {
      if (!r.league_slug || !r.team_id || !r.player_id) return;
      const key = teamKey(r.league_slug, r.team_id);
      if (!map.has(key)) map.set(key, new Set());
      map.get(key).add(r.player_id);
    });
    remoteByTeam = map;
  } catch (e) {
    console.warn("watchlist: failed to fetch remote sheet", e);
    remoteByTeam = new Map(); // don't keep retrying every render this page load
  }
  return remoteByTeam;
}

export const isSyncConfigured = () => !!(WATCHLIST_SHEET_ID && WATCHLIST_WEBAPP_URL);

// Set<player_id> for this league+team - remote sheet if configured, else
// whatever's saved locally in this browser. teamId may be null (no "your
// team" picked yet), in which case there's no identity to key a list on.
export async function loadWatchlist(leagueSlug, teamId) {
  if (teamId === null || teamId === undefined) return new Set();
  const key = teamKey(leagueSlug, teamId);
  const remote = await fetchRemote();
  if (remote) return new Set(remote.get(key) || []);
  return new Set(loadLocal()[key] || []);
}

// Fire-and-forget: caller already has its own Set it's updating optimistically
// for the current render; this just persists that same change.
export function setWatched(leagueSlug, teamId, playerId, watched) {
  const key = teamKey(leagueSlug, teamId);

  const local = loadLocal();
  const localSet = new Set(local[key] || []);
  if (watched) localSet.add(playerId);
  else localSet.delete(playerId);
  local[key] = [...localSet];
  saveLocal(local);

  if (remoteByTeam) {
    if (!remoteByTeam.has(key)) remoteByTeam.set(key, new Set());
    if (watched) remoteByTeam.get(key).add(playerId);
    else remoteByTeam.get(key).delete(playerId);
  }

  if (!WATCHLIST_WEBAPP_URL) return;
  const entry = { league_slug: leagueSlug, team_id: teamId, player_id: playerId };
  const body = watched ? { adds: [entry], removes: [] } : { adds: [], removes: [entry] };
  // text/plain avoids a CORS preflight (OPTIONS) - Apps Script Web Apps
  // don't handle those - same trick settings.js uses; the .gs handler still
  // JSON.parses the body itself.
  fetch(WATCHLIST_WEBAPP_URL, { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify(body) }).catch((e) =>
    console.warn("watchlist: failed to sync toggle to sheet", e)
  );
}
