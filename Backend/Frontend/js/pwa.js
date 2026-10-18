/**
 * Progressive Web App lifecycle.
 *
 * - registers the service worker without making page loading depend on it
 * - offers an explicit update action instead of silently swapping assets
 * - implements a real install flow with browser prompt or manual instructions
 *
 * The service worker itself never caches API responses, authenticated pages or
 * financial data. See Frontend/sw.js.
 */
(function () {
  "use strict";

  const MB = window.MB;
  if (!MB) return;

  const isStandalone = function () {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  };

  const isIOS = function () {
    const ua = window.navigator.userAgent || "";
    return (
      /iPad|iPhone|iPod/.test(ua) ||
      (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1)
    );
  };

  // ------------------------------------------------------------- install UI

  const installButtons = MB.$$("[data-install]");
  const installDialog = document.getElementById("installDialog");
  const installBody = document.getElementById("installBody");
  const installConfirm = document.getElementById("installConfirm");
  const iosSteps = document.getElementById("iosSteps");
  let deferredPrompt = null;
  let installed = isStandalone();

  function refreshInstallButtons() {
    installButtons.forEach(function (button) {
      button.hidden = installed;
    });
  }

  function showManualInstructions() {
    if (!installDialog) return;
    if (installBody) {
      installBody.textContent = isIOS()
        ? "Safari installs the app from the Share menu. The installed app opens your dashboard and keeps public pages available."
        : "Use your browser menu and choose “Install app” or “Add to Home Screen”. The installed app opens your dashboard.";
    }
    if (iosSteps) iosSteps.hidden = !isIOS();
    if (installConfirm) installConfirm.hidden = true;
    MB.openDialog(installDialog);
  }

  async function triggerInstall() {
    if (deferredPrompt) {
      const prompt = deferredPrompt;
      deferredPrompt = null;
      try {
        prompt.prompt();
        const choice = await prompt.userChoice;
        if (choice && choice.outcome === "accepted") {
          installed = true;
          refreshInstallButtons();
          MB.toast("M-Biashara is being installed.", { kind: "success" });
        }
      } catch (error) {
        showManualInstructions();
      }
      return;
    }
    showManualInstructions();
  }

  installButtons.forEach(function (button) {
    button.addEventListener("click", triggerInstall);
  });

  if (installConfirm) {
    installConfirm.addEventListener("click", async function () {
      if (!deferredPrompt) {
        MB.closeDialog(installDialog);
        return;
      }
      const prompt = deferredPrompt;
      deferredPrompt = null;
      MB.closeDialog(installDialog);
      try {
        prompt.prompt();
        const choice = await prompt.userChoice;
        if (choice && choice.outcome === "accepted") {
          installed = true;
          refreshInstallButtons();
        }
      } catch (error) {
        showManualInstructions();
      }
    });
  }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    deferredPrompt = event;
    installed = false;
    refreshInstallButtons();
  });

  window.addEventListener("appinstalled", function () {
    installed = true;
    deferredPrompt = null;
    refreshInstallButtons();
    MB.closeDialog(installDialog);
    MB.toast("M-Biashara has been installed.", { kind: "success" });
  });

  refreshInstallButtons();

  // ------------------------------------------------------- service worker

  if (!("serviceWorker" in navigator)) return;

  const secure =
    window.location.protocol === "https:" ||
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1";
  if (!secure) return;

  let reloadRequested = false;

  function offerUpdate(registration) {
    MB.toast("A new version is available.", {
      persistent: true,
      actionLabel: "Refresh",
      onAction: function () {
        reloadRequested = true;
        if (registration.waiting) {
          registration.waiting.postMessage({ type: "SKIP_WAITING" });
        } else {
          window.location.reload();
        }
      },
    });
  }

  navigator.serviceWorker.addEventListener("controllerchange", function () {
    if (reloadRequested) window.location.reload();
  });

  window.addEventListener("load", function () {
    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then(function (registration) {
        if (registration.waiting) offerUpdate(registration);

        registration.addEventListener("updatefound", function () {
          const worker = registration.installing;
          if (!worker) return;
          worker.addEventListener("statechange", function () {
            if (worker.state === "installed" && navigator.serviceWorker.controller) {
              offerUpdate(registration);
            }
          });
        });
      })
      .catch(function () {
        // The site remains a normal website when registration fails.
      });
  });
})();
