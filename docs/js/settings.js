import { escapeHtml, getTheme, setTheme } from "./state.js";
import { SETTINGS_WEBAPP_URL } from "./settingsConfig.js";

const FIELDS = [
  { key: "matchup_dampening", label: "Matchup dampening", type: "percent", help: "How much a matchup's difficulty shifts a player's weekly projection. 0% = ignore matchups entirely, 100% = full swing." },
  { key: "pa_basis", label: "Points-allowed basis", type: "select", options: ["season", "l5", "blend"], help: "Which window of opponent history feeds the matchup index." },
  { key: "pa_l5_weight", label: "Last-5-weeks weight (when basis = blend)", type: "percent", help: "How much of the blended points-allowed basis comes from the last 5 weeks vs. the full season." },
  { key: "division_winners_first", label: "Seed division winners first", type: "bool", help: "If your league has divisions, guarantee division winners the top seeds ahead of wildcards." },
];

function fieldControl(field, value) {
  if (field.type === "percent") {
    const pct = Math.round(Number(value) * 100);
    return `<input type="range" min="0" max="100" value="${pct}" data-key="${field.key}" data-type="percent" /> <span class="value-readout" data-readout="${field.key}">${pct}%</span>`;
  }
  if (field.type === "select") {
    return `<select data-key="${field.key}" data-type="select">${field.options.map((o) => `<option value="${o}" ${o === value ? "selected" : ""}>${o}</option>`).join("")}</select>`;
  }
  if (field.type === "bool") {
    return `<input type="checkbox" data-key="${field.key}" data-type="bool" ${value ? "checked" : ""} />`;
  }
  return "";
}

// Playoff seeding: a shared "division ranking" tiebreak chain (used both to
// pick each division's #1 team and to rank multiple division winners
// against each other for the top seeds) plus one row per playoff seed, each
// with a "fill from the division-winner queue" checkbox and its own 4-deep
// tiebreak chain (only consulted for a seed that isn't filled from that
// queue - see engine/standings.py's compute_current_seeds). Flat
// seed_<n>_tiebreak_<1-4> / seed_<n>_division_priority / division_tiebreak_
// <1-4> keys, same settings-sheet mechanism as every other field above.
const TIEBREAK_CRITERIA = [
  { value: "", label: "(unused)" },
  { value: "wins", label: "Wins" },
  { value: "points_for", label: "Points For" },
  { value: "points_against", label: "Points Against" },
  { value: "head_to_head", label: "Head-to-Head" },
];

function tiebreakSelect(prefix, index, chainValues, editable) {
  const key = `${prefix}_${index}`;
  const current = chainValues[index - 1] || "";
  const usedElsewhere = new Set(chainValues.filter((v, i) => i !== index - 1 && v));
  const options = TIEBREAK_CRITERIA.filter((c) => c.value === "" || c.value === current || !usedElsewhere.has(c.value))
    .map((c) => `<option value="${c.value}" ${c.value === current ? "selected" : ""}>${c.label}</option>`)
    .join("");
  return `<select data-key="${key}" data-type="select" data-group="seeding" ${editable ? "" : "disabled"}>${options}</select>`;
}

function tiebreakChainHtml(prefix, seeding, editable) {
  const chainValues = [1, 2, 3, 4].map((i) => seeding[`${prefix}_${i}`] || "");
  return `<span class="seeding-chain">${[1, 2, 3, 4].map((i) => tiebreakSelect(prefix, i, chainValues, editable)).join("")}</span>`;
}

function renderSeedingSection(seeding, playoffTeamCount, editable) {
  if (!playoffTeamCount) {
    return `<p class="muted small">Seeding needs this league's playoff team count, not available until the next pipeline run.</p>`;
  }
  const divisionRow = `
    <div class="bar-row settings-row">
      <div class="bar-label settings-label">Division ranking</div>
      ${tiebreakChainHtml("division_tiebreak", seeding, editable)}
    </div>
    <p class="muted small settings-help">Picks each division's #1 team, then ranks multiple division winners against each other for the top seeds.</p>`;
  const seedRows = Array.from({ length: playoffTeamCount }, (_, i) => i + 1)
    .map((n) => {
      const divKey = `seed_${n}_division_priority`;
      const checked = String(seeding[divKey] ?? "").toLowerCase() === "true";
      return `
        <div class="bar-row settings-row">
          <div class="bar-label settings-label">Seed ${n}</div>
          <label class="seeding-checkbox"><input type="checkbox" data-key="${divKey}" data-type="bool" data-group="seeding" ${checked ? "checked" : ""} ${editable ? "" : "disabled"} /> Next division winner</label>
          ${tiebreakChainHtml(`seed_${n}_tiebreak`, seeding, editable)}
        </div>`;
    })
    .join("");
  return `${divisionRow}${seedRows}<p class="muted small settings-help">Each seed's tiebreak chain (Wins/PF/PA/Head-to-Head) only applies when "Next division winner" is unchecked, or once the division-winner queue runs dry.</p>`;
}

export function renderSettings(container, data) {
  const settings = data.meta.settings || {};
  const sheetConfigured = !!data.meta.settings_sheet_id;
  const writeConfigured = !!SETTINGS_WEBAPP_URL;

  container.innerHTML = `
    <div class="card">
      <h2>Display</h2>
      <div class="select-row">
        <label>Theme:</label>
        <select id="theme-select">
          <option value="system">Match system</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </div>
    </div>
    <div class="card">
      <h2>League settings</h2>
      ${!sheetConfigured
        ? `<p class="muted small">No settings sheet configured for this league - these values come from <code>config/leagues/*.yml</code> and can only be changed by editing that file. See the README's "Settings sheet" section to make them live-editable here.</p>`
        : !writeConfigured
        ? `<p class="muted small">Settings sheet is configured but this browser doesn't have a write URL set (<code>docs/js/settingsConfig.js</code>). Showing current values read-only.</p>`
        : `<p class="muted small">Changes save to the settings sheet and take effect on the next pipeline run (daily, or trigger it manually from GitHub Actions).</p>`}
      <div id="settings-fields"></div>
    </div>
    <div class="card">
      <h2>Playoff seeding</h2>
      ${!sheetConfigured
        ? `<p class="muted small">No settings sheet configured for this league - seeding falls back to <code>sim.division_winners_first</code> plus Wins/Points For from <code>config/leagues/*.yml</code>. See the README's "Settings sheet" section to make it live-editable here.</p>`
        : !writeConfigured
        ? `<p class="muted small">Settings sheet is configured but this browser doesn't have a write URL set. Showing current values read-only.</p>`
        : `<p class="muted small">Configure how each playoff seed is filled - either the next-best division winner, or the best remaining team by that seed's own Wins/PF/PA/Head-to-Head chain.</p>`}
      <div id="seeding-fields"></div>
      ${writeConfigured && sheetConfigured ? `<button id="settings-save">Save</button> <span id="settings-status" class="muted small"></span>` : ""}
      ${data.meta.settings_overrides_applied && data.meta.settings_overrides_applied.length
        ? `<h3>Applied on last build</h3><ul class="small">${data.meta.settings_overrides_applied.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>`
        : ""}
    </div>
  `;

  const themeSelect = container.querySelector("#theme-select");
  themeSelect.value = getTheme();
  themeSelect.addEventListener("change", (e) => setTheme(e.target.value));

  const fieldsEl = container.querySelector("#settings-fields");
  const pending = {};
  fieldsEl.innerHTML = FIELDS.map(
    (f) => `<div class="bar-row settings-row"><div class="bar-label settings-label">${f.label}</div>${fieldControl(f, settings[f.key])}</div><p class="muted small settings-help">${f.help}</p>`
  ).join("");

  const editable = writeConfigured && sheetConfigured;
  fieldsEl.querySelectorAll("[data-key]").forEach((el) => {
    el.disabled = !editable;
    el.addEventListener("input", () => {
      const key = el.dataset.key;
      let value;
      if (el.dataset.type === "percent") {
        value = (Number(el.value) / 100).toString();
        fieldsEl.querySelector(`[data-readout="${key}"]`).textContent = `${el.value}%`;
      } else if (el.dataset.type === "bool") {
        value = el.checked ? "true" : "false";
      } else {
        value = el.value;
      }
      pending[key] = value;
    });
  });

  // Re-rendered on every change (not just wired once like the FIELDS loop
  // above) so each tiebreak dropdown's options stay in sync as siblings in
  // the same chain get picked - a value already chosen elsewhere in its
  // chain shouldn't be selectable twice (see tiebreakSelect's usedElsewhere).
  const seedingEl = container.querySelector("#seeding-fields");
  function drawSeeding() {
    const seeding = { ...(settings.seeding_raw || {}), ...pending };
    seedingEl.innerHTML = renderSeedingSection(seeding, data.meta.playoff_team_count, editable);
    seedingEl.querySelectorAll("[data-key]").forEach((el) => {
      el.addEventListener("input", () => {
        pending[el.dataset.key] = el.dataset.type === "bool" ? (el.checked ? "true" : "false") : el.value;
        drawSeeding();
      });
    });
  }
  drawSeeding();

  const saveBtn = container.querySelector("#settings-save");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const status = container.querySelector("#settings-status");
      if (!Object.keys(pending).length) {
        status.textContent = "No changes to save.";
        return;
      }
      status.textContent = "Saving...";
      try {
        const updates = Object.entries(pending).map(([key, value]) => ({ league_slug: data.meta.slug || "", key, value }));
        // Apps Script Web Apps don't handle a CORS preflight (OPTIONS) request,
        // so this must avoid triggering one: text/plain keeps it a "simple
        // request" (the .gs handler still JSON.parses the body itself).
        await fetch(SETTINGS_WEBAPP_URL, { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify({ updates }) });
        status.textContent = "Saved - will take effect on the next pipeline run.";
      } catch (err) {
        status.textContent = `Failed to save: ${err.message}`;
      }
    });
  }
}
