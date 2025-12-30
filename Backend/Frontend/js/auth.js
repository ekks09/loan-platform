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
    const raw = String(input || "").trim().replace(/\s+/g, "").replace(/-/g, "");
    if (!raw) throw new Error("Phone number is required.");
    
    const plusStripped = raw.startsWith("+") ? raw.slice(1) : raw;
    
    if (plusStripped.startsWith("254") && plusStripped.length === 12) {
      const prefix = plusStripped[3];
      if (prefix !== "7" && prefix !== "1") {
        throw new Error("Invalid Kenyan phone number. Must start with 07 or 01.");
      }
      return plusStripped;
    }
    
    if (/^0(7|1)\d{8}$/.test(plusStripped)) {
      return "254" + plusStripped.slice(1);
    }
    
    if (/^(7|1)\d{8}$/.test(plusStripped)) {
      return "254" + plusStripped;
    }
    
    throw new Error("Invalid phone number format. Use 07XXXXXXXX or 2547XXXXXXXX.");
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

  // Parse JWT payload (without verification - that's done server-side)
  function parseJWT(token) {
    try {
      if (!token) return null;
      const parts = token.split(".");
      if (parts.length !== 3) return null;
      
      // Handle base64url encoding
      let payload = parts[1];
      payload = payload.replace(/-/g, '+').replace(/_/g, '/');
      
      // Add padding if needed
      while (payload.length % 4) {
        payload += '=';
      }
      
      const decoded = atob(payload);
      return JSON.parse(decoded);
    } catch (e) {
      console.error("JWT parse error:", e);
      return null;
    }
  }

  function isTokenExpired(token) {
    const payload = parseJWT(token);
    if (!payload) return true;
    
    const exp = payload.exp;
    if (!exp) return false;
    
    // Add 30 second buffer
    const now = Math.floor(Date.now() / 1000);
    return now >= (exp - 30);
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
      if (token) {
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
      console.error("Network error:", networkError);
      throw new Error("Network error. Please check your connection.");
    }

    const text = await res.text();
    let data;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { error: text || "Unknown error" };
    }

    if (!res.ok) {
      let errorMsg = "Request failed";
      
      if (data) {
        // Handle different error formats
        if (typeof data.error === "string") {
          errorMsg = data.error;
        } else if (typeof data.error === "object") {
          // Handle nested errors like {"error": {"phone": ["error msg"]}}
          const firstKey = Object.keys(data.error)[0];
          const firstValue = data.error[firstKey];
          if (Array.isArray(firstValue)) {
            errorMsg = firstValue[0];
          } else if (typeof firstValue === "string") {
            errorMsg = firstValue;
          }
        } else if (data.detail) {
          errorMsg = data.detail;
        } else if (data.message) {
          errorMsg = data.message;
        }
      }

      // Handle auth errors
      if (res.status === 401 || res.status === 403) {
        removeToken();
      }

      throw new Error(errorMsg);
    }

    return data;
  }

  Auth.register = async function ({ phone, national_id, password }) {
    // Client-side validation
    if (!phone || !phone.trim()) {
      throw new Error("Phone number is required.");
    }
    if (!national_id || !national_id.trim()) {
      throw new Error("National ID is required.");
    }
    if (!password || password.length < 8) {
      throw new Error("Password must be at least 8 characters.");
    }

    const normalized = normalizeKenyanPhone(phone);
    const nid = String(national_id).trim();
    
    if (!/^\d{6,10}$/.test(nid)) {
      throw new Error("National ID must be 6-10 digits.");
    }

    const data = await api("/users/register/", {
      method: "POST",
      auth: false,
      body: { phone: normalized, national_id: nid, password }
    });

    // Store token if returned
    if (data && data.access) {
      setToken(data.access);
    }

    return data;
  };

  Auth.login = async function ({ phone, password }) {
    // Client-side validation
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
