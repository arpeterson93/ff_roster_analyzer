// Grouped stat-column spec shared by the points-against modal and the
// player modal's game log, so both render identical columns for the same
// position. Mirrors engine/points_against.py's stat_columns() exactly -
// block CONTENTS are shared across QB/RB/WR/TE, only the lead-category
// ORDER differs per position.
const PASSING_BLOCK = ["Passing", [["passing_yards", "Yds"], ["passing_tds", "TD"], ["passing_interceptions", "Int"]]];
const RUSHING_BLOCK = ["Rushing", [["carries", "Att"], ["rushing_yards", "Yds"], ["rushing_tds", "TD"]]];
const RECEIVING_BLOCK = ["Receiving", [["receptions", "Rec"], ["receiving_yards", "Yds"], ["receiving_tds", "TD"], ["targets", "Tgt"]]];
const RET_TD_BLOCK = ["Ret", [["special_teams_tds", "TD"]]];
const MISC_2PT_BLOCK = ["Misc", [["two_pt_conversions", "2PT"]]];
const FUM_BLOCK = ["Fum", [["fumbles_lost_total", "Lost"]]];

const OFFENSE_BLOCKS = {
  QB: [PASSING_BLOCK, RUSHING_BLOCK, RECEIVING_BLOCK, RET_TD_BLOCK, MISC_2PT_BLOCK, FUM_BLOCK],
  RB: [RUSHING_BLOCK, RECEIVING_BLOCK, PASSING_BLOCK, RET_TD_BLOCK, MISC_2PT_BLOCK, FUM_BLOCK],
  WR: [RECEIVING_BLOCK, RUSHING_BLOCK, PASSING_BLOCK, RET_TD_BLOCK, MISC_2PT_BLOCK, FUM_BLOCK],
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
    top: `<tr>${lead}${groupRow}<th></th></tr>`,
    bottom: `<tr>${leadColumns.map((l) => `<th>${escapeAttr(l)}</th>`).join("")}${labelRow}<th>FPts</th></tr>`,
    flatColumns: blocks.flatMap(([, cols]) => cols),
  };
}

export function statCellsHtml(stats, flatColumns) {
  return flatColumns.map(([key]) => `<td>${stats && stats[key] !== null && stats[key] !== undefined ? stats[key] : "-"}</td>`).join("");
}

function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
