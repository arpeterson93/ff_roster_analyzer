import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { POSITION_COLOR, sortByPositionOrder, colorForRatio, shortName, teamLabel, weeklyProjection, winProbColor } from "./colors.js";

function posTag(pos) {
  return `<span class="pos-tag" style="background:${POSITION_COLOR[pos] || "#888"}">${pos}</span>`;
}

// A played week's actual score always wins over any projection; otherwise
// this defers to weeklyProjection's site-wide rule (current week -> ESPN's
// number, every other week -> our own proprietary one - see colors.js).
// p.weekly only spans current_week..final_week, so a played week before
// that has no entry here at all - callers showing an `actual: true` lineup
// (see actualLineupWeek/box-score weeks) should prefer that lineup's own
// `points` map and only fall back to this for projected weeks.
function pointsForWeek(p, week, currentWeek) {
  const w = (p.weekly || []).find((e) => e.week === week);
  if (w && w.actual) return w.actual.points;
  return weeklyProjection(p, week, currentWeek);
}

// Prefers a real lineup's own recorded points over p.weekly, which doesn't
// cover weeks before current_week at all: a past week's box-score snapshot's
// final "points", or - for the current week once a gameday live tick is
// running - that tick's own "projected_points" (points-so-far plus the
// player's remaining-game-fraction share of their pregame projection,
// computed server-side by engine/standings_stage.py's live_team_mean_sd).
// Plain live points-so-far ("points") is deliberately never used here - 0
// for anyone who hasn't played yet reads as broken rather than projected.
// Falls back to pointsForWeek whenever neither is available (no live tick
// running yet this week, or a past snapshot missing this particular player).
function pointsForPlayerInLineup(p, week, lineupWeek, currentWeek) {
  const key = week === currentWeek ? "projected_points" : "points";
  const recorded = lineupWeek && lineupWeek[key] ? lineupWeek[key][p.id] : undefined;
  return recorded !== undefined ? recorded : pointsForWeek(p, week, currentWeek);
}

// A past week's real ESPN lineup (see engine/pipeline.py) can name a player
// who's since been dropped from every roster and isn't in the current
// top-N free-agent pull, so they're missing from data.playersById (the
// site's normal "current universe" of rostered + top free agents). They're
// still resolvable - the pipeline stashes a minimal name/position/team stub
// for exactly this case in lineups._unrostered_players - just without the
// projections/ownership/etc. fields a current player has, which is fine
// here since a past week's lineup only ever needs name/position/points.
function resolvePlayer(pid, data) {
  return (
    data.playersById.get(pid) ||
    (data.lineups._unrostered_players || {})[pid] ||
    // A gameday live starter who wasn't in this build's own players.json
    // (added/promoted after the last full/refresh run) - see engine/live.py.
    (data.live?.players || {})[pid]
  );
}

// ESPN's raw lineupSlot strings -> the same base labels engine/lineup.py's
// display_label uses for its own computed-lineup slot keys (D/ST -> DST,
// the three flex-eligible composites -> FLEX) - so a real ESPN slot and a
// computed-optimal slot instance line up under one label space below.
function slotBase(rawSlot) {
  if (rawSlot === "D/ST") return "DST";
  if (rawSlot === "RB/WR/TE" || rawSlot === "RB/WR" || rawSlot === "WR/TE") return "FLEX";
  if (rawSlot === "TQB") return "QB";
  return rawSlot;
}

function actualStarters(teamId, data) {
  return data.players.filter((p) => p.fantasy_team_id === teamId && p.lineup_slot && p.lineup_slot !== "BE" && p.lineup_slot !== "IR");
}

// The real, ESPN-set lineup for the CURRENT week - not our optimal computed
// one - since that's the lineup that's actually live for a week already
// underway, built from the live current-roster lineup_slot (data.players).
// Already-played weeks get their own real lineup too, but from the pipeline's
// box-score snapshot instead (data.lineups[...].weeks[week], flagged
// `actual: true`) since ESPN's live roster state has moved on since then.
// Future weeks have no "actual" lineup to pull (ESPN can't know it yet), so
// those keep using the computed optimal lineup. Shaped like a computed
// week's {slots, bench} so all three paths render identically.
function actualLineupWeek(teamId, data) {
  const roster = data.players.filter((p) => p.fantasy_team_id === teamId);
  const byBase = new Map();
  actualStarters(teamId, data)
    .slice()
    .sort((a, b) => (b.espn_projected_week || 0) - (a.espn_projected_week || 0))
    .forEach((p) => {
      const base = slotBase(p.lineup_slot);
      if (!byBase.has(base)) byBase.set(base, []);
      byBase.get(base).push(p.id);
    });
  const slots = {};
  byBase.forEach((ids, base) => {
    ids.forEach((id, i) => {
      slots[ids.length > 1 ? `${base}${i + 1}` : base] = id;
    });
  });
  const bench = roster.filter((p) => !p.lineup_slot || p.lineup_slot === "BE" || p.lineup_slot === "IR").map((p) => p.id);
  return { slots, bench };
}

// A gameday live tick (engine/live.py) writes live.json for the CURRENT
// week only - .week lets a stale live.json (week rolled over, next full/
// refresh run hasn't cleaned it up yet - see run_league's own stale-live.json
// deletion) be told apart from a genuinely current one.
function isLiveDataCurrent(data) {
  return !!data.live && data.live.week === data.meta.current_week;
}

// live.json's own remaining_fraction (see engine/live.py, sourced from
// ingest.espn_scoreboard.fetch_remaining_game_fraction), keyed by NFL team
// abbreviation - 1.0 before kickoff, 0.0 once final, in between mid-game.
// Missing entirely for a bye-week team or when there's no live tick running.
function remainingFractionFor(p, data) {
  return isLiveDataCurrent(data) ? data.live.remaining_fraction?.[p.nfl_team] : undefined;
}

function lineupWeekFor(teamId, week, data) {
  if (week === data.meta.current_week) {
    if (isLiveDataCurrent(data)) {
      const liveTeam = data.live.teams[String(teamId)];
      if (liveTeam) return liveTeam;
    }
    return actualLineupWeek(teamId, data);
  }
  const lineupTeam = data.lineups[String(teamId)];
  return lineupTeam ? lineupTeam.weeks[String(week)] : null;
}

function scoreOf(m, side, data) {
  if (m.played) return side === "home" ? m.home_score : m.away_score;
  const teamId = side === "home" ? m.home_team_id : m.away_team_id;
  // Deliberately ignores schedule.json's own live home_score/away_score (a
  // gameday tick's sum of real per-player points so far, 0 for anyone who
  // hasn't played yet) in favor of summing the same per-player numbers the
  // expanded lineup view shows (pointsForPlayerInLineup) over the same
  // live-aware lineup composition (lineupWeekFor) - so this row's total and
  // the expanded table's total always agree, and the current week shows a
  // meaningful in-progress projection instead of a partial box score.
  if (m.week === data.meta.current_week) {
    const lineupWeek = lineupWeekFor(teamId, m.week, data);
    const pids = Object.values((lineupWeek && lineupWeek.slots) || {});
    return pids.reduce((acc, pid) => {
      const p = resolvePlayer(pid, data);
      return p ? acc + (pointsForPlayerInLineup(p, m.week, lineupWeek, data.meta.current_week) || 0) : acc;
    }, 0);
  }
  return (data.lineups[String(teamId)]?.weeks[String(m.week)] || {}).total ?? null;
}

// One shared table, home team's players/scores on the outside-left and away
// team's on the outside-right, mirrored around a single center "slot" column
// - rather than two separate side-by-side tables - so the two lineups read
// as one symmetric comparison instead of two unrelated lists.
function symmetricLineupHtml(homeTeamId, awayTeamId, week, data) {
  const home = lineupWeekFor(homeTeamId, week, data);
  const away = lineupWeekFor(awayTeamId, week, data);
  if (!home || !away) return `<p class="muted small">No projection for this week.</p>`;

  const streamed = new Set([...(home.streamed || []), ...(away.streamed || [])]);
  const keys = [...new Set([...Object.keys(home.slots || {}), ...Object.keys(away.slots || {})])].sort(
    (a, b) => sortByPositionOrder(a, b, (s) => s.replace(/\d+$/, "")) || a.localeCompare(b)
  );

  const playerCell = (pid) => {
    if (!pid) return "";
    const p = resolvePlayer(pid, data);
    if (!p) return "";
    const streamBadge = streamed.has(pid) ? ` <span class="pill small stream-badge" title="Free-agent bye-week fill-in, not on your roster">FA</span>` : "";
    const irBadge = p.lineup_slot === "IR" ? ` <span class="muted small">(IR)</span>` : "";
    // Mobile drops the position chip and abbreviates to "F. Last" (see
    // styles.css's ".lineup-player .pos-tag"/".full-name"/".short-name"
    // rules) - this table is already tight with two full lineups mirrored
    // side by side, and a chip + full name doesn't fit a phone width.
    const nameHtml = `<span class="full-name">${escapeHtml(p.name)}</span><span class="short-name">${escapeHtml(shortName(p.name))}</span>`;
    return `${posTag(p.position)} ${nameHtml}${streamBadge}${irBadge}`;
  };
  const scoreCell = (pid, lineupWeek) => {
    const p = pid && resolvePlayer(pid, data);
    if (!p) return "–";
    const projected = pointsForPlayerInLineup(p, week, lineupWeek, data.meta.current_week);
    // Actual-so-far is only worth surfacing alongside the projection while
    // this player's own NFL game is actually mid-play (0 < frac < 1) - a
    // player who hasn't kicked off yet still reads "0 so far" as broken
    // rather than informative, and once their game's final the two numbers
    // already agree (see pointsForPlayerInLineup's projected_points blend),
    // so the second line would be pure redundant clutter.
    const frac = remainingFractionFor(p, data);
    const actual = frac > 0 && frac < 1 ? lineupWeek?.points?.[p.id] : undefined;
    const liveLine = actual !== undefined ? `<span class="lineup-score-live">${fmt(actual, 1)} now</span>` : "";
    return `${fmt(projected, 1)}${liveLine}`;
  };
  const lineupRow = (hPid, aPid, middleLabel) => {
    const rowClass = streamed.has(hPid) || streamed.has(aPid) ? "streamed-row" : "";
    return `<tr class="${rowClass}">
      <td class="lineup-player lineup-player-home">${playerCell(hPid)}</td>
      <td class="lineup-score">${scoreCell(hPid, home)}</td>
      <td class="lineup-slot muted small">${middleLabel}</td>
      <td class="lineup-score">${scoreCell(aPid, away)}</td>
      <td class="lineup-player lineup-player-away">${playerCell(aPid)}</td>
    </tr>`;
  };

  const starterRows = keys.map((key) => lineupRow((home.slots || {})[key], (away.slots || {})[key], key.replace(/\d+$/, ""))).join("");

  // Bench sizes/order between the two teams have no natural row-for-row
  // pairing the way starting slots do (each side is independently sorted by
  // its own points) - paired by position in the list purely to share the
  // same row-per-line layout as the starters above, not because a given row
  // means anything about the two players relative to each other.
  const homeBench = home.bench || [];
  const awayBench = away.bench || [];
  const benchRowCount = Math.max(homeBench.length, awayBench.length);
  const benchRows = Array.from({ length: benchRowCount }, (_, i) => lineupRow(homeBench[i], awayBench[i], "")).join("");

  return `
    <table class="lineup-symmetric">
      <colgroup><col style="width:35%"><col style="width:8%"><col style="width:14%"><col style="width:8%"><col style="width:35%"></colgroup>
      <tbody>
        ${starterRows}
        ${benchRowCount ? `<tr class="week-divider"><td colspan="5">Bench</td></tr>${benchRows}` : ""}
      </tbody>
    </table>
  `;
}

// showWeek: the single-team filtered view (see renderSchedule) has exactly
// one row per week for that team, so a per-week "Week N" divider row (the
// all-teams view's own grouping - see draw()) would be redundant scaffolding
// around a single row; a leading WK cell on the row itself carries the same
// information without the extra row, matching how a real season schedule
// table reads for one team.
function matchupRow(m, data, expandedKey, yourTeamId, avg, spread, showWeek) {
  const home = data.teamsById.get(m.home_team_id);
  const away = data.teamsById.get(m.away_team_id);
  const key = `${m.week}-${m.home_team_id}-${m.away_team_id}`;
  const isYours = m.home_team_id === yourTeamId || m.away_team_id === yourTeamId;
  const colspan = showWeek ? 5 : 4;
  const weekCell = showWeek
    ? `<td class="schedule-cell small muted">${m.week}${m.week === data.meta.current_week ? " (current)" : ""}${m.live ? " (live)" : ""}</td>`
    : "";

  const homeScore = scoreOf(m, "home", data);
  const awayScore = scoreOf(m, "away", data);
  const scoreCell = (v) => {
    if (v === null || v === undefined) return `<span class="muted">–</span>`;
    const ratio = spread > 0 ? Math.max(0, Math.min(1, 0.5 + (v - avg) / spread)) : 0.5;
    return `<span class="heat-cell" style="background:${colorForRatio(ratio)}; display:inline-block; width:100%;">${fmt(v, 1)}</span>`;
  };

  // Populated for the current week's real matchups AND every other
  // not-yet-played week (see engine/pipeline.py's _win_pcts) - the current
  // week uses live in-game state, every other one falls back to plain
  // pre-game projections; either way, null for an already-decided game
  // (the real score already answers the question).
  const winPct = (pct) => (pct === null || pct === undefined ? "" : `<div class="muted small">${fmt(pct * 100, 0)}% to win</div>`);

  // A sliding indicator rather than a proportional stacked bar: the
  // colored bar is ALWAYS exactly half the track's width - at 50/50 it
  // sits centered on the track's midpoint (25%-75%); the more lopsided the
  // matchup, the further it slides toward the favored side (fully flush
  // left at 100% home, fully flush right at 100% away), while staying the
  // same length throughout. Each half is colored by THAT team's own win
  // probability (winProbColor - red below 25%, green above 75%, blended
  // between) rather than a fixed home/away color pair, so the bar itself
  // reads as "how good are this team's real chances" at a glance.
  const winProbBar = (homePct, awayPct) =>
    homePct === null || homePct === undefined
      ? ""
      : `<div class="winprob-bar" title="${fmt(homePct * 100, 0)}% / ${fmt(awayPct * 100, 0)}%">
          <div class="winprob-center-line"></div>
          <div class="winprob-slider" style="left:${50 - 50 * homePct}%;">
            <div class="winprob-seg" style="width:${homePct * 100}%; background:${winProbColor(homePct)}"></div>
            <div class="winprob-seg" style="width:${awayPct * 100}%; background:${winProbColor(awayPct)}"></div>
          </div>
        </div>`;

  const expanded = expandedKey === key;
  return `
    <tr class="clickable-row schedule-row ${isYours ? "your-team-row" : ""}" data-key="${key}">
      ${weekCell}
      <td class="schedule-cell">${escapeHtml(teamLabel(home) || m.home_team_id)}${winPct(m.home_win_pct)}</td>
      <td class="schedule-cell small">${scoreCell(homeScore)}</td>
      <td class="schedule-cell small">${scoreCell(awayScore)}</td>
      <td class="schedule-cell">${escapeHtml(teamLabel(away) || m.away_team_id)}${winPct(m.away_win_pct)}</td>
    </tr>
    ${m.home_win_pct !== null && m.home_win_pct !== undefined ? `<tr class="winprob-row"><td colspan="${colspan}">${winProbBar(m.home_win_pct, m.away_win_pct)}</td></tr>` : ""}
    ${expanded
      ? `<tr><td colspan="${colspan}">
          <div class="lineup-symmetric-heading">
            <h3 class="small">${escapeHtml(teamLabel(home))}</h3>
            <h3 class="small">${escapeHtml(teamLabel(away))}</h3>
          </div>
          ${symmetricLineupHtml(m.home_team_id, m.away_team_id, m.week, data)}
        </td></tr>`
      : ""}
  `;
}

export function renderSchedule(container, data, slug) {
  const state = { expandedKey: null, teamFilter: "ALL" };
  const weeks = [...new Set((data.schedule || []).map((m) => m.week))].sort((a, b) => a - b);
  const yourTeamId = getYourTeam(slug);

  // Normalize the red/yellow/green score coloring against the spread of
  // every score shown on the page (actual + projected), not a fixed scale.
  const allScores = [];
  (data.schedule || []).forEach((m) => {
    const h = scoreOf(m, "home", data);
    const a = scoreOf(m, "away", data);
    if (h !== null) allScores.push(h);
    if (a !== null) allScores.push(a);
  });
  const avg = allScores.length ? allScores.reduce((s, v) => s + v, 0) / allScores.length : 0;
  const spread = allScores.length ? Math.max(...allScores.map((v) => Math.abs(v - avg)), 1) : 1;

  function draw() {
    const teamFilter = state.teamFilter && state.teamFilter !== "ALL" ? Number(state.teamFilter) : null;

    // Filtered to one team: every week has exactly one matchup for them, so
    // a per-week divider row would just be scaffolding around a single row -
    // one flat table instead, a leading WK cell per row (see matchupRow's
    // showWeek) standing in for the divider's own week label.
    const rows = teamFilter
      ? data.schedule
          .filter((m) => m.home_team_id === teamFilter || m.away_team_id === teamFilter)
          .slice()
          .sort((a, b) => a.week - b.week)
          .map((m) => matchupRow(m, data, state.expandedKey, yourTeamId, avg, spread, true))
          .join("")
      : // One shared table for every week (not a separate table per week) with
        // fixed column widths, so the home/score/away columns land in the same
        // horizontal position throughout - team-name length can't stagger them.
        weeks
          .map((w) => {
            const weekMatchups = data.schedule.filter((m) => m.week === w);
            const allProjected = weekMatchups.length > 0 && weekMatchups.every((m) => !m.played);
            const anyLive = weekMatchups.some((m) => m.live);
            const statusLabel = anyLive ? " (live)" : allProjected ? " - Projected" : "";
            const weekHeader = `<tr class="week-divider"><td colspan="4">Week ${w}${w === data.meta.current_week ? " (current)" : ""}${statusLabel}</td></tr>`;
            const matchups = weekMatchups.map((m) => matchupRow(m, data, state.expandedKey, yourTeamId, avg, spread, false)).join("");
            return weekHeader + matchups;
          })
          .join("");

    const teamOptions = data.teams
      .slice()
      .sort((a, b) => teamLabel(a).localeCompare(teamLabel(b)))
      .map((t) => `<option value="${t.team_id}" ${teamFilter === t.team_id ? "selected" : ""}>${escapeHtml(teamLabel(t))}</option>`)
      .join("");

    container.innerHTML = `
      <div class="card">
        <h2>Schedule</h2>
        <p class="muted small">Click a matchup to see each team's lineup that week - the actual ESPN-set starters for played weeks, optimal projected lineups for the current/future weeks.</p>
        <div class="select-row">
          <label>Team:</label>
          <select id="schedule-team-filter"><option value="ALL">All teams</option>${teamOptions}</select>
        </div>
        <div class="table-wrap">
          <table class="schedule-table">
            ${teamFilter
              ? `<colgroup><col style="width:10%"><col style="width:27%"><col style="width:17%"><col style="width:17%"><col style="width:27%"></colgroup>`
              : `<colgroup><col style="width:32%"><col style="width:18%"><col style="width:18%"><col style="width:32%"></colgroup>`}
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;

    container.querySelector("#schedule-team-filter").addEventListener("change", (e) => {
      state.teamFilter = e.target.value;
      draw();
    });

    container.querySelectorAll("tr.schedule-row").forEach((row) => {
      row.addEventListener("click", () => {
        state.expandedKey = state.expandedKey === row.dataset.key ? null : row.dataset.key;
        draw();
      });
    });
  }
  draw();
}
