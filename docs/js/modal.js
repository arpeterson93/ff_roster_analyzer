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

export function openModal(html, { wide = false } = {}) {
  const modal = ensureModal();
  modal.classList.toggle("modal-overlay-wide", wide);
  modal.querySelector(".modal-content").innerHTML = html;
  modal.hidden = false;
  // Reset to visible on every open, not just once - openPlayerModal hides
  // this shared external close button (it renders its own, embedded inside
  // its sticky header instead - see playermodal.js) for AS LONG AS that
  // specific content stays open, but every other caller of this function
  // (openComparePlayerModal, the points-against modal, and any future one)
  // still needs it, so the default has to be reasserted centrally here
  // rather than trusted to whichever caller happens to run next.
  const closeBtn = modal.querySelector(".modal-box > .modal-close");
  if (closeBtn) closeBtn.hidden = false;
}
