/** Loan API wrappers and display helpers. */
(function () {
  "use strict";

  const Loan = {};

  Loan.formatMoney = function (value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return number.toLocaleString("en-KE");
  };

  Loan.statusLabel = function (loan) {
    if (!loan) return "No loan";
    return loan.status_label || loan.status || "Unknown";
  };

  Loan.quote = async function (amount) {
    return Auth.api("/loans/quote/?amount=" + encodeURIComponent(amount), { auth: false });
  };

  Loan.apply = async function (payload) {
    return Auth.api("/loans/apply/", {
      method: "POST",
      body: {
        amount: Number(payload.amount),
        mpesa_phone: Auth.normalizeKenyanPhone(payload.mpesa_phone),
      },
    });
  };

  Loan.active = async function () {
    return Auth.api("/loans/active/");
  };

  Loan.detail = async function (loanId) {
    return Auth.api("/loans/" + loanId + "/");
  };

  Loan.history = async function (page) {
    return Auth.api("/loans/history/?page=" + (page || 1));
  };

  Loan.acceptOffer = async function (loanId, termsVersion) {
    return Auth.api("/loans/" + loanId + "/accept-offer/", {
      method: "POST",
      body: { terms_version: termsVersion },
    });
  };

  Loan.cancel = async function (loanId) {
    return Auth.api("/loans/" + loanId + "/cancel/", { method: "POST", body: {} });
  };

  Loan.initPayment = async function (loanId) {
    return Auth.api("/payments/init/", { method: "POST", body: { loan_id: loanId } });
  };

  Loan.verifyPayment = async function (reference) {
    return Auth.api("/payments/verify/", { method: "POST", body: { reference: reference } });
  };

  Loan.paymentStatus = async function (reference) {
    return Auth.api("/paystack/verify/" + encodeURIComponent(reference) + "/");
  };

  Loan.payments = async function (page) {
    return Auth.api("/payments/history/?page=" + (page || 1));
  };

  window.Loan = Loan;
})();
