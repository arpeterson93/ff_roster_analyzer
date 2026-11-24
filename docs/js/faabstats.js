// Pure stats over faab_league.json (engine/faab_league.py) - no DOM, shared by
// the FAAB tab (faab.js) and the player modal's "in this league" strip
// (playermodal.js), and exercised directly by a node smoke test.
//
// Units: a bid's "pct" is bid dollars / that season's budget (the same
// %-of-starting-budget normalisation the global FAAB Lab and the pooled
// baselines use), so seasons with different budgets - and different leagues -
// compare directly.

export const THIN_N = 8; // fewer wins than this in a cell = "thin" (shown muted)
const SHRINK_K = 10;     // credibility: weight n / (n + K) on the league's own ratio, rest on 1.0

export function leagueRows(fl) {
  if (!fl || !fl.rows || !fl.rows.length) return [];
  if (fl._objs) return fl._objs;
  fl._objs = fl.rows.map((r) => {
    const o = {};
    fl.cols.forEach((c, i) => { o[c] = r[i]; });
    o.pct = o.budget > 0 ? o.bid / o.budget : null;
    o.costPct = o.budget > 0 ? o.cost / o.budget : null;
    return o;
  });
  return fl._objs;
}

export function quantile(sorted, q) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

export function summarize(values) {
  const s = values.slice().sort((a, b) => a - b);
  if (!s.length) return { n: 0, p25: null, p50: null, p75: null, p90: null, mean: null };
  return { n: s.length, p25: quantile(s, 0.25), p50: quantile(s, 0.5), p75: quantile(s, 0.75), p90: quantile(s, 0.9), mean: s.reduce((a, b) => a + b, 0) / s.length };
}

// A real contested win: waiver claim, executed, above $0, known budget - the
// exact definition the pooled baselines were built from.
export const isContestedWin = (r) => r.kind === "W" && r.signal === "won" && r.bid > 0 && r.pct !== null;

export function weekBucket(week, buckets) {
  const b = (buckets || []).find(([lo, hi]) => week >= lo && week <= hi);
  return b ? `${b[0]}-${b[1]}` : null;
}

// Pooled baseline for a (position, week-bucket) cell, falling back to ever
// broader cells when the specific one has too few pooled wins.
export function globalBaseline(baselines, pos, bucket) {
  if (!baselines) return null;
  const tries = [
    pos && bucket ? (baselines.by_position_week_bucket || {})[`${pos}|${bucket}`] : null,
    pos ? (baselines.by_position || {})[pos] : null,
    bucket ? (baselines.by_week_bucket || {})[bucket] : null,
    baselines.all,
  ];
  return tries.find((t) => t && t.n >= 30) || null;
}

// League vs pooled price for one slice of wins. ratio = league median / pooled
// median; `shrunk` pulls it toward 1.0 when the league has few wins in the
// slice (n/(n+K)), so one lucky $40 RB can't claim "this league pays 3x".
export function ratioStat(wins, baselines, pos, bucket) {
  const league = summarize(wins.map((r) => r.pct));
  const g = globalBaseline(baselines, pos, bucket);
  if (!league.n || !g || !g.p50) return { n: league.n, league, global: g, ratio: null, shrunk: null, thin: true };
  const ratio = league.p50 / g.p50;
  const w = league.n / (league.n + SHRINK_K);
  return { n: league.n, league, global: g, ratio, shrunk: 1 + (ratio - 1) * w, thin: league.n < THIN_N };
}

// One filtered slice of the history. filter: {seasons:Set|null, pos:string|null, team:number|null}
export function applyFilter(rows, f) {
  return rows.filter((r) =>
    (!f.seasons || f.seasons.has(r.season)) && (!f.pos || r.pos === f.pos) && (!f.team || r.team_id === f.team)
  );
}

// Auction-level facts for the slice: how contested, how big the margin.
export function auctionStats(rows) {
  const waiver = rows.filter((r) => r.kind === "W" && (r.signal === "won" || r.signal === "outbid"));
  const wins = waiver.filter((r) => r.signal === "won");
  const contested = wins.filter((r) => r.n_bids >= 2);
  const margins = contested.filter((r) => r.runner_up !== null && r.budget > 0).map((r) => (r.win_bid - r.runner_up) / r.budget);
  const nBids = wins.map((r) => r.n_bids).filter((n) => n !== null);
  return {
    auctions: wins.length,
    contestedShare: wins.length ? contested.length / wins.length : null,
    avgBidders: nBids.length ? nBids.reduce((a, b) => a + b, 0) / nBids.length : null,
    medianMargin: margins.length ? summarize(margins).p50 : null,
    outbidShare: waiver.length ? waiver.filter((r) => r.signal === "outbid").length / waiver.length : null,
  };
}

// Per-team tendencies over the slice. "overpay" = winning bid minus the best
// losing bid, as % of budget, only for auctions that were actually contested.
export function managerStats(rows) {
  const by = new Map();
  const get = (id) => {
    if (!by.has(id)) by.set(id, { team_id: id, bids: 0, wins: 0, outbid: 0, fa: 0, spend: 0, winPcts: [], overpay: [], pos: {}, biggest: null });
    return by.get(id);
  };
  rows.forEach((r) => {
    const m = get(r.team_id);
    if (r.kind === "FA") { if (r.signal === "won") m.fa++; return; }
    if (r.signal === "won") {
      m.wins++; m.bids++; m.spend += r.cost;
      if (r.pct !== null && r.bid > 0) m.winPcts.push(r.pct);
      if (r.runner_up !== null && r.budget > 0) m.overpay.push((r.win_bid - r.runner_up) / r.budget);
      if (r.pos) m.pos[r.pos] = (m.pos[r.pos] || 0) + 1;
      if (!m.biggest || r.bid > m.biggest.bid) m.biggest = r;
    } else if (r.signal === "outbid") { m.outbid++; m.bids++; }
  });
  return [...by.values()].map((m) => ({
    ...m,
    winRate: m.bids ? m.wins / m.bids : null,
    medianWinPct: m.winPcts.length ? summarize(m.winPcts).p50 : null,
    medianOverpay: m.overpay.length ? summarize(m.overpay).p50 : null,
  }));
}

// Share of a season's spend landing in each week (averaged over the seasons
// in the slice), plus the running total - "how front-loaded is this league".
export function spendByWeek(rows, maxWeek = 17) {
  const perSeason = new Map();
  rows.forEach((r) => {
    if (r.signal !== "won" || !(r.cost > 0)) return;
    if (!perSeason.has(r.season)) perSeason.set(r.season, new Array(maxWeek + 1).fill(0));
    perSeason.get(r.season)[Math.min(r.week, maxWeek)] += r.cost;
  });
  const out = new Array(maxWeek + 1).fill(0);
  perSeason.forEach((weeks) => {
    const total = weeks.reduce((a, b) => a + b, 0);
    if (total > 0) weeks.forEach((v, w) => { out[w] += v / total / perSeason.size; });
  });
  let run = 0;
  return out.map((share, week) => ({ week, share, cumulative: (run += share) })).slice(1);
}

// Median winning pct by week (for the "price by week" strip).
export function pctByWeek(wins, maxWeek = 17) {
  const buckets = Array.from({ length: maxWeek + 1 }, () => []);
  wins.forEach((r) => buckets[Math.min(r.week, maxWeek)].push(r.pct));
  return buckets.map((v, week) => ({ week, ...summarize(v) })).slice(1);
}

// For the player modal: this league's adjustment for a position at a point in
// the season, plus the closest real past winning bids to look at.
export function leagueLens(fl, pos, week, currentSeason) {
  const rows = leagueRows(fl);
  if (!rows.length) return null;
  const bucket = weekBucket(week, fl.week_buckets);
  const wins = rows.filter(isContestedWin);
  const posWins = wins.filter((r) => r.pos === pos);
  const bucketWins = posWins.filter((r) => weekBucket(r.week, fl.week_buckets) === bucket);
  // Prefer the position x week-bucket cell; fall back to the position alone.
  const cell = bucketWins.length >= THIN_N ? ratioStat(bucketWins, fl.baselines, pos, bucket) : ratioStat(posWins, fl.baselines, pos, null);
  const basis = bucketWins.length >= THIN_N ? "bucket" : "position";
  const budget = fl.budgets ? fl.budgets[String(currentSeason)] ?? null : null;
  // Fallback only (a league with no pooled-model history): recent same-position wins, any week.
  const comparable = posWins.slice().sort((a, b) => b.season - a.season || b.week - a.week).slice(0, 6);
  return { ...cell, basis, bucket, pos, comparable, budget, totalPosWins: posWins.length };
}

// Past players in this league whose pooled-model read (median price, bid probability) looked like the one being
// viewed, and what actually happened to each here: won at X% of budget, bid but lost, or nobody bid. Events come
// from model_backtest.events: [season, week, name, pos, outcome w/l/n, actual_pct, pooled_median, p_bid].
// Closeness = relative gap in pooled median plus gap in bid probability, with a small nudge toward recent seasons.
export function similarEvents(bt, pos, pooledMedian, pBid, currentSeason, limit = 8, modelLog = null, logSeason = null) {
  // The backtest is frozen at the last completed season; this season's settled model_log rows (what the live
  // model said before each week's waivers vs what then happened) extend it.
  const live = (modelLog || [])
    .filter((m) => m.status !== "pending" && m.med !== null && m.med !== undefined && m.p !== null && m.p !== undefined)
    .map((m) => [logSeason, m.week, m.name, m.pos, m.status === "won" ? "w" : m.status === "lost" ? "l" : "n", m.win_pct ?? null, m.med, m.p]);
  const ev = [...((bt && bt.events) || []), ...live];
  if (!ev.length || pooledMedian === null || pooledMedian === undefined) return [];
  const p0 = pBid ?? 0;
  return ev
    .filter((e) => e[3] === pos)
    .map((e) => {
      const d = Math.abs(e[6] - pooledMedian) / (0.02 + pooledMedian) + 2 * Math.abs(e[7] - p0) + 0.04 * Math.max(0, currentSeason - e[0]);
      return { season: e[0], week: e[1], name: e[2], outcome: e[4], actual: e[5], pooled: e[6], p: e[7], d };
    })
    .sort((a, b) => a.d - b.d)
    .slice(0, limit)
    .sort((a, b) => b.season - a.season || b.week - a.week);
}

// ---- model vs reality ----
// Predictions and outcomes are both fractions of the season's budget. "miss" =
// actual winning bid - model median, so a positive miss means the model bid too low.

const CALIB_EDGES = [0, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0001];

function missStats(pairs) {
  // pairs: [{pos, pred, actual}]
  const by = new Map();
  const add = (key, pr) => { if (!by.has(key)) by.set(key, []); by.get(key).push(pr); };
  pairs.forEach((pr) => { add("All", pr); add(pr.pos || "?", pr); });
  return [...by.entries()].map(([pos, list]) => {
    const miss = list.map((x) => x.actual - x.pred);
    const abs = miss.map(Math.abs);
    return {
      pos, n: list.length,
      pred: summarize(list.map((x) => x.pred)).p50, actual: summarize(list.map((x) => x.actual)).p50,
      bias: summarize(miss).p50, mae: abs.reduce((a, b) => a + b, 0) / abs.length,
      within: abs.filter((v) => v <= 0.03).length / abs.length, // inside 3 points of budget
    };
  }).sort((a, b) => (a.pos === "All" ? -1 : b.pos === "All" ? 1 : a.pos.localeCompare(b.pos)));
}

function calibration(points) {
  // points: [[p_bid, 1|0]] -> predicted-vs-actual bid rate per probability bucket
  return CALIB_EDGES.slice(0, -1).map((lo, i) => {
    const hi = CALIB_EDGES[i + 1];
    const inB = points.filter(([p]) => p >= lo && p < hi);
    return {
      lo, hi: Math.min(hi, 1), n: inB.length,
      predicted: inB.length ? inB.reduce((a, [p]) => a + p, 0) / inB.length : null,
      actual: inB.length ? inB.reduce((a, [, y]) => a + y, 0) / inB.length : null,
    };
  });
}

// Historical held-out backtest (model_backtest in faab_league.json).
export function backtestStats(bt, pos = null) {
  if (!bt || !bt.won || !bt.won.length) return null;
  const won = bt.won.filter((r) => !pos || r[3] === pos).map((r) => ({ season: r[0], week: r[1], name: r[2], pos: r[3], actual: r[4], pred: r[5], p: r[6] }));
  return { won, byPos: missStats(won), calib: calibration(bt.calib || []) };
}

// This season's live tracking (model_log in faab_league.json).
export function liveModelStats(log, pos = null) {
  const rows = (log || []).filter((r) => !pos || r.pos === pos);
  const settled = rows.filter((r) => r.status !== "pending");
  const won = rows.filter((r) => r.status === "won" && r.win_pct !== null && r.med !== null);
  return {
    rows, pending: rows.length - settled.length, settled: settled.length,
    bids: rows.filter((r) => r.status === "won" || r.status === "lost"),
    won, byPos: won.length ? missStats(won.map((r) => ({ pos: r.pos, pred: r.med, actual: r.win_pct }))) : [],
    calib: calibration(settled.filter((r) => r.p !== null).map((r) => [r.p, r.status === "no_bid" ? 0 : 1])),
  };
}
