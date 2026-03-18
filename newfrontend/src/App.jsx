import { Routes, Route, Navigate } from 'react-router-dom';
import { LogsProvider } from './context/LogsContext';
import { WorkspaceProvider } from './context/WorkspaceContext';

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

export default function App() {
  return (
    <WorkspaceProvider>
      <LogsProvider>
        <Routes>
          <Route element={<DashboardLayout />}>
            <Route index element={<Navigate to="/explore" replace />} />
            <Route path="/explore"       element={<ExplorePage />} />
            <Route path="/projects"      element={<ProjectsPage />} />
            <Route path="/projects/new"  element={<CreateProjectPage />} />
            <Route path="/projects/:id/config" element={<ProjectConfigPage />} />
            <Route path="/jobs"          element={<ProjectJobsPage />} />
            <Route path="/model-mapping" element={<ModelMappingPage />} />
            <Route path="/settings"      element={<SettingsPage />} />
          </Route>

          {/* Fallback */}
          <Route path="*" element={<Navigate to="/explore" replace />} />
        </Routes>
      </LogsProvider>
    </WorkspaceProvider>
  );
}
