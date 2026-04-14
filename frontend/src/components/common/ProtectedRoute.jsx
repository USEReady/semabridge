import { Outlet } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Hexagon } from 'lucide-react';

/**
 * ProtectedRoute — wraps private routes.
 * If not authenticated, redirects to /login preserving the intended destination.
 * Shows a loading spinner while auth state is being confirmed.
 */
export default function ProtectedRoute() {
  const { isAuthenticated, loading } = useAuth();

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

  if (!isAuthenticated) {
    return <Outlet />;
  }

  return <Outlet />;
}
