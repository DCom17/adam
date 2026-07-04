/* JARVIS service worker — offline shell + Web Push delivery.
 *
 * Two jobs:
 *   1. Offline shell: precache the tiny app shell so a launch with no network
 *      renders the UI (and turns then fail with a clear in-app error) instead of
 *      Safari's blank "not connected to the internet" page (PWA-12).
 *   2. Web Push: on a finished job the server pushes {title, body, session_id, ts}.
 *      If the app is open and focused, the page already spoke the reply via its
 *      poll loop, so we stay silent. Otherwise we show a banner; tapping it
 *      focuses/opens the app and tells the page to replay the latest result.
 *
 * Caching strategy is deliberately conservative to avoid the stale-client trap
 * (PWA-10): navigations are NETWORK-FIRST (always fetch fresh HTML when online,
 * fall back to cache only when the network fails), so a redeploy is picked up on
 * the next online launch. Only the static icon/manifest are served cache-first.
 */
const CACHE = "jarvis-shell-v1";
const SHELL = ["/", "/manifest.json", "/icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Drop old shell caches so a new version never serves stale assets.
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                 // never touch API POSTs
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // only our own origin

  // App shell navigations: network-first so online launches always get fresh
  // HTML; fall back to the cached shell only when the network is unreachable.
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        const c = await caches.open(CACHE);
        c.put("/", fresh.clone());                  // keep the offline copy current
        return fresh;
      } catch (_) {
        return (await caches.match("/")) || Response.error();
      }
    })());
    return;
  }

  // Static shell assets: cache-first (they rarely change; refresh in background).
  if (url.pathname === "/manifest.json" || url.pathname === "/icon.png") {
    event.respondWith((async () => {
      const cached = await caches.match(req);
      const network = fetch(req).then((res) => {
        caches.open(CACHE).then((c) => c.put(req, res.clone()));
        return res;
      }).catch(() => cached);
      return cached || network;
    })());
  }
  // Everything else (API, /poll, /speak, /upload, /sounds…) falls through to the
  // network untouched — offline, those fail and the app shows its own error.
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  // iOS already shows "from JARVIS" as the source line, so use the reply itself
  // as the title instead of a redundant "JARVIS" — no body line needed.
  const title = data.body || data.title || "Done, sir.";

  // Always show — iOS requires a notification per push, and the SERVER already
  // decided to send this only because the app wasn't on-screen (foreground
  // heartbeat). Trying to suppress here would just make iOS show a generic one.
  event.waitUntil(
    self.registration.showNotification(title, {
      icon: "/icon.png",
      badge: "/icon.png",
      tag: "jarvis-result",   // collapse to one standing notification
      renotify: true,
      data: { ts: data.ts || 0, session_id: data.session_id || "" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const c of wins) {
      if ("focus" in c) {
        c.postMessage({ type: "jarvis-open-play" });
        return c.focus();
      }
    }
    // No open window — launch one; the page reads ?play=1 and speaks on load.
    return self.clients.openWindow("/?play=1");
  })());
});
