(function () {
  const Paystack = {};

  Paystack.payServiceFeeInline = function ({ paystack_public_key, email, amount_kes, reference, metadata }) {
    return new Promise((resolve, reject) => {
      if (!window.PaystackPop) return reject(new Error("Paystack script failed to load."));
      if (!paystack_public_key) return reject(new Error("Missing Paystack public key."));
      const amt = Number(amount_kes);
      if (!Number.isFinite(amt) || amt <= 0) return reject(new Error("Invalid fee amount."));

      const handler = window.PaystackPop.setup({
        key: paystack_public_key,
        email: String(email || ""),
        amount: Math.round(amt * 100), // kobo
        currency: "KES",
        ref: String(reference),
        metadata: metadata || {},
        callback: function (response) {
          // Do not mark loan complete here.
          // Backend will verify reference server-side.
          if (!response || !response.reference) {
            reject(new Error("Payment callback missing reference."));
            return;
          }
          resolve(response.reference);
        },
        onClose: function () {
          reject(new Error("Payment window closed."));
        }
      });

      try {
        handler.openIframe();
      } catch (e) {
        reject(new Error("Failed to open Paystack payment window."));
      }
    });
  };

  window.Paystack = Paystack;
})();