import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Hexagon, AlertCircle, Mail } from 'lucide-react';
import { api } from '../utils/api';

/**
 * ForgotPasswordPage — request a password reset link by email.
 */
export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError('');
    setSuccessMsg('');
    try {
      const data = await api.requestPasswordReset(email.trim().toLowerCase());
      if (data?.reset_url) {
        // Dev mode: SMTP not configured — navigate directly to the reset page
        navigate(new URL(data.reset_url).pathname);
        return;
      }
      setSuccessMsg('Check your email for a reset link. It may take a few minutes to arrive.');
    } catch (err) {
      setError(err?.message || 'Something went wrong. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

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
          <p className="text-xs text-tertiary">Reset your password</p>
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

        {!successMsg && (
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {/* Email */}
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-secondary">Email address</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
                placeholder="you@example.com"
                className="h-9 px-3 rounded-md text-sm bg-app border border-main text-primary"
                style={{ outline: 'none' }}
                onFocus={(e) => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={(e) => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
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
              <Mail size={14} />
              {submitting ? 'Sending…' : 'Send Reset Link'}
            </button>
          </form>
        )}

        {/* Back to login */}
        <div className="mt-6 text-center text-xs text-tertiary">
          <Link
            to="/login"
            className="text-accent-blue hover:underline font-medium"
          >
            Back to Sign In
          </Link>
        </div>
      </div>
    </div>
  );
}
