// Service worker minimal : cache uniquement l'app shell (HTML/CSS/icônes),
// jamais les appels /api/* — les métriques doivent toujours venir du
// réseau, une version en cache serait trompeuse (faux CA, faux graphiques).
//
// v2 : le nom de cache change à chaque fois qu'on veut forcer les navigateurs
// à purger l'ancien app shell (l'activate handler ci-dessous supprime tout
// cache dont le nom ne correspond plus). Incrémenter ce suffixe est le seul
// moyen fiable de "casser" un cache déjà posé chez un visiteur.
const CACHE_NAME = "metrics-dash-shell-v2";
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

  // App shell : network-first — toujours essayer d'avoir la dernière
  // version en ligne (le HTML change à chaque déploiement, contrairement à
  // une vraie appli statique versionnée), et ne retomber sur le cache que
  // si le réseau est indisponible (mode hors-ligne). L'inverse (cache-first)
  // servait indéfiniment une version figée du site tant que ce fichier
  // sw.js lui-même ne changeait pas — un rechargement normal ne suffisait
  // plus à voir les mises à jour, seul un rechargement forcé (Cmd+Shift+R)
  // contournant le service worker les révélait.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
