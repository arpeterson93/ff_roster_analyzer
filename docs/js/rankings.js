import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { POSITION_COLOR, INJURY_BADGE, opponentCellHtml, sortByPositionOrder, teamLabel } from "./colors.js";
import { openPlayerModal } from "./playermodal.js";
import { openPointsAgainstModal } from "./pointsagainstmodal.js";
import { loadWatchlist, setWatched } from "./watchlist.js";

const FLEX_POSITIONS = ["RB", "WR", "TE"];

function healthBadge(status) {
  const letters = INJURY_BADGE[status];
  return letters ? `<span class="pill small" style="background:var(--red-600)">${letters}</span>` : "";
}

function ownerName(p, teamsById) {
  if (p.fantasy_team_id === null) return "FA";
  return teamLabel(teamsById.get(p.fantasy_team_id));
}

function thisWeekEntry(p, currentWeek) {
  return (p.weekly || []).find((w) => w.week === currentWeek) || {};
}

function columns(data, watched) {
  const yourTeamId = getYourTeam(data.meta.slug);
  return [
    ...(yourTeamId !== null
      ? [{
          key: "_watch", label: "★", sortable: false,
          fmt: (_v, p) => {
            const isWatched = watched.has(p.id);
            return `<span class="watch-star ${isWatched ? "watched" : ""}" data-watch-toggle data-player-id="${p.id}" title="${isWatched ? "Remove from watch list" : "Add to watch list"}">${isWatched ? "★" : "☆"}</span>`;
          },
        }]
      : []),
    {
      key: "ros_overall_rank", label: "Rank",
      fmt: (v, p) => (v === null ? "–" : `${v} <span class="muted small">(${p.position}${p.ros_pos_rank ?? "–"})</span>`),
    },
    {
      key: "name", label: "Player",
      fmt: (v, p) => `<span class="pos-tag" style="background:${POSITION_COLOR[p.position] || "#888"}">${p.position}</span> ${escapeHtml(v)} ${healthBadge(p.injury_status)}`,
    },
    { key: "nfl_team", label: "Team" },
    {
      key: "_opp", label: "Opp", sortable: false,
      fmt: (_v, p) => `<span data-opp-cell data-team="${escapeHtml(thisWeekEntry(p, data.meta.current_week).opponent || "")}" data-pos="${p.position}">${opponentCellHtml(thisWeekEntry(p, data.meta.current_week))}</span>`,
    },
    { key: "espn_projected_week", label: "ESPN wk", fmt: (v) => fmt(v, 1) },
    { key: "fp_week_projected_pts", label: "FP wk", fmt: (v) => (v === null || v === undefined ? "–" : fmt(v, 1)) },
    { key: "baseline_ppg", label: "Baseline", fmt: (v) => fmt(v, 1) },
    { key: "ros_total", label: "ROS", fmt: (v) => fmt(v, 1) },
    { key: "fp_week_pos_rank_label", label: "WK RK", fmt: (v) => v ?? "–" },
    { key: "percent_owned", label: "Own%", fmt: (v) => fmt(v, 1) },
    {
      key: "percent_owned_delta", label: "Own Δ",
      fmt: (v) => (v === null || v === undefined ? "–" : `${v >= 0 ? "+" : ""}${fmt(v, 1)}`),
    },
    { key: "owner", label: "Owner", fmt: (v) => escapeHtml(v) },
    {
      key: "_faab_est", label: "FAAB Est.", sortable: false,
      fmt: (_v, p) => {
        const est = (data.faabEstimates || {})[p.id];
        if (!est) return "–";
        const d = est.distribution;
        return d
          ? `$${fmt(est.comp_based, 0)} <span class="muted small">($${fmt(d.p25, 0)}–$${fmt(d.p75, 0)})</span>`
          : `$${fmt(est.comp_based, 0)}`;
      },
    },
    ...(yourTeamId !== null
      ? [{
          key: "_fa_value", label: "NMD",
          fmt: (_v, p) => {
            if (p.fantasy_team_id !== null) return "–";
            const teamValues = (data.faValues || {})[String(yourTeamId)] || {};
            const gain = teamValues[p.id];
            return gain === undefined ? "–" : `${gain >= 0 ? "+" : ""}${fmt(gain, 1)}`;
          },
        }]
      : []),
  ];
}

let sortState = { key: "ros_overall_rank", dir: 1 };

function sortValue(p, key, data) {
  if (key === "owner") return ownerName(p, data.teamsById);
  if (key === "_fa_value") {
    const yourTeamId = getYourTeam(data.meta.slug);
    if (yourTeamId === null || p.fantasy_team_id !== null) return -Infinity;
    const v = ((data.faValues || {})[String(yourTeamId)] || {})[p.id];
    return v === undefined ? -Infinity : v;
  }
  return p[key];
}

function render(container, data, filters, watched) {
  const cols = columns(data, watched);
  let rows = data.players.filter((p) => {
    if (filters.watchedOnly && !watched.has(p.id)) return false;
    if (filters.faOnly && p.fantasy_team_id !== null) return false;
    if (filters.team !== "ALL" && p.nfl_team !== filters.team) return false;
    if (filters.position === "ALL") return true;
    if (filters.position === "FLEX") return FLEX_POSITIONS.includes(p.position);
    return p.position === filters.position;
  });

  rows = rows.slice().sort((a, b) => {
    let av = sortValue(a, sortState.key, data);
    let bv = sortValue(b, sortState.key, data);
    if (av === null || av === undefined) av = sortState.dir === 1 ? Infinity : -Infinity;
    if (bv === null || bv === undefined) bv = sortState.dir === 1 ? Infinity : -Infinity;
    if (typeof av === "string") return av.localeCompare(bv) * sortState.dir;
    return (av - bv) * sortState.dir;
  });

  const header = cols
    .map((c) => `<th data-key="${c.key}" ${c.sortable === false ? "" : ""}>${c.label}${sortState.key === c.key ? (sortState.dir === 1 ? " ▲" : " ▼") : ""}</th>`)
    .join("");
  const body = rows
    .slice(0, 300)
    .map((p) => {
      const isYours = getYourTeam(data.meta.slug) !== null && p.fantasy_team_id === getYourTeam(data.meta.slug);
      const cells = cols.map((c) => `<td>${c.fmt ? c.fmt(c.key === "owner" ? ownerName(p, data.teamsById) : p[c.key], p) : p[c.key] ?? "–"}</td>`).join("");
      return `<tr data-player-id="${p.id}" class="clickable-row ${isYours ? "your-team-row" : ""}">${cells}</tr>`;
    })
    .join("");

  const wrap = container.querySelector("#rankings-table-wrap");
  wrap.innerHTML = `<table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table>`;
  wrap.querySelectorAll("th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      sortState = { key, dir: sortState.key === key ? -sortState.dir : key === "ros_overall_rank" ? 1 : -1 };
      render(container, data, filters, watched);
    });
  });
  wrap.querySelectorAll("tr[data-player-id]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("[data-opp-cell]") || e.target.closest("[data-watch-toggle]")) return;
      const p = data.playersById.get(row.dataset.playerId);
      if (p) openPlayerModal(p, data);
    });
  });
  wrap.querySelectorAll("[data-watch-toggle]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const pid = el.dataset.playerId;
      const yourTeamId = getYourTeam(data.meta.slug);
      const nowWatched = !watched.has(pid);
      if (nowWatched) watched.add(pid);
      else watched.delete(pid);
      setWatched(data.meta.slug, yourTeamId, pid, nowWatched);
      render(container, data, filters, watched);
    });
  });
  wrap.querySelectorAll("[data-opp-cell]").forEach((cell) => {
    cell.addEventListener("click", (e) => {
      e.stopPropagation();
      const team = cell.dataset.team;
      if (team) openPointsAgainstModal(team, cell.dataset.pos, data);
    });
  });
}

export function renderRankings(container, data, slug) {
  const yourTeamId = getYourTeam(slug);
  const positions = ["ALL", ...[...data.meta.positions].sort((a, b) => sortByPositionOrder(a, b)), "FLEX"];
  const nflTeams = ["ALL", ...new Set(data.players.map((p) => p.nfl_team).filter(Boolean))].sort();
  container.innerHTML = `
    <div class="card">
      <div class="select-row rankings-filters" id="rankings-filters">
        <select id="rankings-pos-filter">${positions.map((p) => `<option value="${p}">${p}</option>`).join("")}</select>
        <select id="rankings-team-filter">${nflTeams.map((t) => `<option value="${t}">${t === "ALL" ? "All NFL teams" : t}</option>`).join("")}</select>
        <label><input type="checkbox" id="rankings-fa-only" /> Free agents only</label>
        ${yourTeamId !== null ? `<label><input type="checkbox" id="rankings-watched-only" /> Watch list only</label>` : ""}
      </div>
      <div class="table-wrap" id="rankings-table-wrap"></div>
    </div>
  `;

  // Keeps the table header's own sticky offset (--rankings-filters-h, see
  // styles.css) in sync with this row's real height - it wraps onto two
  // lines on narrow screens, so a fixed guess would leave a gap or overlap.
  const filtersEl = container.querySelector("#rankings-filters");
  const setFiltersHeightVar = () => document.documentElement.style.setProperty("--rankings-filters-h", `${filtersEl.offsetHeight}px`);
  setFiltersHeightVar();
  new ResizeObserver(setFiltersHeightVar).observe(filtersEl);

  const filters = { position: "ALL", faOnly: false, team: "ALL", watchedOnly: false };
  let watched = new Set();
  render(container, data, filters, watched);

  if (yourTeamId !== null) {
    loadWatchlist(slug, yourTeamId).then((set) => {
      watched = set;
      render(container, data, filters, watched);
    });
  }

  container.querySelector("#rankings-pos-filter").addEventListener("change", (e) => {
    filters.position = e.target.value;
    render(container, data, filters, watched);
  });
  container.querySelector("#rankings-team-filter").addEventListener("change", (e) => {
    filters.team = e.target.value;
    render(container, data, filters, watched);
  });
  container.querySelector("#rankings-fa-only").addEventListener("change", (e) => {
    filters.faOnly = e.target.checked;
    render(container, data, filters, watched);
  });
  container.querySelector("#rankings-watched-only")?.addEventListener("change", (e) => {
    filters.watchedOnly = e.target.checked;
    render(container, data, filters, watched);
  });
}
