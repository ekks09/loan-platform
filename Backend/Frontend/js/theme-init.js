/**
 * Paint the selected appearance before the stylesheet is parsed.
 *
 * Loaded synchronously in <head> (same origin, CSP-friendly) so there is no
 * flash of the wrong theme. The preference is "system" by default; an explicit
 * light/dark choice is stored under the same key used by core.js.
 *
 * It also enables scroll-reveal hiding only when the browser supports both
 * IntersectionObserver and motion. If the deferred core script never runs, a
 * short watchdog removes the hiding class so content cannot stay invisible.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var KEY = "mb-theme";
  var preference = "system";

  try {
    var stored = window.localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") preference = stored;
  } catch (error) {
    // Storage is optional; fall back to the system preference.
  }

  var media =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
  var theme = preference === "system" ? (media && media.matches ? "dark" : "light") : preference;

  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
  root.classList.remove("no-js");

  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", theme === "dark" ? "#071320" : "#F7F4EE");
  }

  var reduce =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (!reduce && "IntersectionObserver" in window) {
    root.classList.add("has-reveal");

    window.setTimeout(function () {
      if (!window.__mbRevealReady) root.classList.remove("has-reveal");
    }, 2200);

    window.addEventListener("error", function () {
      root.classList.remove("has-reveal");
    });
  }
})();
