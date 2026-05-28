import { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { clearUIStoreStorage } from '../store/uiStore';
import { fetchWithTimeout, tryRefreshToken, resetRefreshCooldown } from '../utils/api';


const AUTH_BASE = (import.meta.env.VITE_AUTH_BASE_URL || '/auth').replace(/\/$/, '');
const TOKEN_KEY = 'semabridge-token';
const AUTH_REQUEST_TIMEOUT_MS = 8000;
const AUTH_BOOTSTRAP_RETRY_DELAY_MS = 350;

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
  const queryClient = useQueryClient();
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Guard against concurrent recovery attempts
  const recoveringRef = useRef(false);
  // Timer for proactive token refresh
  const refreshTimerRef = useRef(null);
  const previousTokenRef = useRef(token);

  // ── helpers ──────────────────────────────────────────────────

  const saveToken = useCallback((jwt) => {
    localStorage.setItem(TOKEN_KEY, jwt);
    setToken(jwt);
  }, []);

  const clearAuth = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
  }, []);

  /** Fetch /auth/me with current token */
  const fetchMe = useCallback(async (jwt) => {
    try {
      const res = await fetchWithTimeout(`${AUTH_BASE}/me`, {
        headers: { Authorization: `Bearer ${jwt}` },
      }, AUTH_REQUEST_TIMEOUT_MS);
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
  const scheduleProactiveRefresh = useCallback(function scheduleRefresh(jwt) {
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
          // Delegate to api.js singleton to avoid one-time-use refresh
          // token race conditions between this timer and 401 recovery.
          const newToken = await tryRefreshToken();
          if (newToken) {
            saveToken(newToken);
            resetRefreshCooldown();
            scheduleRefresh(newToken);
            return;
          }
          // tryRefreshToken already tried /auth/refresh + /auth/auto-login
          // and both failed. Schedule another attempt after a short delay.
        } catch {
          // Network error — will retry on next API call
        }
      }, refreshInMs);
    } catch {
      // JWT decode failed — skip proactive refresh
    }
  }, [saveToken]);

  /** Auto-login: create a dev user and get JWT without credentials */
  const autoLogin = useCallback(async () => {
    try {
      const res = await fetchWithTimeout(`${AUTH_BASE}/auto-login`, {
        method: 'POST',
        credentials: 'include',
      }, AUTH_REQUEST_TIMEOUT_MS);
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

  const autoLoginWithRetry = useCallback(async (attempts = 2) => {
    for (let i = 0; i < attempts; i += 1) {
      const ok = await autoLogin();
      if (ok) return true;
      if (i < attempts - 1) {
        await new Promise((resolve) => window.setTimeout(resolve, AUTH_BOOTSTRAP_RETRY_DELAY_MS));
      }
    }
    return false;
  }, [autoLogin]);

  /**
   * Silent recovery: attempt to restore the session WITHOUT clearing
   * UI state first. Only clears state if ALL recovery paths fail.
   * This prevents the "flash to Disconnected" problem.
   */
  const silentRecover = useCallback(async () => {
    if (recoveringRef.current) return; // Already recovering
    recoveringRef.current = true;

    try {
      // Step 1: Try refresh via HttpOnly cookie
      try {
        const res = await fetchWithTimeout(`${AUTH_BASE}/refresh`, {
          method: 'POST',
          credentials: 'include',
        }, AUTH_REQUEST_TIMEOUT_MS);
        if (res.ok) {
          const data = await res.json();
          if (data.access_token) {
            saveToken(data.access_token);
            await fetchMe(data.access_token);
            scheduleProactiveRefresh(data.access_token);
            return; // Success — UI state preserved
          }
        }
      } catch {
        // Network error on refresh
      }

      // Step 2: Try auto-login (dev mode fallback)
      const recovered = await autoLogin();
      if (recovered) return; // Success — UI state preserved

      // Step 3: All recovery failed — NOW clear state
      clearAuth();
    } finally {
      recoveringRef.current = false;
    }
  }, [saveToken, fetchMe, clearAuth, autoLogin, scheduleProactiveRefresh]);

  // On mount: validate existing token, or auto-login if none
  useEffect(() => {
    let cancelled = false;
    const markLoaded = () => {
      if (!cancelled) setLoading(false);
    };

    (async () => {
      const existingToken = localStorage.getItem(TOKEN_KEY);

      if (existingToken) {
        const valid = await fetchMe(existingToken);
        if (valid) {
          scheduleProactiveRefresh(existingToken);
          return;
        }
        await silentRecover();
        return;
      }

      await autoLoginWithRetry();
    })()
      .catch(() => {
        // Non-blocking bootstrap: app can still render login and public UI states.
      })
      .finally(() => {
        markLoaded();
      });

    return () => {
      cancelled = true;
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
      }
    };
  }, [fetchMe, silentRecover, autoLoginWithRetry]); // eslint-disable-line react-hooks/exhaustive-deps

  // Listen for auth events from api.js 401 handler
  useEffect(() => {
    const expiredHandler = async () => {
      await silentRecover();
    };
    
    // api.js interceptor successfully got a new token
    const refreshHandler = (e) => {
      const newToken = e.detail;
      saveToken(newToken);
      resetRefreshCooldown();
      scheduleProactiveRefresh(newToken);
    };

    window.addEventListener('semabridge:auth-expired', expiredHandler);
    window.addEventListener('semabridge:token-refreshed', refreshHandler);
    
    return () => {
      window.removeEventListener('semabridge:auth-expired', expiredHandler);
      window.removeEventListener('semabridge:token-refreshed', refreshHandler);
    };
  }, [silentRecover, saveToken, scheduleProactiveRefresh]);

  // If token changes (auto-login / refresh / login), refetch all stale query data
  // that may have loaded before auth became available.
  useEffect(() => {
    const previousToken = previousTokenRef.current;
    if (!token || token === previousToken) return;
    previousTokenRef.current = token;
    queryClient.invalidateQueries();
  }, [token, queryClient]);

  // ── public API ───────────────────────────────────────────────

  const register = useCallback(async (username, email, password) => {
    setError(null);
    let res;
    try {
      res = await fetchWithTimeout(`${AUTH_BASE}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, email, password }),
      }, AUTH_REQUEST_TIMEOUT_MS);
    } catch (err) {
      const msg = err?.name === 'AbortError' ? 'Request timed out. Please try again.' : 'Network error. Please check your connection.';
      setError(msg);
      throw new Error(msg);
    }
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
    let res;
    try {
      res = await fetchWithTimeout(`${AUTH_BASE}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password }),
      }, AUTH_REQUEST_TIMEOUT_MS);
    } catch (err) {
      const msg = err?.name === 'AbortError' ? 'Request timed out. Please try again.' : 'Network error. Please check your connection.';
      setError(msg);
      throw new Error(msg);
    }
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
