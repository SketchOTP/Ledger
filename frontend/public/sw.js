/* PWA install + Web Push + cache hashed assets for faster repeat opens. */
// Bump whenever the production shell changes so an installed PWA drops stale
// hashed chunks instead of booting an older bundle after a deploy.
const VERSION = "ledger-v7";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isCacheableAsset(url) {
  try {
    const path = new URL(url).pathname;
    return path.includes("/assets/") || path.endsWith("ledger-icon.png") || path.endsWith("ledger-icon-192.png") || path.endsWith("ledger-icon-512.png");
  } catch {
    return false;
  }
}

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  if (!isCacheableAsset(event.request.url)) return;

  event.respondWith(
    caches.open(VERSION).then(async (cache) => {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      const response = await fetch(event.request);
      if (response.ok) cache.put(event.request, response.clone());
      return response;
    })
  );
});

function iconUrl() {
  return new URL("ledger-icon-192.png", self.registration.scope).href;
}

function resolveAppUrl(raw) {
  const fallback = new URL("./notifications", self.registration.scope).href;
  if (!raw) return fallback;
  if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
  if (raw.startsWith("/")) {
    const origin = new URL(self.registration.scope).origin;
    return `${origin}${raw}`;
  }
  return new URL(raw, self.registration.scope).href;
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }

  const title = data.title || "Ledger";
  const icon = iconUrl();
  const options = {
    body: data.body || "You have a Ledger alert.",
    icon,
    badge: icon,
    tag: data.tag || "ledger-alert",
    data: {
      url: data.url || "./notifications",
      notificationId: data.notificationId || null,
    },
    requireInteraction: data.requireInteraction ?? false,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = resolveAppUrl(event.notification.data?.url);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(target);
      }
    })
  );
});
