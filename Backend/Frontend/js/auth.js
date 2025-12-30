(function () {
  const Auth = {};
  let API_BASE = null;

  Auth.init = function () {
    if (window.location.hostname.endsWith("onrender.com")) {
      API_BASE = "https://loan-platform.onrender.com/api";
    } else {
      API_BASE =
        localStorage.getItem("API_BASE") || "http://127.0.0.1:8000/api";
    }

    if (!API_BASE.endsWith("/api")) {
      API_BASE = API_BASE.replace(/\/$/, "") + "/api";
    }
  };

  function normalizeKenyanPhone(input) {
    const raw = String(input || "")
      .trim()
      .replace(/\s+/g, "")
      .replace(/-/g, "");

    if (!raw) throw new Error("Phone number is required.");

    const plusStripped = raw.startsWith("+") ? raw.slice(1) : raw;

    if (plusStripped.startsWith("254") && plusStripped.length === 12) {
      const prefix = plusStripped[3];
      if (prefix !== "7" && prefix !== "1") {
        throw new Error("Invalid Kenyan phone number.");
      }
      return plusStripped;
    }

    if (/^0(7|1)\d{8}$/.test(plusStripped)) {
      return "254" + plusStripped.slice(1);
    }

    if (/^(7|1)\d{8}$/.test(plusStripped)) {
      return "254" + plusStripped;
    }

    throw new Error("Invalid phone number format.");
  }

  function getToken() {
    return localStorage.getItem("access_token") || "";
  }

  function setToken(token) {
    if (token) {
      localStorage.setItem("access_token", token);
    }
  }

  function removeToken() {
    localStorage.removeItem("access_token");
  }

  function parseJWT(token) {
    try {
      if (!token) return null;
      const parts = token.split(".");
      if (parts.length !== 3) return null;

      let payload = parts[1]
        .replace(/-/g, "+")
        .replace(/_/g, "/");

      while (payload.length % 4) payload += "=";

      return JSON.parse(atob(payload));
    } catch {
      return null;
    }
  }

  function isTokenExpired(token) {
    const payload = parseJWT(token);
    if (!payload || !payload.exp) return true;

    const now = Math.floor(Date.now() / 1000);
    return now >= payload.exp - 30;
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

  async function api(path, { method = "GET", body = null, auth = true } = {}) {
    if (!API_BASE) Auth.init();

    const headers = { "Content-Type": "application/json" };

    if (auth) {
      const token = getToken();
      if (!token) throw new Error("Not authenticated.");

      if (isTokenExpired(token)) {
        removeToken();
        throw new Error("Session expired. Please login again.");
      }

      headers.Authorization = "Bearer " + token;
    }

    const res = await fetch(API_BASE + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : null,
    });

    const text = await res.text();
    const data = text ? JSON.parse(text) : null;

    if (!res.ok) {
      if (res.status === 401 || res.status === 403) {
        removeToken();
      }
      throw new Error(data?.error || data?.detail || "Request failed");
    }

    return data;
  }

  // REGISTER = CREATE ACCOUNT ONLY
  Auth.register = async function ({ phone, national_id, password }) {
    if (!phone || !national_id || !password) {
      throw new Error("All fields are required.");
    }

    if (password.length < 8) {
      throw new Error("Password must be at least 8 characters.");
    }

    const normalized = normalizeKenyanPhone(phone);

    await api("/users/register/", {
      method: "POST",
      auth: false,
      body: {
        phone: normalized,
        national_id: String(national_id).trim(),
        password,
      },
    });

    // IMPORTANT:
    // NO TOKEN STORED HERE
    // User must login explicitly
    return { registered: true };
  };

  // LOGIN = ONLY PLACE TOKEN IS STORED
  Auth.login = async function ({ phone, password }) {
    if (!phone || !password) {
      throw new Error("Phone and password required.");
    }

    const normalized = normalizeKenyanPhone(phone);

    const data = await api("/users/login/", {
      method: "POST",
      auth: false,
      body: { phone: normalized, password },
    });

    if (!data?.access) {
      throw new Error("Login failed.");
    }

    setToken(data.access);
    return data;
  };

  Auth.me = async function () {
    return api("/users/me/", { auth: true });
  };

  Auth.api = api;
  Auth.getToken = getToken;
  Auth.normalizeKenyanPhone = normalizeKenyanPhone;

  window.Auth = Auth;
})();
