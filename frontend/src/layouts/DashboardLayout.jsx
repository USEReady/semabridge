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
      {/* ── Top row: Sidebar + Main ── */}
      <div className="flex flex-1 overflow-hidden min-h-0 relative">
        {/* ── Sidebar (Floating or Pinned) ── */}
        <LeftNavigation onEmergencyReset={handleEmergencyReset} />


        {/* ── Main area ── */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          {/* App header */}
          <header
            className="flex items-center gap-3 flex-shrink-0"
            style={{
              height: 64,
              padding: '0 20px',
              borderBottom: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
            }}
          >
            <SearchInput
              value={search}
              onChange={(next) => {
                setSearch(next);
                setShowCommandPalette(true);
              }}
              useRegex={searchUseRegex}
              onToggleRegex={setSearchUseRegex}
              allowRegex
              placeholder="Search… (Ctrl+K)"
              width="100%"
              className="flex-1"
              onFocus={() => setShowCommandPalette(true)}
            />
            <ThemeToggle />

            {/* Version Control */}
            <button
              title="Version Control"
              onClick={() => setShowVersionControl(true)}
              className="flex items-center justify-center rounded-lg theme-transition"
              style={{
                width: 40,
                height: 40,
                color: 'var(--text-tertiary)',
                background: 'transparent',
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; e.currentTarget.style.color = 'var(--accent-blue)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}
            >
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/>
                <path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9"/><path d="M12 12v3"/>
              </svg>
            </button>

            {/* Notifications / Logs */}
            <button
              title="Logs & Activity"
              onClick={() => setShowLogs(true)}
              className="flex items-center justify-center rounded-lg theme-transition"
              style={{
                width: 40,
                height: 40,
                color: 'var(--text-tertiary)',
                background: 'transparent',
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
                position: 'relative',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; e.currentTarget.style.color = 'var(--accent-blue)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}
            >
              <Bell size={19} />
              {unreadCount > 0 && (
                <span
                  style={{
                    position: 'absolute',
                    top: 4,
                    right: 4,
                    minWidth: 14,
                    height: 14,
                    borderRadius: '50%',
                    background: 'var(--color-danger)',
                    color: '#fff',
                    fontSize: 8,
                    fontWeight: 700,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    padding: '0 2px',
                  }}
                >
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </button>
          </header>

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
