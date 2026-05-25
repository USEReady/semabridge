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
            <Route path="/explore"       element={<Suspense fallback={<PageFallback />}><ExplorePage /></Suspense>} />
            <Route path="/projects"      element={<ProjectsPage />} />
            <Route path="/projects/new"  element={<Suspense fallback={<PageFallback />}><CreateProjectPage /></Suspense>} />
            <Route path="/projects/:id" element={<Suspense fallback={<PageFallback />}><ProjectDetailPage /></Suspense>} />
            <Route path="/projects/:id/edit" element={<Suspense fallback={<PageFallback />}><EditProjectPage /></Suspense>} />
            <Route path="/projects/:id/config" element={<Suspense fallback={<PageFallback />}><ProjectConfigPage /></Suspense>} />
            <Route path="/jobs"          element={<Suspense fallback={<PageFallback />}><ProjectJobsPage /></Suspense>} />
            <Route path="/model-mapping" element={<Navigate to="/projects/new?step=4" replace />} />
            <Route path="/comparator"    element={<Suspense fallback={<PageFallback />}><ComparatorPage /></Suspense>} />
            <Route path="/settings"      element={<Suspense fallback={<PageFallback />}><SettingsPage /></Suspense>} />
            <Route path="/global-config" element={<Suspense fallback={<PageFallback />}><GlobalConfigPage /></Suspense>} />
            <Route path="/version-control" element={<Suspense fallback={<PageFallback />}><VersionControlPage /></Suspense>} />
          </Route>
        </Route>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
}
