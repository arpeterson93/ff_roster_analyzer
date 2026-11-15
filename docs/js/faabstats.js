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
  const comparable = (bucketWins.length ? bucketWins : posWins)
    .slice()
    .sort((a, b) => b.season - a.season || b.week - a.week)
    .slice(0, 6);
  return { ...cell, basis, bucket, pos, comparable, budget, totalPosWins: posWins.length };
}
