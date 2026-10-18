/** Login, password reset and the registration wizard. */
(function () {
  "use strict";

  const MB = window.MB;
  const Auth = window.Auth;
  if (!MB || !Auth) return;

  const body = document.body;
  const consentVersion = body.getAttribute("data-consent-version") || "";

  // Phone fields accept every Kenyan format; the badge reports the detected
  // carrier and the canonical national form without blocking Airtel or
  // Telkom numbers.
  if (window.KEPhone) {
    const pairs = [
      ["loginPhone", "loginCarrier", "loginCarrierNote"],
      ["resetPhone", "resetCarrier", "resetCarrierNote"],
      ["regPhone", "regCarrier", "regCarrierNote"],
    ];
    pairs.forEach(function (pair) {
      const input = document.getElementById(pair[0]);
      const badge = document.getElementById(pair[1]);
      const note = document.getElementById(pair[2]);
      if (input) {
        window.KEPhone.attach(input, {
          badge: badge,
          note: note,
          markets: ["KE", "CD"],
          nonMpesaNote:
            "M-Pesa payouts are only available on Kenyan lines. You can still use this account; a Safaricom number is needed later for a payout.",
        });
      }
    });
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.dataset.label = button.dataset.label || button.textContent;
      button.textContent = label || "Please wait…";
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function humanPhone(value) {
    const digits = String(value || "").replace(/\D/g, "");
    if (digits.length === 12 && digits.startsWith("254")) {
      return "0" + digits.slice(3);
    }
    return value || "—";
  }

  function onlineGuard(messageElement) {
    if (navigator.onLine === false) {
      MB.setMessage(
        messageElement,
        "You are offline. Reconnect before signing in or creating an account.",
        "error"
      );
      return false;
    }
    return true;
  }

  // ------------------------------------------------------------------ login

  const loginForm = document.getElementById("loginForm");
  if (loginForm) {
    const phone = document.getElementById("loginPhone");
    const password = document.getElementById("loginPassword");
    const button = document.getElementById("loginBtn");
    const message = document.getElementById("loginMsg");

    loginForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      MB.clearFieldErrors(loginForm);
      MB.setMessage(message, "", "");
      if (!onlineGuard(message)) return;

      let normalized;
      try {
        normalized = Auth.normalizeKenyanPhone(phone.value);
      } catch (error) {
        MB.fieldError("err-loginPhone", error.message);
        phone.setAttribute("aria-invalid", "true");
        phone.focus();
        return;
      }
      if (!password.value) {
        MB.fieldError("err-loginPassword", "Enter your password.");
        password.setAttribute("aria-invalid", "true");
        password.focus();
        return;
      }

      setBusy(button, true, "Signing in…");
      try {
        await Auth.login({ phone: normalized, password: password.value });
        MB.setMessage(message, "Signed in. Opening your dashboard…", "success");
        window.location.assign("/dashboard/");
      } catch (error) {
        if (error.status === 401) {
          MB.setMessage(message, "Phone number or password is incorrect.", "error");
        } else if (error.status === 429) {
          MB.setMessage(message, "Too many attempts. Wait a minute and try again.", "error");
        } else {
          MB.setMessage(message, error.message || "Could not sign in.", "error");
        }
        setBusy(button, false);
      }
    });

    // --------------------------------------------------------- reset flow
    const showReset = document.getElementById("showReset");
    const resetForm = document.getElementById("resetForm");
    const resetStep1 = document.getElementById("resetStep1");
    const resetStep2 = document.getElementById("resetStep2");
    const resetPhone = document.getElementById("resetPhone");
    const resetCode = document.getElementById("resetCode");
    const resetPassword = document.getElementById("resetPassword");
    const resetConfirm = document.getElementById("resetConfirm");
    const requestButton = document.getElementById("requestResetBtn");
    const submitButton = document.getElementById("resetSubmitBtn");
    const resetMessage = document.getElementById("resetMsg");
    const backToLogin = document.getElementById("backToLogin");

    function showLogin() {
      resetForm.hidden = true;
      loginForm.hidden = false;
      MB.setMessage(resetMessage, "", "");
      loginForm.querySelector("input")?.focus();
    }

    if (showReset && resetForm) {
      showReset.addEventListener("click", function () {
        loginForm.hidden = true;
        resetForm.hidden = false;
        resetStep1.hidden = false;
        resetStep2.hidden = true;
        MB.setMessage(resetMessage, "", "");
        resetPhone.focus();
      });
    }
    if (backToLogin) backToLogin.addEventListener("click", showLogin);

    if (requestButton) {
      requestButton.addEventListener("click", async function () {
        MB.setMessage(resetMessage, "", "");
        if (!onlineGuard(resetMessage)) return;
        let normalized;
        try {
          normalized = Auth.normalizePhone(resetPhone.value);
        } catch (error) {
          MB.setMessage(resetMessage, error.message, "error");
          resetPhone.focus();
          return;
        }

        // A 429 carries a Retry-After header, but the shared API helper only
        // surfaces the body. Start the cooldown from the body when present.
        if (cooldownUntil > Date.now()) {
          MB.setMessage(
            resetMessage,
            "Please wait " + Math.ceil((cooldownUntil - Date.now()) / 1000) + "s before requesting another code.",
            "error"
          );
          return;
        }

        setBusy(requestButton, true, "Sending…");
        try {
          const result = await Auth.requestPasswordReset(normalized);
          resetStep1.hidden = true;
          resetStep2.hidden = false;
          const minutes = Math.round((result && result.expires_in_seconds ? result.expires_in_seconds : 600) / 60);
          MB.setMessage(
            resetMessage,
            "If that number is registered, a 6-digit code has been sent. It expires in " + minutes + " minutes.",
            "info"
          );
          resetCode.focus();
          startResendCooldown(result && result.resend_after_seconds ? result.resend_after_seconds : 60);
        } catch (error) {
          const retry = error && error.details && error.details.retry_after_seconds;
          if (error && error.status === 429) {
            startResendCooldown(retry || 900);
          }
          MB.setMessage(
            resetMessage,
            error.message || "Could not send a code.",
            "error"
          );
        } finally {
          setBusy(requestButton, false);
        }
      });
    }

    // ---- resend cooldown -------------------------------------------------
    const resendButton = document.getElementById("resendResetBtn");
    const resendHint = document.getElementById("resendHint");
    let cooldownUntil = 0;
    let cooldownTimer = null;

    function paintCooldown() {
      if (!resendButton) return;
      const left = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
      if (left <= 0) {
        resendButton.disabled = false;
        resendButton.textContent = "Resend code";
        if (resendHint) resendHint.textContent = "";
        window.clearInterval(cooldownTimer);
        cooldownTimer = null;
        return;
      }
      resendButton.disabled = true;
      resendButton.textContent = "Resend code in " + left + "s";
      if (resendHint) {
        resendHint.textContent =
          "One code at a time keeps your account safe. You can request another after the timer.";
      }
    }

    function startResendCooldown(seconds) {
      cooldownUntil = Date.now() + Math.max(1, Number(seconds) || 60) * 1000;
      window.clearInterval(cooldownTimer);
      paintCooldown();
      cooldownTimer = window.setInterval(paintCooldown, 1000);
    }

    if (resendButton) {
      resendButton.addEventListener("click", function () {
        if (cooldownUntil > Date.now()) return;
        if (requestButton) requestButton.click();
      });
    }

    const otpInput = document.getElementById("resetCode");
    if (otpInput) {
      otpInput.addEventListener("input", function () {
        const digits = otpInput.value.replace(/\D/g, "").slice(0, 6);
        if (digits !== otpInput.value) otpInput.value = digits;
      });
    }

    if (submitButton) {
      submitButton.addEventListener("click", async function () {
        MB.setMessage(resetMessage, "", "");
        if (!onlineGuard(resetMessage)) return;
        if (!/^\d{6}$/.test(resetCode.value.trim())) {
          MB.setMessage(resetMessage, "Enter the 6-digit code.", "error");
          resetCode.focus();
          return;
        }
        if (resetPassword.value.length < 8) {
          MB.setMessage(resetMessage, "Use at least 8 characters for the new password.", "error");
          resetPassword.focus();
          return;
        }
        if (resetPassword.value !== resetConfirm.value) {
          MB.setMessage(resetMessage, "The passwords do not match.", "error");
          resetConfirm.focus();
          return;
        }
        let normalized;
        try {
          normalized = Auth.normalizePhone(resetPhone.value);
        } catch (error) {
          MB.setMessage(resetMessage, error.message, "error");
          return;
        }
        setBusy(submitButton, true, "Updating…");
        try {
          await Auth.confirmPasswordReset({
            phone: normalized,
            code: resetCode.value.trim(),
            password: resetPassword.value,
          });
          resetStep2.hidden = true;
          MB.setMessage(
            resetMessage,
            "Password updated. You can sign in with the new password.",
            "success"
          );
          window.setTimeout(showLogin, 1600);
        } catch (error) {
          MB.setMessage(
            resetMessage,
            error.message || "The code is invalid or has expired.",
            "error"
          );
          setBusy(submitButton, false);
        }
      });
    }

    // Keep the reset button state correct if the user switches views.
    document.getElementById("resetForm")?.addEventListener("reset", function () {
      setBusy(submitButton, false);
    });
  }

  // ------------------------------------------------------------- registration

  const registerForm = document.getElementById("registerForm");
  if (registerForm) {
    const step1 = document.getElementById("regStep1");
    const step2 = document.getElementById("regStep2");
    const phone = document.getElementById("regPhone");
    const nationalId = document.getElementById("regNationalId");
    const password = document.getElementById("regPassword");
    const confirm = document.getElementById("regPasswordConfirm");
    const consent = document.getElementById("regConsent");
    const next = document.getElementById("regNext");
    const back = document.getElementById("regBack");
    const submit = document.getElementById("regSubmit");
    const message = document.getElementById("registerMsg");
    const success = document.getElementById("registerSuccess");
    const progress = MB.$$("[data-progress]");
    const summaryPhone = document.getElementById("sumPhone");
    const summaryId = document.getElementById("sumNationalId");

    let current = 1;
    let registered = false;

    function showStep(step) {
      current = step;
      step1.hidden = step !== 1;
      step2.hidden = step !== 2;
      back.hidden = step === 1;
      next.hidden = step !== 1;
      submit.hidden = step !== 2;
      progress.forEach(function (item) {
        const value = Number(item.getAttribute("data-progress"));
        item.classList.toggle("is-active", value === step);
        item.classList.toggle("is-done", value < step);
      });
      const target = step === 1 ? phone : consent;
      if (target) target.focus();
    }

    function validateStepOne() {
      MB.clearFieldErrors(step1);
      let normalized = "";
      try {
        normalized = Auth.normalizeKenyanPhone(phone.value);
      } catch (error) {
        MB.fieldError("err-regPhone", error.message);
        phone.setAttribute("aria-invalid", "true");
        phone.focus();
        return false;
      }
      if (!/^\d{6,10}$/.test(nationalId.value.trim())) {
        MB.fieldError("err-regNationalId", "Enter 6–10 digits, numbers only.");
        nationalId.setAttribute("aria-invalid", "true");
        nationalId.focus();
        return false;
      }
      if (password.value.length < 8) {
        MB.fieldError("err-regPassword", "Use at least 8 characters.");
        password.setAttribute("aria-invalid", "true");
        password.focus();
        return false;
      }
      if (password.value !== confirm.value) {
        MB.fieldError("err-regPasswordConfirm", "The passwords do not match.");
        confirm.setAttribute("aria-invalid", "true");
        confirm.focus();
        return false;
      }
      summaryPhone.textContent = humanPhone(normalized);
      summaryId.textContent = nationalId.value.trim();
      return true;
    }

    next.addEventListener("click", function () {
      MB.setMessage(message, "", "");
      if (validateStepOne()) showStep(2);
    });
    back.addEventListener("click", function () {
      MB.setMessage(message, "", "");
      showStep(1);
    });

    [phone, nationalId, password, confirm].forEach(function (input) {
      input.addEventListener("input", function () {
        input.removeAttribute("aria-invalid");
        const id = input.id;
        const field = id.replace("reg", "err-reg");
        MB.fieldError(field, "");
      });
    });

    consent.addEventListener("change", function () {
      if (consent.checked) MB.fieldError("err-regConsent", "");
    });

    registerForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      if (current === 1) {
        if (validateStepOne()) showStep(2);
        return;
      }
      if (registered) return;
      MB.setMessage(message, "", "");
      MB.fieldError("err-regConsent", "");

      if (!consent.checked) {
        MB.fieldError("err-regConsent", "Record your consent to continue.");
        consent.focus();
        return;
      }
      if (!validateStepOne()) {
        showStep(1);
        return;
      }
      if (!onlineGuard(message)) return;

      setBusy(submit, true, "Creating account…");
      try {
        await Auth.register({
          phone: phone.value,
          nationalId: nationalId.value,
          password: password.value,
          consent: true,
          consentVersion: consentVersion,
        });
        registered = true;
        registerForm.hidden = true;
        success.hidden = false;
        progress.forEach(function (item) {
          item.classList.add("is-done");
          item.classList.remove("is-active");
        });
        const finalStep = document.querySelector('[data-progress="3"]');
        if (finalStep) finalStep.classList.add("is-active");
        MB.announce("Account created. Opening your dashboard.");
        try {
          await Auth.login({ phone: phone.value, password: password.value });
          window.setTimeout(function () {
            window.location.assign("/dashboard/");
          }, 900);
        } catch (loginError) {
          // `success` IS the banner element (it lives outside the form):
          // querySelector searched its descendants and always found nothing,
          // silently swallowing this fallback. Speak straight to the banner.
          success.innerHTML =
            "<span><strong>Account created.</strong> " +
            '<a href="/login/">Sign in</a> with the password you just chose to continue.</span>';
        }
      } catch (error) {
        showStep(2);
        const details = error.details || {};
        const phoneError = MB.firstError(details, "phone");
        const idError = MB.firstError(details, "national_id");
        const passwordError = MB.firstError(details, "password");
        const consentError = MB.firstError(details, "consent");
        if (phoneError) MB.fieldError("err-regPhone", phoneError);
        if (idError) MB.fieldError("err-regNationalId", idError);
        if (passwordError) MB.fieldError("err-regPassword", passwordError);
        if (consentError) MB.fieldError("err-regConsent", consentError);
        if (error.status === 409 || error.code === "account_exists") {
          MB.setMessage(
            message,
            "An account with that phone number or National ID already exists. Try signing in instead.",
            "error"
          );
        } else {
          MB.setMessage(
            message,
            error.message || "Could not create the account. Check the fields and try again.",
            "error"
          );
        }
        if (phoneError || idError) showStep(1);
      } finally {
        setBusy(submit, false);
      }
    });
  }
})();
