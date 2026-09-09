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
    (f) => `<div class="bar-row"><div class="bar-label" style="width:220px">${f.label}</div>${fieldControl(f, settings[f.key])}</div><p class="muted small" style="margin:-4px 0 10px 220px">${f.help}</p>`
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
