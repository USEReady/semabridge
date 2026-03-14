import { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react';

const AUTH_BASE = 'http://127.0.0.1:8000/auth';
const TOKEN_KEY = 'semabridge-token';

const AuthContext = createContext(undefined);

/**
 * Provides authentication state & helpers to the entire app.
 *
 * Stores the JWT in localStorage and auto-fetches the user profile
 * on mount if a token exists.
 */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [loading, setLoading] = useState(!!localStorage.getItem(TOKEN_KEY));
  const [error, setError] = useState(null);

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
        clearAuth();
        return;
      }
      const data = await res.json();
      setUser(data);
    } catch {
      clearAuth();
    } finally {
      setLoading(false);
    }
  }, [clearAuth]);

  // On mount: if token present, validate it by fetching profile
  useEffect(() => {
    if (token) {
      fetchMe(token);
    } else {
      setLoading(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
    // Fetch user profile immediately
    await fetchMe(data.access_token);
    return data;
  }, [saveToken, fetchMe]);

  const logout = useCallback(() => {
    clearAuth();
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
