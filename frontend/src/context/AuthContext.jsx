import { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { clearUIStoreStorage } from '../store/uiStore';


const AUTH_BASE = (import.meta.env.VITE_AUTH_BASE_URL || '/auth').replace(/\/$/, '');
const TOKEN_KEY = 'semabridge-token';

const AuthContext = createContext(undefined);

/**
 * Provides authentication state & helpers to the entire app.
 *
 * In development mode, automatically calls /auth/auto-login to get a JWT
 * without showing any login page. The login page is available at /login
 * for future production use.
 *
 * Industry best practice: Never clear UI state until all recovery attempts
 * have been exhausted. Uses proactive refresh to avoid 401 flashes.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Guard against concurrent recovery attempts
  const recoveringRef = useRef(false);
  // Timer for proactive token refresh
  const refreshTimerRef = useRef(null);

  // ── helpers ──────────────────────────────────────────────────

  const saveToken = useCallback((jwt) => {
    localStorage.setItem(TOKEN_KEY, jwt);
    setToken(jwt);
  }, []);

  const clearAuth = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem('semabridge-fabric-token');
    localStorage.removeItem('semabridge-fabric-token-expires');
    sessionStorage.clear();
    setToken(null);
    setUser(null);
  }, []);

  /** Fetch /auth/me with current token */
  const fetchMe = useCallback(async (jwt) => {
    try {
      const res = await fetch(`${AUTH_BASE}/me`, {
        headers: { Authorization: `Bearer ${jwt}` },
      });
      if (!res.ok) {
        return false;
      }
      const data = await res.json();
      setUser(data);
      return true;
    } catch {
      return false;
    }
  }, []);

  /**
   * Schedule a proactive token refresh at ~80% of the token's TTL.
   * This prevents the token from ever actually expiring during use,
   * eliminating 401 flashes entirely.
   */
  const scheduleProactiveRefresh = useCallback((jwt) => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }

    try {
      // Decode JWT payload (base64url) to read exp claim
      const parts = jwt.split('.');
      if (parts.length !== 3) return;
      const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
      const exp = payload.exp;
      if (!exp) return;

      const nowSec = Math.floor(Date.now() / 1000);
      const ttlSec = exp - nowSec;

      if (ttlSec <= 0) return; // Already expired

      // Refresh at 80% of TTL (e.g., 12 min into a 15-min token)
      const refreshInMs = Math.max(ttlSec * 0.8, 30) * 1000;

      refreshTimerRef.current = setTimeout(async () => {
        try {
          const res = await fetch(`${AUTH_BASE}/refresh`, {
            method: 'POST',
            credentials: 'include',
          });
          if (res.ok) {
            const data = await res.json();
            if (data.access_token) {
              saveToken(data.access_token);
              scheduleProactiveRefresh(data.access_token);
              return;
            }
          }
          // Refresh cookie failed — try auto-login silently
          const autoRes = await fetch(`${AUTH_BASE}/auto-login`, {
            method: 'POST',
            credentials: 'include',
          });
          if (autoRes.ok) {
            const autoData = await autoRes.json();
            if (autoData.access_token) {
              saveToken(autoData.access_token);
              await fetchMe(autoData.access_token);
              scheduleProactiveRefresh(autoData.access_token);
            }
          }
        } catch {
          // Network error — will retry on next API call
        }
      }, refreshInMs);
    } catch {
      // JWT decode failed — skip proactive refresh
    }
  }, [saveToken, fetchMe]);

  /** Auto-login: create a dev user and get JWT without credentials */
  const autoLogin = useCallback(async () => {
    try {
      const res = await fetch(`${AUTH_BASE}/auto-login`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (data.access_token) {
        saveToken(data.access_token);
        await fetchMe(data.access_token);
        scheduleProactiveRefresh(data.access_token);
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }, [saveToken, fetchMe, scheduleProactiveRefresh]);

  /**
   * Silent recovery: attempt to restore the session WITHOUT clearing
   * UI state first. Only clears state if ALL recovery paths fail.
   * This prevents the "flash to Disconnected" problem.
   */
  const silentRecover = useCallback(async () => {
    const existingToken = localStorage.getItem(TOKEN_KEY);
    if (!existingToken) {
      console.log("[AuthContext] No access token in localStorage. Skipping silent recovery.");
      clearAuth();
      return;
    }

    if (recoveringRef.current) {
      console.log("[AuthContext] Recovery already in progress. Coalescing...");
      return;
    }
    recoveringRef.current = true;

    try {
      console.log("[AuthContext] Initiating silent recovery...");
      // Step 1: Try refresh via HttpOnly cookie
      try {
        console.log("[AuthContext] silentRecover: Attempting POST /auth/refresh");
        const res = await fetch(`${AUTH_BASE}/refresh`, {
          method: 'POST',
          credentials: 'include',
        });
        if (res.ok) {
          const data = await res.json();
          if (data.access_token) {
            console.log("[AuthContext] silentRecover: refresh token renewal succeeded.");
            saveToken(data.access_token);
            const userFetched = await fetchMe(data.access_token);
            if (userFetched) {
              scheduleProactiveRefresh(data.access_token);
              return; // Success — UI state preserved
            }
          }
        } else {
          console.warn(`[AuthContext] silentRecover: refresh returned HTTP ${res.status}`);
        }
      } catch (err) {
        console.error("[AuthContext] silentRecover: refresh fetch exception:", err);
      }

      // Step 2: Try auto-login (dev mode fallback)
      console.log("[AuthContext] silentRecover: refresh failed, attempting autoLogin fallback...");
      const recovered = await autoLogin();
      if (recovered) {
        console.log("[AuthContext] silentRecover: dev autoLogin recovery succeeded.");
        return; // Success — UI state preserved
      }

      // Step 3: All recovery failed — NOW clear state
      console.error("[AuthContext] silentRecover: All recovery attempts failed. Clearing authentication state completely.");
      clearAuth();
    } finally {
      recoveringRef.current = false;
    }
  }, [saveToken, fetchMe, clearAuth, autoLogin, scheduleProactiveRefresh]);

  const initializedRef = useRef(false);

  // On mount: validate existing token, or auto-login if none
  useEffect(() => {
    if (initializedRef.current) {
      console.log("[AuthContext] AuthProvider mount triggered again (React StrictMode). Bypassing duplicate run.");
      return;
    }
    initializedRef.current = true;

    (async () => {
      const existingToken = localStorage.getItem(TOKEN_KEY);
      console.log("[AuthContext] Initializing session... Token present:", !!existingToken);
      
      try {
        if (existingToken) {
          console.log("[AuthContext] Found existing token. Validating user identity...");
          const valid = await fetchMe(existingToken);
          if (valid) {
            console.log("[AuthContext] Token validated. Starting proactive refresh scheduler.");
            scheduleProactiveRefresh(existingToken);
            return;
          }
          console.warn("[AuthContext] Token validation failed. Attempting silent session recovery...");
          await silentRecover();
        } else {
          console.log("[AuthContext] No session token. Attempting auto-login...");
          await autoLogin();
        }
      } catch (err) {
        console.error("[AuthContext] Error during session initialization:", err);
      } finally {
        console.log("[AuthContext] Session initialization flow completed.");
        setLoading(false);
      }
    })();

    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
      }
    };
  }, [fetchMe, silentRecover, autoLogin, scheduleProactiveRefresh]);

  // Listen for auth events from api.js 401 handler
  useEffect(() => {
    const expiredHandler = async () => {
      await silentRecover();
    };
    
    // api.js interceptor successfully got a new token
    const refreshHandler = (e) => {
      const newToken = e.detail;
      saveToken(newToken);
      scheduleProactiveRefresh(newToken);
    };

    window.addEventListener('semabridge:auth-expired', expiredHandler);
    window.addEventListener('semabridge:token-refreshed', refreshHandler);
    
    return () => {
      window.removeEventListener('semabridge:auth-expired', expiredHandler);
      window.removeEventListener('semabridge:token-refreshed', refreshHandler);
    };
  }, [silentRecover, saveToken, scheduleProactiveRefresh]);

  // ── public API ───────────────────────────────────────────────

  const register = useCallback(async (username, email, password) => {
    setError(null);
    const res = await fetch(`${AUTH_BASE}/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const msg = body.detail || `Registration failed (${res.status})`;
      setError(msg);
      throw new Error(msg);
    }
    return res.json();
  }, []);

  const login = useCallback(async (username, password) => {
    setError(null);
    const res = await fetch(`${AUTH_BASE}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      const msg = body.detail || `Login failed (${res.status})`;
      setError(msg);
      throw new Error(msg);
    }
    const data = await res.json();
    saveToken(data.access_token);
    await fetchMe(data.access_token);
    scheduleProactiveRefresh(data.access_token);
    return data;
  }, [saveToken, fetchMe, scheduleProactiveRefresh]);

  const logout = useCallback(async () => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    try {
      await fetch(`${AUTH_BASE}/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {
      // Network error — still clear local state
    }
    clearAuth();
    clearUIStoreStorage();
    sessionStorage.clear();
  }, [clearAuth]);

  const value = useMemo(() => ({
    user,
    token,
    loading,
    error,
    isAuthenticated: !!user,
    register,
    login,
    logout,
    clearError: () => setError(null),
  }), [user, token, loading, error, register, login, logout]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
