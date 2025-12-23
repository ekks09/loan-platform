(function () {
  const Loan = {};

  Loan.formatMoney = function (n) {
    const x = Number(n);
    if (!Number.isFinite(x)) return "0";
    return x.toLocaleString("en-KE");
  };

  Loan.computeServiceFee = function (amount) {
    const a = Number(amount);
    if (!Number.isFinite(a) || Math.floor(a) !== a) throw new Error("Amount must be a whole number.");
    if (a < 1000) throw new Error("Minimum loan amount is 1,000.");
    if (a > 60000) throw new Error("Maximum loan amount is 60,000.");

    const brackets = [
      { min: 1000, max: 1000, fee: 200, label: "KES 1,000" },
      { min: 2000, max: 2000, fee: 290, label: "KES 2,000" },
      { min: 3000, max: 5000, fee: 680, label: "KES 3,000–5,000" },
      { min: 6000, max: 11000, fee: 1200, label: "KES 6,000–11,000" },
      { min: 12000, max: 22000, fee: 2200, label: "KES 12,000–22,000" },
      { min: 23000, max: 32000, fee: 3200, label: "KES 23,000–32,000" },
      { min: 33000, max: 42000, fee: 4200, label: "KES 33,000–42,000" },
      { min: 43000, max: 52000, fee: 5200, label: "KES 43,000–52,000" },
      { min: 53000, max: 60000, fee: 6000, label: "KES 53,000–60,000" },
    ];

    const b = brackets.find(x => a >= x.min && a <= x.max);
    if (!b) throw new Error("Unsupported amount.");
    return { fee: b.fee, label: b.label };
  };

  Loan.createLoan = async function ({ amount, mpesa_phone }) {
    const a = Number(amount);
    const mp = window.Auth.normalizeKenyanPhone(mpesa_phone);
    return await window.Auth.api("/loans/apply/", { method: "POST", auth: true, body: { amount: a, mpesa_phone: mp } });
  };

  Loan.getMyActiveLoan = async function () {
    return await window.Auth.api("/loans/active/", { method: "GET", auth: true });
  };

  Loan.initServiceFeePayment = async function ({ loan_id }) {
    return await window.Auth.api("/payments/init/", { method: "POST", auth: true, body: { loan_id } });
  };

  Loan.verifyServiceFeePayment = async function ({ reference }) {
    return await window.Auth.api("/payments/verify/", { method: "POST", auth: true, body: { reference } });
  };

  window.Loan = Loan;
})();
