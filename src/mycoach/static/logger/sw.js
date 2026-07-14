/* MyCoach Logger service worker — caches the app shell so the logger works
   with no signal at the gym. Scoped to /logger (see Service-Worker-Allowed
   header set by the /logger/sw.js route). */

const CACHE = "mycoach-logger-v3";
// Only purge this SW's own caches on activate — never the main app's
// `mycoach-v*` caches, which live on the same origin.
const CACHE_PREFIX = "mycoach-logger-";
const SHELL = [
    "/logger",
    "/static/logger/app.css",
    "/static/logger/app.js",
    "/static/logger/icon.svg",
    "/static/logger/icon-192.png",
    "/static/logger/icon-512.png",
    "/static/logger/manifest.json",
];

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys
                    .filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE)
                    .map((k) => caches.delete(k))
            )
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET") return; // never cache POST syncs

    const url = new URL(req.url);

    // API calls: always go to network (offline → the app queues locally).
    if (url.pathname.startsWith("/api/")) return;

    // Navigations: network-first, fall back to the cached shell offline.
    if (req.mode === "navigate") {
        event.respondWith(
            fetch(req).catch(() => caches.match("/logger"))
        );
        return;
    }

    // Static shell assets: cache-first, then network (and cache the result).
    event.respondWith(
        caches.match(req).then(
            (hit) =>
                hit ||
                fetch(req).then((resp) => {
                    if (resp.ok && url.origin === self.location.origin) {
                        const copy = resp.clone();
                        caches.open(CACHE).then((c) => c.put(req, copy));
                    }
                    return resp;
                })
        )
    );
});
