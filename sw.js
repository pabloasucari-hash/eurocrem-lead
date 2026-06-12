const CACHE = 'eurocrem-v1';
const ASSETS = [
  '/eurocrem-lead/',
  '/eurocrem-lead/index.html',
  '/eurocrem-lead/manifest.json',
  '/eurocrem-lead/icon-192.png',
  '/eurocrem-lead/icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

// Network first, fallback to cache
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
