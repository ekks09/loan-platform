/**
 * Mobile number handling for M-Biashara (Kenya +254, DR Congo +243).
 *
 * Account creation, sign-in and password reset accept every format a person
 * actually types:
 *
 *   Kenya     0712 345 678 · 0112 345 678 · 712345678 · +254 712 345 678
 *   DR Congo  0812 345 678 · 0991 234 567 · +243 812 345 678 · 00243…
 *
 * M-Pesa payouts are a Kenyan (Safaricom) service. Numbers in the DR Congo
 * market are accepted for the account, but the payout field states the
 * limitation instead of implying a payout can be sent there.
 *
 * The carrier label is inferred from the prefix purely as a convenience; it
 * never changes what the form accepts.
 */
(function () {
  "use strict";

  const KEPhone = {};

  const MARKETS = {
    KE: {
      code: "KE",
      name: "Kenya",
      dial: "254",
      prefix: "+254",
      // 254 + 9 digits
      regex: /^254(7|1)\d{8}$/,
      national: /^0[71]\d{8}$/,
      bare: /^[71]\d{8}$/,
    },
    CD: {
      code: "CD",
      name: "DR Congo",
      dial: "243",
      prefix: "+243",
      regex: /^243[89]\d{8}$/,
      national: /^0[89]\d{8}$/,
      bare: /^[89]\d{8}$/,
    },
  };

  // Kenyan significant prefixes (first three digits after the leading 0).
  const CARRIERS = [
    {
      name: "Safaricom",
      tone: "safaricom",
      mpesa: true,
      ranges: [
        [110, 115],
        [700, 729],
        [740, 749],
        [750, 759],
        [760, 762],
        [764, 769],
        [790, 799],
      ],
    },
    {
      name: "Airtel",
      tone: "airtel",
      mpesa: false,
      ranges: [
        [100, 106],
        [730, 739],
        [780, 789],
      ],
    },
    {
      name: "Telkom",
      tone: "telkom",
      mpesa: false,
      ranges: [[770, 779]],
    },
    {
      name: "Equitel",
      tone: "equitel",
      mpesa: false,
      ranges: [[763, 763]],
    },
  ];

  /**
   * Return canonical digits (``254712345678`` or ``243812345678``) or null.
   *
   * @param {string} value
   * @param {{markets?: string[]}} [options]
   */
  KEPhone.normalize = function (value, options) {
    const allowed = (options && options.markets) || ["KE", "CD"];
    let raw = String(value == null ? "" : value)
      .trim()
      .replace(/[\s\-().]/g, "");
    if (!raw) return null;
    if (raw.charAt(0) === "+") raw = raw.slice(1);
    else if (raw.indexOf("00") === 0) raw = raw.slice(2);
    if (!/^\d+$/.test(raw)) return null;

    for (const code of allowed) {
      const market = MARKETS[code];
      if (!market) continue;
      if (market.regex.test(raw)) return raw;
    }

    // National (0 + 9) and bare (9) forms are resolved by their block.
    const candidates = [];
    if (/^0\d{9}$/.test(raw)) {
      const national = raw.slice(1);
      if (/^[71]/.test(national)) candidates.push("254" + national);
      if (/^[89]/.test(national)) candidates.push("243" + national);
    } else if (/^[7891]\d{8}$/.test(raw)) {
      if (/^[71]/.test(raw)) candidates.push("254" + raw);
      if (/^[89]/.test(raw)) candidates.push("243" + raw);
    }

    for (const candidate of candidates) {
      const market = MARKETS[candidate.slice(0, 3) === "254" ? "KE" : "CD"];
      if (market && allowed.includes(market.code) && market.regex.test(candidate)) {
        return candidate;
      }
    }
    return null;
  };

  KEPhone.isValid = function (value, options) {
    return KEPhone.normalize(value, options) !== null;
  };

  KEPhone.market = function (canonical) {
    const value = String(canonical || "");
    if (MARKETS.KE.regex.test(value)) return MARKETS.KE;
    if (MARKETS.CD.regex.test(value)) return MARKETS.CD;
    return null;
  };

  /** "+254 712 345 678" or "+243 812 345 678" */
  KEPhone.international = function (canonical) {
    const value = String(canonical || "");
    if (!/^\d{12}$/.test(value)) return "";
    return (
      "+" + value.slice(0, 3) + " " + value.slice(3, 6) + " " +
      value.slice(6, 9) + " " + value.slice(9, 12)
    );
  };

  /** "0712 345 678" — what most Kenyan forms expect. */
  KEPhone.national = function (canonical) {
    const value = String(canonical || "");
    if (!/^\d{12}$/.test(value)) return "";
    return (
      "0" + value.slice(3, 6) + " " + value.slice(6, 9) + " " +
      value.slice(9, 12)
    );
  };

  KEPhone.carrier = function (canonical) {
    const value = String(canonical || "");
    if (!/^\d{12}$/.test(value)) return null;
    const market = KEPhone.market(value);
    if (!market) return null;
    if (market.code === "CD") {
      return { name: "DR Congo mobile", tone: "drc", mpesa: false };
    }
    const prefix = Number(value.slice(3, 6));
    if (!Number.isFinite(prefix)) return null;
    for (const carrier of CARRIERS) {
      for (const range of carrier.ranges) {
        if (prefix >= range[0] && prefix <= range[1]) {
          return { name: carrier.name, tone: carrier.tone, mpesa: carrier.mpesa };
        }
      }
    }
    return null;
  };

  /**
   * Attach live feedback to an input.
   *
   * @param {HTMLInputElement} input
   * @param {{badge?: HTMLElement, note?: HTMLElement, markets?: string[],
   *          nonMpesaNote?: string, rewrite?: boolean}} options
   */
  KEPhone.attach = function (input, options) {
    if (!input) return;
    const opts = options || {};
    if (input.dataset.phoneReady === "1" && !opts.markets) return;
    input.dataset.phoneReady = "1";
    const badge = opts.badge || null;
    const note = opts.note || null;
    const markets = opts.markets || ["KE"];
    const nonMpesaNote =
      opts.nonMpesaNote ||
      "M-Pesa prompts are sent to Safaricom lines. You can still create the account with this number; a Safaricom line is needed later to receive a payout.";

    function render(rewrite) {
      const canonical = KEPhone.normalize(input.value, { markets: markets });
      const empty = input.value.trim() === "";

      if (badge) {
        badge.classList.remove(
          "is-safaricom", "is-airtel", "is-telkom", "is-equitel", "is-drc", "is-invalid", "is-neutral"
        );
        if (empty) {
          badge.textContent = "";
          badge.hidden = true;
        } else if (!canonical) {
          badge.hidden = false;
          badge.classList.add("is-invalid");
          badge.textContent = markets.indexOf("CD") >= 0
            ? "Keep typing — 07…/01… (Kenya) or 08…/09… (DR Congo)"
            : "Keep typing — Kenyan mobile numbers have 9 digits after the prefix";
        } else {
          const carrier = KEPhone.carrier(canonical);
          const market = KEPhone.market(canonical);
          badge.hidden = false;
          badge.classList.add(carrier ? "is-" + carrier.tone : "is-neutral");
          badge.textContent =
            (carrier ? carrier.name + " · " : (market ? market.name + " · " : "Mobile · ")) +
            KEPhone.national(canonical);
        }
      }

      if (note) {
        const carrier = canonical ? KEPhone.carrier(canonical) : null;
        if (canonical && carrier && !carrier.mpesa) {
          note.hidden = false;
          note.textContent = nonMpesaNote;
        } else {
          note.hidden = true;
          note.textContent = "";
        }
      }

      if (canonical) input.removeAttribute("aria-invalid");

      if (rewrite && opts.rewrite !== false && canonical) {
        input.value = KEPhone.national(canonical);
      }
    }

    input.addEventListener("input", function () {
      render(false);
    });
    input.addEventListener("blur", function () {
      render(true);
    });
    render(false);
  };

  /** Re-run the badge/note for a programmatically filled input. */
  KEPhone.refresh = function (input) {
    if (input) input.dispatchEvent(new Event("input", { bubbles: true }));
  };

  window.KEPhone = KEPhone;
})();
