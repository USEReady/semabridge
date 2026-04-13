import { useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { LogsProvider } from './context/LogsContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import { SyncStatusProvider } from './context/SyncStatusContext';
import { useAuth } from './context/AuthContext';
import { useUIStore } from './store/uiStore';

// Layouts
import DashboardLayout from './layouts/DashboardLayout';

// Pages
import LoginPage        from './pages/LoginPage';
import ExplorePage      from './pages/ExplorePage';
import ProjectsPage     from './pages/ProjectsPage';
import CreateProjectPage from './pages/CreateProjectPage';
import ProjectConfigPage from './pages/ProjectConfigPage';
import ProjectJobsPage  from './pages/ProjectJobsPage';
import ModelMappingPage from './pages/ModelMappingPage';
import SettingsPage    from './pages/SettingsPage';
import ProjectDetailPage from './pages/ProjectDetailPage';

/**
 * Auth gate — shows loading spinner while auto-login resolves.
 * In dev mode, auto-login runs transparently so user never sees the login page.
 * Falls back to login page redirect only if auto-login fails.
 */
function ProtectedRoute({ children }) {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', background: 'var(--bg-app, #0d1117)',
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{
            width: 28, height: 28,
            border: '2.5px solid rgba(88, 166, 255, 0.3)',
            borderTopColor: '#58a6ff',
            borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
            margin: '0 auto 12px',
          }} />
          <span style={{ color: '#8b949e', fontSize: 13 }}>Initializing SemaBridge…</span>
        </div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  // If auto-login failed and user is still not authenticated, show login
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children;
}

export default function App() {
  const location = useLocation();
  const hasHydrated = useUIStore(state => state.hasHydrated);
  const lastNavigatedPath = useUIStore(state => state.lastNavigatedPath);
  const setLastNavigatedPath = useUIStore(state => state.setLastNavigatedPath);

  useEffect(() => {
    if (!hasHydrated) return;
    const nextPath = `${location.pathname}${location.search}${location.hash}`;
    if (nextPath === lastNavigatedPath) return;
    setLastNavigatedPath(nextPath || '/projects');
  }, [
    hasHydrated,
    location.pathname,
    location.search,
    location.hash,
    lastNavigatedPath,
    setLastNavigatedPath,
  ]);

  return (
    <div style={{ minHeight: '100vh' }}>
      <Routes>
        {/* Public: Login page (for future production use) */}
        <Route path="/login" element={<LoginPage />} />

        {/* Protected: All app routes — auto-login handles auth transparently */}
        <Route element={
          <ProtectedRoute>
            <WorkspaceProvider>
              <LogsProvider>
                <SyncStatusProvider>
                  <DashboardLayout />
                </SyncStatusProvider>
              </LogsProvider>
            </WorkspaceProvider>
          </ProtectedRoute>
        }>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="/explore"       element={<ExplorePage />} />
          <Route path="/projects"      element={<ProjectsPage />} />
          <Route path="/projects/new"  element={<CreateProjectPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/projects/:id/edit" element={<ProjectConfigPage />} />
          <Route path="/projects/:id/config" element={<ProjectConfigPage />} />
          <Route path="/jobs"          element={<ProjectJobsPage />} />
          <Route path="/model-mapping" element={<ModelMappingPage />} />
          <Route path="/settings"      element={<SettingsPage />} />
        </Route>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
}
