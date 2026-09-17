/* MyCoach Logger service worker — caches the app shell so the logger works
   with no signal at the gym. Served from its own origin's root, so the
   default scope (/) already covers the whole app — no Service-Worker-Allowed
   header trick needed here, unlike the legacy /logger-mounted copy. */

const CACHE = "mycoach-logger-v9";
const CACHE_PREFIX = "mycoach-logger-";
const SHELL = [
    "/",
    "/app.css",
    "/app.js",
    "/icon.svg",
    "/icon-192.png",
    "/icon-512.png",
    "/manifest.json",
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
    // Cross-origin now (MyCoach itself lives elsewhere), but pathname alone
    // is enough to identify them.
    if (url.pathname.startsWith("/api/")) return;

    // Navigations: stale-while-revalidate. Off-LAN, the domain can resolve to
    // an unreachable address that hangs instead of rejecting, so a
    // network-first `.catch()` never fires and the tab sits on a blank
    // screen. Serving the cached shell first means the app opens immediately
    // either way, and a reachable network still refreshes the cache in the
    // background.
    if (req.mode === "navigate") {
        const update = fetch(req)
            .then((resp) => {
                if (!resp.ok) return resp;
                const copy = resp.clone();
                return caches
                    .open(CACHE)
                    .then((c) => c.put("/", copy))
                    .then(() => resp);
            })
            .catch(() => undefined);

        event.waitUntil(update);
        event.respondWith(
            caches.match("/").then((hit) =>
                hit ? hit : update.then((resp) => resp || fetch(req))
            )
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
