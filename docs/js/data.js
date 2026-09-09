// Fetches data/leagues.json then a selected league's files, cached in memory
// for the lifetime of the page (no need to re-fetch on tab switches).

const cache = new Map();

export async function loadLeagues() {
  const res = await fetch("data/leagues.json");
  if (!res.ok) throw new Error(`failed to load leagues.json: ${res.status}`);
  return res.json();
}

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`failed to load ${path}: ${res.status}`);
  return res.json();
}

export async function loadLeagueData(slug) {
  if (cache.has(slug)) return cache.get(slug);
  const base = `data/${slug}`;
  const [meta, players, teams, lineups, matchups, standings, recentResults, pointsAgainst, faValues, schedule] = await Promise.all([
    fetchJson(`${base}/meta.json`),
    fetchJson(`${base}/players.json`),
    fetchJson(`${base}/teams.json`),
    fetchJson(`${base}/lineups.json`),
    fetchJson(`${base}/matchups.json`),
    fetchJson(`${base}/standings.json`),
    fetchJson(`${base}/recent_results.json`),
    fetchJson(`${base}/points_against.json`),
    fetchJson(`${base}/fa_values.json`),
    fetchJson(`${base}/schedule.json`),
  ]);
  const playersById = new Map(players.map((p) => [p.id, p]));
  const teamsById = new Map(teams.map((t) => [t.team_id, t]));
  const data = {
    meta, players, playersById, teams, teamsById, lineups, matchups, standings,
    recentResults, pointsAgainst, faValues, schedule,
  };
  cache.set(slug, data);
  return data;
}
