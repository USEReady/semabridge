import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Hexagon, Eye, EyeOff, LogIn, UserPlus, AlertCircle } from 'lucide-react';

/**
 * LoginPage — authentication screen.
 * After successful login, redirects to the originally requested page (or /explore).
 */
export default function LoginPage() {
  const { login, register, error, clearError, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from?.pathname ?? '/explore';

  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [username, setUsername] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');

  const switchMode = (newMode) => {
    setMode(newMode);
    clearError();
    setSuccessMsg('');
    setUsername('');
    setEmail('');
    setPassword('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setSuccessMsg('');
    clearError();
    try {
      if (mode === 'register') {
        await register(username, email, password);
        setSuccessMsg('Account created! You can now sign in.');
        switchMode('login');
      } else {
        await login(username, password);
        navigate(from, { replace: true });
      }
    } catch {
      // error is set in context
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-app">
        <div className="flex flex-col items-center gap-3">
          <Hexagon size={32} className="text-accent-blue animate-pulse" />
          <span className="text-secondary text-sm">Loading…</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex items-center justify-center h-screen bg-app"
      style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' }}
    >
      <div
        className="w-full max-w-sm rounded-xl bg-surface border border-main p-8"
        style={{ boxShadow: 'var(--shadow-lg)' }}
      >
        {/* Logo */}
        <div className="flex flex-col items-center gap-2 mb-8">
          <Hexagon size={28} className="text-accent-blue" strokeWidth={2.5} />
          <h1 className="text-lg font-bold tracking-tight text-primary">SemaBridge</h1>
          <p className="text-xs text-tertiary">
            {mode === 'login' ? 'Sign in to your account' : 'Create a new account'}
          </p>
        </div>

        {/* Success message */}
        {successMsg && (
          <div
            className="flex items-center gap-2 px-3 py-2 mb-4 rounded-md text-xs font-medium"
            style={{ background: 'var(--color-success-muted)', color: 'var(--color-success)' }}
          >
            {successMsg}
          </div>
        )}

        {/* Error message */}
        {error && (
          <div
            className="flex items-center gap-2 px-3 py-2 mb-4 rounded-md text-xs font-medium"
            style={{ background: 'var(--color-danger-muted)', color: 'var(--color-danger)' }}
          >
            <AlertCircle size={14} />
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          {/* Username */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-secondary">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
              placeholder="Enter username"
              className="h-9 px-3 rounded-md text-sm bg-app border border-main text-primary"
              style={{ outline: 'none' }}
              onFocus={(e) => { e.target.style.borderColor = 'var(--accent-blue)'; }}
              onBlur={(e) => { e.target.style.borderColor = 'var(--border-main)'; }}
            />
          </div>

          {/* Email (register only) */}
          {mode === 'register' && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-secondary">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="you@example.com"
                className="h-9 px-3 rounded-md text-sm bg-app border border-main text-primary"
                style={{ outline: 'none' }}
                onFocus={(e) => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={(e) => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
            </div>
          )}

          {/* Password */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-secondary">Password</label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                placeholder="Enter password"
                className="w-full h-9 px-3 pr-9 rounded-md text-sm bg-app border border-main text-primary"
                style={{ outline: 'none' }}
                onFocus={(e) => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={(e) => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-tertiary hover:text-secondary"
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting}
            className="flex items-center justify-center gap-2 h-9 rounded-md text-sm font-medium"
            style={{
              background: submitting ? 'var(--color-accent-faint)' : 'var(--accent-blue)',
              color: submitting ? 'var(--text-tertiary)' : '#fff',
              cursor: submitting ? 'not-allowed' : 'pointer',
              border: 'none',
            }}
          >
            {mode === 'login' ? <LogIn size={14} /> : <UserPlus size={14} />}
            {submitting ? 'Please wait…' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>

        {/* Toggle mode */}
        <div className="mt-6 text-center text-xs text-tertiary">
          {mode === 'login' ? (
            <>
              Don&apos;t have an account?{' '}
              <button
                onClick={() => switchMode('register')}
                className="text-accent-blue hover:underline font-medium"
                style={{ background: 'none', border: 'none', cursor: 'pointer' }}
              >
                Sign up
              </button>
            </>
          ) : (
            <>
              Already have an account?{' '}
              <button
                onClick={() => switchMode('login')}
                className="text-accent-blue hover:underline font-medium"
                style={{ background: 'none', border: 'none', cursor: 'pointer' }}
              >
                Sign in
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
