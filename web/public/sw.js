const CACHE = "antalya-transit-v3";

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      const home = await fetch("/", { cache: "no-store" });
      if (home.ok) {
        await cache.put("/", home);
      }
      await cache.addAll(["/manifest.webmanifest", "/icon.svg"]);
    })(),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/transit/live")) {
    return;
  }
  if (url.origin === self.location.origin) {
    event.respondWith(networkThenCache(request, url.pathname === "/"));
    return;
  }
  if (url.hostname.endsWith("openfreemap.org") || url.hostname.endsWith("openstreetmap.org")) {
    event.respondWith(cacheThenNetwork(request));
  }
});

async function networkThenCache(request, navigation) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) {
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = (await cache.match(request)) || (navigation ? await cache.match("/") : undefined);
    if (cached) {
      return cached;
    }
    throw error;
  }
}

async function cacheThenNetwork(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) {
    return cached;
  }
  const response = await fetch(request);
  if (response.ok) {
    await cache.put(request, response.clone());
  }
  return response;
}
