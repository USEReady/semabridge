import { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { clearUIStoreStorage } from '../store/uiStore';


const AUTH_BASE = (import.meta.env.VITE_AUTH_BASE_URL || '/auth').replace(/\/$/, '');
const TOKEN_KEY = 'semabridge-token';

const AuthContext = createContext(undefined);

function formatAuthError(body, fallbackMessage) {
  const detail = body?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return detail.trim();
  }

  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item.trim();
        if (!item || typeof item !== 'object') return String(item || '').trim();

        const location = Array.isArray(item.loc) ? item.loc.join('.') : '';
        const message = typeof item.msg === 'string'
          ? item.msg.trim()
          : typeof item.message === 'string'
            ? item.message.trim()
            : '';
        if (!message) return '';
        return location ? `${location}: ${message}` : message;
      })
      .filter(Boolean);

    if (parts.length > 0) {
      return parts.join('; ');
    }
  }

  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) {
      return detail.message.trim();
    }
    try {
      return JSON.stringify(detail);
    } catch {
      // Ignore stringify failures and fall through to the fallback.
    }
  }

  if (typeof body?.message === 'string' && body.message.trim()) {
    return body.message.trim();
  }

  if (typeof body?.error === 'string' && body.error.trim()) {
    return body.error.trim();
  }

  return fallbackMessage;
}

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
    if (recoveringRef.current) return; // Already recovering
    recoveringRef.current = true;

    try {
      // Step 1: Try refresh via HttpOnly cookie
      try {
        const res = await fetch(`${AUTH_BASE}/refresh`, {
          method: 'POST',
          credentials: 'include',
        });
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
    (async () => {
      const existingToken = localStorage.getItem(TOKEN_KEY);
      
      if (existingToken) {
        // Try the existing token
        const valid = await fetchMe(existingToken);
        if (valid) {
          scheduleProactiveRefresh(existingToken);
          setLoading(false);
          return;
        }
        // Token invalid — try silent recovery (don't clear state yet)
        await silentRecover();
      } else {
        // No token at all — auto-login
        await autoLogin();
      }

      setLoading(false);
    })();

    return () => {
      if (refreshTimerRef.current) {
        clearTimeout(refreshTimerRef.current);
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
      const msg = formatAuthError(body, `Registration failed (${res.status})`);
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
      const msg = formatAuthError(body, `Login failed (${res.status})`);
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
