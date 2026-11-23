// Playoff Paths - the Standings page's "Paths" view. Reads sim_paths.json
// (engine.standings.encode_paths): for every one of the playoff sim's
// iterations, who won each remaining game and what seed each team finished
// with. Everything here is just filtering those iterations ("sims where Team X
// beats Team Y in Wk 10") and counting, so no sim is re-run in the browser.

import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { colorForRatio, teamLabel } from "./colors.js";

// ---------- pure core (no DOM; exercised directly by the node smoke test) ----------

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

// Decoded sims: games[g].homeWon[i] = 1 if the home side won game g in sim i;
// seeds.get(teamId)[i] = that team's seed in sim i (0 = missed the playoffs);
// wins2.get(teamId)[i] = final wins x2 (so a tie's half-win stays an integer),
// rebuilt from the current record plus the game results.
export function decodePaths(raw) {
  if (!raw || !raw.games || !raw.seeds || !raw.iterations) return null;
  const n = raw.iterations;
  const games = raw.games.map((g) => {
    const bytes = b64ToBytes(g.bits);
    const homeWon = new Uint8Array(n);
    for (let i = 0; i < n; i++) homeWon[i] = (bytes[i >> 3] >> (7 - (i & 7))) & 1;
    return { week: g.week, home: g.home, away: g.away, homeWon };
  });
  const seeds = new Map();
  raw.team_ids.forEach((tid) => seeds.set(tid, b64ToBytes(raw.seeds[String(tid)])));
  const wins2 = new Map();
  raw.team_ids.forEach((tid, k) => wins2.set(tid, new Uint16Array(n).fill(Math.round(raw.base_wins[k] * 2))));
  games.forEach((g) => {
    const h = wins2.get(g.home), a = wins2.get(g.away);
    for (let i = 0; i < n; i++) {
      if (g.homeWon[i]) h[i] += 2;
      else a[i] += 2;
    }
  });
  // Final points scored per sim (tenths, little-endian uint16) - only present in newer builds.
  let pf = null, basePf = null;
  if (raw.pf) {
    pf = new Map();
    raw.team_ids.forEach((tid) => {
      const b = b64ToBytes(raw.pf[String(tid)]);
      const arr = new Float32Array(n);
      for (let i = 0; i < n; i++) arr[i] = (b[2 * i] | (b[2 * i + 1] << 8)) / 10;
      pf.set(tid, arr);
    });
    basePf = new Map(raw.team_ids.map((tid, k) => [tid, raw.base_pf[k]]));
  }
  // The last seed is decided by points scored (not record) when its first tiebreak is points_for.
  const pfDecidesLast = !!(pf && raw.last_seed_tiebreak && raw.last_seed_tiebreak[0] === "points_for");
  return { n, games, seeds, wins2, pf, basePf, pfDecidesLast, teamIds: raw.team_ids, playoffCount: raw.playoff_team_count, byeSeeds: raw.bye_seeds };
}

// picks: Map gameIndex -> "home" | "away". Returns {mask, count} of the sims
// consistent with every pick except `exceptGame` (so a picked game can still
// show what its own other side would have meant).
export function conditionMask(sim, picks, exceptGame = -1) {
  const mask = new Uint8Array(sim.n).fill(1);
  picks.forEach((side, g) => {
    if (g === exceptGame) return;
    const want = side === "home" ? 1 : 0;
    const hw = sim.games[g].homeWon;
    for (let i = 0; i < sim.n; i++) if (hw[i] !== want) mask[i] = 0;
  });
  let count = 0;
  for (let i = 0; i < sim.n; i++) count += mask[i];
  return { mask, count };
}

// Plain win probability for a game's home side: share of ALL sims it won
// (ignores picks, so it stays the pre-scenario number).
export function homeWinProb(sim, gi) {
  const hw = sim.games[gi].homeWon;
  let c = 0;
  for (let i = 0; i < sim.n; i++) c += hw[i];
  return sim.n ? c / sim.n : null;
}

// Seed distribution (index 0 = out) plus playoff/bye odds for one team over a mask.
export function teamOdds(sim, teamId, mask) {
  const seeds = sim.seeds.get(teamId);
  const dist = new Array(sim.playoffCount + 1).fill(0);
  let count = 0;
  for (let i = 0; i < sim.n; i++) {
    if (!mask[i]) continue;
    count++;
    dist[seeds[i]]++;
  }
  if (!count) return { count: 0, playoff: null, bye: null, dist: dist.map(() => 0) };
  let bye = 0;
  for (let s = 1; s <= sim.byeSeeds; s++) bye += dist[s];
  return { count, playoff: 1 - dist[0] / count, bye: bye / count, dist: dist.map((c) => c / count) };
}

// Metric of one team given each side of one game, over a mask. {home, away}
// are each null when no remaining sim has that side winning.
export function gameSplit(sim, teamId, gameIdx, mask, metric) {
  const g = sim.games[gameIdx];
  const seeds = sim.seeds.get(teamId);
  const c = [0, 0], hit = [0, 0];
  for (let i = 0; i < sim.n; i++) {
    if (!mask[i]) continue;
    const side = g.homeWon[i];
    c[side]++;
    const s = seeds[i];
    if (metric === "bye" ? s >= 1 && s <= sim.byeSeeds : s > 0) hit[side]++;
  }
  const total = c[0] + c[1];
  return {
    home: c[1] ? hit[1] / c[1] : null,
    away: c[0] ? hit[0] / c[0] : null,
    homeWinPct: total ? c[1] / total : null,
    nHome: c[1],
    nAway: c[0],
  };
}

// ---- leverage ----
// A result is "must" when NOT getting it leaves odds at/below max(10%, half
// of the current odds) and it is a big swing; "big" is a 15+ pt swing; "minor"
// 1-15 pt; "flat" under 1 pt. A side backed by fewer than MIN_SIMS sims is
// never labelled (too noisy to claim "you need this").
export const MUST_ABS = 0.10, MUST_REL = 0.5, BIG_SWING = 0.15, MIN_SIMS = 30;

export function classifyResult(withIt, without, baseline, nWith, nWithout) {
  if (withIt === null || without === null || nWith < MIN_SIMS || nWithout < MIN_SIMS) return "flat";
  const swing = withIt - without;
  if (swing < 0.01) return "flat";
  if (swing >= BIG_SWING && without <= Math.max(MUST_ABS, MUST_REL * (baseline ?? 0))) return "must";
  return swing >= BIG_SWING ? "big" : "minor";
}

// One row per remaining game for `teamId` under `picks`: odds if each side
// wins, the side to root for (`best`), and its tier. A picked game is scored
// against the OTHER picks only, so its alternative stays readable.
export function leverageRows(sim, teamId, picks, metric) {
  const { mask, count } = conditionMask(sim, picks);
  const o = teamOdds(sim, teamId, mask);
  const baseline = metric === "bye" ? o.bye : o.playoff;
  const rows = sim.games.map((g, gi) => {
    const picked = picks.get(gi) || null;
    const m = picked ? conditionMask(sim, picks, gi).mask : mask;
    const sp = gameSplit(sim, teamId, gi, m, metric);
    const both = sp.home !== null && sp.away !== null;
    const swing = both ? Math.abs(sp.home - sp.away) : 0;
    const best = both && swing >= 0.01 ? (sp.home > sp.away ? "home" : "away") : null;
    let tier = "flat", bestOdds = null, worstOdds = null;
    if (best) {
      const other = best === "home" ? "away" : "home";
      bestOdds = sp[best];
      worstOdds = sp[other];
      tier = classifyResult(bestOdds, worstOdds, baseline, best === "home" ? sp.nHome : sp.nAway, other === "home" ? sp.nHome : sp.nAway);
    }
    return { gi, g, picked, sp, swing, best, tier, bestOdds, worstOdds, own: g.home === teamId || g.away === teamId };
  });
  return { rows, baseline, count, odds: o };
}

// Must-have results (for the strip), biggest loss-if-missed first, unpicked games only.
export function mustHaves(rows) {
  return rows.filter((r) => r.tier === "must" && !r.picked).sort((a, b) => a.worstOdds - b.worstOdds);
}

// "If you slip" branches: for each of the team's own unpicked games, assume it
// LOSES, and list what is still a must-have afterwards. Skipped when that
// branch leaves under MIN_BRANCH_SIMS sims or barely moves the odds.
export const MIN_BRANCH_SIMS = 100;
export function slipBranches(sim, teamId, picks, metric, baseline) {
  const out = [];
  sim.games.forEach((g, gi) => {
    if (picks.has(gi) || (g.home !== teamId && g.away !== teamId)) return;
    const lossSide = g.home === teamId ? "away" : "home";
    const p2 = new Map(picks).set(gi, lossSide);
    const { count } = conditionMask(sim, p2);
    if (count < MIN_BRANCH_SIMS) return;
    const lv = leverageRows(sim, teamId, p2, metric);
    if (baseline - lv.baseline < 0.05) return;
    out.push({ gi, g, odds: lv.baseline, count, needs: mustHaves(lv.rows).slice(0, 4) });
  });
  return out.sort((a, b) => a.g.week - b.g.week);
}

// Final-win-total ladder for one team over a mask: [{wins, share, playoff, bye, dist}].
export function winsLadder(sim, teamId, mask) {
  const seeds = sim.seeds.get(teamId), w2 = sim.wins2.get(teamId);
  const buckets = new Map();
  let total = 0;
  for (let i = 0; i < sim.n; i++) {
    if (!mask[i]) continue;
    total++;
    let b = buckets.get(w2[i]);
    if (!b) buckets.set(w2[i], (b = { n: 0, dist: new Array(sim.playoffCount + 1).fill(0) }));
    b.n++;
    b.dist[seeds[i]]++;
  }
  return [...buckets.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([w2v, b]) => {
      let bye = 0;
      for (let s = 1; s <= sim.byeSeeds; s++) bye += b.dist[s];
      return { wins: w2v / 2, n: b.n, share: b.n / total, playoff: 1 - b.dist[0] / b.n, bye: bye / b.n, dist: b.dist.map((c) => c / b.n) };
    });
}

// The points-for race for the LAST seed. Where that seed goes to the most
// points scored among everyone outside the other seeds, the bar a team must
// clear is different in every sim (it depends on who ends up in seeds 1..N-1).
// For each sim where `teamId` finishes outside seeds 1..N-1, the bar is the
// highest final PF among the OTHER teams outside seeds 1..N-1: the team makes
// the last seed exactly when it beats that bar. Returns null if the sims carry no PF.
export function pfRace(sim, teamId, mask) {
  if (!sim.pf) return null;
  const last = sim.playoffCount;
  const mySeeds = sim.seeds.get(teamId), myPf = sim.pf.get(teamId);
  const others = sim.teamIds.filter((t) => t !== teamId);
  let count = 0, top = 0, viaLast = 0, outside = 0;
  const bars = [], mine = [], gaps = [];
  const rivals = new Map();
  for (let i = 0; i < sim.n; i++) {
    if (!mask[i]) continue;
    count++;
    const s = mySeeds[i];
    if (s >= 1 && s < last) { top++; continue; }
    outside++;
    if (s === last) viaLast++;
    let barPf = -Infinity, barTeam = null;
    for (const t of others) {
      const ts = sim.seeds.get(t)[i];
      if (ts >= 1 && ts < last) continue; // in the top seeds: not part of this race
      const v = sim.pf.get(t)[i];
      if (v > barPf) { barPf = v; barTeam = t; }
    }
    if (barTeam === null) continue;
    bars.push(barPf); mine.push(myPf[i]); gaps.push(barPf - myPf[i]);
    const r = rivals.get(barTeam) || { team_id: barTeam, n: 0, finalPf: [], gap: [] };
    r.n++; r.finalPf.push(barPf); r.gap.push(barPf - myPf[i]);
    rivals.set(barTeam, r);
  }
  const base = sim.basePf.get(teamId);
  const myGames = sim.games.filter((g) => g.home === teamId || g.away === teamId).length;
  return {
    count, outside, top: count ? top / count : null, viaLast: count ? viaLast / count : null,
    madeWhenOutside: outside ? viaLast / outside : null,
    bar: summarize(bars), mine: summarize(mine), basePf: base, myGames,
    rivals: [...rivals.values()]
      .map((r) => ({ team_id: r.team_id, share: r.n / (bars.length || 1), basePf: sim.basePf.get(r.team_id), finalPf: summarize(r.finalPf), gap: summarize(r.gap) }))
      .sort((a, b) => b.share - a.share),
  };
}

function summarize(values) {
  const s = Array.from(values).sort((a, b) => a - b);
  if (!s.length) return { n: 0, p25: null, p50: null, p75: null };
  const q = (f) => { const pos = (s.length - 1) * f, lo = Math.floor(pos), hi = Math.ceil(pos); return s[lo] + (s[hi] - s[lo]) * (pos - lo); };
  return { n: s.length, p25: q(0.25), p50: q(0.5), p75: q(0.75) };
}

// ---------- rendering ----------

const pct = (x) => (x === null || x === undefined ? "–" : `${fmt(x * 100, 0)}%`);

function distBar(dist, height = 18) {
  const segs = dist
    .map((p, s) => {
      if (p < 0.004) return "";
      const label = s === 0 ? "Out" : `Seed ${s}`;
      const bg = s === 0 ? "var(--border)" : colorForRatio(1 - (s - 1) / Math.max(1, dist.length - 2));
      const text = p >= 0.07 ? `<span>${s === 0 ? "" : s}</span>` : "";
      return `<div class="path-seg" style="width:${p * 100}%; background:${bg};" title="${label}: ${fmt(p * 100, 0)}%">${text}</div>`;
    })
    .join("");
  return `<div class="path-distbar" style="height:${height}px">${segs}</div>`;
}

// Per-container UI state, kept across re-renders of the same league so a
// team/scenario survives switching Table <-> Paths.
const stateBySlug = new Map();

export function renderPaths(container, data, slug, { teamId } = {}) {
  const sim = decodePaths(data.simPaths);
  if (!sim) {
    container.innerHTML = `<div class="card"><h2>Playoff paths</h2><p class="muted">No simulation paths in this build yet - they appear after the next data refresh.</p></div>`;
    return;
  }
  const st = stateBySlug.get(slug) || { teamId: null, metric: "playoff", sortBy: "swing", picks: new Map() };
  stateBySlug.set(slug, st);
  if (teamId !== undefined && teamId !== null) st.teamId = teamId;
  if (st.teamId === null || !sim.seeds.has(st.teamId)) {
    const yours = getYourTeam(slug);
    st.teamId = sim.seeds.has(yours) ? yours : sim.teamIds[0];
  }

  const teamName = (id) => {
    const t = data.teamsById.get(id);
    return t ? teamLabel(t) : String(id);
  };
  const teamOrder = data.standings.slice().sort((a, b) => (a.seed ?? 99) - (b.seed ?? 99)).map((s) => s.team_id).filter((id) => sim.seeds.has(id));

  const draw = () => {
    const tid = st.teamId;
    const metricLabel = st.metric === "bye" ? "Bye" : "Playoff";
    const { mask, count } = conditionMask(sim, st.picks);
    const odds = teamOdds(sim, tid, mask);
    const baseline = st.metric === "bye" ? odds.bye : odds.playoff;
    const ladder = count ? winsLadder(sim, tid, mask) : [];
    const lowSample = count > 0 && count < 200;

    // Wins needed: the lowest final win total whose playoff odds clear 50% / 90%.
    const need = (thr) => ladder.find((r) => r.playoff >= thr && r.n >= 5);
    const need50 = need(0.5), need90 = need(0.9);
    const wf = (r) => fmt(r.wins, r.wins % 1 ? 1 : 0);
    let needLine = "";
    if (ladder.length) {
      if (!need50) needLine = "No final win total gets above a coin flip in this scenario.";
      else {
        const prev = ladder[ladder.indexOf(need50) - 1];
        needLine = `<strong>${wf(need50)} wins</strong> is the line: ${pct(need50.playoff)} to make it${prev && prev.n >= 5 ? `, vs ${pct(prev.playoff)} with ${wf(prev)}` : ""}.${need90 && need90 !== need50 ? ` Safe (90%+) at <strong>${wf(need90)}</strong>.` : ""}`;
      }
    }

    const pfr = sim.pfDecidesLast && count ? pfRace(sim, tid, mask) : null;
    const lev = leverageRows(sim, tid, st.picks, st.metric);
    const rows = lev.rows;
    const musts = mustHaves(rows);
    const branches = count ? slipBranches(sim, tid, st.picks, st.metric, baseline) : [];
    const sideName = (r, side) => escapeHtml(teamName(side === "home" ? r.g.home : r.g.away));
    const mustText = (r) => `<strong>${sideName(r, r.best)}</strong> wins Wk ${r.g.week}`;
    const mustStrip = !count ? "" : musts.length || branches.length
      ? `<div class="path-needs">
          <div class="path-needs-title">What has to happen</div>
          ${musts.length
            ? `<ul>${musts.map((r) => `<li class="must">${mustText(r)} <span class="muted small">- miss it and you're down to <strong>${pct(r.worstOdds)}</strong> (from ${pct(baseline)})</span></li>`).join("")}</ul>`
            : `<p class="small muted">No single result is an absolute must - nothing drops ${escapeHtml(teamName(tid))} below ${pct(Math.max(MUST_ABS, MUST_REL * baseline))} on its own.</p>`}
          ${branches.length
            ? `<div class="path-needs-title sub">If you slip</div><ul>${branches.map((b) => `<li>Lose Wk ${b.g.week} <span class="muted small">(${pct(b.odds)}, ${b.count.toLocaleString()} sims)</span>${b.needs.length
                ? ` - then you also need: ${b.needs.map((r) => `${mustText(r)} <span class="muted small">(else ${pct(r.worstOdds)})</span>`).join("; ")}`
                : ` <span class="muted small">- and nothing single is a must after that</span>`}</li>`).join("")}</ul>`
            : ""}
        </div>`
      : "";
    const sorted = rows.slice().sort((a, b) =>
      st.sortBy === "week" ? a.g.week - b.g.week || b.swing - a.swing : b.swing - a.swing || a.g.week - b.g.week
    );


    // Points-for race card: shown when the last seed is decided by points scored and this team actually lives in that race.
    let pfCard = "";
    if (pfr && pfr.outside >= 50 && (pfr.viaLast >= 0.03 || (odds.playoff ?? 0) < 0.97)) {
      const lastSeed = sim.playoffCount;
      const viaTopShare = odds.playoff ? pfr.top / odds.playoff : 0;
      const viaPfShare = odds.playoff ? pfr.viaLast / odds.playoff : 0;
      const gamesLeft = pfr.myGames;
      const need = pfr.bar.p50 - pfr.basePf;
      const projMine = pfr.mine.p50 - pfr.basePf;
      const perGame = gamesLeft ? need / gamesLeft : null;
      const projPerGame = gamesLeft ? projMine / gamesLeft : null;
      // Points scored so far in games still in progress (schedule.json carries them on the live rows;
      // ESPN's own points_for only adds a week once it is over).
      const liveBy = new Map();
      (data.schedule || []).forEach((m) => {
        if (!m.live) return;
        liveBy.set(m.home_team_id, m.home_score || 0);
        liveBy.set(m.away_team_id, m.away_score || 0);
      });
      const anyLive = liveBy.size > 0;
      const withLive = (id, base) => base + (liveBy.get(id) || 0);
      const myLive = withLive(tid, pfr.basePf);
      const signed = (v) => `${v >= 0 ? "+" : "−"}${fmt(Math.abs(v), 1)}`;
      const liveCell = (id, base) => (anyLive
        ? `<td title="Points for in finished games plus points scored so far this week">${fmt(withLive(id, base), 1)} <span class="muted small">(${id === tid ? "you" : signed(withLive(id, base) - myLive) + " vs you"})</span></td>`
        : "");
      const youRow = `
        <tr class="your-team-row"><td><strong>${escapeHtml(teamName(tid))}</strong> <span class="muted small">(you)</span></td><td class="muted">–</td>
          <td>${fmt(pfr.basePf, 1)}</td>${liveCell(tid, pfr.basePf)}
          <td>${fmt(pfr.mine.p50, 0)}</td>
          <td class="muted">–</td></tr>`;
      const rivalRows = youRow + pfr.rivals.slice(0, 6).map((r) => `
        <tr><td>${escapeHtml(teamName(r.team_id))}</td><td>${pct(r.share)}</td>
          <td>${fmt(r.basePf, 1)}${anyLive ? "" : ` <span class="muted small">(${signed(r.basePf - pfr.basePf)} vs you)</span>`}</td>${liveCell(r.team_id, r.basePf)}
          <td>${fmt(r.finalPf.p50, 0)}</td>
          <td title="Median final-PF gap on you in the sims where this team is the bar; negative = you finished ahead of them but still missed because someone else got in">${r.gap.p50 >= 0 ? "+" : "−"}${fmt(Math.abs(r.gap.p50), 0)}</td></tr>`).join("");
      pfCard = `
      <div class="card">
        <h2>The points-for race <span class="muted small">(seed ${lastSeed} goes to the most points scored outside seeds 1-${lastSeed - 1}, not to the best record)</span></h2>
        <p>You finish outside the top ${lastSeed - 1} in <strong>${pct(pfr.outside / pfr.count)}</strong> of sims, and take seed ${lastSeed} in <strong>${pct(pfr.madeWhenOutside)}</strong> of those.
          Of your playoff odds, about <strong>${pct(viaTopShare)}</strong> come from finishing in the top ${lastSeed - 1} and <strong>${pct(viaPfShare)}</strong> from winning the points race.</p>
        ${viaPfShare >= 0.4 ? `<p class="small"><strong>This is mostly a points race for ${escapeHtml(teamName(tid))}.</strong> Wins still matter, but mainly because they come with the points that clear the bar, and because they shape who else lands in the top ${lastSeed - 1}.</p>` : ""}
        <p>The bar moves from sim to sim with who occupies seeds 1-${lastSeed - 1}: projected median <strong>${fmt(pfr.bar.p50, 0)}</strong> final points (middle half ${fmt(pfr.bar.p25, 0)}-${fmt(pfr.bar.p75, 0)}).
          You have <strong>${fmt(pfr.basePf, 1)}</strong> in finished games${anyLive ? ` (<strong>${fmt(myLive, 1)}</strong> counting this week's live points)` : ""}, so you need roughly <strong>${fmt(Math.max(0, need), 0)}</strong> more over your last ${gamesLeft} game${gamesLeft === 1 ? "" : "s"}${perGame !== null ? ` (<strong>${fmt(Math.max(0, perGame), 1)}</strong>/game; the sim has you scoring ${fmt(projPerGame, 1)}/game)` : ""}.</p>
        <h3>You vs. who you have to pass</h3>
        <div class="table-wrap"><table class="path-ladder"><thead><tr><th>Team</th><th title="Share of the sims where you're outside the top ${lastSeed - 1} that this team is the highest-scoring team you must beat">Is the bar</th><th title="Points for in finished games (what ESPN has recorded)">PF now</th>${anyLive ? `<th title="Finished games plus points scored so far in games in progress">PF + live</th>` : ""}<th title="Projected final points for, median across the sims (includes this week's games and the rest of the season)">Projected final PF</th><th title="Median points they finish ahead of you, when they're the bar">Gap on you</th></tr></thead><tbody>${rivalRows}</tbody></table></div>
        <p class="small muted">Computed from the same sims, so it follows your what-if picks above. Tiebreaks beyond points scored (points against) are included in the sim; head-to-head is not.</p>
      </div>`;
    }

    const sideBtn = (r, side) => {
      const id = side === "home" ? r.g.home : r.g.away;
      const v = r.sp[side];
      const other = r.sp[side === "home" ? "away" : "home"];
      const best = r.best === side;
      const isMe = id === tid;
      const delta = v === null || baseline === null ? "" : v - baseline > 0.005 ? "up" : v - baseline < -0.005 ? "down" : "";
      const hwp = homeWinProb(sim, r.gi);
      const wp = hwp === null ? null : side === "home" ? hwp : 1 - hwp;
      return `<button class="path-side ${r.picked === side ? "picked" : ""} ${best ? "root" : ""} ${isMe ? "me" : ""}" data-game="${r.gi}" data-side="${side}" title="${escapeHtml(teamName(id))} wins (sims: ${wp === null ? "–" : fmt(wp * 100, 0) + "% chance"}) - your ${metricLabel.toLowerCase()} odds: ${pct(v)}">
        <span class="path-name">${best ? "★ " : ""}${escapeHtml(teamName(id))}<span class="path-wp" title="Chance to win this matchup (from the sims)">${wp === null ? "" : fmt(wp * 100, 0) + "% to win"}</span></span>
        <span class="path-pct ${delta}">${pct(v)}${v === null || baseline === null || Math.abs(v - baseline) < 0.005 ? "" : `<small>${v > baseline ? "+" : "−"}${fmt(Math.abs(v - baseline) * 100, 0)}</small>`}</span>
      </button>`;
    };

    const rowHtml = (r) => `
      <div class="path-game ${r.own ? "own" : ""} ${r.swing < 0.01 ? "flat" : ""} tier-${r.tier}">
        <span class="path-wk">Wk ${r.g.week}</span>
        ${sideBtn(r, "home")}
        <span class="path-at">vs</span>
        ${sideBtn(r, "away")}
        <span class="path-swing tier-${r.tier}" title="Swing: how far this game moves ${escapeHtml(teamName(tid))}'s ${metricLabel.toLowerCase()} odds">${r.tier === "must" ? "MUST " : ""}${r.swing < 0.01 ? "–" : fmt(r.swing * 100, 0) + " pt"}</span>
      </div>`;
    const live = sorted.filter((r) => r.swing >= 0.01 || r.picked);
    const flat = sorted.filter((r) => !(r.swing >= 0.01 || r.picked));
    const gameRows = live.map(rowHtml).join("") +
      (flat.length ? `<details class="path-flat"><summary class="muted small">${flat.length} other game${flat.length > 1 ? "s" : ""} with no real effect on ${escapeHtml(teamName(tid))}</summary>${flat.map(rowHtml).join("")}</details>` : "");

    const pickList = [...st.picks.entries()]
      .map(([gi, side]) => {
        const g = sim.games[gi];
        return `Wk ${g.week}: ${escapeHtml(teamName(side === "home" ? g.home : g.away))} wins`;
      })
      .join(" · ");

    const ladderRows = ladder
      .filter((r) => r.share >= 0.005)
      .map((r) => `
        <tr>
          <td><strong>${fmt(r.wins, r.wins % 1 ? 1 : 0)}</strong></td>
          <td>${pct(r.share)}</td>
          <td style="background:${colorForRatio(r.playoff)}33;">${pct(r.playoff)}</td>
          <td>${pct(r.bye)}</td>
          <td>${distBar(r.dist, 14)}</td>
        </tr>`)
      .join("");

    container.innerHTML = `
      <div class="card">
        <h2>Playoff paths</h2>
        <div class="select-row">
          <div class="filter-group"><span class="filter-label">Team</span>
            <select id="paths-team">${teamOrder.map((id) => `<option value="${id}" ${id === tid ? "selected" : ""}>${escapeHtml(teamName(id))}</option>`).join("")}</select></div>
          <div class="filter-group"><span class="filter-label">Odds for</span>
            <select id="paths-metric"><option value="playoff" ${st.metric === "playoff" ? "selected" : ""}>Making playoffs</option><option value="bye" ${st.metric === "bye" ? "selected" : ""}>Getting a bye</option></select></div>
          <div class="filter-group"><span class="filter-label">Order games</span>
            <select id="paths-sort"><option value="swing" ${st.sortBy === "swing" ? "selected" : ""}>Biggest swing</option><option value="week" ${st.sortBy === "week" ? "selected" : ""}>By week</option></select></div>
        </div>
        ${count === 0
          ? `<p class="muted">No simulations match that combination of results - clear a pick.</p>`
          : `<div class="path-headline">
              <div><div class="path-big">${pct(odds.playoff)}</div><div class="muted small">make playoffs</div></div>
              <div><div class="path-big">${pct(odds.bye)}</div><div class="muted small">get a bye</div></div>
              <div class="path-dist">${distBar(odds.dist, 22)}<div class="muted small">Seed distribution (grey = out)</div></div>
            </div>
            <p class="small">${needLine}</p>${mustStrip}`}
        <p class="small muted">${st.picks.size
          ? `Scenario: ${pickList} - <strong>${count.toLocaleString()}</strong> of ${sim.n.toLocaleString()} sims match. <a href="#" id="paths-clear">Clear picks</a>`
          : `All ${sim.n.toLocaleString()} sims. Tap a team below to assume it wins that game.`}
          ${lowSample ? `<span class="seed-down"> Few sims match - treat these odds as rough.</span>` : ""}</p>
      </div>

      ${pfCard}

      <div class="card">
        <h2>Who to root for <span class="muted small">(${metricLabel.toLowerCase()} odds for ${escapeHtml(teamName(tid))} if each side wins, and the change from now · ★ = root for · MUST = missing it leaves ${pct(Math.max(MUST_ABS, MUST_REL * (baseline ?? 0)))} or less)</span></h2>
        <div class="path-games">${gameRows || `<p class="muted">No regular-season games left.</p>`}</div>
      </div>

      <div class="card">
        <h2>Wins needed <span class="muted small">(final win total ${st.picks.size ? "in this scenario" : "across all sims"})</span></h2>
        ${ladderRows
          ? `<div class="table-wrap"><table class="path-ladder"><thead><tr><th>Wins</th><th>Likelihood</th><th>Playoffs</th><th>Bye</th><th>Seed mix</th></tr></thead><tbody>${ladderRows}</tbody></table></div>`
          : `<p class="muted">No data.</p>`}
        <p class="small muted">Uses the sim's tiebreaks (wins, then points scored); real seeding also looks at head-to-head and points against, so razor-thin calls can differ.</p>
      </div>`;

    container.querySelector("#paths-team").addEventListener("change", (e) => { st.teamId = Number(e.target.value); draw(); });
    container.querySelector("#paths-metric").addEventListener("change", (e) => { st.metric = e.target.value; draw(); });
    container.querySelector("#paths-sort").addEventListener("change", (e) => { st.sortBy = e.target.value; draw(); });
    const clear = container.querySelector("#paths-clear");
    if (clear) clear.addEventListener("click", (e) => { e.preventDefault(); st.picks.clear(); draw(); });
    container.querySelectorAll(".path-side").forEach((btn) => {
      btn.addEventListener("click", () => {
        const gi = Number(btn.dataset.game), side = btn.dataset.side;
        if (st.picks.get(gi) === side) st.picks.delete(gi);
        else st.picks.set(gi, side);
        draw();
      });
    });
  };
  draw();
}
