// Generic modal shell shared by the player-detail modal and the
// points-against modal - one overlay element, reused for whichever content
// is currently open.

let modalEl = null;
let bodyScrollLockY = null;

// Locks the BACKGROUND page's own scroll while a modal is open - not just a
// nicety, a real bug fix: .modal-overlay is position:fixed, and on mobile
// Safari/Chrome a position:fixed element is well-documented to visibly
// detach/mis-position for a frame while the PAGE BEHIND IT scrolls (the
// exact trigger for the browser's own address-bar chrome collapsing or
// expanding, which happens on any touch-scroll gesture ANYWHERE on the page,
// not just ones the background page's own layout visibly responds to) -
// confirmed live as the remaining cause of stat-grid content still
// appearing above the modal's frozen player-name header while scrolling on
// a phone, even after .modal-box's own scroll/overscroll-behavior were
// already fixed (see openModal's/styles.css's own comments) - those fixes
// addressed the modal's OWN scroll content, not the fixed overlay itself
// briefly losing its true viewport position out from under it.
// position:fixed + a negative top offset (not overflow:hidden alone) is the
// standard iOS-safe recipe - overflow:hidden on <body> alone still lets
// iOS Safari scroll the page via rubber-banding. Restores the exact scroll
// position on unlock rather than resetting to 0.
function lockBodyScroll() {
  if (bodyScrollLockY !== null) return; // already locked - don't clobber the saved position
  bodyScrollLockY = window.scrollY || window.pageYOffset || 0;
  document.body.style.position = "fixed";
  document.body.style.top = `-${bodyScrollLockY}px`;
  document.body.style.width = "100%";
}

function unlockBodyScroll() {
  if (bodyScrollLockY === null) return;
  document.body.style.position = "";
  document.body.style.top = "";
  document.body.style.width = "";
  window.scrollTo(0, bodyScrollLockY);
  bodyScrollLockY = null;
}

function ensureModal() {
  if (modalEl) return modalEl;
  modalEl = document.createElement("div");
  modalEl.className = "modal-overlay";
  modalEl.hidden = true;
  // .modal-header-slot is a genuinely separate, non-scrolling element - NOT
  // position:sticky - see openModal's own comment on why that distinction
  // is the actual fix, not a redundant belt-and-suspenders one.
  modalEl.innerHTML = `<div class="modal-box"><button class="modal-close" aria-label="Close">&times;</button><div class="modal-header-slot"></div><div class="modal-content"></div></div>`;
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
  unlockBodyScroll();
}

// headerHtml (optional): rendered into a SEPARATE, non-scrolling sibling of
// .modal-content instead of being sticky-positioned INSIDE the scrolling
// content - see playermodal.js's openPlayerModal, the only caller that
// passes it today. Earlier versions kept the player modal's header as a
// position:sticky child of the scrolling element itself (first .modal-
// overlay, then .modal-box - see git history/this file's own past
// comments); each of those rounds fixed one real, reproducible bug
// (overlay vs. box as the actual scroll container, stale scrollTop carried
// across redraws, phone address-bar vh/dvh mismatches, body-scroll-lock)
// but a real scroll gesture could STILL show later content above the
// header, which no synchronous/programmatic scrollTop test here ever
// reproduced - position:sticky's offset is recalculated by the browser
// relative to live scroll position, and that recalculation is well-known
// (WebKit especially) to occasionally lag a frame or more behind an actual
// touch/momentum scroll, briefly exposing whatever the header ITSELF hasn't
// yet scrolled out of the way of. A plain non-sticky element that's simply
// never part of the scrolling box at all has no such recalculation to lag -
// it's always exactly where it is, full stop. No headerHtml (every other
// caller - the points-against modal, and the compare view's own two
// per-column sticky headers) keeps the old sticky-inside-.modal-content
// behavior unchanged; .modal-header-slot stays empty and collapses via
// styles.css's :empty rule.
export function openModal(html, { wide = false, headerHtml = "" } = {}) {
  const modal = ensureModal();
  lockBodyScroll();
  modal.classList.toggle("modal-overlay-wide", wide);
  modal.querySelector(".modal-header-slot").innerHTML = headerHtml;
  modal.querySelector(".modal-content").innerHTML = html;
  modal.hidden = false;
  // .modal-content (not .modal-box) is the actual scrolling element (see
  // styles.css) and is reused across opens/redraws - the SAME node, still
  // scrolled from whatever was open before, now holding brand-new content.
  // A sticky child (a caller that DIDN'T pass headerHtml, e.g. the compare
  // view's own per-column headers) is positioned relative to that leftover
  // scroll offset until the next real scroll event nudges the browser into
  // recomputing it, so without this reset the new content's own top section
  // could render ABOVE the still-catching-up sticky header instead of
  // starting flush at the top - confirmed live (a redraw - closing one
  // player's modal and opening another's - left the new modal's stat-grid
  // floating above its own frozen name header). Every caller redraws by
  // re-calling this (see e.g. pointsagainstmodal.js's own draw()), so
  // resetting centrally here covers all of them, not just the first open.
  const content = modal.querySelector(".modal-content");
  if (content) content.scrollTop = 0;
  // Reset to visible on every open, not just once - openPlayerModal hides
  // this shared external close button (it renders its own, embedded inside
  // headerHtml instead - see playermodal.js) for AS LONG AS that specific
  // content stays open, but every other caller of this function
  // (openComparePlayerModal, the points-against modal, and any future one)
  // still needs it, so the default has to be reasserted centrally here
  // rather than trusted to whichever caller happens to run next.
  const closeBtn = modal.querySelector(".modal-box > .modal-close");
  if (closeBtn) closeBtn.hidden = false;
}
