/* Sophia PWA service worker.
 * Strategy:
 *  - Precache the app shell (HTML + manifest + icons) on install.
 *  - Navigation / static assets: cache-first, fall back to cached shell when offline.
 *  - API/auth/dispatch requests: network-first; if offline, the client queues the
 *    interaction in IndexedDB and retries on reconnect (see index.html).
 */
const CACHE = "sophia-shell-v1";
const SHELL = [
  "/",
  "/desktop",
  "/m",
  "/mobile",
  "/static/manifest.webmanifest",
  "/static/icons/icon.svg",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // let non-GET (POST capture/tasks) hit network/client queue
  const url = new URL(req.url);
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/") || url.pathname.startsWith("/voiceprints/") || url.pathname.startsWith("/dispatch/") || url.pathname.startsWith("/tasks/") || url.pathname.startsWith("/system/") || url.pathname.startsWith("/capture") || url.pathname.startsWith("/meeting/")) {
    // Network-first for live data; fall back to cache only if both fail.
    event.respondWith(
      fetch(req).catch(() => caches.match(req).then((r) => r || caches.match("/")))
    );
    return;
  }
  // Cache-first for shell/static.
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }).catch(() => caches.match("/")))
  );
});
