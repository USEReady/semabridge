import { useEffect } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { LogsProvider } from './context/LogsContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import { SyncStatusProvider } from './context/SyncStatusContext';
import { useUIStore } from './store/uiStore';
import MobileGate from './components/MobileGate';
import { useState } from 'react';
import ProtectedRoute from './components/common/ProtectedRoute';

// Layouts
import DashboardLayout from './layouts/DashboardLayout';
import { ConfigurationProvider } from './context/ConfigurationContext';

// Pages
import ExplorePage      from './pages/ExplorePage';
import ProjectsPage     from './pages/ProjectsPage';
import CreateProjectPage from './pages/CreateProjectPage';
import EditProjectPage  from './pages/EditProjectPage';
import ProjectConfigPage from './pages/ProjectConfigPage';
import ProjectJobsPage  from './pages/ProjectJobsPage';
import SettingsPage    from './pages/SettingsPage';
import GlobalConfigPage from './pages/GlobalConfigPage';
import ProjectDetailPage from './pages/ProjectDetailPage';
import ComparatorPage from './pages/ComparatorPage';
import VersionControlPage from './pages/VersionControlPage';

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
        {/* Protected: All app routes */}
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
          <Route path="/explore"       element={<ExplorePage />} />
          <Route path="/projects"      element={<ProjectsPage />} />
          <Route path="/projects/new"  element={<CreateProjectPage />} />
          <Route path="/projects/:id" element={<ProjectDetailPage />} />
          <Route path="/projects/:id/edit" element={<EditProjectPage />} />
          <Route path="/projects/:id/config" element={<ProjectConfigPage />} />
          <Route path="/jobs"          element={<ProjectJobsPage />} />
          <Route path="/model-mapping" element={<Navigate to="/projects/new?step=4" replace />} />
          <Route path="/comparator"    element={<ComparatorPage />} />
          <Route path="/settings"      element={<SettingsPage />} />
          <Route path="/global-config" element={<GlobalConfigPage />} />
          <Route path="/version-control" element={<VersionControlPage />} />
          </Route>
        </Route>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
}
