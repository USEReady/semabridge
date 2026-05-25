import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Hexagon } from 'lucide-react';

/**
 * ProtectedRoute — wraps private routes.
 * If not authenticated, redirects to /login preserving the intended destination.
 * Shows a loading spinner while auth state is being confirmed.
 */
export default function ProtectedRoute() {
  const { isAuthenticated, loading, token } = useAuth();
  const location = useLocation();

  if (loading && !token) {
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

  // Optimistic route access: if a token exists, allow app routes while
  // AuthContext finishes background profile recovery.
  if (!isAuthenticated && token) {
    return <Outlet />;
  }

  if (!isAuthenticated) {
    const nextPath = `${location.pathname}${location.search}${location.hash}`;
    return <Navigate to="/login" replace state={{ from: nextPath }} />;
  }

  return <Outlet />;
}
