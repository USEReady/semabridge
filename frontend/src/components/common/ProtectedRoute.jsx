import { Outlet } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { Hexagon } from 'lucide-react';

/**
 * ProtectedRoute — wraps private routes.
 * Shows a loading spinner while auth state is being confirmed.
 * Login-route redirects were removed; unauthenticated users now see a static notice.
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
    return (
      <div
        className="flex items-center justify-center h-screen bg-app"
        style={{ flexDirection: 'column', gap: 12 }}
      >
        <Hexagon size={28} className="text-accent-blue" />
        <span className="text-secondary text-sm">
          Authentication required. Login UI is archived in scratch.
        </span>
      </div>
    );
  }

  return <Outlet />;
}
