(function () {
  const Auth = {};
  let API_BASE = null;

  Auth.init = function () {
    if (window.location.hostname.endsWith('onrender.com')) {
      API_BASE = 'https://loan-platform.onrender.com/api';
    } else {
      API_BASE = localStorage.getItem("API_BASE") || "http://127.0.0.1:8000/api";
    }
    if (!API_BASE.endsWith("/api")) {
      API_BASE = API_BASE.replace(/\/$/, "") + "/api";
    }
  };

  function normalizeKenyanPhone(input) {
    const raw = String(input || "").trim().replace(/\s+/g, "");
    if (!raw) throw new Error("Phone number is required.");
    
    const plusStripped = raw.startsWith("+") ? raw.slice(1) : raw;
    
    if (plusStripped.startsWith("254")) {
      const rest = plusStripped.slice(3);
      if (!/^(7|1)\d{8}$/.test(rest)) throw new Error("Invalid Kenyan phone number.");
      return "254" + rest;
    }
    if (/^0(7|1)\d{8}$/.test(plusStripped)) {
      return "254" + plusStripped.slice(1);
    }
    throw new Error("Invalid Kenyan phone number. Use format: 07XXXXXXXX or 2547XXXXXXXX");
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

  // Parse JWT payload without verification (for client-side expiry check)
  function parseJWT(token) {
    try {
      if (!token) return null;
      const parts = token.split(".");
      if (parts.length !== 3) return null;
      
      const payload = parts[1];
      // Handle base64url encoding
      const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
      const decoded = atob(base64);
      return JSON.parse(decoded);
    } catch (e) {
      return null;
    }
  }

  function isTokenExpired(token) {
    const payload = parseJWT(token);
    if (!payload) return true;
    
    const exp = payload.exp;
    if (!exp) return false; // No expiry claim
    
    // exp is in seconds, Date.now() is in milliseconds
    // Add 30 second buffer to account for clock skew
    return Date.now() >= (exp * 1000) - 30000;
  }

  Auth.isAuthenticated = function () {
    const token = getToken();
    if (!token) return false;
    
    // Check if token is expired
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
      if (token) {
        // Check if token is expired before making request
        if (isTokenExpired(token)) {
          removeToken();
          throw new Error("Session expired. Please login again.");
        }
        headers["Authorization"] = "Bearer " + token;
      }
    }

    let res;
    try {
      res = await fetch(API_BASE + path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : null,
      });
    } catch (networkError) {
      throw new Error("Network error. Please check your connection and try again.");
    }

    const text = await res.text();
    let data;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }

    // Handle different error statuses with clear messages
    if (!res.ok) {
      // Extract error message from various response formats
      let errorMsg = "Request failed";
      
      if (data) {
        if (data.error) {
          errorMsg = data.error;
        } else if (data.detail) {
          errorMsg = data.detail;
        } else if (data.message) {
          errorMsg = data.message;
        } else if (typeof data === 'string') {
          errorMsg = data;
        }
      }

      // Add context for specific HTTP status codes
      switch (res.status) {
        case 400:
          // Bad request - validation error, use message as is
          break;
        case 401:
          removeToken();
          errorMsg = errorMsg || "Invalid credentials. Please check your phone number and password.";
          break;
        case 403:
          removeToken();
          errorMsg = "Access denied. Please login again.";
          break;
        case 404:
          errorMsg = "Account not found. Please check your phone number or register.";
          break;
        case 500:
          errorMsg = "Server error. Please try again later.";
          break;
        default:
          if (!errorMsg || errorMsg === "Request failed") {
            errorMsg = `Request failed (${res.status})`;
          }
      }

      throw new Error(errorMsg);
    }

    return data;
  }

  Auth.register = async function ({ phone, national_id, password }) {
    const normalized = normalizeKenyanPhone(phone);
    const nid = String(national_id || "").trim();
    
    if (!/^\d{6,10}$/.test(nid)) {
      throw new Error("National ID must be 6-10 digits.");
    }
    if (String(password || "").length < 8) {
      throw new Error("Password must be at least 8 characters.");
    }

    const data = await api("/users/register/", {
      method: "POST",
      auth: false,
      body: { phone: normalized, national_id: nid, password }
    });

    // If registration returns a token, store it
    if (data && data.access) {
      setToken(data.access);
    }

    return data;
  };

  Auth.login = async function ({ phone, password }) {
    // Validate inputs before sending
    if (!phone || !phone.trim()) {
      throw new Error("Phone number is required.");
    }
    if (!password) {
      throw new Error("Password is required.");
    }

    const normalized = normalizeKenyanPhone(phone);

    const data = await api("/users/login/", {
      method: "POST",
      auth: false,
      body: { phone: normalized, password }
    });

    if (!data || !data.access) {
      throw new Error("Login failed. Please try again.");
    }

    setToken(data.access);
    return data;
  };

  Auth.me = async function () {
    return await api("/users/me/", { method: "GET", auth: true });
  };

  Auth.getToken = getToken;
  Auth.api = api;
  Auth.normalizeKenyanPhone = normalizeKenyanPhone;

  window.Auth = Auth;
})();
