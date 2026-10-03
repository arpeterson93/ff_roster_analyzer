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

// Exported for standings.js's own ROS PF/PA columns, which need this exact
// same "real score if played, live-aware in-progress sum for the current
// week, optimal-lineup projection for every other remaining week" number -
// not a simpler re-derivation that could silently drift from what the
// Schedule tab itself shows for the same matchup.
export function scoreOf(m, side, data) {
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

// {actual, projected} for one player in one week - shared by the expanded
// lineup's per-player cell and the matchup row's team total (see
// actualAndProjectedTotal below), so both read off the exact same numbers
// rather than two independent, driftable derivations.
//
// actual: real points once this player's own game has started - "-" (left
// undefined) before kickoff, or for a future week. A non-current played
// week's lineupWeek (the pipeline's own box-score snapshot, "actual":
// true) already carries the real final total in its own `points` map;
// the current week gets the same number from the live gameday tick
// (engine/live.py) while remainingFractionFor is defined and < 1 (mid-game
// through final, same "0 reads as a real score, not broken" reasoning
// pointsForPlayerInLineup's own docstring already established for that map).
//
// projected: the live in-game blend (points-so-far + remaining-game-
// fraction share of the pregame number) ONLY while this player's game is
// actually mid-play (0 < frac < 1) - otherwise the STATIC pregame/future
// projection (p.espn_projected_week for the current week, weeklyProjection
// for any other week), even once their game is final. Deliberately never
// the live blend post-final - by then it's numerically equal to `actual`,
// which would make the second line pure redundant clutter instead of the
// "how did this compare to expectation" comparison it's meant to be - and
// for a past (non-current) week there's no stored original projection at
// all (engine/pipeline.py explicitly nulls it out once a week is played -
// "nothing to project for a week that already happened"), so this is left
// undefined there, same as actual is left undefined pre-kickoff.
function actualAndProjected(p, week, lineupWeek, data) {
  const currentWeek = data.meta.current_week;
  const isCurrentWeek = week === currentWeek;
  const frac = isCurrentWeek ? remainingFractionFor(p, data) : undefined;
  let actual;
  if (isCurrentWeek) {
    if (frac !== undefined && frac < 1) actual = lineupWeek?.points?.[p.id];
  } else if (week < currentWeek) {
    actual = lineupWeek?.points?.[p.id];
  }
  const liveProjected = isCurrentWeek && frac > 0 && frac < 1 ? lineupWeek?.projected_points?.[p.id] : undefined;
  const staticProjected = isCurrentWeek ? p.espn_projected_week : weeklyProjection(p, week, currentWeek);
  const projected = liveProjected !== undefined ? liveProjected : staticProjected;
  return { actual, projected };
}

// Team-level twin of actualAndProjected above, for the matchup row's own
// colored team-total cell. A fully played week has no per-player original
// projection to sum (see that function's own docstring) - the real
// recorded total (same number scoreOf returns) is the only line shown
// there. Otherwise sums each starter's own actual/projected (an "actual"
// team total is a real LIVE SCORE - a starter who hasn't kicked off yet
// contributes 0 to it, same as any other fantasy site's live scoreboard,
// rather than leaving the whole team total blank just because one starter
// is in a late game) - `anyActual`/`anyProjected` distinguish "genuinely
// nobody's played yet" (real "-") from "some real points are in" (a real,
// if partial, number).
function actualAndProjectedTotal(m, side, data) {
  const teamId = side === "home" ? m.home_team_id : m.away_team_id;
  if (m.played) {
    return { actual: (side === "home" ? m.home_score : m.away_score) ?? null, projected: null };
  }
  const lineupWeek = lineupWeekFor(teamId, m.week, data);
  if (!lineupWeek) return { actual: null, projected: null };
  const pids = Object.values(lineupWeek.slots || {});
  let actualSum = 0, anyActual = false, projectedSum = 0, anyProjected = false;
  pids.forEach((pid) => {
    const p = resolvePlayer(pid, data);
    if (!p) return;
    const { actual, projected } = actualAndProjected(p, m.week, lineupWeek, data);
    if (actual !== undefined && actual !== null) {
      actualSum += actual;
      anyActual = true;
    }
    if (projected !== undefined && projected !== null) {
      projectedSum += projected;
      anyProjected = true;
    }
  });
  return { actual: anyActual ? actualSum : null, projected: anyProjected ? projectedSum : null };
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
    // Mobile drops the position chip and abbreviates to "F. Last" (see
    // styles.css's ".lineup-player .pos-tag"/".full-name"/".short-name"
    // rules) - this table is already tight with two full lineups mirrored
    // side by side, and a chip + full name doesn't fit a phone width.
    const nameHtml = `<span class="full-name">${escapeHtml(p.name)}</span><span class="short-name">${escapeHtml(shortName(p.name))}</span>`;
    return `${posTag(p.position)} ${nameHtml}${streamBadge}`;
  };
  // Same stacked actual-over-projected format as Start/Sit's xFPTS cells
  // (see docs/js/startsit.js's fmtPtsWithXfp) - full-contrast actual on top
  // ("-" before this player's game has started, or for a future week),
  // smaller muted projected underneath (see actualAndProjected above for
  // exactly which number that is at each stage).
  const scoreCell = (pid, lineupWeek) => {
    const p = pid && resolvePlayer(pid, data);
    if (!p) return "–";
    const { actual, projected } = actualAndProjected(p, week, lineupWeek, data);
    const actualLine = `<span style="color:var(--ink-900)">${actual !== undefined && actual !== null ? fmt(actual, 1) : "–"}</span>`;
    const projLine = projected !== undefined && projected !== null ? `<span class="sub">${fmt(projected, 1)}</span>` : "";
    return `<span class="comp-recent-cell">${actualLine}${projLine}</span>`;
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
  // pairing the way starting slots do - paired by position in the list
  // purely to share the same row-per-line layout as the starters above, not
  // because a given row means anything about the two players relative to
  // each other. Each side is independently sorted by its own projected
  // points, with IR split into its own group entirely (not just sorted
  // last within one combined list) - index-pairing a single combined list
  // put each side's IR players at whatever row their OWN bench length
  // happened to push them to, which almost never matched the other side's
  // (confirmed live: a 7-bench team's 2 IR landed on rows 6-7, a 9-bench
  // team's 2 IR landed on rows 8-9 - 4 different rows each flagged "IR"
  // since a row only needs ONE side to be IR, reading as "4 IR slots" for
  // a league where every team only has 2). Zipping the two sides' IR lists
  // together as their own trailing block - the same way the non-IR bench
  // above it is zipped - keeps both teams' IR rows aligned at the bottom
  // regardless of how many healthy bench players either side has.
  const isOnIR = (pid) => {
    const p = pid && resolvePlayer(pid, data);
    return !!(p && p.lineup_slot === "IR");
  };
  const splitBench = (bench, lineupWeek) => {
    const scored = bench.map((pid) => {
      const p = pid && resolvePlayer(pid, data);
      const pts = p ? pointsForPlayerInLineup(p, week, lineupWeek, data.meta.current_week) || 0 : -Infinity;
      return { pid, ir: isOnIR(pid), pts };
    });
    return {
      nonIR: scored
        .filter((x) => !x.ir)
        .sort((a, b) => b.pts - a.pts)
        .map((x) => x.pid),
      ir: scored.filter((x) => x.ir).map((x) => x.pid),
    };
  };
  const homeSplit = splitBench(home.bench || [], home);
  const awaySplit = splitBench(away.bench || [], away);
  const nonIRRowCount = Math.max(homeSplit.nonIR.length, awaySplit.nonIR.length);
  const irRowCount = Math.max(homeSplit.ir.length, awaySplit.ir.length);
  const benchRowCount = nonIRRowCount + irRowCount;
  const nonIRRows = Array.from({ length: nonIRRowCount }, (_, i) => lineupRow(homeSplit.nonIR[i], awaySplit.nonIR[i], "")).join("");
  const irRows = Array.from({ length: irRowCount }, (_, i) => lineupRow(homeSplit.ir[i], awaySplit.ir[i], "IR")).join("");
  const benchRows = nonIRRows + irRows;

  return `
    <table class="lineup-symmetric">
      <colgroup><col class="lineup-col-name"><col class="lineup-col-score"><col class="lineup-col-slot"><col class="lineup-col-score"><col class="lineup-col-name"></colgroup>
      <tbody>
        ${starterRows}
        ${benchRowCount ? `<tr class="week-divider"><td colspan="5">Bench</td></tr>${benchRows}` : ""}
      </tbody>
    </table>
  `;
}

// filterTeamId: the single-team filtered view (see renderSchedule) has
// exactly one row per week for that team, so a per-week "Week N" divider row
// (the all-teams view's own grouping - see draw()) would be redundant
// scaffolding around a single row; a leading WK cell on the row itself
// carries the same information without the extra row, matching how a real
// season schedule table reads for one team. Also flips home/away so the
// filtered team always renders on the LEFT (readability - the team you
// picked shouldn't jump sides week to week depending on who hosted), and
// skips the your-team-row highlight entirely - every row already IS that
// team's own game, so highlighting all of them (or none, for someone else's
// team) adds nothing a plain "you're looking at his schedule" view doesn't
// already make obvious.
function matchupRow(m, data, expandedKey, yourTeamId, avg, spread, filterTeamId) {
  const flip = !!filterTeamId && m.away_team_id === filterTeamId;
  const leftId = flip ? m.away_team_id : m.home_team_id;
  const rightId = flip ? m.home_team_id : m.away_team_id;
  const left = data.teamsById.get(leftId);
  const right = data.teamsById.get(rightId);
  const key = `${m.week}-${m.home_team_id}-${m.away_team_id}`;
  const isYours = !filterTeamId && (m.home_team_id === yourTeamId || m.away_team_id === yourTeamId);
  const colspan = filterTeamId ? 5 : 4;
  const weekCell = filterTeamId ? `<td class="schedule-cell small muted">${m.week}</td>` : "";

  const leftSide = flip ? "away" : "home";
  const rightSide = flip ? "home" : "away";
  const leftScore = scoreOf(m, leftSide, data);
  const rightScore = scoreOf(m, rightSide, data);
  const leftWinPct = flip ? m.away_win_pct : m.home_win_pct;
  const rightWinPct = flip ? m.home_win_pct : m.away_win_pct;
  // Heat color still keys off scoreOf's own single best-current-estimate
  // number (unchanged, also what standings.js's ROS PF/PA reads - see that
  // function's own docstring) - this just additionally stacks the real
  // actual-so-far team total over the projected one inside the same
  // colored cell (see actualAndProjectedTotal above), same visual language
  // as the per-player cells above.
  const scoreCell = (v, side) => {
    if (v === null || v === undefined) return `<span class="muted">–</span>`;
    const ratio = spread > 0 ? Math.max(0, Math.min(1, 0.5 + (v - avg) / spread)) : 0.5;
    const { actual, projected } = actualAndProjectedTotal(m, side, data);
    // Unlike the per-player cells (plain card background, so the theme's
    // own ink-900/--ink-500 contrast correctly), this cell sits on
    // .heat-cell's own bright inline ratio-color fill - both lines are
    // pinned to the same #111 .heat-cell already hardcodes for exactly that
    // reason (see its own CSS comment), overriding what var(--ink-900)/.sub
    // would otherwise resolve to in dark mode (near-white, illegible here).
    // The actual line keeps a bold weight so it still visually leads over
    // the smaller projected one now that color alone can't do that job.
    const actualLine = `<span style="color:#111; font-weight:600">${actual !== null && actual !== undefined ? fmt(actual, 1) : "–"}</span>`;
    const projLine = projected !== null && projected !== undefined ? `<span class="sub" style="color:#111">${fmt(projected, 1)}</span>` : "";
    return `<span class="heat-cell comp-recent-cell" style="background:${colorForRatio(ratio)}; display:inline-block; width:100%;">${actualLine}${projLine}</span>`;
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
  // left at 100% leftPct, fully flush right at 100% rightPct), while
  // staying the same length throughout. Each half is colored by THAT team's
  // own win probability (winProbColor - red below 25%, green above 75%,
  // blended between) rather than a fixed left/right color pair, so the bar
  // itself reads as "how good are this team's real chances" at a glance.
  // Purely positional (left side first) - works the same whether "left" is
  // the real home team or, in the filtered view, whichever side got flipped
  // there to keep the picked team on the left.
  const winProbBar = (leftPct, rightPct) =>
    leftPct === null || leftPct === undefined
      ? ""
      : `<div class="winprob-bar" title="${fmt(leftPct * 100, 0)}% / ${fmt(rightPct * 100, 0)}%">
          <div class="winprob-center-line"></div>
          <div class="winprob-slider" style="left:${50 - 50 * leftPct}%;">
            <div class="winprob-seg" style="width:${leftPct * 100}%; background:${winProbColor(leftPct)}"></div>
            <div class="winprob-seg" style="width:${rightPct * 100}%; background:${winProbColor(rightPct)}"></div>
          </div>
        </div>`;

  const expanded = expandedKey === key;
  return `
    <tr class="clickable-row schedule-row ${isYours ? "your-team-row" : ""}" data-key="${key}">
      ${weekCell}
      <td class="schedule-cell">${escapeHtml(teamLabel(left) || leftId)}${winPct(leftWinPct)}</td>
      <td class="schedule-cell small">${scoreCell(leftScore, leftSide)}</td>
      <td class="schedule-cell small">${scoreCell(rightScore, rightSide)}</td>
      <td class="schedule-cell">${escapeHtml(teamLabel(right) || rightId)}${winPct(rightWinPct)}</td>
    </tr>
    ${leftWinPct !== null && leftWinPct !== undefined ? `<tr class="winprob-row"><td colspan="${colspan}">${winProbBar(leftWinPct, rightWinPct)}</td></tr>` : ""}
    ${expanded
      ? `<tr><td colspan="${colspan}">
          <div class="lineup-symmetric-heading">
            <h3 class="small">${escapeHtml(teamLabel(left))}</h3>
            <h3 class="small">${escapeHtml(teamLabel(right))}</h3>
          </div>
          ${symmetricLineupHtml(leftId, rightId, m.week, data)}
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
    // filterTeamId) standing in for the divider's own week label.
    const rows = teamFilter
      ? data.schedule
          .filter((m) => m.home_team_id === teamFilter || m.away_team_id === teamFilter)
          .slice()
          .sort((a, b) => a.week - b.week)
          .map((m) => matchupRow(m, data, state.expandedKey, yourTeamId, avg, spread, teamFilter))
          .join("")
      : // One shared table for every week (not a separate table per week) with
        // fixed column widths, so the home/score/away columns land in the same
        // horizontal position throughout - team-name length can't stagger them.
        weeks
          .map((w) => {
            const weekMatchups = data.schedule.filter((m) => m.week === w);
            const allProjected = weekMatchups.length > 0 && weekMatchups.every((m) => !m.played);
            // The current week gets no suffix at all (not "(current)", not
            // "(live)") - it's already the week you land on/are looking at,
            // so the label would be redundant noise. A genuinely future week
            // still gets "- Projected" since those numbers aren't real yet.
            const statusLabel = w === data.meta.current_week ? "" : allProjected ? " - Projected" : "";
            const weekHeader = `<tr class="week-divider"><td colspan="4">Week ${w}${statusLabel}</td></tr>`;
            const matchups = weekMatchups.map((m) => matchupRow(m, data, state.expandedKey, yourTeamId, avg, spread, null)).join("");
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
