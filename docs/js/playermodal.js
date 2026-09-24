import { fmt, escapeHtml } from "./state.js";
import { POSITION_COLOR, opponentCellHtml, teamLabel, playerPhotoHtml, weeklyProjection, colorForRatio, ratioForRank, snapPct, attPct, tgtPct } from "./colors.js";
import { openModal } from "./modal.js";
import { groupedHeaderHtml, statCellsHtml } from "./statcolumns.js";

// Bar-per-play chart (x = elapsed game time, y = points scored on THAT
// play) plus a top-plays table, for weeks with a per-play breakdown (see
// engine/play_log.py - offense/kicker only, so a DST's game log never gets
// a clickable row here). Purely percentage-positioned (no fixed pixel
// widths anywhere) so it never needs horizontal scroll on a phone screen -
// the whole point was to see WHEN scoring happened (garbage time or not)
// without fighting the layout to see it.
// "23.45" elapsed minutes -> "2Q 5:33" (quarter + clock REMAINING in it,
// matching how the game clock itself reads, not "minutes since kickoff") -
// remaining time counts DOWN, so it decreases left-to-right on the chart
// same as elapsed time increases left-to-right. OT has no fixed period
// length to count down from on the frontend (10 min in the regular season,
// 15 in the playoffs) - so it counts down to `otEndMin` instead (the same
// real end-of-game/OT value the chart's own OT axis segment is sized to,
// see playLogDetailHtml's totalMinutes), keeping the same left-to-right
// "less time remaining" direction as every quarter instead of inverting it
// into a count-UP.
function formatGameClock(elapsedMin, otEndMin = 70) {
  if (elapsedMin > 60) {
    const remaining = Math.max(0, otEndMin - elapsedMin);
    const mins = Math.floor(remaining);
    const secs = Math.round((remaining - mins) * 60);
    return `OT ${mins}:${String(secs).padStart(2, "0")}`;
  }
  const quarter = Math.min(4, Math.floor(elapsedMin / 15) + 1);
  const remaining = Math.max(0, 15 - (elapsedMin - (quarter - 1) * 15));
  let mins = Math.floor(remaining);
  let secs = Math.round((remaining - mins) * 60);
  if (secs === 60) {
    secs = 0;
    mins += 1;
  }
  return `${quarter}Q ${mins}:${String(secs).padStart(2, "0")}`;
}

// Bars are colored by ROLE (which stat category the play came from), not
// the player's own roster position - a WR on an end-around still shows as
// a rushing-colored bar, a RB catching a swing pass shows as a receiving-
// colored bar. Reuses the position-pill palette already established
// elsewhere (colors.js's POSITION_COLOR) rather than inventing a new hue
// set: passing = QB color, rushing = RB color, receiving = WR color.
const ROLE_COLOR = { pass: POSITION_COLOR.QB, rush: POSITION_COLOR.RB, reception: POSITION_COLOR.WR };

function shortFieldNote(p) {
  return p.short_field ? ` - started at opp ${fmt(p.yardline, 0)}` : "";
}

function shortFieldMarkerHtml(p) {
  // A play that started at the opponent's 5 or closer (see engine/
  // play_log.py's _short_field_yardline) is a high-value goal-to-go look
  // regardless of what happened on it - flagged with a small dot, nested
  // as a CHILD of its own bar/marker (positioned at the parent's own
  // `bottom:100%`, i.e. just above whatever the parent's own top edge is,
  // whichever direction that bar happens to grow) rather than a sibling
  // pinned to a fixed chart height. Nesting means it automatically stays
  // above the right play - both visually (no chance of reading as
  // belonging to a taller neighboring bar instead) and positionally (it
  // moves with its parent if resolveBarOverlap nudges it apart from
  // others), and it needs no color of its own.
  if (!p.short_field) return "";
  return `<div class="play-shortfield-marker" title="Started at opponent's ${fmt(p.yardline, 0)}"></div>`;
}

function tdLabelHtml(p) {
  // Mirrors shortFieldMarkerHtml but on the opposite side: `top:100%` on a
  // nested child lands at the parent bar's own BOTTOM edge - which, for a
  // TD (always a positive, bottom-anchored bar in practice - a fumbled-away
  // TD belongs to the recovering defense, not this player), is exactly the
  // zero line, so the label sits just below it, same neighborhood as the
  // incomplete-target triangles. Nested rather than a chart-level sibling
  // for the same reason as the short-field dot: it travels with its own
  // bar if resolveBarOverlap nudges it apart from a neighbor.
  if (!p.is_td) return "";
  return `<div class="play-td-label">TD</div>`;
}

function playLogDetailHtml(plays, incompletions = [], gameDurationMin = 60, zeroPointPlays = []) {
  const positives = plays.filter((p) => p.points > 0).map((p) => p.points);
  const negatives = plays.filter((p) => p.points < 0).map((p) => -p.points);
  const maxPos = Math.max(1, ...positives, 0);
  const maxNeg = Math.max(1, ...negatives, 0);
  // baselinePct = % of the chart's height reserved BELOW the zero-line -
  // real fantasy scoring plays are overwhelmingly positive (a fumble/INT is
  // the rare exception), so most of the height should go to the common
  // case above the line, not be split evenly with it.
  const baselinePct = maxNeg > 0 ? 18 : 4;
  // sqrt (not linear) scale: a single TD is worth 3-7x a typical catch, and
  // on a linear scale that one play stretches the axis until every other
  // play is a sliver a couple pixels tall. sqrt keeps big plays reading as
  // bigger (ordering is preserved) without letting one outlier flatten
  // everything else - exact values are still in the tooltip/top table, this
  // only changes bar HEIGHT, not the numbers shown anywhere.
  const scaled = (v, max) => (max > 0 ? Math.sqrt(v / max) : 0);
  // Reserved headroom above the tallest possible bar for its own nested
  // short-field dot (see shortFieldMarkerHtml) - without this, a play that
  // was BOTH the game's highest-scoring play AND a short-field snap would
  // have its dot clipped off by the chart's overflow:hidden top edge.
  const TOP_MARGIN_PCT = 14;
  // The axis is 60 minutes (4 real 15-min quarters) unless the game went to
  // OT - then it extends to gameDurationMin (see engine/pipeline.py's
  // game_durations_by_game_id), the REAL end of the game across every play,
  // not just this player's own. A player's own last touch can land well
  // before the game actually ended (someone else wins it on a late OT
  // field goal without this player getting the ball back again) - sizing
  // the axis off only their own plays would draw the OT segment far
  // narrower than the overtime that actually happened. Still guarded by
  // every list's own elapsed times in case gameDurationMin is missing
  // (older cached data) or, in theory, undershoots.
  const REGULATION_MIN = 60;
  const allElapsed = [...plays, ...incompletions, ...zeroPointPlays].map((p) => p.elapsed_min);
  const totalMinutes = Math.max(REGULATION_MIN, gameDurationMin || 0, ...allElapsed);
  const hasOt = totalMinutes > REGULATION_MIN;
  // Every x-position on this chart goes through toChartPct rather than a
  // plain (minutes/total)*100 - bars/markers are centered on their own x
  // position (translateX(-50%)) and can be several pixels wide, so a play
  // right at kickoff or right at the final whistle would otherwise have
  // half its own shape clipped off by the chart's overflow:hidden edge.
  // Reserving a small margin on both ends keeps every play's shape fully
  // visible without needing to turn off the edge clipping entirely.
  const EDGE_MARGIN_PCT = 3;
  const toChartPct = (minutes) => EDGE_MARGIN_PCT + (minutes / totalMinutes) * (100 - 2 * EDGE_MARGIN_PCT);
  const bars = plays
    .map((p) => {
      const leftPct = toChartPct(p.elapsed_min);
      const isNeg = p.points < 0;
      // bottom/top are measured from OPPOSITE edges of the container, so
      // the same "baselinePct up from the bottom" line is `bottom:
      // baselinePct%` for a bar growing UP from it, but `top: (100 -
      // baselinePct)%` for one growing DOWN from that same line.
      const heightPct = isNeg
        ? Math.max(3, scaled(Math.abs(p.points), maxNeg) * baselinePct)
        : Math.max(3, scaled(p.points, maxPos) * (100 - baselinePct - TOP_MARGIN_PCT));
      const posStyle = isNeg ? `top:${100 - baselinePct}%; height:${heightPct}%;` : `bottom:${baselinePct}%; height:${heightPct}%;`;
      // Role color applies regardless of sign - which stat category the
      // play came from is exactly as true for a fumble as for a gain, and
      // "this cost you points" already reads from the bar growing DOWN
      // from the baseline instead of up, so it doesn't also need a
      // dedicated color. A TD keeps its role color but gets a gold OUTLINE
      // instead of a flat fill (see .play-bar-td in styles.css), so a
      // rushing TD still reads differently from a passing TD.
      const roleStyle = ROLE_COLOR[p.role] ? `background:${ROLE_COLOR[p.role]};` : "";
      const title = `${formatGameClock(p.elapsed_min, totalMinutes)} - ${escapeHtml(p.label)}${shortFieldNote(p)}: ${p.points >= 0 ? "+" : ""}${fmt(p.points, 1)} pts`;
      return `<div class="play-bar ${isNeg ? "play-bar-neg" : ""}" style="left:${leftPct}%; ${posStyle} ${roleStyle}" title="${title}">${shortFieldMarkerHtml(p)}${tdLabelHtml(p)}</div>`;
    })
    .join("");
  const top = plays.slice().sort((a, b) => b.points - a.points).slice(0, 5);
  const topRows = top
    .map((p) => `<tr><td class="num">${formatGameClock(p.elapsed_min, totalMinutes)}</td><td>${escapeHtml(p.label)}</td><td class="num">${p.points >= 0 ? "+" : ""}${fmt(p.points, 1)}</td></tr>`)
    .join("");
  // Incomplete targets are always 0 points (see engine/play_log.py's
  // incomplete_targets_for_player) - never a bar, but still a real,
  // time-stamped event worth marking. Reuses the border-bottom triangle
  // trick: a 0-height box's own position IS the apex, and the visible
  // triangle (the border) renders BELOW that point - so anchoring the box
  // itself at `bottom: baselinePct%` (the same y=0 line the real baseline
  // sits on) would put the apex exactly on the line - EXCEPT border-bottom
  // also adds to the box's own rendered height, which pushes what
  // `bottom:%` actually anchors (the box's outer/bottom edge, i.e. the
  // triangle's BASE) down onto the line instead, leaving the apex hovering
  // above it. The extra translateY nudges the whole box down by exactly
  // that border height so the apex - not the base - lands on the line.
  const incompleteMarkers = (incompletions || [])
    .map((inc) => {
      const leftPct = toChartPct(inc.elapsed_min);
      const title = `${formatGameClock(inc.elapsed_min, totalMinutes)} - ${escapeHtml(inc.label)}${shortFieldNote(inc)}`;
      return `<div class="play-incomplete-marker" style="left:${leftPct}%; bottom:${baselinePct}%" title="${title}">${shortFieldMarkerHtml(inc)}</div>`;
    })
    .join("");
  // A real carry/catch that scored exactly 0 points (see engine/
  // play_log.py's zero_point_plays_for_player - a stuffed goal-line rush,
  // a 0-yard catch in non-PPR) still happened and is worth seeing. Shaped
  // like the other bars (same class, so it also gets resolveBarOverlap's
  // spacing) but with a small FIXED height straddling the zero line rather
  // than one derived from the sqrt scale - there's no real point value to
  // represent, so plugging it into that scale would be meaningless, and a
  // fixed height would otherwise land on the exact same 3%-floor height as
  // countless genuinely tiny real plays. The plain gray fill (no role
  // color) is what actually keeps it from reading as a scoring play.
  const ZERO_BAR_HALF_SPAN_PCT = 3;
  const zeroMarkers = (zeroPointPlays || [])
    .map((z) => {
      const leftPct = toChartPct(z.elapsed_min);
      const title = `${formatGameClock(z.elapsed_min, totalMinutes)} - ${escapeHtml(z.label)}${shortFieldNote(z)}: 0 pts`;
      const bottom = Math.max(0, baselinePct - ZERO_BAR_HALF_SPAN_PCT);
      return `<div class="play-bar play-zero-bar" style="left:${leftPct}%; bottom:${bottom}%; height:${ZERO_BAR_HALF_SPAN_PCT * 2}%" title="${title}">${shortFieldMarkerHtml(z)}</div>`;
    })
    .join("");
  // Segments: four fixed 15-minute quarters, plus one more (regulation to
  // totalMinutes) only when the game actually went there. Interior
  // boundaries (everything but the very last segment's end, which is the
  // right edge of the chart) get a divider line, same as the existing
  // Q1/Q2/Q3 lines - a game that reaches OT now also gets one at the 60
  // minute mark, separating Q4 from OT.
  const segments = [15, 30, 45, 60].map((end, i) => ({ start: i * 15, end, label: `Q${i + 1}` }));
  if (hasOt) segments.push({ start: REGULATION_MIN, end: totalMinutes, label: "OT" });
  const qlines = segments
    .slice(0, -1)
    .map((s) => `<div class="play-chart-qline" style="left:${toChartPct(s.end).toFixed(2)}%"></div>`)
    .join("");
  const axisLabels = segments
    .map((s) => `<span style="left:${toChartPct((s.start + s.end) / 2).toFixed(2)}%">${s.label}</span>`)
    .join("");
  return `
    <div class="play-chart-wrap">
      <div class="play-chart-row">
        <div class="play-chart-plotcol">
          <div class="play-chart">
            <div class="play-chart-baseline" style="bottom:${baselinePct}%"></div>
            ${qlines}
            ${bars}
            ${zeroMarkers}
            ${incompleteMarkers}
          </div>
          <div class="play-chart-axis">
            ${axisLabels}
          </div>
        </div>
        <div class="play-chart-yaxis">
          <div class="play-chart-yaxis-label play-chart-yaxis-max">${fmt(maxPos, 1)} pts</div>
          <div class="play-chart-yaxis-label play-chart-yaxis-zero" style="bottom:${baselinePct}%">0</div>
        </div>
      </div>
    </div>
    <table class="play-top-table">
      <thead><tr><th>Time</th><th>Play</th><th class="num">Pts</th></tr></thead>
      <tbody>${topRows}</tbody>
    </table>
  `;
}

// Snap %/Att %/Tgt % (see colors.js's snapPct/attPct/tgtPct) - a skill-
// position-only concept, appended after Misc rather than baked into
// statcolumns.js's own OFFENSE_BLOCKS: a kicker's own participation lives
// under snap_counts' SPECIAL-TEAMS snaps, not offense_snaps, and neither K
// nor DST has carries/targets at all, so neither gets this block.
const USAGE_BLOCK = ["Usage", [["_snap_pct", "Snap%"], ["_att_pct", "Att%"], ["_tgt_pct", "Tgt%"]]];
const _USAGE_POSITIONS = new Set(["QB", "RB", "WR", "TE"]);

function fmtUsagePct(v) {
  return v === null || v === undefined ? "-" : `${Math.round(v * 100)}%`;
}

// Same grouped stat columns as the points-against modal, so a position's
// actual-results columns read identically in both places. A played week
// with a per-play breakdown available (data.gameLogPlays - offense/kicker
// only, see engine/play_log.py) is clickable to expand it; weeks without
// one (DST, or a build from before this existed) render exactly as before.
function gameLogTable(player, data) {
  const extraBlocks = _USAGE_POSITIONS.has(player.position) ? [USAGE_BLOCK] : [];
  const { top, bottom, flatColumns, blockEnds } = groupedHeaderHtml(player.position, ["Wk", "Opp"], extraBlocks);
  const colCount = flatColumns.length + 3;
  const currentWeek = data.meta.current_week;
  const playsByWeek = (data.gameLogPlays || {})[player.id] || {};
  const rows = (player.weekly || [])
    .filter((w) => w.actual)
    .slice()
    .reverse()
    .map((w) => {
      const fpts = w.actual.points !== undefined && w.actual.points !== null ? fmt(w.actual.points, 1) : "-";
      const weekDetail = playsByWeek[String(w.week)];
      const scoringPlays = weekDetail?.plays || [];
      const incompletions = weekDetail?.incompletions || [];
      const zeroPointPlays = weekDetail?.zero_point_plays || [];
      const hasDetail = scoringPlays.length > 0 || incompletions.length > 0 || zeroPointPlays.length > 0;
      const stats = extraBlocks.length
        ? {
            ...w.actual.stats,
            _snap_pct: fmtUsagePct(snapPct(player, w.week, currentWeek)),
            _att_pct: fmtUsagePct(attPct(player, w.week, currentWeek)),
            _tgt_pct: fmtUsagePct(tgtPct(player, w.week, currentWeek)),
          }
        : w.actual.stats;
      const mainRow = `<tr class="game-log-row ${hasDetail ? "clickable-row" : ""}" data-week="${w.week}"><td>${w.week}</td><td>${opponentCellHtml(w)}</td>${statCellsHtml(stats, flatColumns, blockEnds)}<td><strong>${fpts}</strong></td></tr>`;
      const detailRow = hasDetail
        ? `<tr class="game-log-detail" data-week-detail="${w.week}" hidden><td colspan="${colCount}">${playLogDetailHtml(scoringPlays, incompletions, weekDetail?.game_duration_min, zeroPointPlays)}</td></tr>`
        : "";
      return mainRow + detailRow;
    })
    .join("");
  if (!rows) return `<p class="muted small">No games played yet this season.</p>`;
  return `<div class="table-wrap"><table>${top}${bottom}<tbody>${rows}</tbody></table></div>`;
}

// Bars/markers are positioned by percent-of-elapsed-time, so two plays
// seconds apart really can land on the same pixels - and the chart is
// `hidden` (0 width) until a row is expanded, so this can't run until then.
// Reads each element's real on-screen position/width (only known once
// visible), then nudges overlapping ones apart by a minimum pixel gap while
// keeping them in time order - a forward pass pushes each one clear of the
// one before it, and a backward pass pulls everything back inside the
// chart if that pushed the last one(s) past the right edge. Covers
// .play-incomplete-marker alongside .play-bar (which the zero-point bars
// share a class with, so they're already included) so nothing renders on
// top of - and reads as part of - a different, unrelated play. Each item is
// also pre-nudged clear of the fixed period-boundary lines (Q1/Q2/Q3/end-of-
// regulation) BEFORE the bar-vs-bar spacing pass runs, so that pass always
// has the final say - nudging away from a line can never reintroduce an
// overlap between two plays, even if it means one ends up a little closer
// to a divider than ideal (rare, and far less visible than actual overlap).
function resolveBarOverlap(chartEl) {
  const width = chartEl.clientWidth;
  const bars = Array.from(chartEl.querySelectorAll(".play-bar, .play-incomplete-marker"));
  if (!width || bars.length < 1) return;
  const GAP = 3;
  const LINE_GAP = 3;
  const qlineCenters = Array.from(chartEl.querySelectorAll(".play-chart-qline")).map(
    (el) => (parseFloat(el.style.left) / 100) * width
  );
  const items = bars
    .map((el) => {
      const w = el.offsetWidth;
      let center = (parseFloat(el.style.left) / 100) * width;
      qlineCenters.forEach((lineX) => {
        const minDist = w / 2 + LINE_GAP;
        if (Math.abs(center - lineX) < minDist) {
          center = center >= lineX ? lineX + minDist : lineX - minDist;
        }
      });
      return { el, w, center };
    })
    .sort((a, b) => a.center - b.center);

  if (items.length > 1) {
    for (let i = 1; i < items.length; i++) {
      const minCenter = items[i - 1].center + items[i - 1].w / 2 + GAP + items[i].w / 2;
      if (items[i].center < minCenter) items[i].center = minCenter;
    }
    const last = items[items.length - 1];
    const maxCenter = width - last.w / 2;
    if (last.center > maxCenter) {
      last.center = maxCenter;
      for (let i = items.length - 2; i >= 0; i--) {
        const maxAllowed = items[i + 1].center - items[i + 1].w / 2 - GAP - items[i].w / 2;
        if (items[i].center > maxAllowed) items[i].center = maxAllowed;
      }
    }
  }

  items.forEach(({ el, center }) => {
    el.style.left = `${(center / width) * 100}%`;
  });
}

function wireGameLogRows(scopeEl) {
  scopeEl.querySelectorAll("tr.game-log-row.clickable-row").forEach((row) => {
    row.addEventListener("click", () => {
      const detail = scopeEl.querySelector(`tr.game-log-detail[data-week-detail="${row.dataset.week}"]`);
      if (!detail) return;
      detail.hidden = !detail.hidden;
      if (!detail.hidden) {
        const chart = detail.querySelector(".play-chart");
        if (chart) resolveBarOverlap(chart);
      }
    });
  });
}

function wirePriceCompRows(scopeEl) {
  scopeEl.querySelectorAll("tr[data-price-comp-row].clickable-row").forEach((row) => {
    row.addEventListener("click", () => {
      const detail = scopeEl.querySelector(`tr.price-comp-detail[data-price-comp-detail="${row.dataset.priceCompRow}"]`);
      if (detail) detail.hidden = !detail.hidden;
    });
  });
}

function projectionTable(player, currentWeek) {
  // opponentCellHtml already colors/labels the Opp cell by matchup rank
  // (see colors.js) - a separate Matchup column repeated that. Proj IS
  // ESPN's own projection now, taken outright (see engine/valuation.py's
  // module docstring) - weeklyProjection's current-week special case just
  // prefers the fresher live fetch (espn_projected_week) over the batched
  // one, same number either way. "Our proj" is the old proprietary rank ->
  // curve -> baseline*matchup method, kept as a reference-only column where
  // ESPN's own number used to sit - w.our_projected, no current-week
  // special-casing needed since it's computed the same way every week. SD
  // is only ever ours (ESPN doesn't publish one), so it stays w.sd
  // regardless of week.
  // !w.actual alone isn't enough to mean "still to come" - a past bye week
  // or a past week where this player had no stat row (inactive, hadn't
  // joined the league yet) also has no actual, but it already happened -
  // see engine/pipeline.py's weekly-array comment. w.week >= currentWeek
  // is what actually means "remaining".
  const rows = (player.weekly || [])
    .filter((w) => !w.actual && w.week >= currentWeek)
    .map((w) => `<tr><td>${w.week}</td><td>${opponentCellHtml(w)}</td><td>${fmt(weeklyProjection(player, w.week, currentWeek), 1)}</td></tr>`)
    .join("");
  // Only a total-points projection is computed for future weeks (not a full
  // stat line), so this can't show the grouped stat columns the game log
  // does - just the scalar projection. SD/"Our proj" (the old proprietary
  // rank->curve->baseline*matchup reference number) used to have their own
  // columns here - dropped per the conversation this was built from, this
  // table now consolidates with Game Log/Value below it and didn't need the
  // extra reference columns competing for attention.
  return `<table><thead><tr><th>Wk</th><th>Opp</th><th>Proj</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// JS port of engine/faab_estimate.py's weighted_percentile - same "first
// value whose cumulative weight reaches the target" convention, kept in
// sync deliberately rather than shipping a lookup table, so the confidence
// slider computes any percentage live instead of snapping between a few
// values baked in at build time (see faabEstimateSection's price_confidence_samples).
function weightedPercentileJs(samples, pct) {
  if (!samples || !samples.length) return 0;
  const ordered = samples.slice().sort((a, b) => a[0] - b[0]);
  const total = ordered.reduce((s, [, w]) => s + w, 0);
  if (total <= 0) return ordered[ordered.length - 1][0];
  const target = (pct / 100) * total;
  let cum = 0;
  for (const [v, w] of ordered) {
    cum += w;
    if (cum >= target) return v;
  }
  return ordered[ordered.length - 1][0];
}

// Shared math/axis helpers for the two bid-distribution charts below (the
// per-comp dot plot and the aggregate confidence histogram) - see the
// conversation this was built from for why these replaced the old smoothed
// sparkline: a handful of real winning prices smoothed into a curve implied
// a shape with no data behind it, and the curve's own axis (0 to that
// comp's own max) gave no absolute sense of where the bulk of real bids
// actually sat.
function median(values) {
  const s = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}
function meanOf(values) {
  return values.reduce((a, b) => a + b, 0) / values.length;
}
function percentileOf(sorted, p) {
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}
// "Nice" round step (1/2/2.5/5/10 x 10^n) targeting ~targetCount divisions
// of `rough` - used for both chart's axis ticks and the histogram's bin
// width, so both land on numbers a person would actually pick by hand
// rather than an arbitrary n-based split.
function niceStep(rough) {
  if (rough <= 0) return 0.01;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const niceNorm = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return niceNorm * mag;
}
// Rounds UP to the nearest nice step so the axis itself tops out at a round
// number (not an arbitrary value like "19.3%") while still clearing the
// highest real value with a little headroom.
function niceAxisMax(rawMax, targetCount) {
  const step = niceStep(rawMax / targetCount);
  return Math.ceil(rawMax / step) * step;
}
function niceTicksJs(axisMax, targetCount) {
  const step = niceStep(axisMax / targetCount);
  const ticks = [];
  for (let t = 0; t <= axisMax + 1e-9; t += step) ticks.push(t);
  return ticks;
}
// Nearby real prices (within `tolerance` of the axis's own domain, ~1/60th
// of it) collapse into ONE cluster, sized by how many real bids landed
// there - the non-stacking answer to duplicate/near-duplicate bids (see the
// conversation this was built from: piling dots vertically got busy fast,
// and a text "xN" badge got dense with many duplicates - so the count is
// shown ONLY as the dot's own size, nothing else).
function clusterBidValues(sortedValues, tolerance) {
  const clusters = [];
  sortedValues.forEach((v) => {
    const last = clusters[clusters.length - 1];
    if (last && v - last.avg < tolerance) {
      last.values.push(v);
      last.avg = meanOf(last.values);
    } else {
      clusters.push({ values: [v], avg: v });
    }
  });
  return clusters;
}

// Rank/recency-average pairs stacked in one cell (see styles.css's
// .comp-rank-cell) rather than four separate columns - a comp table with
// player/when/signal/bid/%/pts/snap/rank/recent/flags would run to 9-10
// columns, wider than most phone screens can show without scrolling for
// what reads just as well stacked two-to-a-cell.
function rankCellHtml(weeklyRank, rosRank) {
  const wk = weeklyRank !== null && weeklyRank !== undefined ? `wk #${Math.round(weeklyRank)}` : "wk unranked";
  const ros = rosRank !== null && rosRank !== undefined ? `ROS #${Math.round(rosRank)}` : "ROS unranked";
  return `<span class="comp-rank-cell">${wk}<span class="sub">${ros}</span></span>`;
}
function recentCellHtml(pts, trailing, season) {
  const main = pts !== null && pts !== undefined ? `${fmt(pts, 1)} pts` : "–";
  return `<span class="comp-recent-cell">${main}<span class="sub">3wk ${fmt(trailing, 1)} · szn ${fmt(season, 1)}</span></span>`;
}
// SNAP always shown; ATT (RB carry share of his TEAM's own RB-position
// carries - see engine/faab_estimate.py's recent_carry_share) or TGT (WR/TE
// target share, any position - recent_target_share) added for the one
// position it's actually diagnostic for - see the conversation this was
// built from: meant to help a reader spot a high-snap-share player (a
// blocking fullback, an in-line TE) who isn't actually seeing the ball,
// something snap share alone can't distinguish from a real featured
// back/receiver. No % sign, no decimals, per the same conversation - these
// read as quick relative comps, not precise figures.
function usageCellHtml(position, snapPct, carryShare, targetShare) {
  const pct = (v) => (v !== null && v !== undefined ? Math.round(v * 100) : null);
  const snap = pct(snapPct);
  const main = snap !== null ? `SNAP ${snap}` : "–";
  const secondary =
    position === "RB" ? { label: "ATT", value: pct(carryShare) } : position === "WR" || position === "TE" ? { label: "TGT", value: pct(targetShare) } : null;
  const sub = secondary ? `<span class="sub">${secondary.label} ${secondary.value !== null ? secondary.value : "–"}</span>` : "";
  return `<span class="comp-recent-cell">${main}${sub}</span>`;
}
function flagsCellHtml(ownInjury, teammateInjury) {
  const flags = [ownInjury ? `<span class="comp-flag">inj</span>` : "", teammateInjury ? `<span class="comp-flag tm">tm inj</span>` : ""].join("");
  return flags || `<span class="muted small">–</span>`;
}
// Binary per-league count, not a weighted blend - of the leagues we have
// real data for this exact event (a real bid, or roster data confirming he
// was a free agent there - see consolidate_cross_league_events), how many
// saw ANY bid at all.
function bidRateCellHtml(leaguesWithBid, leaguesEligible) {
  if (leaguesEligible === null || leaguesEligible === undefined) return `<span class="muted small">–</span>`;
  const pct = leaguesEligible > 0 ? fmt((leaguesWithBid / leaguesEligible) * 100, 0) + "%" : "–";
  return `<span class="comp-recent-cell">${leaguesWithBid}/${leaguesEligible}<span class="sub">${pct} of leagues</span></span>`;
}

const INTEREST_LABEL = { high: "High", medium: "Med", low: "Low" };

// "Which owners in the league are likely to want this guy, and why" - see
// engine/pipeline.py's team_interest_for. Two independent reasons roll up
// into one Low/Med/High level per owner: a real handcuff tie (they own the
// injured starter this player would step in for - always shown at High,
// regardless of the roster-math gain number) and/or a real lineup upgrade
// (fa_values' actual best add/drop point swing, bucketed by percentile of
// this week's own gain spread across the league - the same NMD number
// Rankings shows, just for every owner here instead of only yours).
function teamInterestSection(teamInterest, data) {
  if (!teamInterest || !teamInterest.length) return "";
  const rows = teamInterest
    .map((ti) => {
      const team = data.teamsById.get(ti.team_id);
      const owner = team ? escapeHtml(teamLabel(team)) : `Team ${ti.team_id}`;
      const reason = ti.handcuff_of
        ? `<span class="handcuff-tag">owns ${escapeHtml(ti.handcuff_of)}</span>`
        : ti.nmd_gain !== null && ti.nmd_gain !== undefined
        ? `<span class="muted small">${ti.nmd_gain >= 0 ? "+" : ""}${fmt(ti.nmd_gain, 1)} pts/wk to their lineup</span>`
        : `<span class="muted small">–</span>`;
      return `<tr><td>${owner}</td><td><span class="interest-chip ${ti.level}">${INTEREST_LABEL[ti.level] || ti.level}</span></td><td>${reason}</td></tr>`;
    })
    .join("");
  return `
    <h3>Likely interested owners</h3>
    <div class="table-wrap"><table><thead><tr><th>Owner</th><th>Interest</th><th>Why</th></tr></thead><tbody>${rows}</tbody></table></div>
  `;
}

// Three deliberately-explainable numbers (see engine/faab_estimate.py) -
// the comps table IS the explanation for comp_based, not just supporting
// detail, so it's shown in full rather than collapsed away.
function faabEstimateSection(player, data) {
  const est = (data.faabEstimates || {})[player.id];
  if (!est) return "";
  const inputs = est.inputs || {};
  if (est.below_relevance_threshold) {
    return `
      <div class="bar-row"><div class="bar-label">Estimate</div><div class="bar-value">0%</div></div>
      <p class="muted small">Not enough recent usage or production (last week: ${inputs.prior_week_had_stat_row ? `${fmt(inputs.prior_week_actual_points, 1)} pts` : "no stat line"}, ${inputs.snap_pct_prior_week !== null && inputs.snap_pct_prior_week !== undefined ? `${fmt(inputs.snap_pct_prior_week * 100, 0)}% snaps` : "no recent snap data"}) to be worth modeling as a real bid target - defaulting to 0% rather than extrapolating from unrelated comps.</p>
      ${teamInterestSection(est.team_interest, data)}
    `;
  }
  // o_league_detail is only set when this comp is a cross-league
  // consolidated event (see engine/faab_estimate.py's
  // consolidate_cross_league_events) that The O League itself was also
  // part of - real specificity from the one league a reader actually
  // knows, on top of the cross-league statistical read.
  const oLeagueTag = (detail) =>
    !detail
      ? ""
      : detail.signal === "won"
      ? `<div class="muted small">The O League: won $${fmt(detail.bid_dollars, 2)}</div>`
      : `<div class="muted small">The O League: ${detail.signal.replace("_", " ")}</div>`;

  // bid_distribution (see engine/faab_estimate.py's _price_comp_bid_distribution)
  // is every pooled league's own real WINNING price for this comp's same
  // event - never a losing bid (a losing bid is a censored observation,
  // never the real win/lose threshold - see that function's docstring), all
  // in %-of-budget units so they're meaningfully comparable across leagues
  // with different budgets, each tagged with its own source_league_id.
  //
  // Rendered as a DOT PLOT, not a smoothed sparkline - a curve through as
  // few as 2-3 real prices implied a shape with no data behind it (see the
  // conversation this was built from). Every real price is its own dot;
  // dots within a small tolerance of each other cluster into ONE dot sized
  // by count (clusterBidValues) rather than stacking vertically or getting
  // an "xN" text tag - both were tried and got busy fast with several
  // duplicate/near-duplicate bids. Axis runs 0 to niceAxisMax (a round
  // number a bit past the highest real price), with tick labels, so "where
  // the bulk sits" reads in absolute terms rather than just relative
  // position between two comp-specific endpoints. Median (solid line) and
  // mean (triangle) both always shown - median-line has a gap below its own
  // label so the two never visually overlap. A dot beyond 1.5x the
  // IQR past p75 renders in the "outlier" color - a real winning price far
  // above the rest of this SAME real-world event, i.e. one league's bidder
  // paid well beyond what everyone else did for the identical player/week.
  // Plain HTML/CSS throughout (no SVG) - a circle sized in real px stays a
  // circle regardless of the track's own width, where an SVG viewBox
  // stretched non-uniformly to fill a wide container would render every
  // dot as an ellipse.
  function priceCompDistributionHtml(c) {
    const raw = (c.bid_distribution || []).map((v) => v.value);
    if (!raw.length) return "";
    const sorted = raw.slice().sort((a, b) => a - b);
    const axisMax = niceAxisMax(sorted[sorted.length - 1] * 1.15, 4);
    const xPct = (v) => (v / axisMax) * 100;
    const p25 = percentileOf(sorted, 25), p75 = percentileOf(sorted, 75);
    const med = median(raw), avg = meanOf(raw);
    const iqr = p75 - p25;

    const clusters = clusterBidValues(sorted, axisMax / 30);
    const baseR = 4, maxR = 9;
    const dots = clusters
      .map((cl) => {
        const r = Math.min(maxR, baseR * Math.sqrt(cl.values.length));
        const isOutlier = iqr > 1e-6 && cl.avg > p75 + iqr * 1.5;
        const title = cl.values.length > 1 ? `${cl.values.length} winning bids near ${fmt(cl.avg * 100, 1)}%` : `${fmt(cl.avg * 100, 1)}%`;
        return `<div class="faab-dot${isOutlier ? " faab-dot-outlier" : ""}" style="left:${xPct(cl.avg).toFixed(2)}%; width:${(r * 2).toFixed(1)}px; height:${(r * 2).toFixed(1)}px;" title="${title}"></div>`;
      })
      .join("");

    const selfMineMarkers = (c.bid_distribution || [])
      .filter((v) => Math.abs(v.value - c.pct_of_remaining_budget) < 1e-9 || (data.meta.league_id !== null && data.meta.league_id !== undefined && v.source_league_id === data.meta.league_id))
      .map((v) => {
        const isSelf = Math.abs(v.value - c.pct_of_remaining_budget) < 1e-9;
        const isMine = data.meta.league_id !== null && data.meta.league_id !== undefined && v.source_league_id === data.meta.league_id;
        const label = isSelf && isMine ? " (this comp, your league)" : isSelf ? " (this comp)" : " (your league)";
        const cls = ["faab-tick-marker", isSelf ? "faab-tick-marker-self" : "", isMine ? "faab-tick-marker-mine" : ""].filter(Boolean).join(" ");
        return `<div class="${cls}" style="left:${xPct(v.value).toFixed(2)}%" title="${fmt(v.value * 100, 1)}%${label}"></div>`;
      })
      .join("");

    const ticks = niceTicksJs(axisMax, 4);
    const gridlines = ticks.map((t) => `<div class="faab-gridline" style="left:${xPct(t).toFixed(2)}%"></div>`).join("");
    const tickLabels = ticks.map((t) => `<span class="faab-axis-tick" style="left:${xPct(t).toFixed(2)}%">${fmt(t * 100, 0)}%</span>`).join("");

    return `
      <div class="faab-dist">
        <div class="faab-chart-track">
          <div class="faab-chart-inset">
            ${gridlines}
            <div class="faab-iqr-band" style="left:${xPct(p25).toFixed(2)}%; width:${Math.max(0.5, xPct(p75) - xPct(p25)).toFixed(2)}%"></div>
            ${dots}
            ${selfMineMarkers}
            <div class="faab-median-line" style="left:${xPct(med).toFixed(2)}%"></div>
            <span class="faab-median-tag" style="left:${xPct(med).toFixed(2)}%">med ${fmt(med * 100, 1)}%</span>
            <div class="faab-mean-tick" style="left:${xPct(avg).toFixed(2)}%"></div>
            <span class="faab-mean-tag" style="left:${xPct(avg).toFixed(2)}%">avg ${fmt(avg * 100, 1)}%</span>
          </div>
        </div>
        <div class="faab-axis-ticks"><div class="faab-chart-inset">${tickLabels}</div></div>
        <p class="muted small faab-dist-caption">${raw.length} winning bid${raw.length === 1 ? "" : "s"} across every league</p>
      </div>
    `;
  }

  const priceComps = (est.comps || [])
    .map((c, i) => {
      const hasDist = (c.bid_distribution || []).length > 1;
      const rowId = `price-comp-${i}`;
      const mainRow = `<tr class="${hasDist ? "clickable-row" : ""}" data-price-comp-row="${rowId}">
        <td class="num">${c.weight_capped !== undefined ? fmt(c.weight_capped * 100, 1) + "%" : "–"} / ${fmt(c.weight * 100, 1)}%</td>
        <td>${escapeHtml(c.name)}<div class="muted small">${c.season} wk${c.week}</div>${oLeagueTag(c.o_league_detail)}</td>
        <td>${fmt(c.pct_of_remaining_budget * 100, 1)}%</td>
        <td>${recentCellHtml(c.prior_week_actual_points, c.trailing_2_3_avg_points, c.season_avg_points)}</td>
        <td>${usageCellHtml(inputs.position, c.snap_pct_prior_week, c.carry_share_prior_week, c.target_share_prior_week)}</td>
        <td>${rankCellHtml(c.weekly_rank, c.ros_rank)}</td>
        <td>${flagsCellHtml(c.own_injury_flag, c.teammate_position_injury_flag)}</td>
      </tr>`;
      const detailRow = hasDist
        ? `<tr class="price-comp-detail" data-price-comp-detail="${rowId}" hidden><td colspan="7">${priceCompDistributionHtml(c)}</td></tr>`
        : "";
      return mainRow + detailRow;
    })
    .join("");

  const interestComps = (est.interest_comps || [])
    .map(
      (c) => `<tr>
        <td class="num">${fmt(c.weight * 100, 1)}%</td>
        <td>${escapeHtml(c.name)}<div class="muted small">${c.season} wk${c.week}</div>${oLeagueTag(c.o_league_detail)}</td>
        <td>${bidRateCellHtml(c.leagues_with_bid, c.leagues_eligible)}</td>
        <td>${recentCellHtml(c.prior_week_actual_points, c.trailing_2_3_avg_points, c.season_avg_points)}</td>
        <td>${usageCellHtml(inputs.position, c.snap_pct_prior_week, c.carry_share_prior_week, c.target_share_prior_week)}</td>
        <td>${rankCellHtml(c.weekly_rank, c.ros_rank)}</td>
        <td>${flagsCellHtml(c.own_injury_flag, c.teammate_position_injury_flag)}</td>
      </tr>`
    )
    .join("");

  // The K price comps (comp["comps"]) are real WINNING bids only - their
  // spread is the "if this goes to auction" distribution, not the blended
  // headline number (which also folds in P(anyone bids) - see
  // bid_probability/conditional_price below). Two markers now, not one -
  // comp_based_mean and comp_based_median (see engine/faab_estimate.py's
  // comp_based_estimate) are two independent reads off the SAME k comps,
  // both real candidates for "price if contested," so both get plotted
  // rather than picking one to show.
  // Real bids from other pooled leagues on THIS exact player THIS exact
  // week (engine/faab_estimate.py's FaabModel.same_week_signal) - defined
  // here (not down by sameWeekCard below) so distHtml's own confidence
  // slider can show it alongside the k-NN read, per player request.
  const sameWeek = est.same_week;
  const dist = est.distribution;
  const condPriceMean = (est.conditional_price || {}).comp_based_mean;
  const condPriceMedian = (est.conditional_price || {}).comp_based_median;
  const samples = est.price_confidence_samples || [];
  // Same dot-plot-family visual language as priceCompDistributionHtml
  // (median line + mean triangle, tick-labeled axis), but a HISTOGRAM here
  // rather than dots - this pools every real winning price behind every
  // price comp (via price_confidence_samples, not just the K comps dist
  // above summarizes), often 20-50+ real prices, exactly the regime where a
  // real bar shape earns its keep instead of individual dots. Binned by
  // WEIGHT, not raw count - each sample's own k-NN weight, split across the
  // real per-league wins it expands into (see price_confidence_samples'
  // docstring) - so a heavily-contested real auction doesn't count for less
  // just because more leagues happened to lose it. Bin width is a "nice"
  // round number (niceStep), not just an n-scaled count, so the bars land
  // on numbers a reader would pick by hand. condPriceMean/condPriceMedian
  // (not a value recomputed from the samples here) drive the median/mean
  // markers - the exact same two numbers the Comp-based method card above
  // already shows, so this chart never contradicts them.
  // Same-week bids, equal-weighted (no k-NN distance concept applies to a
  // literal same-player-same-week observation - see same_week_signal's own
  // docstring) - reused here so the confidence slider below can show "what
  // would this confidence level have cost, going by other leagues' real
  // bids on this exact player this week" alongside the k-NN read, per
  // player request. weightedPercentileJs already accepts [value, weight]
  // pairs, so weight=1 per real bid is a direct, no-new-code reuse.
  const sameWeekSamples = (sameWeek?.bid_distribution || []).map((d) => [d.value, 1]);
  const distHtml = dist
    ? (() => {
        const sampleValues = samples.map(([v]) => v);
        const sameWeekValues = sameWeekSamples.map(([v]) => v);
        const axisMax = niceAxisMax(Math.max(dist.max, condPriceMean || 0, condPriceMedian || 0, ...sampleValues, ...sameWeekValues, 0.001) * 1.15, 5);
        const xPct = (v) => Math.max(0, Math.min(100, (v / axisMax) * 100));
        const defaultConfidence = 80;
        const defaultBid = weightedPercentileJs(samples, defaultConfidence);
        const sameWeekDefaultBid = sameWeekSamples.length ? weightedPercentileJs(sameWeekSamples, defaultConfidence) : null;

        const ticks = niceTicksJs(axisMax, 5);
        const gridlines = ticks.map((t) => `<div class="faab-gridline" style="left:${xPct(t).toFixed(2)}%"></div>`).join("");
        const tickLabels = ticks.map((t) => `<span class="faab-axis-tick" style="left:${xPct(t).toFixed(2)}%">${fmt(t * 100, 0)}%</span>`).join("");

        let bars = "";
        if (samples.length) {
          const binWidth = niceStep(axisMax / 10);
          const binCount = Math.max(1, Math.round(axisMax / binWidth));
          const binWeights = new Array(binCount).fill(0);
          samples.forEach(([v, w]) => {
            binWeights[Math.min(binCount - 1, Math.floor(v / binWidth))] += w;
          });
          const maxBinWeight = Math.max(...binWeights, 1e-9);
          bars = binWeights
            .map((w, i) => {
              const lo = fmt(i * binWidth * 100, 0), hi = fmt((i + 1) * binWidth * 100, 0);
              return `<div class="faab-hist-bar" style="height:${((w / maxBinWeight) * 100).toFixed(1)}%" title="${lo}%–${hi}%"></div>`;
            })
            .join("");
        }

        return `
          <div class="faab-dist">
            <div class="faab-chart-track" data-axis-max="${axisMax}">
              <div class="faab-chart-inset">
                ${gridlines}
                <div class="faab-hist-bars">${bars}</div>
                <div class="faab-median-line" style="left:${xPct(condPriceMedian).toFixed(2)}%"></div>
                <span class="faab-median-tag" style="left:${xPct(condPriceMedian).toFixed(2)}%">med ${fmt(condPriceMedian * 100, 1)}%</span>
                <div class="faab-mean-tick" style="left:${xPct(condPriceMean).toFixed(2)}%"></div>
                <span class="faab-mean-tag" style="left:${xPct(condPriceMean).toFixed(2)}%">avg ${fmt(condPriceMean * 100, 1)}%</span>
                ${samples.length ? `<div class="faab-confidence-marker" data-confidence-marker style="left:${xPct(defaultBid).toFixed(2)}%;"></div>` : ""}
                ${sameWeekDefaultBid !== null ? `<div class="faab-same-week-marker" data-same-week-confidence-marker style="left:${xPct(sameWeekDefaultBid).toFixed(2)}%;"></div>` : ""}
              </div>
            </div>
            <div class="faab-axis-ticks"><div class="faab-chart-inset">${tickLabels}</div></div>
          </div>
          ${samples.length
            ? `
            <div class="faab-confidence">
              <div class="faab-confidence-row">
                <span>Bid for <b data-confidence-pct>${defaultConfidence}%</b> confidence</span>
                <span class="faab-confidence-bid" data-confidence-bid>${fmt(defaultBid * 100, 1)}%</span>
              </div>
              ${sameWeekDefaultBid !== null
                ? `
              <div class="faab-confidence-row">
                <span>Same confidence, this week elsewhere</span>
                <span class="faab-confidence-bid faab-confidence-bid-same-week" data-same-week-confidence-bid>${fmt(sameWeekDefaultBid * 100, 1)}%</span>
              </div>
              `
                : ""}
              <input type="range" min="50" max="99" value="${defaultConfidence}" class="faab-confidence-slider" data-confidence-slider>
              <p class="muted small">Bid that would have won this share of comparable historical auctions. Pooled from ${samples.length} winning prices behind the comps below.${
                sameWeekDefaultBid !== null ? ` The green marker/row is the same read against ${sameWeekSamples.length} real bid${sameWeekSamples.length === 1 ? "" : "s"} on THIS player, other leagues, this week - not backtested.` : ""
              }</p>
            </div>
          `
            : ""}
        `;
      })()
    : "";

  // Two-stage model (see engine/faab_estimate.py module docstring), but
  // deliberately NOT shown as one blended P(bid) x price number - that
  // product is an ex-ante expected cost, not "what to bid if you want him".
  // The headline per method is conditional_price (price IF contested);
  // bid_probability is separate context, shown right alongside it per
  // method's own card rather than multiplied in.
  const bidProb = est.bid_probability || {};
  const condPriceByMethod = est.conditional_price || {};
  const pctOrDash = (v, digits) => (v !== undefined && v !== null ? fmt(v * 100, digits) + "%" : "–");
  // Comp-based median is the primary estimate (see the conversation this
  // was built from) - mean shown alongside it, not as a separate card, so
  // MED reads first. comp_based_mean and comp_based_median always share the
  // SAME bid_probability value (capping/median only ever apply to the PRICE
  // side - see comp_based_estimate) - one Interest number for the merged
  // card, not two identical ones. Merging these two into one card (instead
  // of the three separate cards this used to be) is also what makes room
  // for Regression to sit on the same line as Comp-based on a narrow/mobile
  // viewport.
  const compBasedCard = `
    <div class="faab-method-card">
      <div class="faab-method-label">Comp-based</div>
      <div class="faab-method-values faab-method-values-triple">
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(condPriceByMethod.comp_based_median, 1)}</span><span class="faab-method-sub">MED</span></div>
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(condPriceByMethod.comp_based_mean, 1)}</span><span class="faab-method-sub">AVG</span></div>
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(bidProb.comp_based_median, 0)}</span><span class="faab-method-sub">INT</span></div>
      </div>
    </div>
  `;
  const regressionCard = `
    <div class="faab-method-card">
      <div class="faab-method-label">Regression</div>
      <div class="faab-method-values">
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(condPriceByMethod.regression, 1)}</span><span class="faab-method-sub">FAAB</span></div>
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(bidProb.regression, 0)}</span><span class="faab-method-sub">INT</span></div>
      </div>
    </div>
  `;
  // Real bids from other pooled leagues on THIS exact player THIS exact
  // week (engine/faab_estimate.py's FaabModel.same_week_signal) - not a
  // k-NN read on similar-but-different situations, an observed fact about
  // this one. Deliberately never blended into Comp-based/Regression above
  // (see that function's own docstring) - a separate card, absent entirely
  // (not a zero) whenever nothing pulled for this player this week, which
  // is most players most weeks. INT here is a plain COUNT of leagues with
  // any real activity (won/outbid/other_failure), not a %-style rate like
  // the other two cards' INT - there's no "eligible" denominator without a
  // roster pull (see same_week_signal's own docstring), so it isn't
  // dressed up as one. Not backtested (can't be - see the conversation
  // this was built from), so labelled as such rather than implying the
  // same evidentiary weight as the two methods above. (sameWeek itself is
  // defined up near dist/samples above, not here.)
  const sameWeekCard = sameWeek
    ? `
    <div class="faab-method-card">
      <div class="faab-method-label" title="Real bids from other pooled leagues on this exact player this week - not backtested, shown as-is.">This week, elsewhere</div>
      <div class="faab-method-values faab-method-values-triple">
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(sameWeek.conditional_price?.median, 1)}</span><span class="faab-method-sub">MED</span></div>
        <div class="faab-method-value"><span class="faab-method-num">${pctOrDash(sameWeek.conditional_price?.mean, 1)}</span><span class="faab-method-sub">AVG</span></div>
        <div class="faab-method-value"><span class="faab-method-num">${sameWeek.leagues_with_activity}</span><span class="faab-method-sub">LG</span></div>
      </div>
    </div>
  `
    : "";
  const methodCards = compBasedCard + regressionCard + sameWeekCard;

  // The actual query inputs driving every method/comp above, formatted the
  // SAME way a Price/Interest comp row is (see recentCellHtml/rankCellHtml/
  // flagsCellHtml below) so this player reads as directly comparable to
  // the historical comps rather than a separate wall of text - placed
  // right above Price comps, the first table it's feeding.
  const thisPlayerSection = `
    <h3>This player</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Player</th><th>Recent pts</th><th>USAGE %</th><th>Rank</th><th>Flags</th></tr></thead>
        <tbody>
          <tr>
            <td>${escapeHtml(player.name)}<div class="muted small">Week ${inputs.week}</div></td>
            <td>${recentCellHtml(inputs.prior_week_had_stat_row ? inputs.prior_week_actual_points : null, inputs.had_trailing_2_3_avg_points ? inputs.trailing_2_3_avg_points : null, inputs.had_season_avg_points ? inputs.season_avg_points : null)}</td>
            <td>${usageCellHtml(inputs.position, inputs.snap_pct_prior_week, inputs.had_carry_share_prior_week ? inputs.carry_share_prior_week : null, inputs.had_target_share_prior_week ? inputs.target_share_prior_week : null)}</td>
            <td>${rankCellHtml(inputs.had_weekly_rank ? inputs.weekly_rank : null, inputs.had_ros_rank ? inputs.ros_rank : null)}</td>
            <td>${flagsCellHtml(inputs.own_injury_flag, inputs.teammate_position_injury_flag)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  `;

  // Reuses priceCompDistributionHtml unchanged against a synthetic "comp" -
  // {bid_distribution, pct_of_remaining_budget: undefined} is exactly the
  // shape it already expects, and the self/mine tick-marker logic degrades
  // harmlessly to "show nothing" when pct_of_remaining_budget is undefined
  // (there's no single "this landed here" value for a live same-week read
  // the way a historical comp has its own real settled price). Expanded by
  // default (<details open>, not collapsed) - this is the first time we've
  // ever had a real, this-exact-player distribution rather than a
  // similar-situations k-NN neighborhood, worth surfacing prominently.
  const sameWeekDistHtml =
    sameWeek && sameWeek.bid_distribution.length
      ? `
    <details open>
      <summary class="small">This week's real bids, other leagues</summary>
      ${priceCompDistributionHtml({ bid_distribution: sameWeek.bid_distribution, pct_of_remaining_budget: undefined })}
    </details>
  `
      : "";

  return `
    <p class="muted small">% of your league's starting budget</p>
    <div class="faab-method-grid">${methodCards}</div>
    ${distHtml}

    ${teamInterestSection(est.team_interest, data)}

    ${thisPlayerSection}

    ${sameWeekDistHtml}

    <h3>Price comps</h3>
    <div class="table-wrap"><table><thead><tr><th title="This comp's share of the total weight behind the price estimates above - MED first, then AVG. MED's weight is capped so no single comp can carry more than 5x any other's; AVG's isn't. Comps are already listed highest-weight-first (by AVG).">WT (MED/AVG)</th><th>Player</th><th>% of budget</th><th>Recent pts</th><th>USAGE %</th><th>Rank</th><th>Flags</th></tr></thead><tbody>${priceComps || `<tr><td colspan="7" class="muted small">No comparable winning bids found.</td></tr>`}</tbody></table></div>

    <h3>Interest comps</h3>
    <div class="table-wrap"><table><thead><tr><th title="This comp's share of the total weight behind the weighted-average bid_probability above - every comp's weight sums to 100%. Derived from 1/(distance+0.05), so a closer comp counts for more. Comps are already listed highest-weight-first.">Weight</th><th>Player</th><th title="Of the leagues we have real data for this exact player/week (a real bid, or roster data confirming he was a genuine free agent there), how many actually saw a bid - not weighted, one binary count per league.">Leagues bid</th><th>Recent pts</th><th>USAGE %</th><th>Rank</th><th>Flags</th></tr></thead><tbody>${interestComps || `<tr><td colspan="7" class="muted small">No comparable situations found.</td></tr>`}</tbody></table></div>
  `;
}

// Tab is labeled "Game Log" (see playerModalContentHtml) - this used to be
// two separate tabs (Weekly Projections, Week-to-Week NMD/Value), merged
// into one per the conversation this was built from: a player's actual
// stats, remaining schedule, and roster-value breakdown are all "what
// happened/will happen with this guy," not three unrelated views. No
// redundant "Game log" heading here (the tab title already says that) - each
// remaining section still gets its own heading.
function overviewTabHtml(player, data) {
  const hasGameLog = (player.weekly || []).some((w) => w.actual);
  const nmdHtml = nmdDetailSection(player, data);
  return `
    ${hasGameLog ? gameLogTable(player, data) : ""}
    <h3>${hasGameLog ? "Remaining schedule" : "Weekly projections"}</h3>
    <div class="table-wrap">${projectionTable(player, data.meta.current_week)}</div>
    ${nmdHtml}
  `;
}

// Names whichever free agent set that week's replacement bar (see
// engine/team_strength.py's position_value_by_player/fa_pool_value) -
// ALWAYS a free agent under this calc, never a bench teammate (unlike the
// old lineup-delta engine this replaced, which could name either) - so
// there's no "is this a free agent" distinction left to highlight, just
// the name and his own points that week.
function nmdReplacementCellHtml(w, data, currentWeek) {
  if (!w.replacement_id) return `<span class="muted small">&ndash;</span>`;
  const replacement = data.playersById.get(w.replacement_id);
  if (!replacement) return `<span class="muted small">${escapeHtml(w.replacement_id)}</span>`;
  return `${escapeHtml(replacement.name)} <span class="muted small">(${fmt(weeklyProjection(replacement, w.week, currentWeek), 1)})</span>`;
}

// NMD week-by-week (see engine/team_strength.py's position_value_by_player/
// fa_pool_value) - points above replacement, broken into Starting/Depth per
// week (the same split the stat-grid tile's single "Value" number above
// blends together), naming which free agent set each week's bar. Same
// underlying shape for a rostered player (Starting nonzero the weeks he
// started, Depth nonzero the weeks he sat, never both the same week) and a
// free agent (Starting always 0 - he's nobody's starter by definition,
// Depth is his own leave-one-out value against the rest of the FA pool at
// his position) - one renderer, two data sources.
function nmdDetailSection(player, data) {
  const currentWeek = data.meta.current_week;
  if (player.fantasy_team_id !== null) {
    const team = data.teamsById.get(player.fantasy_team_id);
    const entry = (team?.depth?.[player.position] || []).find((d) => d.id === player.id);
    if (!entry) return "";
    const rows = entry.weekly
      .map(
        (w) => `<tr>
          <td>Wk ${w.week}</td>
          <td>${fmt(w.starting_value, 1)}</td>
          <td>${fmt(w.depth_value, 1)}</td>
          <td>${nmdReplacementCellHtml(w, data, currentWeek)}</td>
        </tr>`
      )
      .join("");
    return `
      <h3>Value week-by-week</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Week</th><th>Starting</th><th>Depth</th><th>Replace by</th></tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr class="totals-row"><td>Total</td><td>${fmt(entry.starting_value, 1)}</td><td>${fmt(entry.depth_value, 1)}</td><td><strong>${fmt(entry.value_delta, 1)}</strong></td></tr></tfoot>
        </table>
      </div>
    `;
  }

  const detail = (data.faValuesDetail || {})[player.id];
  if (!detail) return "";
  const rows = detail.weekly
    .map((w) => `<tr><td>Wk ${w.week}</td><td>${fmt(w.depth_value, 1)}</td><td>${nmdReplacementCellHtml(w, data, currentWeek)}</td></tr>`)
    .join("");
  const totalDepth = detail.weekly.reduce((acc, w) => acc + w.depth_value, 0);
  return `
    <h3>Value week-by-week</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Week</th><th>Depth</th><th>Replace by</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr class="totals-row"><td>Total</td><td>${fmt(totalDepth, 1)}</td><td><strong>${fmt(player.value_delta ?? 0, 1)}</strong></td></tr></tfoot>
      </table>
    </div>
  `;
}

// A single "best remaining schedule" rank pill (1-32, 1 = best), colored the
// same way every other matchup rank on the site is (see colors.js's
// ratioForRank/colorForRatio - the same 0-1 spread opponentCellHtml uses for
// a single week's matchup, just applied to this player's team's whole
// remaining-schedule average instead of one week). avgIndex (the raw
// averaged opponent-adjustment factor behind the rank) rides along as the
// hover tooltip, same pattern as opponentCellHtml's own rank tooltip.
function scheduleRankPillHtml(rank, avgIndex, timeframeLabel) {
  if (rank === null || rank === undefined) return `<span class="muted">&ndash;</span>`;
  const color = colorForRatio(ratioForRank(rank));
  const title = avgIndex !== null && avgIndex !== undefined
    ? `title="${timeframeLabel} schedule rank ${rank} of 32 (1 = best) - opponents allow ${(avgIndex * 100).toFixed(0)}% of an average defense's rate, on average, over this span"`
    : `title="${timeframeLabel} schedule rank ${rank} of 32 (1 = best)"`;
  return `<span class="pill" style="background:${color}" ${title}>${rank}</span>`;
}

// The single-player modal's full inner HTML: header/stat-grid, then always
// a Game Log tab (actual stats + remaining schedule + roster-value
// breakdown, all folded into one tab - see overviewTabHtml) plus a second
// (FAAB Lab) only for players on ESPN "WAIVERS" status this week (see
// engine/pipeline.py's _compute_faab_estimates) - everyone else just
// doesn't get a second tab, rather than an empty one. Factored out of
// openPlayerModal so openComparePlayerModal can render the exact same
// content twice, side by side, rather than reimplementing it.
function playerModalContentHtml(player, data, { showCompareTrigger = true } = {}) {
  const color = POSITION_COLOR[player.position] || "#888";
  const team = player.fantasy_team_id !== null ? data.teamsById.get(player.fantasy_team_id) : null;
  const faabHtml = faabEstimateSection(player, data);

  const header = `
    <div class="player-modal-header">
      ${playerPhotoHtml(player, "player-photo-lg")}
      <div>
        <h2><span class="pos-tag" style="background:${color}">${player.position}</span> ${escapeHtml(player.name)} <span class="muted small">${escapeHtml(player.nfl_team || "")}</span></h2>
        <p class="muted small">${team ? escapeHtml(teamLabel(team)) : "Free agent"} · ROS rank ${player.ros_pos_rank ?? "–"} · Bye ${player.bye ?? "–"}</p>
      </div>
      ${showCompareTrigger ? `<button class="compare-btn" type="button" data-compare-trigger>+ Compare</button>` : ""}
    </div>
    ${showCompareTrigger ? compareSearchHtml() : ""}
    <div class="player-stat-grid">
      <div class="stat-tile">
        <div class="stat-label">Reg / Playoff sched</div>
        <div class="stat-value">${scheduleRankPillHtml(player.reg_schedule_rank, player.reg_schedule_index, "Regular season")} / ${scheduleRankPillHtml(player.playoff_schedule_rank, player.playoff_schedule_index, "Fantasy playoff")}</div>
      </div>
      <div class="stat-tile"><div class="stat-label">Value</div><div class="stat-value">${player.value_delta === undefined || player.value_delta === null ? "–" : fmt(player.value_delta, 1)}</div></div>
    </div>
  `;

  const tabs = [
    { key: "projections", label: "Game Log", html: overviewTabHtml(player, data) },
    ...(faabHtml ? [{ key: "faab", label: "FAAB Lab", html: faabHtml }] : []),
  ];

  return `
    ${header}
    <div class="modal-tabs">
      ${tabs.map((t, i) => `<button class="modal-tab-btn${i === 0 ? " active" : ""}" data-modal-tab="${t.key}">${t.label}</button>`).join("")}
    </div>
    ${tabs.map((t, i) => `<div class="modal-tabpanel${i === 0 ? " active" : ""}" data-modal-panel="${t.key}">${t.html}</div>`).join("")}
  `;
}

// Hidden until "+ Compare" is clicked (see wireCompareTrigger) - a plain
// text search over data.players rather than a second picker UI, so
// comparing two players never needs a checkbox anywhere in the app: open
// player A's modal, click Compare, type player B's name, pick them from the
// results.
function compareSearchHtml() {
  return `
    <div class="compare-search" data-compare-search hidden>
      <input type="search" data-compare-search-input placeholder="Search for a player to compare..." autocomplete="off" />
      <div class="compare-search-results" data-compare-search-results></div>
    </div>
  `;
}

function wireCompareTrigger(scopeEl, player, data) {
  const trigger = scopeEl.querySelector("[data-compare-trigger]");
  const panel = scopeEl.querySelector("[data-compare-search]");
  if (!trigger || !panel) return;
  const input = panel.querySelector("[data-compare-search-input]");
  const results = panel.querySelector("[data-compare-search-results]");

  trigger.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (!panel.hidden) input.focus();
  });

  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) {
      results.innerHTML = "";
      return;
    }
    const matches = data.players.filter((p) => p.id !== player.id && p.name.toLowerCase().includes(q)).slice(0, 8);
    results.innerHTML = matches.length
      ? matches
          .map((p) => {
            const c = POSITION_COLOR[p.position] || "#888";
            return `<div class="compare-search-result" data-compare-pick="${p.id}"><span class="pos-tag" style="background:${c}">${p.position}</span> ${escapeHtml(p.name)} <span class="muted small">${escapeHtml(p.nfl_team || "")}</span></div>`;
          })
          .join("")
      : `<p class="muted small">No players found.</p>`;
    results.querySelectorAll("[data-compare-pick]").forEach((row) => {
      row.addEventListener("click", () => {
        const chosen = data.playersById.get(row.dataset.comparePick);
        if (chosen) openComparePlayerModal(player, chosen, data);
      });
    });
  });
}

// Scoped to scopeEl (the whole modal for a single player, or one
// .compare-col for the compare view) rather than the whole document, so two
// independent Overview/FAAB tab groups on screen at once (compare mode)
// don't cross-wire each other's clicks.
function wirePlayerModalTabs(scopeEl) {
  scopeEl.querySelectorAll(".modal-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      scopeEl.querySelectorAll(".modal-tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
      scopeEl.querySelectorAll(".modal-tabpanel").forEach((p) => p.classList.toggle("active", p.dataset.modalPanel === btn.dataset.modalTab));
    });
  });
}

// Scoped the same way wirePlayerModalTabs is (compare mode has two
// independent sliders on screen at once). Looks est back up from
// data.faabEstimates rather than threading it through return values, the
// same lookup faabEstimateSection itself does.
function wireFaabConfidenceSlider(scopeEl, player, data) {
  const slider = scopeEl.querySelector("[data-confidence-slider]");
  if (!slider) return;
  const est = (data.faabEstimates || {})[player.id];
  const samples = est?.price_confidence_samples || [];
  const dist = est?.distribution;
  if (!dist) return;
  // Same equal-weighted reuse of weightedPercentileJs as faabEstimateSection's
  // own distHtml (see that IIFE's sameWeekSamples) - kept in sync by reading
  // straight off est.same_week here too, rather than passed through, same
  // pattern this function already uses for samples/dist.
  const sameWeekSamples = (est?.same_week?.bid_distribution || []).map((d) => [d.value, 1]);
  // Same 0-to-axisMax domain the histogram itself was drawn against (see
  // faabEstimateSection's distHtml) - stashed on the track element rather
  // than recomputed here, so the slider's marker always matches whatever
  // axis the bars actually rendered at.
  const track = scopeEl.querySelector(".faab-chart-track[data-axis-max]");
  const axisMax = track ? Number(track.dataset.axisMax) : Math.max(0.001, dist.max);
  const pctPos = (v) => Math.max(0, Math.min(100, (v / axisMax) * 100));
  slider.addEventListener("input", () => {
    const confidence = Number(slider.value);
    const bid = weightedPercentileJs(samples, confidence);
    scopeEl.querySelectorAll("[data-confidence-pct]").forEach((el) => (el.textContent = `${confidence}%`));
    scopeEl.querySelectorAll("[data-confidence-bid]").forEach((el) => (el.textContent = `${fmt(bid * 100, 1)}%`));
    const marker = scopeEl.querySelector("[data-confidence-marker]");
    if (marker) marker.style.left = `${pctPos(bid)}%`;
    if (sameWeekSamples.length) {
      const sameWeekBid = weightedPercentileJs(sameWeekSamples, confidence);
      scopeEl.querySelectorAll("[data-same-week-confidence-bid]").forEach((el) => (el.textContent = `${fmt(sameWeekBid * 100, 1)}%`));
      const sameWeekMarker = scopeEl.querySelector("[data-same-week-confidence-marker]");
      if (sameWeekMarker) sameWeekMarker.style.left = `${pctPos(sameWeekBid)}%`;
    }
  });
}

export function openPlayerModal(player, data) {
  openModal(playerModalContentHtml(player, data));
  const scope = document.querySelector(".modal-content");
  wirePlayerModalTabs(scope);
  wireFaabConfidenceSlider(scope, player, data);
  wireGameLogRows(scope);
  wirePriceCompRows(scope);
  wireCompareTrigger(scope, player, data);
}

// Side-by-side on a wide screen (see .compare-grid/.modal-overlay-wide in
// styles.css); on a narrow one the same markup collapses to one column at a
// time behind the .compare-side-btn toggle instead - same content either
// way, CSS alone decides which layout renders it.
export function openComparePlayerModal(playerA, playerB, data) {
  const html = `
    <div class="compare-toggle">
      <button class="compare-side-btn active" data-compare-side="a">${escapeHtml(playerA.name)}</button>
      <button class="compare-side-btn" data-compare-side="b">${escapeHtml(playerB.name)}</button>
    </div>
    <div class="compare-grid">
      <div class="compare-col active" data-compare-col="a">${playerModalContentHtml(playerA, data, { showCompareTrigger: false })}</div>
      <div class="compare-col" data-compare-col="b">${playerModalContentHtml(playerB, data, { showCompareTrigger: false })}</div>
    </div>
  `;
  openModal(html, { wide: true });

  const modalContent = document.querySelector(".modal-content");
  modalContent.querySelectorAll(".compare-col").forEach((col) => wirePlayerModalTabs(col));
  wireFaabConfidenceSlider(modalContent.querySelector('[data-compare-col="a"]'), playerA, data);
  wireFaabConfidenceSlider(modalContent.querySelector('[data-compare-col="b"]'), playerB, data);
  modalContent.querySelectorAll(".compare-col").forEach((col) => wireGameLogRows(col));
  modalContent.querySelectorAll(".compare-col").forEach((col) => wirePriceCompRows(col));
  modalContent.querySelectorAll(".compare-side-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      modalContent.querySelectorAll(".compare-side-btn").forEach((b) => b.classList.toggle("active", b === btn));
      modalContent.querySelectorAll(".compare-col").forEach((c) => c.classList.toggle("active", c.dataset.compareCol === btn.dataset.compareSide));
    });
  });
}
