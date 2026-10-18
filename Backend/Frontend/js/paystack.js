/**
 * Paystack checkout helper.
 *
 * Uses Paystack Inline when available. If the script cannot load (blocked
 * network, CSP, ad blocker) the user is redirected to the provider-hosted
 * authorization URL instead; fulfilment never depends on the callback firing
 * because the server-side webhook is authoritative.
 */
(function () {
  "use strict";

  const Paystack = {};

  Paystack.isAvailable = function () {
    return Boolean(window.PaystackPop && window.PaystackPop.setup);
  };

  Paystack.checkout = function (options) {
    return new Promise(function (resolve, reject) {
      const reference = String(options.reference || "");
      if (!reference) {
        reject(new Error("Missing payment reference."));
        return;
      }

      if (!Paystack.isAvailable()) {
        if (options.authorizationUrl) {
          // Leave the page; the webhook and the dashboard poll confirm payment.
          window.location.href = options.authorizationUrl;
          return;
        }
        reject(new Error("Payment window is unavailable. Please retry later."));
        return;
      }

      const amount = Number(options.amountKes);
      if (!Number.isFinite(amount) || amount <= 0) {
        reject(new Error("Invalid fee amount."));
        return;
      }

      try {
        const handler = window.PaystackPop.setup({
          key: options.publicKey,
          email: options.email,
          amount: Math.round(amount * 100),
          currency: options.currency || "KES",
          channels: options.channels || ["card", "mobile_money"],
          ref: reference,
          metadata: Object.assign({ provider: options.provider || "mpesa" }, options.metadata || {}),
          callback: function (response) {
            if (!response || !response.reference) {
              reject(new Error("Payment closed without a reference."));
              return;
            }
            resolve(response.reference);
          },
          onClose: function () {
            reject(new Error("Payment window closed before completion."));
          },
        });
        handler.openIframe();
      } catch (e) {
        reject(new Error("Could not open the payment window."));
      }
    });
  };

  window.Paystack = Paystack;
})();
