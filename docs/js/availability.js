// Shared availability helpers: waiver status (players.json's on_waivers /
// waiver_clears, from ESPN's waiverProcessDate) and IR eligibility.

// Injury statuses that make a player IR-eligible: ruled Out, or on IR/PUP
// (ESPN reports PUP as INJURY_RESERVE).
const IR_ELIGIBLE_STATUSES = new Set(["OUT", "INJURY_RESERVE"]);

export function isIrEligible(p) {
  return IR_ELIGIBLE_STATUSES.has(p.injury_status);
}

// "On Waivers (Thu)" - the weekday the player clears, in Eastern time (ESPN
// processes in the small hours, so the Eastern date is the league's day).
export function waiverLabel(p) {
  if (!p.on_waivers) return null;
  if (!p.waiver_clears) return "On Waivers";
  const day = new Date(p.waiver_clears).toLocaleDateString("en-US", { weekday: "short", timeZone: "America/New_York" });
  return `On Waivers (${day})`;
}
