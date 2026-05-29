import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Hexagon } from 'lucide-react';

/**
 * ProtectedRoute — wraps private routes.
 * If not authenticated, redirects to /login preserving the intended destination.
 * Shows a loading spinner while auth state is being confirmed.
 *
 * Security: never renders protected content until isAuthenticated is
 * confirmed true AND loading has resolved. This prevents an unauthenticated
 * user with a stale/invalid token in localStorage from accessing app routes
 * while the bootstrap check is still in-flight.
 */
export default function ProtectedRoute() {
  const { isAuthenticated, loading } = useAuth();
  const location = useLocation();

  // Block rendering until auth resolution is complete — show spinner.
  if (loading) {
    return (
      <div
        className="flex items-center justify-center h-screen bg-app"
        style={{ flexDirection: 'column', gap: 12 }}
      >
        <Hexagon size={28} className="text-accent-blue animate-pulse" />
        <span className="text-secondary text-sm">Loading…</span>
      </div>
    );
  }

  // Auth resolved and user is not authenticated — redirect to login.
  if (!isAuthenticated) {
    const nextPath = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to="/login" replace state={{ from: nextPath }} />;
  }

  return <Outlet />;
}
