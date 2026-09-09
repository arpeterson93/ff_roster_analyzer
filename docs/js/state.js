// URL-hash + localStorage state: which league/view is selected, and
// per-league "your team" pick. Mirrors irrigation_planner's state.js pattern.

const STORAGE_KEY = "ffRosterAnalyzerState_v1";

function loadStored() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (e) {
    return {};
  }
}

function saveStored(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (e) {
    /* private mode / storage disabled - ignore */
  }
}

export function fmt(n, d = 1) {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return Number(n).toFixed(d);
}

export function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function parseHash() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  return { league: params.get("league"), view: params.get("view") };
}

export function setHash(league, view) {
  const params = new URLSearchParams();
  if (league) params.set("league", league);
  if (view) params.set("view", view);
  history.replaceState(null, "", "#" + params.toString());
}

export function getYourTeam(leagueSlug) {
  const stored = loadStored();
  return (stored.yourTeam && stored.yourTeam[leagueSlug]) || null;
}

export function setYourTeam(leagueSlug, teamId) {
  const stored = loadStored();
  stored.yourTeam = stored.yourTeam || {};
  stored.yourTeam[leagueSlug] = teamId;
  saveStored(stored);
}

// "system" (default, follows the OS/browser), "light", or "dark".
export function getTheme() {
  return loadStored().theme || "system";
}

export function setTheme(theme) {
  const stored = loadStored();
  stored.theme = theme;
  saveStored(stored);
  applyTheme(theme);
}

export function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

export function getLastLeague() {
  return loadStored().lastLeague || null;
}

export function setLastLeague(slug) {
  const stored = loadStored();
  stored.lastLeague = slug;
  saveStored(stored);
}
