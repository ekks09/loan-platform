/**
 * Loyalty, savings vault and the M-Biashara credit health panel.
 *
 * Everything rendered here comes from GET /api/loyalty/summary/. The module
 * never computes a score, a tier or a cash-back amount on its own: it displays
 * the figures the server recorded and explains what each factor contributed.
 *
 * The vault only ever moves cash-back that has already been earned. No loan
 * principal is involved, and there is no yield promise in this build.
 */
(function () {
  "use strict";

  const MB = window.MB;
  const Auth = window.Auth;
  if (!MB || !Auth) return;

  const root = document.getElementById("loyaltyCard");
  if (!root) return;

  const $ = function (id) {
    return document.getElementById(id);
  };

  function money(value) {
    return MB.money(Number(value) || 0);
  }

  function tierLabel(payload) {
    return payload.tier_label || payload.tier || "Base";
  }

  function paintScore(payload) {
    const credit = payload.credit || {};
    const score = Number(credit.score) || 300;
    const min = Number(credit.min) || 300;
    const max = Number(credit.max) || 850;
    const ratio = Math.max(0, Math.min(1, (score - min) / (max - min)));

    MB.setText($("loyaltyScore"), String(score));
    MB.setText($("loyaltyScoreBand"), credit.band || "");

    const ring = $("loyaltyRing");
    if (ring) {
      const circumference = 2 * Math.PI * 46;
      ring.style.strokeDasharray = String(circumference);
      ring.style.strokeDashoffset = String(circumference * (1 - ratio));
    }
    MB.setText($("loyaltyScoreScale"), min + " – " + max);

    const list = $("loyaltyFactors");
    if (list) {
      list.textContent = "";
      (credit.factors || []).forEach(function (factor) {
        const item = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = factor.label;
        const points = document.createElement("strong");
        points.textContent = (factor.points >= 0 ? "+" : "") + factor.points;
        item.appendChild(label);
        item.appendChild(points);
        if (factor.detail) item.title = factor.detail;
        list.appendChild(item);
      });
    }
    MB.setText($("loyaltyPortability"), credit.disclosure || "");
  }

  function paintStreak(payload) {
    const wrap = $("loyaltyStreak");
    if (!wrap) return;
    const streak = Number(payload.streak) || 0;
    const target = Number(payload.streak_target) || 3;
    wrap.textContent = "";
    for (let index = 0; index < target; index += 1) {
      const dot = document.createElement("span");
      dot.className = "streak-dot" + (index < streak ? " is-filled" : "");
      dot.setAttribute("aria-hidden", "true");
      wrap.appendChild(dot);
    }
    MB.setText(
      $("loyaltyStreakLabel"),
      streak >= target
        ? "Gold tier reached"
        : streak + " of " + target + " on-time repayments"
    );
  }

  function paintVault(payload) {
    MB.setText($("loyaltyCashback"), money(payload.cashback_kes));
    MB.setText($("loyaltyVault"), money(payload.vault_kes));

    const lock = $("vaultLockBtn");
    const release = $("vaultReleaseBtn");
    const hasCashback = Number(payload.cashback_kes) > 0;
    const hasVault = Number(payload.vault_kes) > 0;
    if (lock) lock.disabled = !hasCashback;
    if (release) release.disabled = !hasVault;

    const hint = $("vaultHint");
    if (hint) {
      hint.textContent = hasCashback || hasVault
        ? "The vault holds cash-back only. No yield is accrued in this build, and it is not lent out."
        : "Cash-back appears here after an on-time repayment is recorded. Repayments are not collected in this environment, so the balance stays at zero.";
    }
  }

  function paintTier(payload) {
    const chip = $("loyaltyTier");
    if (chip) {
      chip.textContent = tierLabel(payload);
      chip.className =
        "chip " +
        (payload.tier === "GOLD"
          ? "chip-gold"
          : payload.tier === "SILVER"
          ? "chip-info"
          : "chip-neutral");
    }
    const discount = Number(payload.fee_discount_bps) || 0;
    const example = payload.fee_example || {};
    MB.setText(
      $("loyaltyBenefit"),
      discount
        ? "Your " + (discount / 100).toFixed(1) + "% fee reduction is applied to new offers: " +
          money(example.standard_fee_kes) + " becomes " + money(example.member_fee_kes) +
          " on a " + money(example.amount_kes) + " loan."
        : "Three on-time repayments unlock Gold: 1.5% off the fee on new offers and a borrowing limit up to " +
          money(payload.max_limit_kes || 60000) + "."
    );
    MB.setText($("loyaltyLimit"), money(payload.max_limit_kes));
    MB.setText($("loyaltyTierSince"), MB.dateOnly(payload.tier_since));

    // The apply form caps its input at the borrower's current limit so the
    // client-side check matches the server's. The API remains authoritative;
    // this only avoids a pointless round trip for an amount that is certain
    // to be refused.
    const limit = Number(payload.max_limit_kes);
    if (limit > 0) {
      document.dispatchEvent(
        new CustomEvent("mb:borrow-limit", { detail: { max: limit, tier: payload.tier } })
      );
    }
  }

  function paintEvents(payload) {
    const list = $("loyaltyEvents");
    if (!list) return;
    list.textContent = "";
    const events = payload.events || [];
    if (!events.length) {
      const item = document.createElement("li");
      item.className = "timeline-empty";
      item.textContent = "No loyalty activity yet. It starts with your first confirmed repayment.";
      list.appendChild(item);
      return;
    }
    events.forEach(function (event) {
      const item = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = event.note || event.kind.replace(/_/g, " ");
      const meta = document.createElement("span");
      meta.className = "event-meta";
      const amount = Number(event.amount_kes) || 0;
      meta.textContent =
        MB.date(event.created_at) +
        (amount ? " · " + (amount > 0 ? "+" : "") + money(Math.abs(amount)) : "");
      item.appendChild(title);
      item.appendChild(meta);
      list.appendChild(item);
    });
  }

  function render(payload) {
    root.hidden = false;
    paintScore(payload);
    paintTier(payload);
    paintStreak(payload);
    paintVault(payload);
    paintEvents(payload);
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      button.textContent = label || "Working…";
    } else {
      button.disabled = false;
      if (button.dataset.label) button.textContent = button.dataset.label;
    }
  }

  function message(text, kind) {
    const box = $("vaultMsg");
    if (box) MB.setMessage(box, text, kind || "");
  }

  function amount() {
    const input = $("vaultAmount");
    const value = Number(String(input && input.value ? input.value : "").replace(/\D/g, ""));
    return Number.isFinite(value) ? value : 0;
  }

  async function move(direction, button) {
    message("", "");
    if (navigator.onLine === false) {
      message("You are offline. Reconnect to move money in the vault.", "error");
      return;
    }
    const value = amount();
    if (!value || value <= 0) {
      message("Enter a whole shilling amount.", "error");
      return;
    }
    setBusy(button, true, direction === "lock" ? "Locking…" : "Releasing…");
    try {
      const payload = await Auth.api("/loyalty/vault/", {
        method: "POST",
        body: { direction: direction, amount_kes: value },
      });
      render(payload);
      const input = $("vaultAmount");
      if (input) input.value = "";
      message(
        direction === "lock"
          ? "Locked in the vault. It stays yours and adds to your credit score."
          : "Released back to your cash-back balance.",
        "success"
      );
      MB.announce("Vault updated.");
    } catch (error) {
      message(error.message || "The vault could not be updated.", "error");
    } finally {
      setBusy(button, false);
    }
  }

  async function refresh() {
    try {
      const payload = await Auth.api("/loyalty/summary/");
      render(payload);
    } catch (error) {
      const errorBox = $("loyaltyError");
      if (errorBox) {
        errorBox.hidden = false;
        errorBox.textContent = "Loyalty details could not be loaded right now.";
      }
    }
  }

  function bind() {
    const lock = $("vaultLockBtn");
    const release = $("vaultReleaseBtn");
    if (lock) {
      lock.addEventListener("click", function () {
        move("lock", lock);
      });
    }
    if (release) {
      release.addEventListener("click", function () {
        move("release", release);
      });
    }
    const input = $("vaultAmount");
    if (input) {
      input.addEventListener("input", function () {
        const digits = input.value.replace(/\D/g, "").slice(0, 6);
        if (digits !== input.value) input.value = digits;
      });
    }
    document.addEventListener("mb:loyalty-refresh", function () {
      refresh();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!Auth.isAuthenticated || !Auth.isAuthenticated()) return;
    bind();
    refresh();
  });

  window.Loyalty = { refresh: refresh };
})();
