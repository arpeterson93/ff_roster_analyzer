import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { colorForRatio, teamLabel } from "./colors.js";

function seedBar(seedProbs) {
  const seeds = Object.keys(seedProbs).sort((a, b) => Number(a) - Number(b));
  const segments = seeds
    .map((s) => {
      const pct = seedProbs[s] * 100;
      if (pct < 0.5) return "";
      const ratio = 1 - (Number(s) - 1) / Math.max(1, seeds.length - 1);
      return `<div style="width:${pct}%; background:${colorForRatio(ratio)};" title="Seed ${s}: ${fmt(pct, 0)}%"></div>`;
    })
    .join("");
  return `<div style="display:flex; height:16px; border-radius:4px; overflow:hidden; width:160px;">${segments}</div>`;
}

export function renderStandings(container, data, slug) {
  const teamsById = data.teamsById;
  const yourTeamId = getYourTeam(slug);
  const rows = data.standings
    .slice()
    .sort((a, b) => b.wins - a.wins || b.points_for - a.points_for)
    .map((s) => {
      const team = teamsById.get(s.team_id);
      const isYours = s.team_id === yourTeamId;
      return `<tr class="${isYours ? "your-team-row" : ""}">
        <td>${isYours ? "<strong>" : ""}${escapeHtml(team ? teamLabel(team) : s.team_id)}${isYours ? "</strong>" : ""}</td>
        <td>${escapeHtml(s.division)}</td>
        <td>${s.wins}-${s.losses}${s.ties ? "-" + s.ties : ""}</td>
        <td>${fmt(s.points_for, 1)}</td>
        <td>${fmt(s.expected_wins, 1)}</td>
        <td>${fmt(s.playoff_odds * 100, 0)}%</td>
        <td>${fmt(s.bye_odds * 100, 0)}%</td>
        <td>${fmt(s.division_win_odds * 100, 0)}%</td>
        <td>${seedBar(s.seed_probs)}</td>
      </tr>`;
    })
    .join("");

  container.innerHTML = `
    <div class="card">
      <h2>Standings &amp; playoff odds <span class="muted small">(${data.standings[0] ? data.standings[0].iterations.toLocaleString() : 0} simulations)</span></h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Team</th><th>Div</th><th>Record</th><th>PF</th><th>xWins</th><th>Playoff%</th><th>Bye%</th><th>Div win%</th><th>Seed dist.</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
  `;
}
