// FAAB tab - this league's own waiver history (faab_league.json, built by
// engine/faab_league.py). The global FAAB Lab in the player modal says what a
// player usually costs across 100+ leagues; this tab shows how THIS league
// actually prices things (vs those pooled leagues), what the market looks like
// week to week, how each manager behaves, and every transaction behind it.
// All aggregation happens here, in the browser, so every filter combination
// is just a re-slice of the same rows (see faabstats.js).

import { fmt, escapeHtml, getYourTeam } from "./state.js";
import { teamLabel } from "./colors.js";
import {
  leagueRows, applyFilter, isContestedWin, ratioStat, summarize, auctionStats, managerStats,
  spendByWeek, pctByWeek, weekBucket, globalBaseline, THIN_N,
} from "./faabstats.js";

const POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"];
const pct1 = (x) => (x === null || x === undefined ? "–" : `${fmt(x * 100, 1)}%`);
const pct0 = (x) => (x === null || x === undefined ? "–" : `${fmt(x * 100, 0)}%`);
const mult = (x) => (x === null || x === undefined ? "–" : `${fmt(x, 2)}×`);

// Per-league UI state so filters survive tab switches.
const stateBySlug = new Map();

// Ratio -> a diverging tint: pays MORE than the pooled leagues = amber,
// LESS = blue, stronger with distance from 1.0. Alpha-based so it works on
// both themes' backgrounds.
function ratioStyle(r) {
  if (r === null || r === undefined) return "";
  const k = Math.min(1, Math.abs(Math.log2(r)) / 1.2);
  const hue = r >= 1 ? "30, 90%, 50%" : "210, 75%, 50%";
  return `background:hsla(${hue}, ${0.08 + k * 0.5});`;
}

function sortTh(label, key, sort, extra = "") {
  const ind = sort.key === key ? (sort.dir === 1 ? " ▲" : " ▼") : "";
  return `<th data-sort="${key}"${extra}>${escapeHtml(label)}${ind}</th>`;
}
function sortBy(rows, sort, valueFor) {
  if (!sort.key) return rows;
  return rows.slice().sort((a, b) => {
    const av = valueFor(a, sort.key), bv = valueFor(b, sort.key);
    if (typeof av === "string" || typeof bv === "string") return String(av ?? "").localeCompare(String(bv ?? "")) * sort.dir;
    return ((av ?? -Infinity) - (bv ?? -Infinity)) * sort.dir;
  });
}
function toggleSort(sort, key, defaultDir = -1) {
  if (sort.key === key) sort.dir = -sort.dir;
  else { sort.key = key; sort.dir = defaultDir; }
}

export function renderFaab(container, data, slug) {
  const fl = data.faabLeague;
  const rows = leagueRows(fl);
  if (!rows.length) {
    container.innerHTML = `<div class="card"><h2>FAAB</h2>
      <p class="muted">No FAAB transaction history for this league yet. It's pulled during each full data refresh, so it appears after the next one (and fills in week by week through the season).</p></div>`;
    return;
  }

  const yourTeamId = getYourTeam(slug);
  const teamName = (id) => {
    const t = data.teamsById.get(id);
    return t ? teamLabel(t) : `Team ${id}`;
  };
  const allSeasons = [...new Set(rows.map((r) => r.season))].sort((a, b) => a - b);
  const currentSeason = fl.season;
  const refBudget = (fl.budgets && (fl.budgets[String(currentSeason)] ?? fl.budgets[String(allSeasons[allSeasons.length - 1])])) || null;
  const dollars = (p) => (p === null || p === undefined || !refBudget ? "" : ` <span class="muted small">≈$${fmt(p * refBudget, 0)}</span>`);

  const st = stateBySlug.get(slug) || {
    seasons: null, pos: null, team: null, result: "all", q: "", limit: 100,
    mgrSort: { key: "wins", dir: -1 }, logSort: { key: "when", dir: -1 },
  };
  stateBySlug.set(slug, st);

  const draw = () => {
    const slice = applyFilter(rows, { seasons: st.seasons, pos: st.pos, team: null });
    const wins = slice.filter(isContestedWin);
    const buckets = fl.week_buckets;
    const bucketLabels = (buckets || []).map(([lo, hi]) => `${lo}-${hi}`);
    const positions = POSITIONS.filter((p) => rows.some((r) => r.pos === p));

    // ---------- vs pooled leagues ----------
    const overall = ratioStat(wins, fl.baselines, st.pos, null);
    const posRows = (st.pos ? [st.pos] : positions).map((p) => ({ p, s: ratioStat(wins.filter((r) => r.pos === p), fl.baselines, p, null) }));
    const ranked = posRows.filter((x) => x.s.ratio !== null && x.s.n >= THIN_N).sort((a, b) => Math.abs(Math.log(b.s.shrunk)) - Math.abs(Math.log(a.s.shrunk)));
    const headline = !fl.baselines
      ? `<p class="muted small">Pooled-league baselines aren't available in this build, so the comparison columns are blank.</p>`
      : ranked.length
      ? `<p>${ranked.slice(0, 3).map((x) => {
          const r = x.s.shrunk;
          return Math.abs(r - 1) < 0.1 ? `<strong>${x.p}</strong> prices about the same as other leagues` : `<strong>${x.p}</strong> winning bids run <strong>${fmt(r, 2)}×</strong> the pooled median (${r > 1 ? "bid more" : "bid less"})`;
        }).join("; ")}.</p>`
      : `<p class="muted small">Not enough winning bids in this slice to compare.</p>`;

    const posTable = posRows.map(({ p, s }) => `
      <tr>
        <td><strong>${p}</strong></td>
        <td class="${s.thin ? "muted" : ""}">${s.n}</td>
        <td>${pct1(s.league.p50)}${dollars(s.league.p50)}</td>
        <td class="muted">${s.league.p25 === null ? "–" : `${pct1(s.league.p25)} – ${pct1(s.league.p75)}`}</td>
        <td>${s.global ? pct1(s.global.p50) : "–"}</td>
        <td style="${ratioStyle(s.shrunk)}" title="Raw ratio ${mult(s.ratio)}; shown value is shrunk toward 1.00 when the league has few wins (n=${s.n}).">${s.thin ? `<span class="muted">${mult(s.shrunk)}</span>` : `<strong>${mult(s.shrunk)}</strong>`}</td>
      </tr>`).join("");

    const gridHead = bucketLabels.map((b) => `<th>Wk ${b}</th>`).join("");
    const gridRows = posRows.map(({ p }) => {
      const cells = bucketLabels.map((b) => {
        const cellWins = wins.filter((r) => r.pos === p && weekBucket(r.week, buckets) === b);
        const s = ratioStat(cellWins, fl.baselines, p, b);
        const base = globalBaseline(fl.baselines, p, b);
        const tip = `${p} weeks ${b}: ${s.n} league wins, median ${pct1(s.league.p50)}${base ? ` vs pooled ${pct1(base.p50)}` : ""}${s.ratio === null ? "" : `, raw ${mult(s.ratio)}`}`;
        return `<td style="${s.n ? ratioStyle(s.shrunk) : ""}" title="${escapeHtml(tip)}">${s.n ? `<span class="${s.thin ? "muted" : ""}">${mult(s.shrunk)}</span><span class="grid-n">n=${s.n}</span>` : `<span class="muted">–</span>`}</td>`;
      }).join("");
      return `<tr><td><strong>${p}</strong></td>${cells}</tr>`;
    }).join("");

    // ---------- distributions ----------
    const axisMax = Math.max(0.1, ...posRows.map(({ s }) => Math.max(s.league.p90 ?? 0, s.global?.p90 ?? 0)));
    const x = (v) => `${Math.min(100, (v / axisMax) * 100)}%`;
    const rangeBar = (st_, cls) => st_ && st_.p50 !== null
      ? `<div class="faabl-range ${cls}">
           <div class="faabl-whisker" style="left:${x(st_.p25)}; width:calc(${x(st_.p90)} - ${x(st_.p25)});"></div>
           <div class="faabl-box" style="left:${x(st_.p25)}; width:calc(${x(st_.p75)} - ${x(st_.p25)});"></div>
           <div class="faabl-med" style="left:${x(st_.p50)};"></div>
         </div>` : `<div class="faabl-range ${cls}"></div>`;
    const distRows = posRows.map(({ p, s }) => `
      <div class="faabl-dist-row" title="${p}: league p25 ${pct1(s.league.p25)} / median ${pct1(s.league.p50)} / p75 ${pct1(s.league.p75)} / p90 ${pct1(s.league.p90)}${s.global ? ` - pooled median ${pct1(s.global.p50)}` : ""}">
        <span class="faabl-dist-label">${p}</span>
        <div class="faabl-dist-bars">${rangeBar(s.league, "league")}${rangeBar(s.global, "pooled")}</div>
        <span class="muted small faabl-dist-n">n=${s.n}</span>
      </div>`).join("");
    const axisTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => `<span style="left:${f * 100}%">${fmt(f * axisMax * 100, 0)}%</span>`).join("");

    const weekPrices = pctByWeek(wins);
    const maxWeekP = Math.max(0.05, ...weekPrices.map((w) => w.p50 ?? 0));
    const weekBars = weekPrices.map((w) => `
      <div class="faabl-week" title="Week ${w.week}: ${w.n ? `median winning bid ${pct1(w.p50)} (n=${w.n})` : "no contested wins"}">
        <div class="faabl-week-bar" style="height:${w.p50 === null ? 0 : (w.p50 / maxWeekP) * 100}%;${w.n && w.n < THIN_N ? "opacity:.45;" : ""}"></div>
        <span>${w.week}</span>
      </div>`).join("");

    const seasonRows = allSeasons.filter((s) => !st.seasons || st.seasons.has(s)).map((season) => {
      const sr = slice.filter((r) => r.season === season);
      const sw = sr.filter(isContestedWin);
      const a = auctionStats(sr);
      const sum = summarize(sw.map((r) => r.pct));
      const budget = fl.budgets ? fl.budgets[String(season)] : null;
      return `<tr><td><strong>${season}</strong></td><td>${a.auctions}</td><td>${a.avgBidders === null ? "–" : fmt(a.avgBidders, 2)}</td><td>${pct0(a.contestedShare)}</td><td>${pct0(a.outbidShare)}</td><td>${a.medianMargin === null ? "–" : pct1(a.medianMargin)}</td><td>${pct1(sum.p50)}</td><td class="muted">${budget ? "$" + fmt(budget, 0) : "–"}</td></tr>`;
    }).join("");
    const aAll = auctionStats(slice);

    // ---------- timing ----------
    const timing = spendByWeek(slice);
    const halfWeek = (timing.find((t) => t.cumulative >= 0.5) || {}).week;
    const maxShare = Math.max(0.01, ...timing.map((t) => t.share));
    const timingBars = timing.map((t) => `
      <div class="faabl-week" title="Week ${t.week}: ${pct0(t.share)} of a season's FAAB spend (${pct0(t.cumulative)} cumulative)">
        <div class="faabl-week-bar alt" style="height:${(t.share / maxShare) * 100}%"></div>
        <span>${t.week}</span>
      </div>`).join("");

    // ---------- managers ----------
    const mgrs = managerStats(slice);
    const mgrValue = (m, k) => ({
      team: teamName(m.team_id), bids: m.bids, wins: m.wins, winRate: m.winRate, fa: m.fa, spend: m.spend,
      medWin: m.medianWinPct, overpay: m.medianOverpay,
    })[k];
    const mgrRows = sortBy(mgrs, st.mgrSort, mgrValue).map((m) => {
      const topPos = Object.entries(m.pos).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([p, n]) => `${p} ${n}`).join(", ") || "–";
      return `<tr class="${m.team_id === yourTeamId ? "your-team-row" : ""}">
        <td>${m.team_id === yourTeamId ? "<strong>" : ""}${escapeHtml(teamName(m.team_id))}${m.team_id === yourTeamId ? "</strong>" : ""}</td>
        <td>${m.bids}</td><td>${m.wins}</td><td>${pct0(m.winRate)}</td><td>${m.fa}</td><td>$${fmt(m.spend, 0)}</td>
        <td>${pct1(m.medianWinPct)}</td><td title="Median gap between this manager's winning bid and the best losing bid, on contested wins">${pct1(m.medianOverpay)}</td><td class="muted">${topPos}</td>
      </tr>`;
    }).join("");

    // ---------- transaction log ----------
    const q = st.q.trim().toLowerCase();
    const logAll = applyFilter(rows, { seasons: st.seasons, pos: st.pos, team: st.team }).filter((r) => {
      if (st.result === "won" && !(r.signal === "won" && r.kind === "W")) return false;
      if (st.result === "outbid" && r.signal !== "outbid") return false;
      if (st.result === "fa" && r.kind !== "FA") return false;
      if (st.result === "failed" && r.signal !== "failed") return false;
      return !q || String(r.name || "").toLowerCase().includes(q);
    });
    const logValue = (r, k) => ({
      when: r.season * 100 + r.week, team: teamName(r.team_id), player: r.name || "", bid: r.bid, pct: r.pct, bidders: r.n_bids, result: r.signal,
    })[k];
    const logSorted = sortBy(logAll, st.logSort, logValue);
    const shown = logSorted.slice(0, st.limit);
    const resultCell = (r) => r.kind === "FA" ? `<span class="muted">FA add</span>` : r.signal === "won" ? `<span class="seed-up">Won</span>` : r.signal === "outbid" ? `<span class="seed-down">Outbid</span>` : `<span class="muted" title="Cancelled or invalid for a reason unrelated to competition">Failed</span>`;
    const logRows = shown.map((r) => {
      const base = r.kind === "W" && r.pct !== null ? globalBaseline(fl.baselines, r.pos, weekBucket(r.week, buckets)) : null;
      const vs = base && r.bid > 0 ? r.pct / base.p50 : null;
      const auction = r.n_bids ? `${r.n_bids} bid${r.n_bids > 1 ? "s" : ""}${r.signal === "won" && r.runner_up !== null ? ` · next best $${fmt(r.runner_up, 2)}` : r.signal === "outbid" && r.win_bid !== null ? ` · winner $${fmt(r.win_bid, 2)}` : ""}` : "";
      return `<tr class="${r.team_id === yourTeamId ? "your-team-row" : ""}">
        <td>${r.season} wk ${r.week}</td>
        <td>${escapeHtml(teamName(r.team_id))}</td>
        <td>${escapeHtml(r.name || "Unknown")} <span class="muted small">${escapeHtml(r.pos || "")}</span></td>
        <td>${resultCell(r)}</td>
        <td>${r.kind === "FA" ? "–" : "$" + fmt(r.bid, 2)}</td>
        <td>${r.pct === null || r.kind === "FA" ? "–" : pct1(r.pct)}</td>
        <td title="This bid vs the pooled-league median winning bid for this position and point in the season" style="${vs === null ? "" : ratioStyle(vs)}">${vs === null ? "–" : mult(vs)}</td>
        <td class="muted small">${auction}</td>
      </tr>`;
    }).join("");

    const seasonChips = allSeasons.map((s) => `<button class="player-chip ${!st.seasons || st.seasons.has(s) ? "selected" : ""}" data-season="${s}">${s}</button>`).join("");
    const nSeasons = st.seasons ? st.seasons.size : allSeasons.length;

    container.innerHTML = `
      <div class="card">
        <h2>FAAB <span class="muted small">(this league's waiver history · ${rows.length.toLocaleString()} transactions · ${allSeasons[0]}–${allSeasons[allSeasons.length - 1]})</span></h2>
        <p class="small muted">Bids are shown as a % of that season's budget, the same units as the FAAB Lab in the player modal, so you can read a Lab number and then nudge it with what this league actually does. ${refBudget ? `Dollar figures use the latest season's $${fmt(refBudget, 0)} budget.` : ""}</p>
        <div class="player-picker" id="faabl-seasons">${seasonChips}<button class="player-chip" data-season="all">All</button></div>
        <div class="select-row">
          <div class="filter-group"><span class="filter-label">Position</span>
            <select id="faabl-pos"><option value="">All</option>${positions.map((p) => `<option ${st.pos === p ? "selected" : ""}>${p}</option>`).join("")}</select></div>
          <span class="muted small">${wins.length.toLocaleString()} contested winning bids in ${nSeasons} season${nSeasons === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div class="card">
        <h2>This league vs the pooled leagues</h2>
        ${headline}
        <div class="table-wrap"><table class="faabl-table"><thead><tr><th>Pos</th><th>Wins</th><th>League median</th><th>Middle half</th><th>Pooled median</th><th title="League median ÷ pooled median, nudged toward 1.00 when there are few wins. Above 1 = this league pays more; multiply a Lab number by it.">Ratio</th></tr></thead><tbody>${posTable}</tbody></table></div>
        <h3>By point in the season</h3>
        <div class="table-wrap"><table class="faabl-table faabl-grid"><thead><tr><th>Pos</th>${gridHead}</tr></thead><tbody>${gridRows}</tbody></table></div>
        <p class="small muted">Amber = this league pays more than the pooled leagues, blue = less. Faded cells rest on fewer than ${THIN_N} wins.</p>
      </div>

      <div class="card">
        <h2>Price distributions</h2>
        <div class="faabl-legend"><span><i class="faabl-swatch league"></i>This league</span><span><i class="faabl-swatch pooled"></i>Pooled leagues</span><span class="muted small">box = middle half · tick = median · line = 90th percentile</span></div>
        <div class="faabl-dist">${distRows}<div class="faabl-axis">${axisTicks}</div></div>
        <h3>Median winning bid by week</h3>
        <div class="faabl-weeks">${weekBars}</div>
        <h3>Competition by season</h3>
        <div class="table-wrap"><table class="faabl-table"><thead><tr><th>Season</th><th>Auctions</th><th title="Average real bids per claimed player">Bidders</th><th title="Share of won claims that had 2+ bidders">Contested</th><th title="Share of all real bids that lost to a higher bid">Outbid</th><th title="Median gap between the winning and best losing bid">Margin</th><th>Median win</th><th>Budget</th></tr></thead><tbody>${seasonRows}<tr class="faabl-total"><td>All</td><td>${aAll.auctions}</td><td>${aAll.avgBidders === null ? "–" : fmt(aAll.avgBidders, 2)}</td><td>${pct0(aAll.contestedShare)}</td><td>${pct0(aAll.outbidShare)}</td><td>${aAll.medianMargin === null ? "–" : pct1(aAll.medianMargin)}</td><td>${pct1(summarize(wins.map((r) => r.pct)).p50)}</td><td></td></tr></tbody></table></div>
      </div>

      <div class="card">
        <h2>When the money gets spent</h2>
        <p>${halfWeek ? `Half of a typical season's FAAB spend is gone by <strong>week ${halfWeek}</strong>.` : "Not enough spend data."} <span class="muted small">Share of a season's total spend landing in each week, averaged over the selected seasons.</span></p>
        <div class="faabl-weeks">${timingBars}</div>
      </div>

      <div class="card">
        <h2>Managers</h2>
        <div class="table-wrap"><table class="faabl-table" id="faabl-mgr"><thead><tr>
          ${sortTh("Team", "team", st.mgrSort)}${sortTh("Bids", "bids", st.mgrSort)}${sortTh("Won", "wins", st.mgrSort)}${sortTh("Win %", "winRate", st.mgrSort)}${sortTh("FA adds", "fa", st.mgrSort)}${sortTh("Spent", "spend", st.mgrSort)}${sortTh("Median win", "medWin", st.mgrSort)}${sortTh("Overpay", "overpay", st.mgrSort)}<th>Wins at</th>
        </tr></thead><tbody>${mgrRows}</tbody></table></div>
        <p class="small muted">Overpay = median gap between a manager's winning bid and the next best bid, on contested wins (a high number means leaving money on the table). Win % counts real bids only.</p>
      </div>

      <div class="card">
        <h2>Transactions <span class="muted small">(${logAll.length.toLocaleString()})</span></h2>
        <div class="select-row">
          <div class="filter-group"><span class="filter-label">Team</span>
            <select id="faabl-team"><option value="">All</option>${[...new Set(rows.map((r) => r.team_id))].sort((a, b) => teamName(a).localeCompare(teamName(b))).map((id) => `<option value="${id}" ${st.team === id ? "selected" : ""}>${escapeHtml(teamName(id))}</option>`).join("")}</select></div>
          <div class="filter-group"><span class="filter-label">Result</span>
            <select id="faabl-result">${[["all", "Everything"], ["won", "Won bids"], ["outbid", "Outbid"], ["fa", "Free-agent adds"], ["failed", "Failed"]].map(([v, l]) => `<option value="${v}" ${st.result === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
          <input type="search" id="faabl-q" placeholder="Search player" value="${escapeHtml(st.q)}">
        </div>
        <div class="table-wrap"><table class="faabl-table" id="faabl-log"><thead><tr>
          ${sortTh("When", "when", st.logSort)}${sortTh("Team", "team", st.logSort)}${sortTh("Player", "player", st.logSort)}${sortTh("Result", "result", st.logSort)}${sortTh("Bid", "bid", st.logSort)}${sortTh("% budget", "pct", st.logSort)}<th title="This bid vs the pooled median winning bid for that position and point in the season">vs pooled</th>${sortTh("Auction", "bidders", st.logSort)}
        </tr></thead><tbody>${logRows || `<tr><td colspan="8" class="muted">No transactions match.</td></tr>`}</tbody></table></div>
        ${logSorted.length > shown.length ? `<p><button class="player-chip" id="faabl-more">Show ${Math.min(200, logSorted.length - shown.length)} more</button> <span class="muted small">${shown.length} of ${logSorted.length.toLocaleString()}</span></p>` : ""}
      </div>`;

    // ---- wiring ----
    container.querySelectorAll("#faabl-seasons button").forEach((b) => b.addEventListener("click", () => {
      const v = b.dataset.season;
      if (v === "all") st.seasons = null;
      else {
        const s = Number(v);
        const cur = st.seasons ? new Set(st.seasons) : new Set(allSeasons);
        if (cur.has(s) && cur.size > 1) cur.delete(s); else cur.add(s);
        st.seasons = cur.size === allSeasons.length ? null : cur;
      }
      st.limit = 100; draw();
    }));
    container.querySelector("#faabl-pos").addEventListener("change", (e) => { st.pos = e.target.value || null; st.limit = 100; draw(); });
    container.querySelector("#faabl-team").addEventListener("change", (e) => { st.team = e.target.value ? Number(e.target.value) : null; st.limit = 100; draw(); });
    container.querySelector("#faabl-result").addEventListener("change", (e) => { st.result = e.target.value; st.limit = 100; draw(); });
    const qEl = container.querySelector("#faabl-q");
    qEl.addEventListener("input", (e) => {
      st.q = e.target.value; st.limit = 100;
      const pos = e.target.selectionStart;
      draw();
      const again = container.querySelector("#faabl-q");
      again.focus(); again.setSelectionRange(pos, pos);
    });
    container.querySelectorAll("#faabl-mgr th[data-sort]").forEach((th) => th.addEventListener("click", () => { toggleSort(st.mgrSort, th.dataset.sort, th.dataset.sort === "team" ? 1 : -1); draw(); }));
    container.querySelectorAll("#faabl-log th[data-sort]").forEach((th) => th.addEventListener("click", () => { toggleSort(st.logSort, th.dataset.sort, th.dataset.sort === "team" || th.dataset.sort === "player" ? 1 : -1); draw(); }));
    const more = container.querySelector("#faabl-more");
    if (more) more.addEventListener("click", () => { st.limit += 200; draw(); });
  };
  draw();
}
