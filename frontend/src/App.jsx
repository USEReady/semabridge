import { useEffect, lazy, Suspense } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { LogsProvider } from './context/LogsContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import { SyncStatusProvider } from './context/SyncStatusContext';
import { useUIStore } from './store/uiStore';
import MobileGate from './components/MobileGate';
import { useState } from 'react';
import ProtectedRoute from './components/common/ProtectedRoute';
import { ConfigurationProvider } from './context/ConfigurationContext';
import ErrorBoundary from './components/ErrorBoundary';

// Layouts
import DashboardLayout from './layouts/DashboardLayout';

// Pages — eagerly load the two most-visited routes; lazy-load the rest
import ProjectsPage     from './pages/ProjectsPage';
import LoginPage        from './pages/LoginPage';

const ExplorePage        = lazy(() => import('./pages/ExplorePage'));
const CreateProjectPage  = lazy(() => import('./pages/CreateProjectPage'));
const EditProjectPage    = lazy(() => import('./pages/EditProjectPage'));
const ProjectConfigPage  = lazy(() => import('./pages/ProjectConfigPage'));
const ProjectJobsPage    = lazy(() => import('./pages/ProjectJobsPage'));
const SettingsPage       = lazy(() => import('./pages/SettingsPage'));
const GlobalConfigPage   = lazy(() => import('./pages/GlobalConfigPage'));
const ProjectDetailPage  = lazy(() => import('./pages/ProjectDetailPage'));
const ComparatorPage     = lazy(() => import('./pages/ComparatorPage'));
const VersionControlPage = lazy(() => import('./pages/VersionControlPage'));
const ForgotPasswordPage = lazy(() => import('./pages/ForgotPasswordPage'));
const ResetPasswordPage  = lazy(() => import('./pages/ResetPasswordPage'));
const UnauthorizedPage   = lazy(() => import('./pages/UnauthorizedPage'));

function PageFallback() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-tertiary)', fontSize: 13 }}>
      Loading…
    </div>
  );
}

export default function App() {
  const [isMobile, setIsMobile] = useState(false);
  const location = useLocation();
  const hasHydrated = useUIStore(state => state.hasHydrated);
  const lastNavigatedPath = useUIStore(state => state.lastNavigatedPath);
  const setLastNavigatedPath = useUIStore(state => state.setLastNavigatedPath);

  useEffect(() => {
    const checkMobile = () => {
      // Basic mobile detection: checking width and user agent
      const isMobileDevice = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
      setIsMobile(isMobileDevice);
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

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

  if (isMobile) {
    return <MobileGate />;
  }

  return (
    <div style={{ minHeight: '100vh' }}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        {/* Public: Password reset flow */}
        <Route path="/auth/forgot-password" element={<Suspense fallback={<PageFallback />}><ForgotPasswordPage /></Suspense>} />
        <Route path="/auth/reset-password/:token" element={<Suspense fallback={<PageFallback />}><ResetPasswordPage /></Suspense>} />
        <Route path="/unauthorized" element={<Suspense fallback={<PageFallback />}><UnauthorizedPage /></Suspense>} />

        {/* Protected: All app routes — auto-login handles auth transparently */}
        <Route element={<ProtectedRoute />}>
          <Route element={
            <ConfigurationProvider>
              <WorkspaceProvider>
                <LogsProvider>
                  <SyncStatusProvider>
                    <DashboardLayout />
                  </SyncStatusProvider>
                </LogsProvider>
              </WorkspaceProvider>
            </ConfigurationProvider>
          }>
            <Route index element={<Navigate to="/projects" replace />} />
            <Route path="/explore"       element={<ErrorBoundary><Suspense fallback={<PageFallback />}><ExplorePage /></Suspense></ErrorBoundary>} />
            <Route path="/projects"      element={<ProjectsPage />} />
            <Route path="/projects/new"  element={<ErrorBoundary><Suspense fallback={<PageFallback />}><CreateProjectPage /></Suspense></ErrorBoundary>} />
            <Route path="/projects/:id" element={<ErrorBoundary><Suspense fallback={<PageFallback />}><ProjectDetailPage /></Suspense></ErrorBoundary>} />
            <Route path="/projects/:id/edit" element={<ErrorBoundary><Suspense fallback={<PageFallback />}><EditProjectPage /></Suspense></ErrorBoundary>} />
            <Route path="/projects/:id/config" element={<ErrorBoundary><Suspense fallback={<PageFallback />}><ProjectConfigPage /></Suspense></ErrorBoundary>} />
            <Route path="/jobs"          element={<ErrorBoundary><Suspense fallback={<PageFallback />}><ProjectJobsPage /></Suspense></ErrorBoundary>} />
            <Route path="/model-mapping" element={<Navigate to="/projects/new?step=4" replace />} />
            <Route path="/comparator"    element={<ErrorBoundary><Suspense fallback={<PageFallback />}><ComparatorPage /></Suspense></ErrorBoundary>} />
            <Route element={<ProtectedRoute requiredRole="admin" />}>
              <Route path="/settings"      element={<ErrorBoundary><Suspense fallback={<PageFallback />}><SettingsPage /></Suspense></ErrorBoundary>} />
              <Route path="/global-config" element={<ErrorBoundary><Suspense fallback={<PageFallback />}><GlobalConfigPage /></Suspense></ErrorBoundary>} />
            </Route>
            <Route path="/version-control" element={<ErrorBoundary><Suspense fallback={<PageFallback />}><VersionControlPage /></Suspense></ErrorBoundary>} />
          </Route>
        </Route>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
}
