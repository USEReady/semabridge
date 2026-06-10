import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Hexagon, Eye, EyeOff, AlertCircle, CheckCircle } from 'lucide-react';
import { api } from '../utils/api';

/**
 * ResetPasswordPage — set a new password using a reset token from email.
 * URL: /auth/reset-password/:token
 */
export default function ResetPasswordPage() {
  const { token } = useParams();

  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [successMsg, setSuccessMsg] = useState('');
  const [error, setError] = useState('');

  function validatePassword(pw) {
    if (pw.length < 8) return 'Password must be at least 8 characters.';
    if (!/[\d!@#$%^&*()_+\-=[\]{};':"\\|,.<>/?`~]/.test(pw)) {
      return 'Password must contain at least one number or symbol.';
    }
    return null;
  }

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    const validationError = validatePassword(newPassword);
    if (validationError) {
      setError(validationError);
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setSubmitting(true);
    try {
      await api.resetPassword(token, newPassword);
      setSuccessMsg('Your password has been reset successfully.');
    } catch (err) {
      setError(err?.message || 'Failed to reset password. The link may have expired.');
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
          <p className="text-xs text-tertiary">Set a new password</p>
        </div>

        {/* Success state */}
        {successMsg ? (
          <div className="flex flex-col items-center gap-4">
            <div
              className="flex items-center gap-2 px-3 py-2 rounded-md text-xs font-medium w-full"
              style={{ background: 'var(--color-success-muted)', color: 'var(--color-success)' }}
            >
              <CheckCircle size={14} />
              {successMsg}
            </div>
            <Link
              to="/login"
              className="flex items-center justify-center gap-2 h-9 w-full rounded-md text-sm font-medium"
              style={{
                background: 'var(--accent-blue)',
                color: '#fff',
                textDecoration: 'none',
              }}
            >
              Sign In
            </Link>
          </div>
        ) : (
          <>
            {/* Error message */}
            {error && (
              <div
                className="flex items-center gap-2 px-3 py-2 mb-4 rounded-md text-xs font-medium"
                style={{ background: 'var(--color-danger-muted)', color: 'var(--color-danger)' }}
              >
                <AlertCircle size={14} />
                <span>{error}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              {/* New password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-secondary">New password</label>
                <div className="relative">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    autoFocus
                    placeholder="At least 8 chars with a number or symbol"
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

              {/* Confirm password */}
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-secondary">Confirm password</label>
                <div className="relative">
                  <input
                    type={showConfirm ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    placeholder="Re-enter your new password"
                    className="w-full h-9 px-3 pr-9 rounded-md text-sm bg-app border border-main text-primary"
                    style={{ outline: 'none' }}
                    onFocus={(e) => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                    onBlur={(e) => { e.target.style.borderColor = 'var(--border-main)'; }}
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirm(!showConfirm)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-tertiary hover:text-secondary"
                    tabIndex={-1}
                  >
                    {showConfirm ? <EyeOff size={14} /> : <Eye size={14} />}
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
                {submitting ? 'Resetting…' : 'Reset Password'}
              </button>
            </form>

            {/* Link to request a new reset if token is expired */}
            <div className="mt-6 text-center text-xs text-tertiary">
              Link expired?{' '}
              <Link
                to="/auth/forgot-password"
                className="text-accent-blue hover:underline font-medium"
              >
                Request a new one
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
