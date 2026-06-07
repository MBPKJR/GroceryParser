const CACHE_NAME = 'vrr-live-monitor-v1';
const ASSETS = [
  '/',
  '/index.html',
  '/style.css',
  '/app.js',
  '/icon.png',
  '/manifest.json'
];

// Install Event - cache assets
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS);
    })
  );
});

// Activate Event - clean old caches
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
});

// Fetch Event - network first with cache fallback for static files
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  
  // Do not cache transit API requests or geolocation lookups to ensure strictly live data
  if (url.pathname.startsWith('/api/') || url.hostname.includes('ipapi.co')) {
    return;
  }
  
  e.respondWith(
    fetch(e.request)
      .then((response) => {
        // Cache new static resources dynamically if correct
        if (response.status === 200 && e.request.method === 'GET') {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(e.request, responseClone);
          });
        }
        return response;
      })
      .catch(() => {
        // Fallback to cache if network is unavailable
        return caches.match(e.request);
      })
  );
});
