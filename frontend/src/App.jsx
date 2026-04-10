import { useEffect } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import { LogsProvider } from './context/LogsContext';
import { WorkspaceProvider } from './context/WorkspaceContext';
import { SyncStatusProvider } from './context/SyncStatusContext';
import { useUIStore } from './store/uiStore';

// Layouts
import DashboardLayout from './layouts/DashboardLayout';

// Pages
import ExplorePage     from './pages/ExplorePage';
import ProjectsPage    from './pages/ProjectsPage';
import CreateProjectPage from './pages/CreateProjectPage';
import ProjectConfigPage from './pages/ProjectConfigPage';
import ProjectJobsPage from './pages/ProjectJobsPage';
import ModelMappingPage from './pages/ModelMappingPage';
import SettingsPage    from './pages/SettingsPage';
import ProjectDetailPage from './pages/ProjectDetailPage';

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
      <WorkspaceProvider>
        <LogsProvider>
          <SyncStatusProvider>
            <Routes>
              <Route element={<DashboardLayout />}>
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
              <Route path="*" element={<Navigate to="/explore" replace />} />
            </Routes>
          </SyncStatusProvider>
        </LogsProvider>
      </WorkspaceProvider>
    </div>
  );
}
