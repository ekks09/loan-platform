/*
 * Service worker for the loan platform PWA.
 *
 * Privacy rules enforced here:
 *   - never cache /api/ responses (accounts, loans, payments, uploads)
 *   - never cache HTML pages (they can contain personalised state)
 *   - never cache /admin/, /health/ or the manifest
 *   - never queue or replay POST/PUT/PATCH/DELETE requests
 *   - only same-origin /static/ assets and the offline fallback are cached
 *
 * The update flow is explicit: a waiting worker stays waiting until the page
 * asks it to activate, then the page reloads once.
 */
"use strict";

const VERSION = "__CACHE_VERSION__";
const CACHE_NAME = "mb-shell-" + VERSION;
const OFFLINE_URL = "/offline/";

const PRECACHE = [
  OFFLINE_URL,
  "/static/css/style.css",
  "/static/js/theme-init.js",
  "/static/js/core.js",
  "/static/js/pwa.js",
  "/static/js/auth.js",
  "/static/js/loan.js",
  "/static/js/phone.js",
  "/static/js/loyalty.js",
  "/static/js/page-auth.js",
  "/static/js/page-index.js",
  "/static/js/page-dashboard.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/favicon-32.png",
  // The offline page renders its band from the cache, so ship it. Never
  // precache account, application or payment responses.
  "/static/img/texture-band.webp",
  "/static/img/texture-band.jpg",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // Add each asset independently: one missing file must not prevent the
      // worker from installing or the offline page from working.
      return Promise.allSettled(PRECACHE.map((url) => cache.add(url)));
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return key.startsWith("mb-shell-") && key !== CACHE_NAME;
            })
            .map(function (key) {
              return caches.delete(key);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

function isPrivatePath(pathname) {
  return (
    pathname.startsWith("/api/") ||
    pathname.startsWith("/admin/") ||
    pathname.startsWith("/health/") ||
    pathname === "/sw.js" ||
    pathname === "/manifest.webmanifest"
  );
}

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;

  let url;
  try {
    url = new URL(request.url);
  } catch (error) {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (isPrivatePath(url.pathname)) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(function () {
        return caches.match(OFFLINE_URL).then(function (cached) {
          return (
            cached ||
            new Response("You are offline.", {
              status: 503,
              headers: { "Content-Type": "text/plain; charset=utf-8" },
            })
          );
        });
      })
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(request).then(function (cached) {
        const network = fetch(request)
          .then(function (response) {
            if (response && response.ok && response.type === "basic") {
              const copy = response.clone();
              caches.open(CACHE_NAME).then(function (cache) {
                cache.put(request, copy);
              });
            }
            return response;
          })
          .catch(function () {
            return cached;
          });
        return cached || network;
      })
    );
  }
});
