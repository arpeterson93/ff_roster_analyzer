// Grouped stat-column spec shared by the points-against modal and the
// player modal's game log, so both render identical columns for the same
// position. Mirrors engine/points_against.py's stat_columns() exactly -
// block CONTENTS are shared across QB/RB/WR/TE, only the lead-category
// ORDER differs per position.
// The passing block's lead column is a combined "C/A" (completions/
// attempts) display, not two separate columns - see statCellsHtml's
// Array-key handling below.
const PASSING_BLOCK = ["Passing", [[["completions", "attempts"], "C/A"], ["passing_yards", "Yds"], ["passing_tds", "TD"], ["passing_interceptions", "Int"]]];
const RUSHING_BLOCK = ["Rushing", [["carries", "Att"], ["rushing_yards", "Yds"], ["rushing_tds", "TD"]]];
const RECEIVING_BLOCK = ["Receiving", [["receptions", "Rec"], ["receiving_yards", "Yds"], ["receiving_tds", "TD"], ["targets", "Tgt"]]];
// One consolidated Misc block (2-pt conversions, fumbles lost, special-
// teams TD) rather than three separate one-column groups - simpler to scan
// as one small "everything else" block.
const MISC_BLOCK = ["Misc", [["two_pt_conversions", "2PT"], ["fumbles_lost_total", "Fuml"], ["special_teams_tds", "TD"]]];

const OFFENSE_BLOCKS = {
  QB: [PASSING_BLOCK, RUSHING_BLOCK, RECEIVING_BLOCK, MISC_BLOCK],
  RB: [RUSHING_BLOCK, RECEIVING_BLOCK, PASSING_BLOCK, MISC_BLOCK],
  WR: [RECEIVING_BLOCK, RUSHING_BLOCK, PASSING_BLOCK, MISC_BLOCK],
};
OFFENSE_BLOCKS.TE = OFFENSE_BLOCKS.WR;

const KICKER_BLOCKS = [
  ["FG Made", [["fg_made_0_19", "0-19"], ["fg_made_20_29", "20-29"], ["fg_made_30_39", "30-39"], ["fg_made_40_49", "40-49"], ["fg_made_50_59", "50-59"], ["fg_made_60_", "60+"]]],
  ["PAT", [["pat_made", "Made"]]],
];

// `xpr` (a defense returning a blocked/missed PAT for 2) has no nflverse
// stat to source it from - always rendered as "-".
const DST_BLOCKS = [
  [null, [["points_allowed", "PA"]]],
  ["Tackles", [["def_sacks", "Sack"], ["def_safeties", "Safety"]]],
  ["Turnovers", [["def_interceptions", "Int"], ["fumble_recovery_opp", "Fum Rec"]]],
  [null, [["def_tds", "TD"]]],
  ["Misc", [["blocked_kicks", "Blk Kick"]]],
  ["Ret", [["xpr", "XPR"], ["special_teams_tds", "TD"]]],
];

export function blocksForPosition(position) {
  if (position === "K") return KICKER_BLOCKS;
  if (position === "DST") return DST_BLOCKS;
  return OFFENSE_BLOCKS[position] || [];
}

// Rankings' Stats tab shows every position in ONE flat table (not one
// table per position like Game Log/Points-Against), so it needs a single
// FIXED block order rather than blocksForPosition's per-position reordering
// - a RB/WR/TE's Passing block (or a QB's Rushing/Receiving blocks) just
// reads mostly "-", same as any other stat that doesn't apply to that row.
// QB's own order (Passing, Rushing, Receiving, Misc) is the arbitrary but
// reasonable pick, matching the order requested for that tab.
export const STATS_TAB_BLOCKS = OFFENSE_BLOCKS.QB;

// Grouped <thead> markup (two <tr>s: block headers, then column labels) for
// a table whose first N columns are `leadColumns` (e.g. Wk/Opp) and last
// column is FPts.
export function groupedHeaderHtml(position, leadColumns) {
  const blocks = blocksForPosition(position);
  const lead = leadColumns.map(() => "<th></th>").join("");
  const groupRow = blocks
    .map(([group, cols]) => `<th colspan="${cols.length}">${group ? escapeAttr(group) : ""}</th>`)
    .join("");
  const labelRow = blocks.flatMap(([, cols]) => cols.map(([, label]) => `<th>${escapeAttr(label)}</th>`)).join("");
  return {
    top: `<tr class="group-header-row">${lead}${groupRow}<th></th></tr>`,
    bottom: `<tr>${leadColumns.map((l) => `<th>${escapeAttr(l)}</th>`).join("")}${labelRow}<th>FPts</th></tr>`,
    flatColumns: blocks.flatMap(([, cols]) => cols),
  };
}

export function statCellsHtml(stats, flatColumns) {
  return flatColumns
    .map(([key]) => {
      // A two-element array key (e.g. ["completions","attempts"]) renders as
      // a single "12/18"-style ratio cell instead of two separate columns -
      // see PASSING_BLOCK's C/A column above.
      if (Array.isArray(key)) {
        const [k1, k2] = key;
        const v1 = stats?.[k1], v2 = stats?.[k2];
        return `<td>${v1 !== null && v1 !== undefined && v2 !== null && v2 !== undefined ? `${v1}/${v2}` : "-"}</td>`;
      }
      return `<td>${stats && stats[key] !== null && stats[key] !== undefined ? stats[key] : "-"}</td>`;
    })
    .join("");
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
