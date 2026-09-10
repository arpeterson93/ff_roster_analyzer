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

function renderHeader(leagueMeta, leagueName) {
  document.getElementById("header-league-name").textContent = leagueName;
  document.getElementById("header-week").textContent = `Week ${leagueMeta.current_week}`;
  const generated = new Date(leagueMeta.generated_at);
  document.getElementById("header-generated").textContent = `Updated ${generated.toLocaleString()}`;

  const banner = document.getElementById("warning-banner");
  const warnings = leagueMeta.warnings || [];
  if (warnings.length) {
    banner.hidden = false;
    banner.textContent = warnings.join(" · ");
  } else {
    banner.hidden = true;
  }
}

async function selectLeague(slug, leagues) {
  currentLeagueSlug = slug;
  setLastLeague(slug);
  document.getElementById("league-select").value = slug;

  const leagueInfo = leagues.find((l) => l.slug === slug);
  currentData = await loadLeagueData(slug);
  renderHeader(currentData.meta, leagueInfo.name);
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
    selectLeague(e.target.value, leagues);
  });
  leagueSelect.hidden = leagues.length <= 1;

  const hash = parseHash();
  const initialLeague = leagues.find((l) => l.slug === hash.league) ? hash.league : getLastLeague() && leagues.find((l) => l.slug === getLastLeague()) ? getLastLeague() : leagues[0].slug;
  const initialView = VIEWS[hash.view] ? hash.view : "strength";
  currentView = initialView;
  document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === initialView));
  document.querySelectorAll(".tabpanel").forEach((p) => p.classList.toggle("active", p.id === `tab-${initialView}`));

  await selectLeague(initialLeague, leagues);
}

init().catch((err) => {
  console.error(err);
  document.body.innerHTML = `<p style="padding:20px; color:#c0392b">Failed to load league data: ${escapeHtml(err.message)}</p>`;
});
