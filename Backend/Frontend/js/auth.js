/**
 * Authentication and API client.
 *
 * The API base comes from `<meta name="api-base">`, which the server renders
 * from the API_BASE_URL setting. That keeps the path out of the JavaScript
 * bundle, so one build works on a Render hostname, a custom domain or a
 * preview URL without a rebuild.
 *
 * Cross-origin overrides are still refused: the token lives in localStorage,
 * and accepting a hostname from page script would let an injected string
 * redirect credentials to another origin. A split deployment must set
 * API_BASE_URL on the server (and CORS_ALLOWED_ORIGINS on the API), which
 * makes the origin a deployment decision rather than a runtime one.
 */
(function () {
  "use strict";

  const Auth = {};
  const TOKEN_KEY = "mb_access_token";
  const LEGACY_TOKEN_KEY = "access_token";

  function metaBase() {
    const tag = document.querySelector('meta[name="api-base"]');
    return tag ? (tag.getAttribute("content") || "").trim() : "";
  }

  function apiBase() {
    // Server-rendered value first: it is the deployment contract. The
    // window override exists for local experiments and must stay same-origin.
    const configured = metaBase() || window.API_BASE_URL || "/api/v1";
    let url;
    try {
      url = new URL(configured, window.location.origin);
    } catch (e) {
      return "/api/v1";
    }
    const renderedByServer = Boolean(metaBase());
    if (url.origin !== window.location.origin && !renderedByServer) {
      console.error("Refusing cross-origin API base override");
      return "/api/v1";
    }
    return url.origin === window.location.origin
      ? url.pathname.replace(/\/$/, "") || "/api/v1"
      : url.href.replace(/\/$/, "");
  }

  const API_BASE = apiBase();

  function getToken() {
    const token = localStorage.getItem(TOKEN_KEY) || "";
    if (token) return token;
    // One-time migration from the pre-redesign key. The value is not copied
    // anywhere else and is removed immediately.
    const legacy = localStorage.getItem(LEGACY_TOKEN_KEY) || "";
    if (legacy) {
      localStorage.setItem(TOKEN_KEY, legacy);
      localStorage.removeItem(LEGACY_TOKEN_KEY);
    }
    return legacy;
  }

  function setToken(token) {
    if (token) localStorage.setItem(TOKEN_KEY, token);
  }

  function removeToken() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(LEGACY_TOKEN_KEY);
  }

  function parseJWT(token) {
    try {
      const parts = String(token || "").split(".");
      if (parts.length !== 3) return null;
      let payload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
      while (payload.length % 4) payload += "=";
      return JSON.parse(atob(payload));
    } catch (e) {
      return null;
    }
  }

  function isTokenExpired(token) {
    const payload = parseJWT(token);
    if (!payload || !payload.exp) return true;
    return Math.floor(Date.now() / 1000) >= payload.exp - 30;
  }

  function messageFromError(data, status) {
    if (!data) return "Request failed (" + status + ").";
    if (typeof data.error === "string") return data.error;
    if (data.error && data.error.message) return data.error.message;
    if (data.detail) return data.detail;
    if (typeof data.message === "string") return data.message;
    return "Request failed (" + status + ").";
  }

  class ApiError extends Error {
    constructor(message, status, code, requestId, details) {
      super(message);
      this.status = status;
      this.code = code || "error";
      this.requestId = requestId || "";
      this.details = details || {};
    }
  }

  async function api(path, options) {
    const opts = options || {};
    const method = opts.method || "GET";
    const auth = opts.auth !== false;
    const headers = { Accept: "application/json" };
    if (opts.body !== undefined && opts.body !== null) {
      headers["Content-Type"] = "application/json";
    }
    if (auth) {
      const token = getToken();
      if (!token) throw new ApiError("Please log in to continue.", 401, "not_authenticated");
      if (isTokenExpired(token)) {
        removeToken();
        throw new ApiError("Your session expired. Please log in again.", 401, "session_expired");
      }
      headers.Authorization = "Bearer " + token;
    }

    let response;
    try {
      response = await fetch(API_BASE + path, {
        method,
        headers,
        body: opts.body !== undefined && opts.body !== null ? JSON.stringify(opts.body) : undefined,
        credentials: "same-origin",
      });
    } catch (e) {
      throw new ApiError(
        navigator.onLine === false
          ? "You are offline. Reconnect and try again."
          : "Network error. Check your connection and try again.",
        0,
        "network_error"
      );
    }

    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = null;
      }
    }

    if (!response.ok) {
      // A 403 is usually a business rule (consent or verification), not an
      // expired session. Only a 401 clears the stored token.
      if (response.status === 401) removeToken();
      const code = data && data.error && data.error.code;
      const requestId = data && data.error && data.error.request_id;
      const details = data && data.error && data.error.details;
      throw new ApiError(
        messageFromError(data, response.status),
        response.status,
        code,
        requestId,
        details
      );
    }
    return data;
  }

  function apiFormData(path, options) {
    const opts = options || {};
    return new Promise(function (resolve, reject) {
      const xhr = new XMLHttpRequest();
      if (opts.onProgress && xhr.upload) {
        xhr.upload.addEventListener("progress", function (event) {
          if (event.lengthComputable) {
            opts.onProgress(Math.round((event.loaded / event.total) * 100));
          }
        });
      }
      xhr.onload = function () {
        let data = null;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch (e) {
          data = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data);
        } else {
          if (xhr.status === 401) removeToken();
          const code = data && data.error && data.error.code;
          const requestId = data && data.error && data.error.request_id;
          const details = data && data.error && data.error.details;
          reject(
            new ApiError(
              messageFromError(data, xhr.status),
              xhr.status,
              code,
              requestId,
              details
            )
          );
        }
      };
      xhr.onerror = function () {
        reject(
          new ApiError(
            navigator.onLine === false
              ? "You are offline. Reconnect and try again."
              : "Network error. Check your connection.",
            0,
            "network_error"
          )
        );
      };
      xhr.ontimeout = function () {
        reject(new ApiError("Upload timed out. Please try again.", 0, "timeout"));
      };
      xhr.open(opts.method || "POST", API_BASE + path);
      xhr.timeout = 120000;
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.send(opts.formData);
    });
  }

  /**
   * Normalise a Kenyan number. Used wherever the number must be able to
   * receive an M-Pesa payout, so it refuses the DR Congo market on purpose.
   */
  function normalizeKenyanPhone(input) {
    const canonical = normalizePhone(input);
    if (!/^254(7|1)\d{8}$/.test(canonical)) {
      throw new Error(
        "Enter a valid Kenyan M-Pesa number, e.g. 0712345678 or +254712345678."
      );
    }
    return canonical;
  }

  /**
   * Normalise an account number in any supported market (Kenya 254, DR Congo
   * 243). Account creation, login and password reset use this so a Congolese
   * customer is not blocked at the first field.
   */
  function normalizePhone(input) {
    let raw = String(input || "").trim().replace(/[\s\-().]/g, "");
    if (!raw) throw new Error("Phone number is required.");
    if (raw.startsWith("+")) raw = raw.slice(1);
    else if (raw.startsWith("00")) raw = raw.slice(2);
    if (!/^\d+$/.test(raw)) throw new Error("Use digits, spaces or a leading +.");

    let candidate = null;
    if (/^254(7|1)\d{8}$/.test(raw) || /^243[89]\d{8}$/.test(raw)) {
      candidate = raw;
    } else if (/^0[7891]\d{8}$/.test(raw)) {
      const national = raw.slice(1);
      if (/^[71]/.test(national)) candidate = "254" + national;
      else candidate = "243" + national;
    } else if (/^[71]\d{8}$/.test(raw)) {
      candidate = "254" + raw;
    } else if (/^[89]\d{8}$/.test(raw)) {
      candidate = "243" + raw;
    }
    if (!candidate) {
      throw new Error(
        "Enter a valid mobile number: 07… / 01… / +254… in Kenya, or 08… / 09… / +243… in DR Congo."
      );
    }
    return candidate;
  }

  Auth.isAuthenticated = function () {
    const token = getToken();
    if (!token) return false;
    if (isTokenExpired(token)) {
      removeToken();
      return false;
    }
    return true;
  };

  Auth.logout = function () {
    removeToken();
  };

  Auth.register = async function (payload) {
    const body = {
      phone: normalizePhone(payload.phone),
      // Accept both spellings: page-auth historically sent `nationalId`,
      // which silently serialised to "" and made registration unwinnable.
      national_id: String(payload.national_id != null ? payload.national_id : payload.nationalId || "").trim(),
      password: payload.password,
      consent: Boolean(payload.consent),
      consent_version: payload.consentVersion || "",
    };
    if (payload.photos && payload.photos.id_front && payload.photos.id_back && payload.photos.selfie) {
      const form = new FormData();
      Object.keys(body).forEach(function (key) {
        form.append(key, body[key] === false ? "false" : body[key]);
      });
      form.append("id_front", payload.photos.id_front);
      form.append("id_back", payload.photos.id_back);
      form.append("selfie", payload.photos.selfie);
      return apiFormData("/users/register/", {
        formData: form,
        onProgress: payload.onProgress,
      });
    }
    return api("/users/register/", { method: "POST", auth: false, body: body });
  };

  Auth.login = async function (credentials) {
    const data = await api("/users/login/", {
      method: "POST",
      auth: false,
      body: {
        phone: normalizePhone(credentials.phone),
        password: credentials.password,
      },
    });
    if (!data || !data.access) throw new ApiError("Login failed.", 400, "login_failed");
    setToken(data.access);
    return data;
  };

  Auth.me = function () {
    return api("/users/me/");
  };

  Auth.verificationStatus = function () {
    return api("/users/verification-status/");
  };

  Auth.uploadDocuments = function (photos, onProgress) {
    const form = new FormData();
    form.append("id_front", photos.id_front);
    form.append("id_back", photos.id_back);
    form.append("selfie", photos.selfie);
    return apiFormData("/users/documents/", { formData: form, onProgress: onProgress });
  };

  Auth.grantConsent = function (version) {
    return api("/users/consent/", {
      method: "POST",
      body: { consent_version: version || "" },
    });
  };

  Auth.requestPasswordReset = function (phone) {
    return api("/users/password-reset/request/", {
      method: "POST",
      auth: false,
      body: { phone: normalizePhone(phone) },
    });
  };

  Auth.confirmPasswordReset = function (payload) {
    return api("/users/password-reset/confirm/", {
      method: "POST",
      auth: false,
      body: {
        phone: normalizePhone(payload.phone),
        code: String(payload.code || "").trim(),
        new_password: payload.password,
      },
    });
  };

  Auth.api = api;
  Auth.apiFormData = apiFormData;
  Auth.ApiError = ApiError;
  Auth.getToken = getToken;
  Auth.setToken = setToken;
  Auth.normalizeKenyanPhone = normalizeKenyanPhone;
  Auth.normalizePhone = normalizePhone;
  Auth.API_BASE = API_BASE;

  window.Auth = Auth;
})();
