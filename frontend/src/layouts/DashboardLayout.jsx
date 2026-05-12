import usePageCache from '../hooks/usePageCache';
import { Outlet } from 'react-router-dom';
import { Bell } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import ThemeToggle from '../components/ThemeToggle';
import SearchInput from '../components/common/SearchInput';
import ToastContainer from '../components/ToastContainer';
import LogsPanel from '../components/LogsPanel';
import VersionControlPanel from '../components/VersionControlPanel';
import CommandPalette from '../components/CommandPalette';
import StatusBar from '../components/StatusBar';
import LiveValidator from '../components/LiveValidator';
import { useLogs } from '../context/LogsContext';
import { useNavigate } from 'react-router-dom';
import LeftNavigation from '../components/LeftNavigation';
import ProjectContextHeader from '../components/ProjectContextHeader';



export default function DashboardLayout() {
  const { user, logout } = useAuth();
  const { logs } = useLogs();
  const navigate = useNavigate();
  const [search, setSearch] = usePageCache('layout:search', '');
  const [searchUseRegex, setSearchUseRegex] = usePageCache('layout:searchUseRegex', false);

  // Overlay panel toggles
  const [showLogs, setShowLogs] = usePageCache('layout:showLogs', false);
  const [showVersionControl, setShowVersionControl] = usePageCache('layout:showVersionControl', false);
  const [showCommandPalette, setShowCommandPalette] = usePageCache('layout:showCommandPalette', false);

  // Notification badge: count warnings + errors
  const unreadCount = logs.filter(l => l.severity === 'warning' || l.severity === 'error').length;

  // CommandPalette action handler — navigate or toggle overlays
  const handlePaletteAction = (action) => {
    switch (action) {
      case 'openPalette':        setShowCommandPalette(true); break;
      case 'toggleRepoMap':      navigate('/explore'); break;
      case 'toggleVersionControl': setShowVersionControl(true); break;
      case 'toggleLogs':         setShowLogs(v => !v); break;
      case 'toggleTerminal':     navigate('/projects'); break;
      case 'toggleConnections':  navigate('/settings'); break;
      case 'sync':               document.dispatchEvent(new CustomEvent('semabridge:sync')); break;
      case 'save':               document.dispatchEvent(new CustomEvent('semabridge:save')); break;
      case 'openModel':          navigate('/projects'); break;
      default: break;
    }
  };

  const handleEmergencyReset = () => {
    localStorage.clear();
    sessionStorage.clear();
    window.location.href = '/';
  };

  return (
    <div
      className="flex flex-col h-screen overflow-hidden bg-app theme-transition"
      style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' }}
    >
      {/* ── Main row: Sidebar + Content ── */}
      <div className="flex flex-1 overflow-hidden min-h-0 relative">
        {/* ── Sidebar (Floating or Pinned) ── */}
        <LeftNavigation 
          onEmergencyReset={handleEmergencyReset} 
          searchQuery={search}
        />

        {/* ── Main content area ── */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <ProjectContextHeader 
            onShowLogs={() => setShowLogs(true)}
            onShowVersionControl={() => setShowVersionControl(true)}
            unreadCount={unreadCount}
            onShowCommandPalette={setShowCommandPalette}
            searchQuery={search}
            setSearchQuery={setSearch}
            searchUseRegex={searchUseRegex}
            setSearchUseRegex={setSearchUseRegex}
          />

          {/* Page content */}
          <main
            className="flex-1 overflow-y-auto custom-scrollbar"
            style={{ background: 'var(--bg-app)' }}
          >
            <Outlet />
          </main>
        </div>
      </div>

      {/* ── Status Bar ── */}
      <StatusBar />

      {/* ── Global Overlays ── */}
      <ToastContainer onOpenLogs={() => setShowLogs(true)} />
      <LiveValidator />

      <LogsPanel
        isOpen={showLogs}
        onClose={() => setShowLogs(false)}
      />

      <VersionControlPanel
        isOpen={showVersionControl}
        onClose={() => setShowVersionControl(false)}
      />

      <CommandPalette
        isOpen={showCommandPalette}
        onClose={() => setShowCommandPalette(false)}
        onAction={handlePaletteAction}
        queryValue={search}
        onQueryChange={setSearch}
        useRegexValue={searchUseRegex}
        onUseRegexChange={setSearchUseRegex}
      />
    </div>
  );
}
