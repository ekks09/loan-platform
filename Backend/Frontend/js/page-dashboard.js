/**
 * Customer dashboard.
 *
 * The server owns every financial fact on this page. This module renders what
 * the API returned, polls the saved payment record while a prompt is open, and
 * never promotes a payment to "paid" from a browser callback.
 */
(function () {
  "use strict";

  const MB = window.MB;
  const Auth = window.Auth;
  const Loan = window.Loan;
  if (!MB || !Auth || !Loan) return;

  const body = document.body;
  const MIN_AMOUNT = Number(body.getAttribute("data-min-amount")) || 100;
  // Starts at the product maximum; the loyalty panel narrows it to this
  // borrower's tier limit once the summary arrives. The API enforces the
  // same ceiling, so this is a convenience check, not the control.
  let MAX_AMOUNT = Number(body.getAttribute("data-max-amount")) || 60000;
  const FEE_MODE = body.getAttribute("data-fee-mode") || "disabled";
  const TERMS_VERSION = body.getAttribute("data-terms-version") || "";
  const CONSENT_VERSION = body.getAttribute("data-consent-version") || "";

  function $(id) {
    return document.getElementById(id);
  }

  function displayPhone(value) {
    if (!value) return "";
    return window.KEPhone ? window.KEPhone.international(value) || value : value;
  }

  let profile = null;
  let activeLoan = null;
  let historyPage = 1;
  let historyPages = 1;
  let quoteTimer = null;

  let paymentsCache = [];
  let loansCache = [];
  let ledgerFilter = "all";
  let ledgerTruncated = false;
  let ledgerLoansLoaded = false;
  let receiptRecord = null;

  const STATUS_CHIP = {
    PENDING: "chip-warn",
    APPROVED: "chip-info",
    DISBURSEMENT_PENDING: "chip-warn",
    DISBURSED: "chip-success",
    REPAID: "chip-success",
    REVERSED: "chip-danger",
    REJECTED: "chip-danger",
    EXPIRED: "chip-danger",
    CANCELLED: "chip-danger",
    FAILED: "chip-danger",
  };

  const STATUS_LABEL = {
    PENDING: "Submitted",
    APPROVED: "Approved",
    DISBURSEMENT_PENDING: "Disbursement pending",
    DISBURSED: "Disbursed",
    REPAID: "Repaid",
    REVERSED: "Reversed",
    REJECTED: "Rejected",
    EXPIRED: "Offer expired",
    CANCELLED: "Cancelled",
    FAILED: "Failed",
  };

  function labelFor(status, fallback) {
    return fallback || STATUS_LABEL[status] || status || "Unknown";
  }

  function chipClass(status) {
    return "chip " + (STATUS_CHIP[status] || "chip-neutral");
  }

  function setChip(element, status) {
    if (!element) return;
    element.textContent = labelFor(status);
    element.className = chipClass(status);
  }

  function syncNetworkButtons() {
    const offline = navigator.onLine === false;
    MB.$$("[data-requires-network]").forEach(function (button) {
      if (offline) {
        if (!button.disabled) {
          button.dataset.networkDisabled = "1";
          button.disabled = true;
        }
      } else if (button.dataset.networkDisabled) {
        button.disabled = false;
        delete button.dataset.networkDisabled;
      }
    });
  }

  document.addEventListener("mb:offline", syncNetworkButtons);

  function applyBorrowLimit(limit, tier) {
    const value = Number(limit);
    if (!Number.isFinite(value) || value <= MIN_AMOUNT || value === MAX_AMOUNT) return;
    MAX_AMOUNT = value;
    const hint = $("amountHint");
    if (hint) {
      hint.textContent =
        "Between " + MB.money(MIN_AMOUNT) + " and " + MB.money(value) +
        (tier === "GOLD"
          ? " — your Gold limit."
          : " — three on-time repayments raise this to " + MB.money(60000) + ".");
    }
    const copy = $("applyLimitCopy");
    if (copy) copy.textContent = MB.money(value);
    const input = $("amount");
    if (input && Number(input.value) > value) input.value = "";
  }

  document.addEventListener("mb:borrow-limit", function (event) {
    const detail = event.detail || {};
    applyBorrowLimit(detail.max, detail.tier);
  });
  document.addEventListener("mb:online", syncNetworkButtons);
  syncNetworkButtons();

  window.addEventListener("online", function () {
    loadPayments().catch(function () {});
  });

  // ------------------------------------------------------------------ auth

  function redirectToLogin() {
    Auth.logout();
    window.location.assign("/login/");
  }

  function bindLogout() {
    ["logoutBtn", "accountLogout"].forEach(function (id) {
      const button = $(id);
      if (button) {
        button.addEventListener("click", function () {
          Auth.logout();
          window.location.assign("/login/");
        });
      }
    });
    MB.$$("[data-mobile-logout]").forEach(function (button) {
      button.addEventListener("click", function () {
        Auth.logout();
        window.location.assign("/login/");
      });
    });
  }

  // --------------------------------------------------------------- profile

  async function loadProfile() {
    profile = await Auth.me();
    MB.setText(
      $("headerHint"),
      displayPhone(profile.phone) + " · member since " + MB.dateOnly(profile.created_at)
    );
    MB.setText($("accountPhone"), displayPhone(profile.phone) || "—");
    MB.setText($("accountId"), profile.national_id_masked || "—");
    MB.setText($("memberSince"), MB.dateOnly(profile.created_at));
    MB.setText($("sidebarPhone"), profile.phone || "Signed in");
    const avatar = $("sidebarAvatar");
    if (avatar) {
      const digits = String(profile.phone || "").replace(/\D/g, "");
      avatar.textContent = digits ? digits.slice(-2) : "\u2014";
    }
    const sidebarLogout = $("sidebarLogout");
    if (sidebarLogout) {
      sidebarLogout.addEventListener("click", function () {
        Auth.logout();
        window.location.assign("/login/");
      });
    }

    const chip = $("verifyChip");
    if (chip) {
      chip.textContent = profile.is_verified
        ? "Verified"
        : profile.verification_status === "rejected"
        ? "Action needed"
        : profile.has_uploaded_documents
        ? "In review"
        : "Not verified";
      chip.className =
        "chip " +
        (profile.is_verified
          ? "chip-success"
          : profile.verification_status === "rejected"
          ? "chip-danger"
          : "chip-warn");
    }

    const uploadBlock = $("uploadBlock");
    const verifiedBlock = $("verifiedBlock");
    const consentBlock = $("consentBlock");
    const notes = $("verifyNotes");
    const hint = $("verifyHint");

    if (uploadBlock) uploadBlock.hidden = true;
    if (verifiedBlock) verifiedBlock.hidden = true;
    if (consentBlock) consentBlock.hidden = Boolean(profile.consent_current);
    if (notes) notes.hidden = true;
    if (hint) hint.textContent = "";

    if (profile.is_verified) {
      if (verifiedBlock) verifiedBlock.hidden = false;
      if (hint) hint.textContent = "Your identity has been reviewed and verified.";
    } else if (profile.has_uploaded_documents) {
      if (hint) {
        hint.textContent =
          "Your documents are with the review team. The status changes here when a reviewer decides. There is no guaranteed decision time.";
      }
    } else {
      if (uploadBlock) uploadBlock.hidden = false;
      if (hint) hint.textContent = "Upload all three documents to start the review.";
      if (profile.verification_status === "rejected" && notes) {
        notes.hidden = false;
        notes.textContent =
          profile.verification_notes ||
          "The documents could not be verified. Upload clearer photos and try again.";
      }
    }

    const phoneInput = $("mpesa_phone");
    if (phoneInput && !phoneInput.value && profile.phone) {
      phoneInput.value = window.KEPhone ? window.KEPhone.national(profile.phone) : profile.phone;
      if (window.KEPhone) window.KEPhone.refresh(phoneInput);
    }
    return profile;
  }

  // ------------------------------------------------------------------ loan

  async function loadLoan() {
    const loading = $("loanLoading");
    const error = $("loanError");
    if (loading) loading.hidden = false;
    if (error) error.hidden = true;

    let data;
    try {
      data = await Loan.active();
    } catch (err) {
      if (loading) loading.hidden = true;
      if (error) {
        error.hidden = false;
        MB.setText($("loanErrorText"), err.message || "Could not load your loan.");
      }
      throw err;
    }

    activeLoan = data.loan || null;
    if (loading) loading.hidden = true;

    if (activeLoan) {
      renderLoan(activeLoan, false);
      return;
    }

    const canApply = Boolean(profile && profile.is_verified && profile.consent_current);
    const latest = data.latest || null;

    if (latest) {
      renderLoan(latest, true);
      const applyBlock = $("applyBlock");
      if (applyBlock) applyBlock.hidden = !canApply;
      if (!canApply) {
        MB.setText($("loanSubtitle"), "Complete verification and consent to apply.");
      }
    } else {
      renderEmptyState(canApply);
    }
  }

  function renderEmptyState(canApply) {
    const detail = $("loanDetail");
    if (detail) detail.hidden = false;
    MB.setText($("loanAmount"), "No application yet");
    MB.setText($("loanFee"), "");
    const status = $("loanStatus");
    if (status) status.hidden = true;
    MB.setText(
      $("loanNext"),
      canApply
        ? "Choose an amount below. The fee is shown before you submit."
        : "Complete identity verification and record consent before applying."
    );
    ["loanRejection", "offerBox", "payBox", "disbursementBox"].forEach(function (id) {
      const element = $(id);
      if (element) element.hidden = true;
    });
    const timeline = $("timeline");
    if (timeline) {
      timeline.textContent = "";
      const item = document.createElement("li");
      item.className = "timeline-empty";
      item.textContent = "No activity yet.";
      timeline.appendChild(item);
    }
    const applyBlock = $("applyBlock");
    if (applyBlock) applyBlock.hidden = !canApply;
    MB.setText($("loanSubtitle"), "The latest state from our records.");
  }

  function renderLoan(loan, readOnly) {
    const detail = $("loanDetail");
    if (detail) detail.hidden = false;
    const status = $("loanStatus");
    if (status) {
      status.hidden = false;
      status.className = chipClass(loan.status);
      status.textContent = loan.status_label || labelFor(loan.status);
    }

    MB.setText($("loanAmount"), MB.money(loan.amount));
    MB.setText($("loanFee"), "Processing fee " + MB.money(loan.service_fee));
    MB.setText($("loanStatusText"), loan.status_label || labelFor(loan.status));
    MB.setText($("loanFeeMeta"), MB.money(loan.service_fee));
    MB.setText($("loanCreated"), MB.dateOnly(loan.created_at));
    MB.setText($("loanSubtitle"), "Application #" + loan.id);

    const next = $("loanNext");
    const messages = {
      await_review:
        "Our team is reviewing your application. This page changes when a reviewer makes a decision.",
      accept_offer: "Review the offer below and accept it to continue.",
      offer_expired: "The offer expired before it was accepted. You can apply again.",
      pay_service_fee:
        "The fee is due before disbursement. The amount is confirmed by the payment provider.",
      await_disbursement:
        "Your offer is accepted. Disbursement starts only after the required checks and provider confirmation.",
      none:
        loan.status === "DISBURSED"
          ? "The provider confirmed the transfer. This is the final state for this application."
          : loan.status === "REVERSED"
          ? "The provider reversed this disbursement. Do not send money to anyone claiming otherwise; contact support."
          : "No action is needed right now.",
    };
    if (next) next.textContent = messages[loan.next_action] || "";

    const rejection = $("loanRejection");
    if (rejection) {
      rejection.hidden = !(loan.status === "REJECTED" && loan.rejection_reason);
      rejection.textContent = loan.rejection_reason || "";
    }

    const offerBox = $("offerBox");
    const offerVisible = loan.next_action === "accept_offer" && !readOnly;
    if (offerBox) offerBox.hidden = !offerVisible;
    MB.setText($("offerAmount"), MB.money(loan.amount));
    MB.setText($("offerFee"), MB.money(loan.service_fee));
    MB.setText($("offerExpiry"), MB.date(loan.offer_expires_at));
    MB.setText($("offerTerms"), loan.offer_terms_version || "—");

    const payBox = $("payBox");
    const payVisible =
      loan.next_action === "pay_service_fee" && FEE_MODE === "upfront" && !readOnly;
    if (payBox) payBox.hidden = !payVisible;
    MB.setText($("payAmount"), MB.money(loan.service_fee));
    if (loan.paystack_reference) MB.setText($("payReferenceValue"), loan.paystack_reference);
    if (payVisible) syncInlinePayState(loan);

    const disbursementBox = $("disbursementBox");
    const shown = ["DISBURSEMENT_PENDING", "DISBURSED", "FAILED", "REPAID", "REVERSED"];
    if (disbursementBox) disbursementBox.hidden = !shown.includes(loan.status);
    const disbursementText = $("disbursementText");
    if (disbursementText) {
      if (loan.status === "DISBURSED") {
        disbursementText.textContent =
          "The provider confirmed the transfer to " +
          (loan.mpesa_phone ? window.KEPhone ? window.KEPhone.national(loan.mpesa_phone) : loan.mpesa_phone : "your M-Pesa number") +
          ". " +
          (loan.disbursed_at ? "Confirmed " + MB.date(loan.disbursed_at) + "." : "");
      } else if (loan.status === "DISBURSEMENT_PENDING") {
        disbursementText.textContent =
          "The transfer is in progress. It is not complete until the provider confirms it.";
      } else if (loan.status === "FAILED") {
        disbursementText.textContent =
          "The transfer could not be completed. Support will review the failure; do not send money to anyone claiming otherwise.";
      } else if (loan.status === "REVERSED") {
        disbursementText.textContent =
          "The provider reversed this disbursement. Contact support before taking any further action.";
      } else {
        disbursementText.textContent = "This application is marked as repaid.";
      }
    }

    const applyBlock = $("applyBlock");
    if (applyBlock) applyBlock.hidden = true;
    loadTimeline(loan.id);
    syncExpress();
  }

  async function loadTimeline(loanId) {
    const list = $("timeline");
    if (!list) return;
    list.textContent = "";
    try {
      const detail = await Loan.detail(loanId);
      if (!detail.events || !detail.events.length) {
        const item = document.createElement("li");
        item.className = "timeline-empty";
        item.textContent = "No activity yet.";
        list.appendChild(item);
        return;
      }
      detail.events.forEach(function (event) {
        const item = document.createElement("li");
        const title = document.createElement("strong");
        const to = labelFor(event.to, event.to);
        title.textContent = event.reason || to;
        const meta = document.createElement("span");
        meta.className = "event-meta";
        meta.textContent = MB.date(event.created_at) + (event.to ? " · " + to : "");
        item.appendChild(title);
        item.appendChild(meta);
        list.appendChild(item);
      });
    } catch (error) {
      const item = document.createElement("li");
      item.className = "timeline-empty";
      item.textContent = "Activity could not be loaded right now.";
      list.appendChild(item);
    }
  }

  // ----------------------------------------------------------------- quote

  async function fetchQuote() {
    const input = $("amount");
    const box = $("quoteBox");
    if (!input || !box) return;
    const raw = input.value.trim();
    box.textContent = "";
    if (!/^\d+$/.test(raw)) return;
    const amount = Number(raw);
    if (amount < MIN_AMOUNT || amount > MAX_AMOUNT) {
      box.textContent =
        "Enter an amount between " + MB.money(MIN_AMOUNT) + " and " + MB.money(MAX_AMOUNT) + ".";
      return;
    }
    try {
      const quote = await Loan.quote(amount);
      const fee = MB.money(quote.service_fee);
      const rate = quote.fee_rate_percent != null ? " · " + quote.fee_rate_percent + "% band rate" : "";
      box.textContent =
        "Processing fee: " +
        fee +
        " (" +
        quote.fee_percent +
        "% effective" +
        rate +
        "). Total to repay: " +
        MB.money(Number(quote.amount) + Number(quote.service_fee)) +
        ". " +
        (quote.fee_collection_mode === "upfront"
          ? "The fee is requested only after approval and offer acceptance."
          : "No fee is collected in this environment.");
    } catch (error) {
      box.textContent = error.message || "Could not fetch a quote.";
    }
  }

  function scheduleQuote() {
    window.clearTimeout(quoteTimer);
    quoteTimer = window.setTimeout(fetchQuote, 300);
  }

  // ----------------------------------------------------------------- apply

  function bindApply() {
    const form = $("applyForm");
    if (!form) return;
    MB.$$("[data-amount]").forEach(function (button) {
      button.addEventListener("click", function () {
        const input = $("amount");
        if (!input) return;
        input.value = button.getAttribute("data-amount");
        scheduleQuote();
      });
    });
    const amount = $("amount");
    if (amount) amount.addEventListener("input", scheduleQuote);

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const message = $("applyMsg");
      const button = $("applyBtn");
      MB.setMessage(message, "", "");
      if (navigator.onLine === false) {
        MB.setMessage(message, "You are offline. Reconnect before submitting an application.", "error");
        return;
      }
      const amountValue = Number(($("amount") || {}).value);
      if (!Number.isInteger(amountValue) || amountValue < MIN_AMOUNT || amountValue > MAX_AMOUNT) {
        MB.setMessage(
          message,
          "Enter a whole amount between " + MB.money(MIN_AMOUNT) + " and " + MB.money(MAX_AMOUNT) + ".",
          "error"
        );
        return;
      }
      let phone;
      try {
        phone = Auth.normalizeKenyanPhone(($("mpesa_phone") || {}).value);
      } catch (error) {
        MB.setMessage(message, error.message, "error");
        return;
      }
      if (button) button.disabled = true;
      MB.setMessage(message, "Submitting your application…", "info");
      try {
        await Loan.apply({ amount: amountValue, mpesa_phone: phone });
        MB.setMessage(message, "Application received. Review is manual; this is not an approval.", "success");
        const amountInput = $("amount");
        if (amountInput) amountInput.value = "";
        const quote = $("quoteBox");
        if (quote) quote.textContent = "";
        await Promise.all([loadLoan(), loadHistory(1), refreshLedger()]);
        MB.announce("Application submitted. It is now awaiting review.");
      } catch (error) {
        MB.setMessage(message, error.message || "Could not submit the application.", "error");
        if (button) button.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------ accept/pay

  function bindOffer() {
    const button = $("acceptBtn");
    if (!button) return;
    button.addEventListener("click", async function () {
      if (!activeLoan) return;
      const message = $("offerMsg");
      MB.setMessage(message, "", "");
      if (navigator.onLine === false) {
        MB.setMessage(message, "You are offline. Reconnect before accepting an offer.", "error");
        return;
      }
      button.disabled = true;
      MB.setMessage(message, "Recording your acceptance…", "info");
      try {
        await Loan.acceptOffer(activeLoan.id, activeLoan.offer_terms_version || TERMS_VERSION);
        MB.setMessage(message, "Offer accepted. The status will update as the application progresses.", "success");
        await Promise.all([loadLoan(), loadHistory(1)]);
      } catch (error) {
        MB.setMessage(message, error.message || "Could not accept the offer.", "error");
        button.disabled = false;
      }
    });
  }

  // ----------------------------------------------------------- payment core

  const PAYMENT_LABEL = {
    PENDING: "Pending",
    INITIALIZED: "STK push sent",
    SUCCEEDED: "Confirmed",
    FAILED: "Failed",
    ABANDONED: "Timed out",
    REFUND_PENDING: "Refund pending",
    REFUNDED: "Refunded",
  };

  const PAYMENT_CHIP = {
    PENDING: "chip-warn",
    INITIALIZED: "chip-info",
    SUCCEEDED: "chip-success",
    FAILED: "chip-danger",
    ABANDONED: "chip-neutral",
    REFUND_PENDING: "chip-gold",
    REFUNDED: "chip-info",
  };

  const RING_CIRCUMFERENCE = 2 * Math.PI * 52;
  const PROMPT_SECONDS = 60;

  const pay = {
    reference: "",
    status: "",
    deadline: 0,
    countdownTimer: null,
    pollTimer: null,
    pollAttempts: 0,
    busy: false,
    terminal: false,
  };

  function payStateChip(state) {
    // The leading token is the lifecycle badge; the rest is plain-language
    // context. DISBURSED is shown on the loan card once a payout is confirmed.
    return {
      idle: ["NOT STARTED", "chip-neutral"],
      stk: ["PENDING · prompt sent", "chip-warn"],
      verifying: ["PROCESSED · verifying", "chip-gold"],
      success: ["CONFIRMED", "chip-success"],
      failed: ["FAILED", "chip-danger"],
      timeout: ["TIMEOUT", "chip-neutral"],
    }[state] || ["NOT STARTED", "chip-neutral"];
  }

  function setRing(secondsLeft) {
    const ratio = Math.max(0, Math.min(1, secondsLeft / PROMPT_SECONDS));
    const ring = $("payRingProgress");
    if (ring) {
      ring.style.strokeDasharray = String(RING_CIRCUMFERENCE);
      ring.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - ratio));
    }
    MB.setText($("payCountdown"), String(Math.max(0, Math.ceil(secondsLeft))));
  }

  function stopCountdown() {
    window.clearInterval(pay.countdownTimer);
    pay.countdownTimer = null;
  }

  function stopPolling() {
    window.clearTimeout(pay.pollTimer);
    pay.pollTimer = null;
  }

  function applyPayState(state, options) {
    const opts = options || {};
    const card = $("payStateCard");
    if (card) card.dataset.state = state;
    const inline = $("payInlineState");
    if (inline) inline.dataset.state = state;

    const chip = payStateChip(state);
    [["payDialogStatus", chip[0], "chip " + chip[1]], ["payStatusChip", chip[0], "chip " + chip[1]]].forEach(
      function (pair) {
        const node = $(pair[0]);
        if (!node) return;
        node.textContent = pair[1];
        node.className = pair[2];
      }
    );

    const titles = {
      idle: "Ready when you are",
      stk: "Waiting for your PIN",
      verifying: "Checking the provider",
      success: "Payment confirmed",
      failed: "The payment did not go through",
      timeout: "No confirmation yet",
    };
    const bodies = {
      idle: "Nothing has been charged. The fee is only collected after an accepted offer.",
      stk: "A prompt was sent to your phone. Enter your M-Pesa PIN, or re-trigger the prompt below.",
      verifying: "Paystack is confirming the charge. This can take a few seconds.",
      success: "Paystack confirmed the charge. The reference below is on your receipt.",
      failed: opts.body || "Paystack declined the charge. Nothing was confirmed.",
      timeout: opts.body || "The prompt was not confirmed within the window. Retry if you did not complete it.",
    };

    const copies = {
      idle: "This window tracks the prompt; it does not prove a payment.",
      stk: "Approve the M-Pesa prompt on your handset. The ring is the prompt window, not a guarantee.",
      verifying: "Paystack is checking the charge now. Keep this window open for a moment.",
      success: "Provider confirmation received. The references below are saved on your receipt.",
      failed: "No prompt is open. Retry when you are ready; nothing has been charged twice.",
      timeout: "The prompt window has closed. If you completed it, the record will still update.",
    };
    MB.setText($("payStateTitle"), opts.title || titles[state] || "");
    MB.setText($("payStateBody"), opts.body || bodies[state] || "");
    MB.setText($("payWindowCopy"), opts.copy || copies[state] || "");

    const window_ = $("payWindow");
    if (window_) window_.hidden = state === "idle" || state === "success";
    const resend = $("payResend");
    if (resend) resend.hidden = !(state === "stk" || state === "timeout" || state === "failed");
    const refs = $("payRefs");
    if (refs) refs.hidden = state !== "success";

    const steps = MB.$$("#payDialogSteps [data-pay-step]");
    const order = { idle: -1, stk: 1, verifying: 2, success: 3, failed: 2, timeout: 1 };
    const reached = order[state] != null ? order[state] : -1;
    steps.forEach(function (step, index) {
      const number = Number(step.getAttribute("data-pay-step"));
      step.className = "";
      if (state === "failed" && number === 2) step.className = "is-error";
      else if (state === "success") step.className = "is-done";
      else if (number <= reached) step.className = "is-done";
      else if (number === reached + 1 && (state === "stk" || state === "verifying")) step.className = "is-active";
    });

    const start = $("payDialogStart");
    if (start) {
      start.disabled = pay.busy || state === "success" || state === "verifying";
      start.hidden = state === "stk" || state === "success" || state === "verifying";
    }
    const check = $("payCheckStatus");
    if (check) check.hidden = !(state === "stk" || state === "verifying" || state === "timeout");
    const inlineBtn = $("payBtn");
    if (inlineBtn) {
      inlineBtn.disabled = pay.busy || state === "success" || FEE_MODE !== "upfront";
    }
    MB.setText(
      $("payStatus"),
      state === "success"
        ? "Confirmed by the provider. The fee is recorded against this application."
        : state === "failed"
        ? "No money was confirmed. You can retry."
        : state === "timeout"
        ? "The prompt was not confirmed in time. Check status or retry."
        : state === "verifying"
        ? "Verifying with Paystack…"
        : state === "stk"
        ? "Prompt sent. Approve it on your phone; this page updates when the provider confirms."
        : "The provider confirms this payment, not the page. Keep your phone nearby."
    );
  }

  function syncInlinePayState(loan) {
    if (!loan) return;
    const dialog = $("payDialog");
    // Only mirror a saved record while the payment window is open; otherwise a
    // stale "prompt sent" badge would imply a live prompt that is long gone.
    if (!dialog || !dialog.open) return;
    const existing = (paymentsCache || []).find(function (row) {
      return row.reference === loan.paystack_reference;
    });
    if (existing) mapPaymentToState(existing);
  }

  function failureCode(payment) {
    const text = String((payment && (payment.failure_reason || payment.gateway_response)) || "").toLowerCase();
    if (/insufficient|balance/.test(text)) return "Insufficient Funds";
    if (/cancel|abandon|closed/.test(text)) return "User Cancelled";
    if (/timeout|timed out|expired|no response/.test(text)) return "STK Timeout";
    if (/declin|fail|error/.test(text)) return "Charge Declined";
    return "";
  }

  function mapPaymentToState(payment) {
    if (!payment) return;
    pay.reference = payment.reference;
    pay.status = payment.status;
    if (payment.status === "SUCCEEDED") {
      stopCountdown();
      pay.terminal = true;
      MB.setText($("payProviderRef"), payment.provider_reference || payment.reference || "—");
      const mpesa = mpesaCode(payment);
      MB.setText($("payMpesaRef"), mpesa || "— (not returned)");
      applyPayState("success", {});
    } else if (payment.status === "FAILED") {
      stopCountdown();
      pay.terminal = true;
      const code = failureCode(payment);
      applyPayState("failed", {
        title: code ? "Payment failed — " + code : "The payment did not go through",
        body:
          (payment.failure_reason || payment.gateway_response || "Paystack declined the charge.") +
          " Nothing was confirmed; you can retry.",
      });
    } else if (payment.status === "ABANDONED") {
      stopCountdown();
      pay.terminal = true;
      applyPayState("timeout", { title: "Payment timed out — STK Timeout" });
    } else if (payment.status === "REFUND_PENDING" || payment.status === "REFUNDED") {
      stopCountdown();
      pay.terminal = true;
      applyPayState("success", {
        title: payment.status === "REFUNDED" ? "Refunded by the provider" : "Refund pending",
        body: "The provider confirmed the refund state. The reference below is on your record.",
      });
    } else if (payment.status === "INITIALIZED" || payment.status === "PENDING") {
      const dialog = $("payDialog");
      if (!pay.terminal && dialog && dialog.open) applyPayState("stk", {});
    }
  }

  function mpesaCode(payment) {
    const candidates = [payment && payment.gateway_response, payment && payment.provider_transaction_id];
    for (const value of candidates) {
      const text = String(value || "").trim().toUpperCase();
      // M-Pesa receipts are 10 characters, e.g. QK12ABC34D.
      if (/^[A-Z]{2,3}[A-Z0-9]{7,8}$/.test(text) && !/^(SUCCESS|APPROVED)/.test(text)) return text;
    }
    return "";
  }

  function maskPhone(phone) {
    const digits = String(phone || "").replace(/\D/g, "");
    if (digits.length < 6) return "the registered number";
    return "+" + digits.slice(0, 3) + " \u2022\u2022\u2022 \u2022\u2022\u2022 " + digits.slice(-3);
  }

  function resetPayDialog() {
    stopCountdown();
    stopPolling();
    pay.reference = "";
    pay.status = "";
    pay.terminal = false;
    pay.busy = false;
    pay.pollAttempts = 0;
    MB.setText(
      $("payDialogMessage"),
      "Choose “Pay with M-Pesa” to start. Paystack asks for the M-Pesa number and sends the prompt to it."
    );
    const error = $("payDialogError");
    if (error) {
      error.hidden = true;
      error.textContent = "";
    }
    const refs = $("payRefs");
    if (refs) refs.hidden = true;
    setRing(PROMPT_SECONDS);
    applyPayState("idle", {});
  }

  function openPayDialog() {
    const dialog = $("payDialog");
    if (!dialog) return;
    resetPayDialog();
    MB.setText($("payDialogAmount"), activeLoan ? MB.money(activeLoan.service_fee) : "—");
    MB.setText($("payDialogPhone"), maskPhone(profile && profile.phone));
    MB.openDialog(dialog);
  }

  function startCountdown() {
    stopCountdown();
    pay.deadline = Date.now() + PROMPT_SECONDS * 1000;
    setRing(PROMPT_SECONDS);
    pay.countdownTimer = window.setInterval(function () {
      const left = (pay.deadline - Date.now()) / 1000;
      setRing(left);
      if (left <= 0) {
        stopCountdown();
        if (!pay.terminal) {
          applyPayState("timeout", {
            body: "The 60-second prompt window ended without a confirmation. If you entered your PIN and the provider confirms later, this record updates automatically.",
          });
          pay.pollAttempts = 0;
          startPolling(pay.reference, true, false);
        }
      }
    }, 250);
  }

  function pollOnce(reference) {
    // The dedicated status route keeps polling to one small response. If it
    // is unavailable (older deployment), fall back to the history page.
    if (typeof Loan.paymentStatus === "function") {
      return Loan.paymentStatus(reference)
        .then(function (result) {
          return (result && result.payment) || null;
        })
        .catch(function () {
          return null;
        });
    }
    return Loan.payments(1).then(function (page) {
      const rows = (page && page.results) || [];
      const match = rows.find(function (row) {
        return row.reference === reference;
      });
      return match || null;
    });
  }

  function startPolling(reference, slow, verifyFirst) {
    stopPolling();
    if (!reference) return;
    const interval = slow ? 6000 : 3500;
    pay.pollAttempts += 1;

    // One provider check is enough; the rest of the loop reads the saved
    // record so a slow webhook cannot hammer the verify throttle.
    const check =
      verifyFirst
        ? Loan.verifyPayment(reference)
            .then(function (result) {
              if (result && result.payment && result.payment.status === "SUCCEEDED") {
                return result.payment;
              }
              return null;
            })
            .catch(function () {
              return null;
            })
        : Promise.resolve(null);

    check
      .then(function (direct) {
        if (direct) return direct;
        return pollOnce(reference);
      })
      .then(function (match) {
        if (match) {
          paymentsCache = mergePayment(paymentsCache, match);
          renderLedger();
          mapPaymentToState(match);
          if (["SUCCEEDED", "FAILED", "ABANDONED", "REFUNDED"].includes(match.status)) {
            stopPolling();
            refreshAfterPayment();
            return;
          }
        }
        if (pay.pollAttempts < 25) {
          pay.pollTimer = window.setTimeout(function () {
            startPolling(reference, slow);
          }, interval);
        } else {
          applyPayState(pay.terminal ? "timeout" : "verifying", {
            body: "Still waiting for the provider. You can close this window; the record updates when the webhook arrives.",
          });
        }
      });
  }

  function mergePayment(list, payment) {
    const next = list.filter(function (row) {
      return row.reference !== payment.reference;
    });
    next.unshift(payment);
    return next.slice(0, 40);
  }

  function refreshAfterPayment() {
    Promise.all([loadLoan(), loadHistory(1), refreshLedger()]).catch(function () {});
    // A confirmed payment can move a loan forward, which can change the
    // loyalty panel. The panel fetches its own data.
    document.dispatchEvent(new CustomEvent("mb:loyalty-refresh"));
  }

  function loadPaystack() {
    if (window.PaystackPop) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      const script = document.createElement("script");
      script.src = "https://js.paystack.co/v1/inline.js";
      script.async = true;
      script.onload = resolve;
      script.onerror = function () {
        reject(new Error("Could not load the payment window."));
      };
      document.head.appendChild(script);
    });
  }

  async function startPrompt(options) {
    if (!activeLoan || pay.busy) return;
    const opts = options || {};
    const start = $("payDialogStart");
    const message = $("payDialogMessage");
    const errorBox = $("payDialogError");
    if (errorBox) errorBox.hidden = true;

    if (navigator.onLine === false) {
      if (errorBox) {
        errorBox.hidden = false;
        errorBox.textContent = "You are offline. Reconnect before starting a payment.";
      }
      return;
    }

    pay.busy = true;
    pay.terminal = false;
    pay.pollAttempts = 0;
    applyPayState("verifying", {
      title: "Requesting the STK push",
      body: "Asking Paystack to send the M-Pesa prompt.",
      copy: "Keep this window open while the prompt is requested.",
    });
    MB.setText(message, "Requesting a prompt from Paystack…");

    let init;
    try {
      init = await Loan.initPayment(activeLoan.id);
    } catch (error) {
      pay.busy = false;
      const reason = error.message || "The payment could not be started. No money was taken.";
      const isDisabled = /disabled|not enabled|unavailable/i.test(reason);
      applyPayState("failed", {
        title: isDisabled ? "Payments are not enabled here" : "Could not start the payment",
        body: reason,
      });
      if (errorBox) {
        errorBox.hidden = false;
        errorBox.textContent = reason;
      }
      MB.setText(message, "No prompt was sent. You can retry.");
      if (start) start.disabled = false;
      return;
    }

    pay.reference = init.reference;
    MB.setText($("payReferenceValue"), init.reference);
    if (init.payment) paymentsCache = mergePayment(paymentsCache, init.payment);
    applyPayState("stk", {});
    startCountdown();
    startPolling(pay.reference, false, false);
    MB.setText(
      message,
      "Checkout requested. Enter the M-Pesa number and your PIN in the Paystack window; this page updates when the provider confirms."
    );
    MB.announce("M-Pesa prompt requested. Enter your PIN on your phone.");

    try {
      try {
        await loadPaystack();
      } catch (error) {
        // Paystack.checkout falls back to the authorization URL.
      }
      await window.Paystack.checkout({
        publicKey: init.paystack_public_key,
        email: init.email,
        amountKes: init.amount_kes,
        reference: init.reference,
        authorizationUrl: init.authorization_url,
        channels: ["mobile_money"],
        provider: "mpesa",
        currency: "KES",
        metadata: {
          loan_id: activeLoan.id,
          provider: "mpesa",
          purpose: "service_fee",
          phone: Auth.normalizeKenyanPhone((profile && profile.phone) || ""),
        },
      });
    } catch (error) {
      // Closing the inline window is not a failure: the prompt may already be
      // on the handset. Keep polling the saved record and say so plainly.
      MB.setText(
        message,
        "The checkout window closed. If you entered a PIN, the provider may still confirm it — status below is authoritative."
      );
    } finally {
      pay.busy = false;
      if (!pay.terminal) {
        applyPayState("stk", {});
        startPolling(pay.reference, opts.slow === true, true);
      }
    }
  }

  function bindPayment() {
    const openButton = $("payBtn");
    if (openButton) {
      openButton.addEventListener("click", function () {
        if (openButton.disabled) return;
        openPayDialog();
      });
    }

    const start = $("payDialogStart");
    if (start) {
      start.addEventListener("click", function () {
        if (start.disabled) return;
        startPrompt({});
      });
    }

    const resend = $("payResend");
    if (resend) {
      resend.addEventListener("click", function () {
        startPrompt({});
      });
    }

    const check = $("payCheckStatus");
    if (check) {
      check.addEventListener("click", function () {
        if (!pay.reference) {
          const box = $("payDialogError");
          if (box) {
            box.hidden = false;
            box.textContent = "Start a payment first; there is no reference to check yet.";
          }
          return;
        }
        pay.pollAttempts = 0;
        applyPayState("verifying", {});
        startPolling(pay.reference, false, true);
      });
    }

    const dialog = $("payDialog");
    if (dialog) {
      dialog.addEventListener("close", function () {
        stopCountdown();
        stopPolling();
        pay.busy = false;
      });
    }

    const expressPay = $("expressPay");
    if (expressPay) {
      expressPay.addEventListener("click", function () {
        closeSheet($("expressSheet"));
        if (activeLoan && activeLoan.next_action === "pay_service_fee" && FEE_MODE === "upfront") {
          openPayDialog();
        } else {
          MB.toast("No fee is due right now.", { kind: "info" });
        }
      });
    }
  }

  // -------------------------------------------------------------- documents

  function validatePhotos() {
    const files = ["doc_front", "doc_back", "doc_selfie"].map(function (id) {
      const input = $(id);
      return input && input.files ? input.files[0] : null;
    });
    if (!files.every(Boolean)) return "Select all three images.";
    for (const file of files) {
      if (file.size > 5 * 1024 * 1024) return "Each image must be 5 MB or smaller.";
      if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
        return "Use JPEG, PNG or WebP images.";
      }
    }
    return "";
  }

  function bindDocuments() {
    const form = $("docForm");
    if (!form) return;
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const message = $("docMsg");
      const button = $("docSubmit");
      const validation = validatePhotos();
      if (validation) {
        MB.setMessage(message, validation, "error");
        return;
      }
      if (navigator.onLine === false) {
        MB.setMessage(message, "You are offline. Reconnect before uploading documents.", "error");
        return;
      }
      button.disabled = true;
      MB.setMessage(message, "Uploading…", "info");
      const wrap = $("docProgressWrap");
      const fill = $("docProgressFill");
      const label = $("docProgressLabel");
      if (wrap) wrap.classList.add("active");
      if (fill) fill.style.width = "0%";
      try {
        await Auth.uploadDocuments(
          {
            id_front: $("doc_front").files[0],
            id_back: $("doc_back").files[0],
            selfie: $("doc_selfie").files[0],
          },
          function (percent) {
            if (fill) fill.style.width = percent + "%";
            if (label) label.textContent = "Uploading… " + percent + "%";
          }
        );
        MB.setMessage(message, "Documents uploaded. Review is manual; this is not an approval.", "success");
        form.reset();
        await Promise.all([loadProfile(), loadLoan()]);
      } catch (error) {
        MB.setMessage(message, error.message || "Upload failed. Try again.", "error");
      } finally {
        button.disabled = false;
        if (wrap) wrap.classList.remove("active");
      }
    });
  }

  function bindConsent() {
    const button = $("consentBtn");
    if (!button) return;
    button.addEventListener("click", async function () {
      button.disabled = true;
      try {
        await Auth.grantConsent(CONSENT_VERSION);
        await Promise.all([loadProfile(), loadLoan()]);
        MB.toast("Consent recorded.", { kind: "success" });
      } catch (error) {
        MB.toast(error.message || "Could not record consent.", { kind: "error" });
      } finally {
        button.disabled = false;
      }
    });
  }

  // --------------------------------------------------------------- history

  function cell(row, text, label) {
    const td = document.createElement("td");
    td.textContent = text;
    if (label) td.setAttribute("data-label", label);
    row.appendChild(td);
    return td;
  }

  async function loadHistory(page) {
    const bodyElement = $("historyBody");
    if (!bodyElement) return;
    try {
      const data = await Loan.history(page);
      historyPage = data.page || 1;
      historyPages = data.pages || 1;
      bodyElement.textContent = "";

      if (!data.results || !data.results.length) {
        const row = bodyElement.insertRow();
        const td = row.insertCell();
        td.colSpan = 5;
        td.className = "muted";
        td.textContent = "No applications yet.";
      } else {
        data.results.forEach(function (loan) {
          const row = bodyElement.insertRow();
          cell(row, MB.dateOnly(loan.created_at), "Date");
          cell(row, MB.number(loan.amount), "Amount (KES)");
          cell(row, MB.number(loan.service_fee), "Fee (KES)");
          const statusCell = row.insertCell();
          statusCell.setAttribute("data-label", "Status");
          const chip = document.createElement("span");
          chip.className = chipClass(loan.status);
          chip.textContent = loan.status_label || labelFor(loan.status);
          statusCell.appendChild(chip);
          cell(row, loan.last_event || "—", "Last update");
          collectLoan(loan);
        });
        renderLedger();
      }

      const pager = $("historyPager");
      if (pager) pager.hidden = historyPages <= 1;
      MB.setText($("pageInfo"), "Page " + historyPage + " of " + historyPages);
      const prev = $("prevPage");
      const next = $("nextPage");
      if (prev) prev.disabled = historyPage <= 1;
      if (next) next.disabled = historyPage >= historyPages;
      syncNetworkButtons();
    } catch (error) {
      bodyElement.textContent = "";
      const row = bodyElement.insertRow();
      const td = row.insertCell();
      td.colSpan = 5;
      td.className = "muted";
      td.textContent = "Could not load history: " + (error.message || "unknown error");
    }
  }

  // ------------------------------------------------------- ledger + receipts

  function collectLoan(loan) {
    if (!loan) return;
    loansCache = loansCache.filter(function (row) {
      return row.id !== loan.id;
    });
    loansCache.push(loan);
  }

  async function loadPayments() {
    const bodyElement = $("paymentsBody");
    if (!bodyElement) return;
    const collected = [];
    let page = 1;
    let pages = 1;
    ledgerTruncated = false;
    try {
      do {
        const data = await Loan.payments(page);
        pages = data.pages || 1;
        (data.results || []).forEach(function (row) {
          collected.push(row);
        });
        page += 1;
      } while (page <= pages && page <= 3);
      if (pages > 3) ledgerTruncated = true;

      paymentsCache = collected;
      renderLedger();
      const loanRef = activeLoan && activeLoan.paystack_reference;
      if (loanRef) {
        const match = paymentsCache.find(function (row) {
          return row.reference === loanRef;
        });
        if (match) mapPaymentToState(match);
      }
    } catch (error) {
      bodyElement.textContent = "";
      const row = bodyElement.insertRow();
      const td = row.insertCell();
      td.colSpan = 5;
      td.className = "muted";
      td.textContent = "Could not load payments: " + (error.message || "unknown error");
    }
  }

  function ledgerRows() {
    const rows = [];
    paymentsCache.forEach(function (payment) {
      rows.push({
        kind: payment.purpose === "repayment" ? "repayment" : "payment",
        date: payment.paid_at || payment.created_at,
        reference: payment.reference,
        amount: payment.amount_kes,
        status: payment.status,
        label: PAYMENT_LABEL[payment.status] || payment.status,
        chip: "chip " + (PAYMENT_CHIP[payment.status] || "chip-neutral"),
        payment: payment,
      });
    });
    loansCache.forEach(function (loan) {
      if (!["DISBURSED", "REPAID", "REVERSED"].includes(loan.status)) return;
      rows.push({
        kind: "disbursement",
        date: loan.disbursed_at || loan.updated_at,
        reference: "Loan #" + loan.id,
        amount: loan.amount,
        status: loan.status,
        label: STATUS_LABEL[loan.status] || loan.status,
        chip: chipClass(loan.status),
        loan: loan,
      });
    });
    return rows.sort(function (a, b) {
      return new Date(b.date || 0) - new Date(a.date || 0);
    });
  }

  function renderLedger() {
    const bodyElement = $("paymentsBody");
    if (!bodyElement) return;
    const rows = ledgerRows().filter(function (row) {
      if (ledgerFilter === "all") return true;
      return row.kind === ledgerFilter;
    });

    bodyElement.textContent = "";
    const note = $("ledgerNote");
    const repaymentNote = $("repaymentNote");
    if (repaymentNote) repaymentNote.hidden = true;

    if (!rows.length) {
      const row = bodyElement.insertRow();
      const td = row.insertCell();
      td.colSpan = 5;
      td.className = "muted";
      td.textContent =
        ledgerFilter === "repayment"
          ? "No repayments recorded. Repayment plans are not implemented in this environment."
          : ledgerFilter === "disbursement"
          ? "No disbursement has been confirmed on your account."
          : "No payments or disbursements yet.";
      if (note) {
        note.hidden = ledgerFilter !== "repayment";
        if (ledgerFilter === "repayment") {
          note.textContent =
            "Repayment collection is not part of this build, so this filter stays empty on purpose. Nothing is simulated.";
        }
      }
      return;
    }

    if (note) {
      note.hidden = !ledgerTruncated;
      note.textContent = ledgerTruncated
        ? "Showing the 30 most recent records. Older entries remain on the server."
        : "";
    }

    rows.forEach(function (record) {
      const row = bodyElement.insertRow();
      cell(row, MB.dateOnly(record.date), "Date");
      const ref = cell(row, record.reference, "Reference");
      ref.className = "mono";
      cell(row, MB.number(record.amount), "Amount (KES)");
      const statusCell = row.insertCell();
      statusCell.setAttribute("data-label", "Status");
      const chip = document.createElement("span");
      chip.className = record.chip;
      chip.textContent = record.label;
      statusCell.appendChild(chip);
      const actionCell = row.insertCell();
      actionCell.setAttribute("data-label", "Receipt");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "link-button";
      button.textContent = record.kind === "disbursement" ? "Details" : "Receipt";
      button.addEventListener("click", function () {
        openReceipt(record);
      });
      actionCell.appendChild(button);
    });
  }

  function openReceipt(record) {
    const dialog = $("receiptDialog");
    if (!dialog) return;
    receiptRecord = record;

    const payment = record.payment || null;
    const loan = record.loan || (payment && payment.loan_id ? { id: payment.loan_id } : null);

    MB.setText($("receiptTitle"), record.kind === "disbursement" ? "Disbursement record" : "Payment receipt");
    MB.setText($("receiptAmount"), MB.money(record.amount));

    const statusNode = $("receiptStatus");
    if (statusNode) {
      statusNode.textContent = record.label;
      statusNode.className = record.chip;
    }
    MB.setText($("receiptStatusText"), record.label);
    MB.setText(
      $("receiptPurpose"),
      record.kind === "disbursement"
        ? "Loan disbursement"
        : payment && payment.purpose === "repayment"
        ? "Loan repayment"
        : "Processing fee"
    );
    MB.setText($("receiptLoan"), loan && loan.id ? "Application #" + loan.id : "—");

    // The fee breakdown is only shown when the matching loan is on this page;
    // otherwise the row stays empty rather than guessing a principal.
    const knownLoan = loan && loan.id
      ? loansCache.find(function (row) { return row.id === loan.id; })
      : null;
    MB.setText($("receiptPrincipal"), knownLoan ? MB.money(knownLoan.amount) : "—");
    MB.setText($("receiptFee"), knownLoan ? MB.money(knownLoan.service_fee) : "—");

    if (loan && loan.id && !knownLoan) {
      // Older payments may reference a loan outside the current history page.
      // Fetch it once, then fill the breakdown without blocking the dialog.
      Loan.detail(loan.id)
        .then(function (detail) {
          if (!detail || !detail.loan) return;
          collectLoan(detail.loan);
          if ($("receiptDialog") && $("receiptDialog").open && receiptRecord === record) {
            MB.setText($("receiptPrincipal"), MB.money(detail.loan.amount));
            MB.setText($("receiptFee"), MB.money(detail.loan.service_fee));
          }
        })
        .catch(function () {});
    }
    MB.setText($("receiptReference"), payment ? payment.provider_reference || payment.reference : "—");
    MB.setText($("receiptMpesa"), payment ? mpesaCode(payment) || "— (not returned by provider)" : "—");
    MB.setText($("receiptChannel"), payment ? payment.channel || "—" : "M-Pesa");
    MB.setText($("receiptDate"), MB.date(payment ? payment.paid_at : record.date));
    MB.setText($("receiptCreated"), MB.date(payment ? payment.created_at : record.date));

    const failure = $("receiptFailure");
    if (failure) {
      const hasFailure = Boolean(payment && (payment.failure_reason || payment.status === "FAILED"));
      failure.hidden = !hasFailure;
      failure.textContent = hasFailure
        ? "Provider note: " + (payment.failure_reason || payment.gateway_response || "charge declined") + "."
        : "";
    }

    MB.openDialog(dialog);
  }

  function bindReceipt() {
    const print = $("receiptPrint");
    if (print) {
      print.addEventListener("click", function () {
        if (!receiptRecord) return;
        document.body.classList.add("is-printing-receipt");
        const cleanup = function () {
          document.body.classList.remove("is-printing-receipt");
          window.removeEventListener("afterprint", cleanup);
        };
        window.addEventListener("afterprint", cleanup);
        window.print();
        window.setTimeout(cleanup, 1500);
      });
    }
  }

  // ------------------------------------------------------------------ copy

  async function copyText(text) {
    if (!text) return false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (error) {
      // Fall through to the selection-based fallback.
    }
    try {
      const field = document.createElement("textarea");
      field.value = text;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(field);
      return ok;
    } catch (error) {
      return false;
    }
  }

  function bindCopy() {
    MB.$$("[data-copy-target]").forEach(function (button) {
      button.addEventListener("click", async function () {
        const source = $(button.getAttribute("data-copy-target"));
        const value = source ? source.textContent.trim() : "";
        if (!value || value === "—") {
          MB.toast("Nothing to copy yet.", { kind: "info" });
          return;
        }
        const ok = await copyText(value);
        MB.toast(ok ? "Copied to clipboard!" : "Copy is blocked in this browser.", {
          kind: ok ? "success" : "error",
        });
      });
    });
  }

  // --------------------------------------------------------- express sheet

  let sheetReturnFocus = null;

  function openSheet(sheet) {
    if (!sheet) return;
    sheetReturnFocus = document.activeElement;
    sheet.hidden = false;
    document.body.classList.add("sheet-open");
    const panel = sheet.querySelector(".sheet-panel");
    window.requestAnimationFrame(function () {
      sheet.classList.add("is-open");
    });
    if (panel) {
      const first = panel.querySelector("button, a");
      if (first) first.focus({ preventScroll: true });
    }
  }

  function closeSheet(sheet) {
    if (!sheet) return;
    sheet.classList.remove("is-open");
    document.body.classList.remove("sheet-open");
    window.setTimeout(function () {
      sheet.hidden = true;
    }, 180);
    if (sheetReturnFocus && typeof sheetReturnFocus.focus === "function") {
      sheetReturnFocus.focus({ preventScroll: true });
    }
    sheetReturnFocus = null;
  }

  function syncExpress() {
    const payHint = $("expressPayHint");
    const disbHint = $("expressDisbursementHint");
    const canPay = Boolean(activeLoan && activeLoan.next_action === "pay_service_fee" && FEE_MODE === "upfront");
    if (payHint) {
      payHint.textContent = canPay
        ? "Fee " + MB.money(activeLoan.service_fee) + " · M-Pesa prompt"
        : "Available after an offer is accepted.";
    }
    if (disbHint) {
      disbHint.textContent =
        activeLoan && activeLoan.disbursed_at
          ? "Confirmed " + MB.dateOnly(activeLoan.disbursed_at)
          : activeLoan && activeLoan.status === "DISBURSEMENT_PENDING"
          ? "Transfer in progress."
          : "No disbursement recorded yet.";
    }
  }

  function bindSheet() {
    MB.$$("[data-sheet-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        openSheet($(button.getAttribute("data-sheet-open")));
      });
    });
    const sheet = $("expressSheet");
    if (!sheet) return;
    MB.$$("[data-sheet-close]", sheet).forEach(function (button) {
      button.addEventListener("click", function () {
        closeSheet(sheet);
      });
    });
    document.addEventListener("keydown", function (event) {
      if (sheet.hidden) return;
      if (event.key === "Escape") {
        closeSheet(sheet);
        return;
      }
      if (event.key !== "Tab") return;
      // Keep focus inside the sheet while it is open.
      const focusables = MB.$$('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])', sheet)
        .filter(function (node) {
          return !node.disabled && node.offsetParent !== null;
        });
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    const receipts = $("expressReceipts");
    if (receipts) {
      receipts.addEventListener("click", function () {
        closeSheet(sheet);
        const target = $("payments");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    const disbursement = $("expressDisbursement");
    if (disbursement) {
      disbursement.addEventListener("click", function () {
        closeSheet(sheet);
        if (!activeLoan || (!activeLoan.disbursed_at && activeLoan.status !== "DISBURSEMENT_PENDING")) {
          MB.toast("No disbursement is recorded yet.", { kind: "info" });
          return;
        }
        const target = $("disbursementBox");
        if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    }
  }

  // ------------------------------------------------------------------- init

  function bindPager() {
    const prev = $("prevPage");
    const next = $("nextPage");
    if (prev) prev.addEventListener("click", function () { loadHistory(Math.max(1, historyPage - 1)); });
    if (next) next.addEventListener("click", function () { loadHistory(Math.min(historyPages, historyPage + 1)); });
  }

  function bindLedgerTabs() {
    MB.$$("[data-ledger-filter]").forEach(function (button) {
      button.addEventListener("click", function () {
        ledgerFilter = button.getAttribute("data-ledger-filter");
        if (ledgerFilter === "disbursement") loadLoansForLedger();
        MB.$$("[data-ledger-filter]").forEach(function (other) {
          const active = other === button;
          other.classList.toggle("is-active", active);
          other.setAttribute("aria-pressed", active ? "true" : "false");
        });
        renderLedger();
      });
    });
  }

  async function refreshLedger() {
    await loadPayments();
  }

  async function loadLoansForLedger() {
    if (ledgerLoansLoaded) return;
    ledgerLoansLoaded = true;
    try {
      let page = 1;
      let pages = 1;
      do {
        const data = await Loan.history(page);
        pages = data.pages || 1;
        (data.results || []).forEach(collectLoan);
        page += 1;
      } while (page <= pages && page <= 3);
      renderLedger();
    } catch (error) {
      ledgerLoansLoaded = false;
    }
  }

  async function init() {
    if (!Auth.isAuthenticated()) {
      window.location.assign("/login/");
      return;
    }
    bindLogout();
    bindApply();
    bindOffer();
    bindPayment();
    bindReceipt();
    bindDocuments();
    bindConsent();
    bindPager();
    bindLedgerTabs();
    bindCopy();
    bindSheet();

    const phoneInput = $("mpesa_phone");
    if (phoneInput && window.KEPhone) {
      window.KEPhone.attach(phoneInput, {
        badge: $("mpesaCarrier"),
        note: $("mpesaCarrierNote"),
        nonMpesaNote:
          "M-Pesa payouts need a Safaricom line. Enter a Safaricom number if this one is not on M-Pesa.",
      });
    }

    const retry = $("retryLoan");
    if (retry) {
      retry.addEventListener("click", function () {
        loadLoan().catch(function () {});
      });
    }

    try {
      await loadProfile();
    } catch (error) {
      if (error.status === 401) {
        redirectToLogin();
        return;
      }
      MB.setText($("headerHint"), "Could not load your profile: " + (error.message || "unknown error"));
    }

    try {
      await loadLoan();
    } catch (error) {
      // loadLoan renders its own error state.
    }
    loadHistory(1);
    loadPayments();
    syncExpress();
    applyPayState("idle", {});
  }

  init();
})();
