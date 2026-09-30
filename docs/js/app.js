import { loadLeagues, loadLeagueData } from "./data.js";
import { parseHash, setHash, getLastLeague, setLastLeague, getTheme, applyTheme, escapeHtml, fmt } from "./state.js";
import { renderStrength } from "./strength.js";
import { renderStartSit } from "./startsit.js";
import { renderRankings } from "./rankings.js";
import { renderStandings } from "./standings.js";
import { renderMatchups } from "./matchups.js";
import { renderTrade } from "./tradeui.js";
import { renderSchedule } from "./schedule.js";
import { renderSettings } from "./settings.js";

const VIEWS = {
  strength: renderStrength,
  startsit: renderStartSit,
  rankings: renderRankings,
  trade: renderTrade,
  matchups: renderMatchups,
  standings: renderStandings,
  schedule: renderSchedule,
  settings: renderSettings,
};

let currentLeagueSlug = null;
let currentView = "strength";
let currentData = null;

function wireTabs() {
  document.querySelectorAll("nav.tabs button").forEach((btn) => {
    btn.addEventListener("click", () => {
      currentView = btn.dataset.tab;
      document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === `tab-${currentView}`));
      setHash(currentLeagueSlug, currentView);
      renderActiveView();
    });
  });
}

function renderActiveView() {
  if (!currentData) return;
  const panel = document.getElementById(`tab-${currentView}`);
  const renderFn = VIEWS[currentView];
  if (renderFn) renderFn(panel, currentData, currentLeagueSlug);
}

function formatUpdateStamp(iso) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

// Page footer's "Full ... · Refresh ... · Live ... · FAAB ..." shorthand -
// one line per update type this site actually has, each showing that
// type's own most recent run (see engine/pipeline.py's run_league, which
// carries last_updates forward across runs so an in-between refresh doesn't
// erase the last real full/FAAB timestamp). A type simply doesn't render
// until it HAS run at least once (no "—" placeholders for one that's never
// fired, e.g. FAAB on a brand new league before its first full run).
function renderHeader(data) {
  const leagueMeta = data.meta;
  const generated = new Date(leagueMeta.generated_at);

  // last_updates is new - older already-built data won't have it yet, so
  // this synthesizes a single entry for whichever stage THIS build actually
  // was rather than showing every type as blank until the next full
  // pipeline run backfills the real dict.
  const lastUpdates = leagueMeta.last_updates || { [leagueMeta.stage]: leagueMeta.generated_at };

  const parts = [];
  if (lastUpdates.full) parts.push(`Full ${formatUpdateStamp(lastUpdates.full)}`);
  if (lastUpdates.refresh) parts.push(`Refresh ${formatUpdateStamp(lastUpdates.refresh)}`);

  // A gameday live tick (engine/live.py) writes live.json's own updated_at
  // separately from meta.json's generated_at (only a full/refresh run
  // touches that) - shown only when it's actually newer, so a stale
  // live.json from before the week rolled over doesn't claim to be "live".
  // Full month/day/time like every other entry here (not time-only) - this
  // used to be a same-day suffix tacked onto a single "Updated {date}" line,
  // where the date was implied; now it's its own standalone footer entry,
  // so it needs its own date too.
  const liveUpdatedAt = data.live?.updated_at;
  if (liveUpdatedAt && new Date(liveUpdatedAt) > generated) {
    parts.push(`Live ${formatUpdateStamp(liveUpdatedAt)}`);
  }
  if (lastUpdates.faab) parts.push(`FAAB ${formatUpdateStamp(lastUpdates.faab)}`);
  document.getElementById("footer-updates").textContent = parts.join(" · ");

  const banner = document.getElementById("warning-banner");
  const warnings = leagueMeta.warnings || [];
  if (warnings.length) {
    banner.hidden = false;
    banner.textContent = warnings.join(" · ");
  } else {
    banner.hidden = true;
  }
}

async function selectLeague(slug) {
  currentLeagueSlug = slug;
  setLastLeague(slug);
  document.getElementById("league-select").value = slug;

  currentData = await loadLeagueData(slug);
  renderHeader(currentData);
  renderActiveView();
}

// Keeps --sticky-top in sync with .app-top's real rendered height, so
// anything that needs to stick below the frozen header/banner/nav (e.g.
// rankings.js's filter row + table header) doesn't have to guess it - the
// banner toggling and the header wrapping on narrow screens both change it.
function watchStickyTopHeight() {
  const top = document.getElementById("app-top");
  const setVar = () => document.documentElement.style.setProperty("--sticky-top", `${top.offsetHeight}px`);
  setVar();
  new ResizeObserver(setVar).observe(top);
}

async function init() {
  applyTheme(getTheme());
  wireTabs();
  watchStickyTopHeight();
  const leagues = await loadLeagues();
  if (!leagues.length) {
    document.body.innerHTML = "<p style='padding:20px'>No leagues configured yet.</p>";
    return;
  }

  const leagueSelect = document.getElementById("league-select");
  leagueSelect.innerHTML = leagues.map((l) => `<option value="${l.slug}">${escapeHtml(l.name)}</option>`).join("");
  leagueSelect.addEventListener("change", (e) => {
    setHash(e.target.value, currentView);
    selectLeague(e.target.value);
  });

  const hash = parseHash();
  const initialLeague = leagues.find((l) => l.slug === hash.league) ? hash.league : getLastLeague() && leagues.find((l) => l.slug === getLastLeague()) ? getLastLeague() : leagues[0].slug;
  const initialView = VIEWS[hash.view] ? hash.view : "strength";
  currentView = initialView;
  document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === initialView));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === `tab-${initialView}`));

  await selectLeague(initialLeague);
}

init().catch((err) => {
  console.error(err);
  document.body.innerHTML = `<p style="padding:20px; color:#c0392b">Failed to load league data: ${escapeHtml(err.message)}</p>`;
});

// PWA installability + an offline-capable app shell (see sw.js's own
// docstring for why league data itself is deliberately NOT cache-first
// there). Registered after load, not blocking init() - a slow/failed SW
// registration should never delay the actual app from rendering.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch((err) => console.error("Service worker registration failed:", err));
  });
}
