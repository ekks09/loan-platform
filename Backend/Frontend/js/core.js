/**
 * Shared UI behaviour for M-Biashara.
 *
 * Everything here is progressive enhancement: pages remain readable without
 * JavaScript. No personal or financial data is stored by this module.
 */
(function () {
  "use strict";

  const MB = {};

  MB.$ = function (selector, root) {
    return (root || document).querySelector(selector);
  };

  MB.$$ = function (selector, root) {
    return Array.from((root || document).querySelectorAll(selector));
  };

  MB.money = function (value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "KES 0";
    return new Intl.NumberFormat("en-KE", {
      style: "currency",
      currency: "KES",
      maximumFractionDigits: 0,
    }).format(number);
  };

  MB.number = function (value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return new Intl.NumberFormat("en-KE", { maximumFractionDigits: 0 }).format(number);
  };

  MB.date = function (value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("en-KE", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  };

  MB.dateOnly = function (value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("en-KE", { dateStyle: "medium" }).format(date);
  };

  MB.setText = function (element, text) {
    if (element) element.textContent = text == null ? "" : String(text);
  };

  MB.setMessage = function (element, text, kind) {
    if (!element) return;
    element.textContent = text || "";
    element.className = "msg" + (kind ? " msg-" + kind : "");
    element.setAttribute("role", kind === "error" ? "alert" : "status");
    element.hidden = !text;
  };

  MB.fieldError = function (id, message) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = message || "";
    element.hidden = !message;
  };

  MB.clearFieldErrors = function (scope) {
    MB.$$(".field-error", scope || document).forEach(function (element) {
      element.textContent = "";
      element.hidden = true;
    });
    MB.$$("[aria-invalid='true']", scope || document).forEach(function (element) {
      element.removeAttribute("aria-invalid");
    });
  };

  MB.firstError = function (details, field) {
    if (!details) return "";
    const value = details[field];
    function walk(node) {
      if (!node) return "";
      if (typeof node === "string") return node;
      if (Array.isArray(node)) {
        for (const item of node) {
          const found = walk(item);
          if (found) return found;
        }
        return "";
      }
      if (typeof node === "object") {
        for (const key of Object.keys(node)) {
          const found = walk(node[key]);
          if (found) return found;
        }
      }
      return "";
    }
    return walk(value);
  };

  MB.announce = function (message) {
    const region = document.getElementById("statusAnnouncer");
    if (region) region.textContent = message;
  };

  MB.toast = function (message, options) {
    const opts = options || {};
    const region = document.getElementById("toastRegion");
    if (!region) return null;

    const toast = document.createElement("div");
    toast.className = "toast" + (opts.kind ? " toast-" + opts.kind : "");
    toast.setAttribute("role", opts.kind === "error" ? "alert" : "status");

    const text = document.createElement("span");
    text.textContent = message;
    toast.appendChild(text);

    if (opts.actionLabel && typeof opts.onAction === "function") {
      const action = document.createElement("button");
      action.type = "button";
      action.className = "link-button";
      action.textContent = opts.actionLabel;
      action.addEventListener("click", function () {
        opts.onAction();
        toast.remove();
      });
      toast.appendChild(action);
    }

    const close = document.createElement("button");
    close.type = "button";
    close.className = "link-button";
    close.textContent = "Dismiss";
    close.setAttribute("aria-label", "Dismiss notification");
    close.addEventListener("click", function () {
      toast.remove();
    });
    toast.appendChild(close);

    region.appendChild(toast);
    const lifetime = opts.persistent ? 0 : 6500;
    if (lifetime) {
      window.setTimeout(function () {
        toast.remove();
      }, lifetime);
    }
    return toast;
  };

  MB.openDialog = function (dialog) {
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else {
      // Older browsers without <dialog> support still get a usable panel.
      dialog.setAttribute("open", "");
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
    }
  };

  MB.closeDialog = function (dialog) {
    if (!dialog || !dialog.open) return;
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  };

  // ---- Appearance ---------------------------------------------------------

  const THEME_KEY = "mb-theme";
  const themeMedia =
    typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;

  function readThemePreference() {
    try {
      const value = window.localStorage.getItem(THEME_KEY);
      if (value === "light" || value === "dark") return value;
    } catch (error) {
      // Storage can be unavailable (private mode, policy). System wins.
    }
    return "system";
  }

  function systemTheme() {
    return themeMedia && themeMedia.matches ? "dark" : "light";
  }

  function resolveTheme(preference) {
    return preference === "system" ? systemTheme() : preference;
  }

  function applyTheme(preference, persist) {
    const resolved = resolveTheme(preference);
    const root = document.documentElement;
    root.setAttribute("data-theme", resolved);
    root.style.colorScheme = resolved;

    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", resolved === "dark" ? "#071320" : "#F7F4EE");
    }

    MB.$$("[data-theme-toggle]").forEach(function (button) {
      // A switch exposes both a name ("Dark theme") and its state, so the
      // announcement is stable even when the OS theme changes underneath.
      button.setAttribute("aria-checked", resolved === "dark" ? "true" : "false");
      button.title = resolved === "dark" ? "Switch to light theme" : "Switch to dark theme";
      button.setAttribute("aria-label", button.title);
    });

    // The sidebar exposes the three-state preference so a user can go back to
    // following the operating system without clearing storage by hand.
    MB.$$("[data-theme-set]").forEach(function (button) {
      const value = button.getAttribute("data-theme-set");
      button.setAttribute("aria-pressed", value === preference ? "true" : "false");
    });

    if (persist) {
      try {
        if (preference === "system") {
          window.localStorage.removeItem(THEME_KEY);
        } else {
          window.localStorage.setItem(THEME_KEY, preference);
        }
      } catch (error) {
        // Keep the theme for this page even when persistence is unavailable.
      }
    }

    MB.themePreference = preference;
    MB.resolvedTheme = resolved;
  }

  MB.theme = {
    get preference() {
      return readThemePreference();
    },
    get resolved() {
      return resolveTheme(readThemePreference());
    },
    set: function (preference) {
      applyTheme(preference, true);
    },
    toggle: function () {
      applyTheme(resolveTheme(readThemePreference()) === "dark" ? "light" : "dark", true);
    },
  };

  function setupTheme() {
    // theme-init.js has already painted the correct theme; this syncs labels,
    // the browser chrome colour and the listener for system changes.
    applyTheme(readThemePreference(), false);

    MB.$$("[data-theme-toggle]").forEach(function (button) {
      button.addEventListener("click", function () {
        MB.theme.toggle();
      });
    });

    MB.$$("[data-theme-set]").forEach(function (button) {
      button.addEventListener("click", function () {
        const value = button.getAttribute("data-theme-set");
        if (value !== "light" && value !== "dark" && value !== "system") return;
        MB.theme.set(value);
        MB.announce(
          value === "system"
            ? "Appearance follows your device setting."
            : "Appearance set to " + value + "."
        );
      });
    });

    if (themeMedia) {
      const onSystemChange = function () {
        if (readThemePreference() === "system") applyTheme("system", false);
      };
      if (typeof themeMedia.addEventListener === "function") {
        themeMedia.addEventListener("change", onSystemChange);
      } else if (typeof themeMedia.addListener === "function") {
        themeMedia.addListener(onSystemChange);
      }
    }

    window.addEventListener("storage", function (event) {
      if (event.key === THEME_KEY || event.key === null) {
        applyTheme(readThemePreference(), false);
      }
    });
  }

  // ---- Header, mobile navigation and password visibility -----------------

  function setupHeader() {
    const header = document.querySelector("[data-header]");
    if (header) {
      const onScroll = function () {
        header.classList.toggle("is-scrolled", window.scrollY > 4);
      };
      onScroll();
      window.addEventListener("scroll", onScroll, { passive: true });
    }

    // The sidebar is a fixed panel on desktop and a slide-over drawer below
    // 1024px. It is hidden from assistive technology and the tab order while
    // it is off-canvas, so no invisible focus traps remain.
    const sidebar = document.getElementById("appSidebar");
    const openers = MB.$$("[data-nav-open]");
    const scrim = document.querySelector("[data-nav-close]");
    if (!sidebar) return;

    const mq = typeof window.matchMedia === "function"
      ? window.matchMedia("(min-width: 1024px)")
      : null;
    let lastFocus = null;

    function isDesktop() {
      return !!(mq && mq.matches);
    }

    function setOpen(open, options) {
      const opts = options || {};
      const wasOpen = document.body.classList.contains("is-nav-open");
      if (open && isDesktop()) open = false;

      document.body.classList.toggle("is-nav-open", open);
      sidebar.classList.toggle("is-open", open);
      if (scrim) scrim.hidden = !open;

      sidebar.toggleAttribute("inert", !open && !isDesktop());
      openers.forEach(function (button) {
        button.setAttribute("aria-expanded", String(open));
      });

      if (open && !wasOpen) {
        lastFocus = document.activeElement;
        const first = sidebar.querySelector("a[href], button:not([disabled])");
        if (first) {
          window.requestAnimationFrame(function () {
            first.focus({ preventScroll: true });
          });
        }
      }
      if (!open && opts.restoreFocus && lastFocus && lastFocus.focus) {
        lastFocus.focus({ preventScroll: true });
      }
      if (!open) lastFocus = null;
    }

    MB.openMenu = function () {
      setOpen(true);
    };
    MB.closeMenu = function (options) {
      if (!document.body.classList.contains("is-nav-open")) return;
      setOpen(false, options);
    };

    openers.forEach(function (button) {
      button.addEventListener("click", function () {
        if (document.body.classList.contains("is-nav-open")) {
          MB.closeMenu({ restoreFocus: true });
        } else {
          MB.openMenu();
        }
      });
    });

    if (scrim) {
      scrim.addEventListener("click", function () {
        MB.closeMenu({ restoreFocus: true });
      });
    }

    sidebar.addEventListener("click", function (event) {
      if (isDesktop()) return;
      if (event.target.closest("a[href]")) MB.closeMenu();
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && document.body.classList.contains("is-nav-open")) {
        MB.closeMenu({ restoreFocus: true });
      }
    });

    function syncViewport() {
      if (isDesktop()) {
        document.body.classList.remove("is-nav-open");
        sidebar.classList.remove("is-open");
        if (scrim) scrim.hidden = true;
        sidebar.removeAttribute("inert");
        openers.forEach(function (button) {
          button.setAttribute("aria-expanded", "false");
        });
      } else if (!document.body.classList.contains("is-nav-open")) {
        sidebar.setAttribute("inert", "");
      }
    }

    syncViewport();
    if (mq) {
      if (typeof mq.addEventListener === "function") {
        mq.addEventListener("change", syncViewport);
      } else if (typeof mq.addListener === "function") {
        mq.addListener(syncViewport);
      }
    }
  }

  function setupPasswordToggles() {
    MB.$$("[data-password-toggle]").forEach(function (button) {
      const input = document.getElementById(button.getAttribute("data-password-toggle"));
      if (!input) return;
      button.addEventListener("click", function () {
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        button.textContent = show ? "Hide" : "Show";
        button.setAttribute("aria-label", show ? "Hide password" : "Show password");
        button.setAttribute("aria-pressed", String(show));
      });
    });
  }

  function setupDialogs() {
    MB.$$("[data-dialog-open]").forEach(function (trigger) {
      trigger.addEventListener("click", function () {
        MB.openDialog(document.getElementById(trigger.getAttribute("data-dialog-open")));
      });
    });

    MB.$$("[data-dialog-close]").forEach(function (button) {
      button.addEventListener("click", function () {
        MB.closeDialog(button.closest("dialog"));
      });
    });

    MB.$$("dialog").forEach(function (dialog) {
      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) MB.closeDialog(dialog);
      });
    });
  }

  function setupReload() {
    MB.$$("[data-reload]").forEach(function (button) {
      button.addEventListener("click", function () {
        window.location.reload();
      });
    });
  }

  // ---- Section navigation -------------------------------------------------

  function prefersReducedMotion() {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function focusTarget(target) {
    if (typeof target.focus !== "function") return;
    if (!target.hasAttribute("tabindex")) {
      target.setAttribute("tabindex", "-1");
      target.addEventListener(
        "blur",
        function () {
          target.removeAttribute("tabindex");
        },
        { once: true }
      );
    }
    target.focus({ preventScroll: true });
  }

  function scrollToTarget(target) {
    if (!target) return;
    target.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "start",
    });
    focusTarget(target);
  }

  function scrollToHash() {
    const hash = window.location.hash;
    if (!hash || hash === "#") return;
    let id = hash.slice(1);
    try {
      id = decodeURIComponent(id);
    } catch (error) {
      return;
    }
    scrollToTarget(document.getElementById(id));
  }

  function setupAnchors() {
    document.addEventListener("click", function (event) {
      if (event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      if (!event.target || typeof event.target.closest !== "function") return;
      const link = event.target.closest("a[href]");
      if (!link) return;

      let destination;
      try {
        destination = new URL(link.href, window.location.href);
      } catch (error) {
        return;
      }

      // Handle both "#section" and "/current-page/#section" without a reload.
      if (destination.origin !== window.location.origin) return;
      if (destination.pathname !== window.location.pathname) return;
      if (!destination.hash || destination.hash === "#") return;

      const hash = destination.hash;
      let id = hash.slice(1);
      try {
        id = decodeURIComponent(id);
      } catch (error) {
        return;
      }
      const target = document.getElementById(id);
      if (!target) return;

      event.preventDefault();
      if (MB.closeMenu) MB.closeMenu();

      if (window.location.hash !== hash) {
        try {
          window.history.pushState(null, "", hash);
        } catch (error) {
          window.location.hash = hash;
        }
      }

      // Let the mobile panel finish collapsing before measuring the target.
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(function () {
          scrollToTarget(target);
        });
      });
    });

    document.addEventListener(
      "touchstart",
      function (event) {
        if (!event.target || typeof event.target.closest !== "function") return;
        if (event.target.closest("[data-nav-panel] a[href], .side-nav a[href]")) {
          if (MB.closeMenu) MB.closeMenu();
        }
      },
      { passive: true }
    );

    window.addEventListener("popstate", scrollToHash);
    window.addEventListener("hashchange", scrollToHash);

    if (window.location.hash) {
      window.requestAnimationFrame(scrollToHash);
    }
  }

  // ---- Scroll spy ---------------------------------------------------------
  //
  // The sidebar and the homepage share one set of section ids. The observer
  // marks the section occupying the reading line, so the gold indicator
  // follows the page rather than the last click.

  function setupScrollSpy() {
    const links = MB.$$("[data-spy]");
    if (!links.length || !("IntersectionObserver" in window)) return;

    const sections = [];
    links.forEach(function (link) {
      const id = link.getAttribute("data-spy");
      const section = id ? document.getElementById(id) : null;
      if (section && !sections.includes(section)) sections.push(section);
    });
    if (!sections.length) return;

    let current = null;

    function activate(section) {
      if (section === current) return;
      current = section;
      links.forEach(function (link) {
        const active = section && link.getAttribute("data-spy") === section.id;
        link.classList.toggle("is-active", Boolean(active));
        if (active) {
          link.setAttribute("aria-current", "location");
        } else {
          link.removeAttribute("aria-current");
        }
      });
    }

    const visible = new Set();
    const observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) visible.add(entry.target);
          else visible.delete(entry.target);
        });
        const ordered = sections.filter(function (section) {
          return visible.has(section);
        });
        if (ordered.length) activate(ordered[0]);
      },
      { rootMargin: "-42% 0px -52% 0px", threshold: 0 }
    );

    sections.forEach(function (section) {
      observer.observe(section);
    });

    // Deep links and clicks should highlight immediately even before the
    // observer fires on the next frame.
    function fromHash() {
      const id = (window.location.hash || "").replace("#", "");
      const match = sections.find(function (section) {
        return section.id === id;
      });
      if (match) activate(match);
    }
    window.addEventListener("hashchange", fromHash);
    fromHash();
  }

  // ---- Scroll reveal ------------------------------------------------------

  function setupReveal() {
    const root = document.documentElement;
    const elements = MB.$$("[data-reveal]");
    window.__mbRevealReady = true;

    if (!elements.length || !root.classList.contains("has-reveal")) {
      elements.forEach(function (element) {
        element.classList.add("is-revealed");
      });
      root.classList.remove("has-reveal");
      return;
    }

    if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
      elements.forEach(function (element) {
        element.classList.add("is-revealed");
      });
      root.classList.remove("has-reveal");
      return;
    }

    const observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          // Tall sections can never reach the ratio threshold on short
          // viewports; reveal them once they fill half the screen instead.
          const visibleEnough =
            entry.intersectionRatio >= 0.12 ||
            entry.intersectionRect.height >= window.innerHeight * 0.5;
          if (!visibleEnough) return;
          entry.target.classList.add("is-revealed");
          observer.unobserve(entry.target);
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
    );

    elements.forEach(function (element) {
      observer.observe(element);
    });

    if (typeof window.matchMedia === "function") {
      const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
      const onMotionChange = function (event) {
        if (!event.matches) return;
        elements.forEach(function (element) {
          element.classList.add("is-revealed");
          observer.unobserve(element);
        });
      };
      if (typeof motion.addEventListener === "function") {
        motion.addEventListener("change", onMotionChange);
      } else if (typeof motion.addListener === "function") {
        motion.addListener(onMotionChange);
      }
    }
  }

  // ---- Offline state -------------------------------------------------------

  function setupNetworkState() {
    const banner = document.getElementById("offlineBanner");

    function apply() {
      const offline = navigator.onLine === false;
      document.body.classList.toggle("is-offline", offline);
      if (banner) banner.hidden = !offline;
      document.dispatchEvent(
        new CustomEvent(offline ? "mb:offline" : "mb:online")
      );
    }

    window.addEventListener("online", apply);
    window.addEventListener("offline", apply);
    apply();
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupTheme();
    setupHeader();
    setupPasswordToggles();
    setupDialogs();
    setupReload();
    setupAnchors();
    setupScrollSpy();
    setupReveal();
    setupNetworkState();
  });

  window.MB = MB;
})();
