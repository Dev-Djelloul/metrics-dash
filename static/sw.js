// Service worker minimal : cache uniquement l'app shell (HTML/CSS/icônes),
// jamais les appels /api/* — les métriques doivent toujours venir du
// réseau, une version en cache serait trompeuse (faux CA, faux graphiques).
const CACHE_NAME = "metrics-dash-shell-v1";
const APP_SHELL = [
  "/",
  "/design-system.css",
  "/favicon.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Jamais de cache pour l'API ni les routes d'authentification : toujours
  // du réseau frais.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) {
    return;
  }

  // App shell : cache-first (rapide au rechargement), avec repli réseau
  // si jamais un fichier n'était pas encore en cache.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
