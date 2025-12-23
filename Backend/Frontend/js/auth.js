(function () {
  const Auth = {};
  let API_BASE = null;

  Auth.init = function () {
    // Configurable by backend-served origin; override with localStorage if needed.
    // For deployment, if window.location.host matches the deployed frontend, set it to the Render backend root
    if (window.location.hostname.endsWith('onrender.com')) {
      // Put your Render backend URL here (update after first deploy!)
      API_BASE = 'https://YOUR-BACKEND-SERVICE.onrender.com/api';
    } else {
      API_BASE = localStorage.getItem("API_BASE") || "http://127.0.0.1:8000";
    }
    // Always end with /api
    if (!API_BASE.endsWith("/api")) API_BASE = API_BASE.replace(/\/$/, "") + "/api";
  };

  function normalizeKenyanPhone(input) {
    const raw = String(input || "").trim().replace(/\s+/g, "");
    if (!raw) throw new Error("Phone number is required.");
    // Allow +254..., 254..., 07..., 01...
    const plusStripped = raw.startsWith("+") ? raw.slice(1) : raw;
    if (plusStripped.startsWith("254")) {
      const rest = plusStripped.slice(3);
      if (!/^(7|1)\d{8}$/.test(rest)) throw new Error("Invalid Kenyan phone number.");
      return "254" + rest;
    }
    if (/^0(7|1)\d{8}$/.test(plusStripped)) {
      return "254" + plusStripped.slice(1);
    }
    throw new Error("Invalid Kenyan phone number.");
  }

  function getToken() {
    return localStorage.getItem("access_token") || "";
  }

  Auth.isAuthenticated = function () {
    return Boolean(getToken());
  };

  Auth.logout = function () {
    localStorage.removeItem("access_token");
  };

  async function api(path, { method = "GET", body = null, auth = true } = {}) {
    if (!API_BASE) Auth.init();
    const headers = { "Content-Type": "application/json" };
    if (auth) {
      const token = getToken();
      if (token) headers["Authorization"] = "Bearer " + token;
    }
    const res = await fetch(API_BASE + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : null,
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) ? (data.detail || data.error) : `Request failed (${res.status})`;
      throw new Error(msg);
    }
    return data;
  }

  Auth.register = async function ({ phone, national_id, password }) {
    const normalized = normalizeKenyanPhone(phone);
    const nid = String(national_id || "").trim();
    if (!/^\d{6,10}$/.test(nid)) throw new Error("National ID must be 6-10 digits.");
    if (String(password || "").length < 8) throw new Error("Password must be at least 8 characters.");
    await api("/users/register/", { method: "POST", auth: false, body: { phone: normalized, national_id: nid, password } });
  };

  Auth.login = async function ({ phone, password }) {
    const normalized = normalizeKenyanPhone(phone);
    if (!password) throw new Error("Password is required.");
    const data = await api("/users/login/", { method: "POST", auth: false, body: { phone: normalized, password } });
    if (!data || !data.access) throw new Error("Login failed.");
    localStorage.setItem("access_token", data.access);
  };

  Auth.me = async function () {
    return await api("/users/me/", { method: "GET", auth: true });
  };

  Auth.api = api;
  Auth.normalizeKenyanPhone = normalizeKenyanPhone;

  window.Auth = Auth;
})();
