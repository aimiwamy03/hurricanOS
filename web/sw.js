// Keeps Shelfwatch usable on a phone that loses signal. Pages and every GET to /api come
// from the network when it answers, else from the last copy this phone saved, stamped
// with when it was saved (X-Shelfwatch-Saved) so the page can say the data is old.
// Asking a question (POST /api/ask) always needs the server: nothing to cache there.
const VERSION = "shelfwatch-v1";
const SHELL = [
  "/", "/ask", "/offline", "/crisis",
  "/static/style.css", "/static/app.js", "/static/ask.js",
  "/static/vendor/leaflet/leaflet.js", "/static/vendor/leaflet/leaflet.css",
  "/static/manifest.webmanifest", "/static/icons/icon-192.png",
];
const NETWORK_WAIT_MS = 8000;  // a weak signal should fall back to saved data, not hang
const TILE_LIMIT = 400;  // map tiles this phone has already seen, oldest dropped first

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => !key.startsWith(VERSION)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function stamped(response) {
  const headers = new Headers(response.headers);
  headers.set("X-Shelfwatch-Saved", new Date().toISOString());
  return response.blob().then((body) => new Response(body, { status: response.status, statusText: response.statusText, headers }));
}

function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]);
}

async function networkFirst(request, fallbackUrl) {
  const cache = await caches.open(VERSION);
  try {
    const response = await withTimeout(fetch(request), NETWORK_WAIT_MS);
    if (response.ok) cache.put(request, await stamped(response.clone()));
    return response;
  } catch (error) {
    // A page opened as "/?source=app" is the same page as the saved "/".
    const saved = await cache.match(request, { ignoreVary: true, ignoreSearch: request.mode === "navigate" });
    if (saved) return saved;
    if (fallbackUrl) {
      const page = await cache.match(fallbackUrl);
      if (page) return page;
    }
    throw error;
  }
}

async function tileFirst(request) {
  const cache = await caches.open(`${VERSION}-tiles`);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await fetch(request);
  if (response.ok || response.type === "opaque") {
    await cache.put(request, response.clone());
    const keys = await cache.keys();
    await Promise.all(keys.slice(0, Math.max(0, keys.length - TILE_LIMIT)).map((key) => cache.delete(key)));
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.hostname === "tile.openstreetmap.org") {
    event.respondWith(tileFirst(request));
  } else if (url.origin === self.location.origin) {
    if (request.mode === "navigate") event.respondWith(networkFirst(request, "/offline"));
    else if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/static/") || url.pathname.startsWith("/images/")) {
      event.respondWith(networkFirst(request));
    }
  }
});
