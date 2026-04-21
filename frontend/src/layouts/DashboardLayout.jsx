import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import {
  Map, FolderOpen, PlayCircle, Settings, GitBranch,
  Hexagon, LogOut, ChevronLeft, ChevronRight, Bell, Split,
} from 'lucide-react';
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
import { useUIStore } from '../store/uiStore';

// Nav items are now generated dynamically based on state

function SidebarNavIcon({ icon: Icon, active }) {
  return (
    <span
      aria-hidden="true"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 20,
        height: 20,
        flexShrink: 0,
        color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
      }}
    >
      <Icon
        size={18}
        strokeWidth={active ? 2.5 : 2}
        style={{ pointerEvents: 'none' }}
      />
    </span>
  );
}

export default function DashboardLayout() {
  const { user, logout } = useAuth();
  const { logs } = useLogs();
  const navigate = useNavigate();
  const collapsed = useUIStore(state => state.sidebarCollapsed);
  const setSidebarCollapsed = useUIStore(state => state.setSidebarCollapsed);
  const isAdvancedMode = useUIStore(state => state.isAdvancedMode);
  const [search, setSearch] = useState('');
  const [searchUseRegex, setSearchUseRegex] = useState(false);

  const NAV_ITEMS = [
    { to: '/projects',      label: 'Projects',        icon: FolderOpen },
    { to: '/jobs',          label: 'Runs',            icon: PlayCircle },
    { to: '/explore',       label: 'Explore',         icon: Map },
    { to: '/model-mapping', label: 'Model Mapping',   icon: GitBranch },
  ];
  
  if (isAdvancedMode) {
     NAV_ITEMS.push({ to: '/comparator', label: 'Comparator', icon: Split });
  }

  NAV_ITEMS.push({ to: '/settings', label: 'Settings', icon: Settings });

  // Overlay panel toggles
  const [showLogs, setShowLogs] = useState(false);
  const [showVersionControl, setShowVersionControl] = useState(false);
  const [showCommandPalette, setShowCommandPalette] = useState(false);

  const sidebarW = collapsed ? 64 : 240;

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
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* ── Sidebar ── */}
        <aside
          className="flex flex-col flex-shrink-0 theme-transition"
          style={{
            width: sidebarW,
            borderRight: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            overflow: 'hidden',
            transition: 'width 0.2s ease',
            zIndex: 20,
          }}
        >
          {/* Logo */}
          <div
            className="flex items-center gap-3 flex-shrink-0"
            style={{
              height: 64,
              padding: collapsed ? '0 20px' : '0 16px',
              borderBottom: '1px solid var(--border-main)',
              overflow: 'hidden',
            }}
          >
            <Hexagon size={22} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} strokeWidth={2.5} />
            {!collapsed && (
              <span
                className="font-bold text-primary"
                style={{ fontSize: 15, whiteSpace: 'nowrap', letterSpacing: '-0.01em' }}
              >
                SemaBridge
              </span>
            )}
          </div>

          {/* Nav */}
          <nav className="flex-1 overflow-y-auto no-scrollbar" style={{ padding: '12px 8px' }}>
            {NAV_ITEMS.map(({ to, label, icon }) => (
              <NavLink
                key={to}
                to={to}
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  `flex items-center rounded-lg mb-1 theme-transition ${isActive ? 'sidebar-link-active' : 'sidebar-link'}`
                }
                style={{ gap: collapsed ? 0 : 10, padding: collapsed ? '9px 0' : '9px 12px', justifyContent: collapsed ? 'center' : 'flex-start' }}
              >
                {({ isActive }) => (
                  <>
                    <SidebarNavIcon icon={icon} active={isActive} />
                    {!collapsed && (
                      <span
                        className="text-sm font-medium"
                        style={{
                          color: isActive ? 'var(--accent-blue)' : 'var(--text-secondary)',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {label}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          {/* User profile + collapse toggle */}
          <div
            className="flex-shrink-0"
            style={{ borderTop: '1px solid var(--border-main)', padding: '10px 8px' }}
          >
            {/* Collapse toggle */}
            <button
              onClick={() => setSidebarCollapsed(!collapsed)}
              className="sidebar-link flex items-center rounded-lg w-full mb-2 theme-transition"
              style={{
                gap: collapsed ? 0 : 10,
                padding: collapsed ? '7px 0' : '7px 12px',
                justifyContent: collapsed ? 'center' : 'flex-start',
                border: 'none',
                cursor: 'pointer',
                background: 'transparent',
              }}
              title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {collapsed
                ? <ChevronRight size={16} style={{ color: 'var(--text-tertiary)' }} />
                : (
                  <>
                    <ChevronLeft size={16} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                    <span className="text-xs text-tertiary" style={{ whiteSpace: 'nowrap' }}>Collapse</span>
                  </>
                )
              }
            </button>

            {/* User info */}
            <div
              className="flex items-center rounded-lg"
              style={{
                gap: collapsed ? 0 : 10,
                padding: collapsed ? '6px 0' : '6px 12px',
                justifyContent: collapsed ? 'center' : 'flex-start',
              }}
            >
              <div
                className="flex items-center justify-center rounded-full flex-shrink-0"
                style={{
                  width: 28,
                  height: 28,
                  background: 'var(--color-accent-faint)',
                  color: 'var(--accent-blue)',
                  fontSize: 12,
                  fontWeight: 700,
                }}
              >
                {user?.username ? user.username[0].toUpperCase() : '?'}
              </div>
              {!collapsed && (
                <div className="flex-1 min-w-0">
                  <p className="text-primary text-xs font-semibold truncate">{user?.username ?? 'User'}</p>
                  <p className="text-tertiary" style={{ fontSize: 10, whiteSpace: 'nowrap' }}>Authenticated</p>
                </div>
              )}
              {!collapsed && (
                <button
                  onClick={logout}
                  title="Sign out"
                  className="flex items-center justify-center rounded-md theme-transition"
                  style={{
                    width: 26,
                    height: 26,
                    color: 'var(--text-tertiary)',
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    flexShrink: 0,
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--color-danger)'; e.currentTarget.style.background = 'var(--color-danger-muted)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-tertiary)'; e.currentTarget.style.background = 'transparent'; }}
                >
                  <LogOut size={14} />
                </button>
              )}
            </div>

            <button
              onClick={handleEmergencyReset}
              title="Emergency Reset State"
              style={{
                width: collapsed ? 28 : '100%',
                marginTop: 6,
                padding: collapsed ? '4px 0' : '4px 8px',
                borderRadius: 6,
                border: '1px dashed transparent',
                background: 'transparent',
                color: 'var(--text-tertiary)',
                fontSize: 10,
                textAlign: 'center',
                cursor: 'pointer',
                opacity: 0.18,
                transition: 'opacity 0.2s ease, border-color 0.2s ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.opacity = '0.8';
                e.currentTarget.style.borderColor = 'var(--border-main)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.opacity = '0.18';
                e.currentTarget.style.borderColor = 'transparent';
              }}
            >
              {collapsed ? 'R' : 'Reset State'}
            </button>
          </div>
        </aside>

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
              width={240}
              onFocus={() => setShowCommandPalette(true)}
            />
            <div className="flex-1" />
            <ThemeToggle />

            {/* Version Control */}
            <button
              title="Version Control"
              onClick={() => setShowVersionControl(true)}
              className="flex items-center justify-center rounded-lg theme-transition"
              style={{
                width: 34,
                height: 34,
                color: 'var(--text-tertiary)',
                background: 'transparent',
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; e.currentTarget.style.color = 'var(--accent-blue)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
                width: 34,
                height: 34,
                color: 'var(--text-tertiary)',
                background: 'transparent',
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
                position: 'relative',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; e.currentTarget.style.color = 'var(--accent-blue)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}
            >
              <Bell size={15} />
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