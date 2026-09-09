// Generic modal shell shared by the player-detail modal and the
// points-against modal - one overlay element, reused for whichever content
// is currently open.

let modalEl = null;

function ensureModal() {
  if (modalEl) return modalEl;
  modalEl = document.createElement("div");
  modalEl.className = "modal-overlay";
  modalEl.hidden = true;
  modalEl.innerHTML = `<div class="modal-box"><button class="modal-close" aria-label="Close">&times;</button><div class="modal-content"></div></div>`;
  modalEl.addEventListener("click", (e) => {
    if (e.target === modalEl) closeModal();
  });
  modalEl.querySelector(".modal-close").addEventListener("click", closeModal);
  document.body.appendChild(modalEl);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
  return modalEl;
}

export function closeModal() {
  if (modalEl) modalEl.hidden = true;
}

export function openModal(html) {
  const modal = ensureModal();
  modal.querySelector(".modal-content").innerHTML = html;
  modal.hidden = false;
}
