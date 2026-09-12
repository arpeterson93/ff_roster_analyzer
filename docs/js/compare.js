// Shared "add to compare" selection state - a module-level array, not DOM
// state, so it survives Start/Sit <-> Rankings tab switches even though
// app.js fully re-renders each tabpanel's innerHTML on every switch (see
// app.js's renderActiveView). Any view that wants a compare checkbox just
// renders compareCheckboxHtml(p) inline and calls wireCompareCheckboxes()
// once after building its rows - no per-view state to manage.
import { escapeHtml } from "./state.js";
import { openComparePlayerModal } from "./playermodal.js";

const selected = []; // [{player, data}, ...], max 2, in the order added

let trayEl = null;
function ensureTray() {
  if (trayEl) return trayEl;
  trayEl = document.createElement("div");
  trayEl.className = "compare-tray";
  trayEl.hidden = true;
  document.body.appendChild(trayEl);
  return trayEl;
}

function syncCheckboxesInDom() {
  // Re-renders happen independently of this module (app.js swaps whole
  // tabpanels), so a checkbox for a player already selected on some OTHER
  // panel needs to come back checked, and one just evicted (see below)
  // needs to come back unchecked, the next time either happens to be drawn.
  document.querySelectorAll(".compare-checkbox").forEach((cb) => {
    cb.checked = selected.some((s) => s.player.id === cb.dataset.compareId);
  });
}

function renderTray() {
  const tray = ensureTray();
  if (selected.length === 0) {
    tray.hidden = true;
    return;
  }
  tray.hidden = false;
  const names = selected.map((s) => escapeHtml(s.player.name)).join(" vs ");
  const remaining = 2 - selected.length;
  tray.innerHTML = `
    <span class="compare-tray-label">${names}</span>
    <button class="compare-tray-go" ${remaining > 0 ? "disabled" : ""}>${remaining > 0 ? `Pick ${remaining} more` : "Compare"}</button>
    <button class="compare-tray-clear" title="Clear selection">&times;</button>
  `;
  tray.querySelector(".compare-tray-clear").addEventListener("click", clearSelection);
  if (remaining === 0) {
    tray.querySelector(".compare-tray-go").addEventListener("click", () => {
      const [a, b] = selected;
      openComparePlayerModal(a.player, b.player, a.data);
      clearSelection();
    });
  }
}

function clearSelection() {
  selected.length = 0;
  renderTray();
  syncCheckboxesInDom();
}

export function compareCheckboxHtml(player) {
  const checked = selected.some((s) => s.player.id === player.id);
  return `<input type="checkbox" class="compare-checkbox" data-compare-id="${player.id}" ${checked ? "checked" : ""} title="Add to compare" onclick="event.stopPropagation()">`;
}

export function wireCompareCheckboxes(container, data) {
  container.querySelectorAll(".compare-checkbox").forEach((cb) => {
    cb.addEventListener("change", (e) => {
      const player = data.playersById.get(e.target.dataset.compareId);
      if (!player) return;
      const idx = selected.findIndex((s) => s.player.id === player.id);
      if (idx !== -1) {
        selected.splice(idx, 1);
      } else if (selected.length >= 2) {
        // Already comparing two - refuse a third rather than silently
        // evicting one, which would be a confusing "why did my pick
        // disappear" surprise.
        e.target.checked = false;
        return;
      } else {
        selected.push({ player, data });
      }
      renderTray();
      syncCheckboxesInDom();
    });
  });
}
