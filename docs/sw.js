// Minimal PWA service worker: caches the app shell (HTML/CSS/JS/icons) so
// the site installs and opens instantly on repeat visits, but deliberately
// does NOT cache-first the league data under data/ - a stale points-above-
// replacement number or a stale FAAB estimate is actively misleading, not
// just slow, so freshness beats speed there. Data requests always try the
// network first and only fall back to whatever's cached if that fails
// (offline), which degrades to "last known good" instead of a blank page.
//
// Bump CACHE_NAME on any app-shell file rename/add/remove - activate()
// deletes every other cache name, so this is also how old shells get
// cleaned up after a deploy.
const CACHE_NAME = "stackademics-shell-v1";

const APP_SHELL = [
  "./",
  "index.html",
  "manifest.json",
  "css/styles.css",
  "js/app.js",
  "js/colors.js",
  "js/data.js",
  "js/matchups.js",
  "js/modal.js",
  "js/playermodal.js",
  "js/pointsagainstmodal.js",
  "js/rankings.js",
  "js/schedule.js",
  "js/settings.js",
  "js/settingsConfig.js",
  "js/standings.js",
  "js/startsit.js",
  "js/statcolumns.js",
  "js/state.js",
  "js/strength.js",
  "js/trade.js",
  "js/tradeui.js",
  "js/watchlist.js",
  "js/watchlistConfig.js",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/apple-touch-icon.png",
  "icons/favicon-32.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // Per-file, not cache.addAll - addAll fails the WHOLE install if even
      // one path 404s (e.g. a file renamed here without updating this list),
      // which would silently leave the app permanently uninstallable rather
      // than just missing one cached asset.
      Promise.allSettled(APP_SHELL.map((url) => cache.add(url)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  if (url.pathname.includes("/data/")) {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ||
        fetch(event.request).then((res) => {
          const copy = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return res;
        })
    )
  );
});
