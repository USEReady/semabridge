import { Outlet } from 'react-router-dom';

/**
 * AuthLayout — minimal centered layout for authentication screens.
 * No sidebar, just a full-screen centered container.
 */
export default function AuthLayout() {
  return (
    <div
      className="flex items-center justify-center min-h-screen bg-app"
      style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' }}
    >
      <Outlet />
    </div>
  );
}
