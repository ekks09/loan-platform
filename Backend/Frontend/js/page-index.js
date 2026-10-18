/**
 * Homepage fee calculator.
 *
 * The slider never computes a fee on its own: every figure is fetched from
 * the public quote endpoint, so the page cannot drift from the schedule the
 * backend actually charges. Band boundaries come from the rendered table.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-fee-calc]");
  const slider = document.getElementById("feeAmount");
  if (!root || !slider) return;

  const amountOut = document.getElementById("feeAmountOut");
  const bandOut = document.getElementById("calcBand");
  const rateOut = document.getElementById("calcRate");
  const feeOut = document.getElementById("calcFee");
  const totalOut = document.getElementById("calcTotal");

  const bands = [];
  const bandSource = document.getElementById("feeBands");
  if (bandSource) {
    bandSource.querySelectorAll("[data-min]").forEach(function (node) {
      bands.push({
        min: Number(node.getAttribute("data-min")),
        max: Number(node.getAttribute("data-max")),
        rate: Number(node.getAttribute("data-rate")),
      });
    });
  }

  function money(value) {
    return new Intl.NumberFormat("en-KE", {
      style: "currency",
      currency: "KES",
      maximumFractionDigits: 0,
    }).format(Number(value) || 0);
  }

  function set(name, text) {
    const node = document.getElementById(name);
    if (node) node.textContent = text;
  }

  function bandFor(amount) {
    return bands.find(function (band) {
      return amount >= band.min && amount <= band.max;
    }) || null;
  }

  let timer = null;
  let latest = 0;

  function renderLocal(amount) {
    if (amountOut) amountOut.textContent = money(amount);
    const band = bandFor(amount);
    if (band) {
      if (bandOut) {
        bandOut.textContent = money(band.min) + " – " + money(band.max);
      }
      if (rateOut) rateOut.textContent = band.rate.toFixed(1) + "%";
    }
  }

  function setBusy() {
    root.classList.add("is-loading");
  }

  function clearBusy() {
    root.classList.remove("is-loading");
  }

  function quote(amount) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    if (controller) {
      window.setTimeout(function () {
        controller.abort();
      }, 6000);
    }
    return fetch("/api/loans/quote/?amount=" + encodeURIComponent(amount), {
      headers: { Accept: "application/json" },
      signal: controller ? controller.signal : undefined,
    }).then(function (response) {
      if (!response.ok) throw new Error("quote");
      return response.json();
    });
  }

  function update(amount, immediate) {
    latest = amount;
    renderLocal(amount);
    window.clearTimeout(timer);
    const run = function () {
      if (amount !== latest) return;
      setBusy();
      quote(amount)
        .then(function (data) {
          if (amount !== latest) return;
          if (feeOut) feeOut.textContent = money(data.service_fee);
          if (totalOut) {
            totalOut.textContent = money(Number(data.amount) + Number(data.service_fee));
          }
          if (rateOut && data.fee_rate_percent != null) {
            rateOut.textContent = Number(data.fee_rate_percent).toFixed(1) + "%";
          }
          clearBusy();
        })
        .catch(function () {
          if (amount !== latest) return;
          if (feeOut) feeOut.textContent = "Unavailable";
          if (totalOut) totalOut.textContent = "—";
          clearBusy();
        });
    };
    if (immediate) {
      run();
    } else {
      timer = window.setTimeout(run, 180);
    }
  }

  slider.addEventListener("input", function () {
    update(Number(slider.value) || 0, false);
  });
  slider.addEventListener("change", function () {
    update(Number(slider.value) || 0, true);
  });

  // Re-price when the connection returns, and never leave stale numbers.
  window.addEventListener("online", function () {
    update(Number(slider.value) || 0, true);
  });

  update(Number(slider.value) || 0, true);
})();
