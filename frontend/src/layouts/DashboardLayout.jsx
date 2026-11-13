import usePageCache from '../hooks/usePageCache';
import { Outlet, useNavigate, useLocation, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';
import { Bell } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import ThemeToggle from '../components/ThemeToggle';
import SearchInput from '../components/common/SearchInput';
import ToastContainer from '../components/ToastContainer';
import LogsPanel from '../components/LogsPanel';
import CommandPalette from '../components/CommandPalette';
import LiveValidator from '../components/LiveValidator';
import { useLogs } from '../context/LogsContext';
import LeftNavigation from '../components/LeftNavigation';

// Top-bar breadcrumb labels for each first path segment (matches the sidebar).
const SECTION_LABELS = {
  projects: 'Projects', runs: 'Runs', ask: 'Ask AI', ontology: 'Explorer',
  'metric-comparator': 'Metric Comparator', 'blast-radius': 'Blast Radius',
  drift: 'Data Monitoring', 'drift-monitoring': 'Data Monitoring', 'data-monitoring': 'Data Monitoring', 'reverse-sync': 'Reverse Sync',
  'bi-truth': 'BI-Truth Suite', 'cross-platform': 'Cross-Platform',
  map: 'Model map', history: 'History', compare: 'Compare', settings: 'Settings',
  'global-config': 'Settings',
};

function Breadcrumb() {
  const { pathname } = useLocation();
  const [section, id, sub] = pathname.split('/').filter(Boolean);
  // Shares the Projects page's cached list; fetched here only when a project
  // page is opened directly.
  const { data: projectList = [] } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.listProjects({ limit: 200, offset: 0 }),
    enabled: section === 'projects' && Boolean(id) && id !== 'new',
    staleTime: 60000,
  });
  const label = SECTION_LABELS[section];
  if (!label) return <span style={{ flex: 1 }} />;
  const crumbs = [{ text: label, to: `/${section === 'global-config' ? 'settings' : section}` }];
  if (section === 'projects' && id) {
    if (id === 'new') crumbs.push({ text: 'New project' });
    else {
      const p = (projectList || []).find(x => String(x.id) === id || String(x.project_id) === id);
      crumbs.push({ text: p?.name || id, to: `/projects/${id}` });
      if (sub === 'edit') crumbs.push({ text: 'Edit' });
      if (sub === 'config') crumbs.push({ text: 'Settings' });
    }
  }
  return (
    <nav aria-label="Breadcrumb" style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, whiteSpace: 'nowrap', overflow: 'hidden' }}>
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        return (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            {i > 0 && <span style={{ color: 'var(--text-quaternary)' }}>/</span>}
            {last || !c.to
              ? <span style={{ color: last ? 'var(--text-primary)' : 'var(--text-tertiary)', fontWeight: last ? 600 : 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>{c.text}</span>
              : <Link to={c.to} style={{ color: 'var(--text-tertiary)', textDecoration: 'none' }}>{c.text}</Link>}
          </span>
        );
      })}
    </nav>
  );
}

export default function DashboardLayout() {
  const { user, logout } = useAuth();
  const { logs } = useLogs();
  const navigate = useNavigate();
  const [search, setSearch] = usePageCache('layout:search', '');
  const [searchUseRegex, setSearchUseRegex] = usePageCache('layout:searchUseRegex', false);

  // Overlay panel toggles
  const [showLogs, setShowLogs] = usePageCache('layout:showLogs', false);
  const [showCommandPalette, setShowCommandPalette] = usePageCache('layout:showCommandPalette', false);

  // Notification badge: count warnings + errors
  const unreadCount = logs.filter(l => l.severity === 'warning' || l.severity === 'error').length;

  // CommandPalette action handler — navigate or toggle overlays
  const handlePaletteAction = (action) => {
    switch (action) {
      case 'openPalette':          setShowCommandPalette(true); break;
      case 'toggleRepoMap':        navigate('/map'); break;
      case 'toggleVersionControl': navigate('/history'); break;
      case 'newProject':           navigate('/projects/new'); break;
      case 'askAi':                navigate('/ask'); break;
      case 'openOntology':         navigate('/ontology'); break;
      case 'openRuns':             navigate('/runs'); break;
      case 'toggleLogs':           setShowLogs(v => !v); break;
      case 'toggleConnections':    navigate('/settings'); break;
      case 'sync':                 document.dispatchEvent(new CustomEvent('semabridge:sync')); break;
      case 'save':                 document.dispatchEvent(new CustomEvent('semabridge:save')); break;
      case 'openModel':            navigate('/projects'); break;
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
      style={{ fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" }}
    >
      {/* ── Top row: Sidebar + Main ── */}
      <div className="flex flex-1 overflow-hidden min-h-0 relative">
        {/* ── Sidebar (Floating or Pinned) ── */}
        <LeftNavigation
          onEmergencyReset={handleEmergencyReset}
          searchQuery={search}
        />

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
            <Breadcrumb />
            <SearchInput
              value={search}
              onChange={(next) => {
                setSearch(next);
                setShowCommandPalette(true);
              }}
              useRegex={searchUseRegex}
              onToggleRegex={setSearchUseRegex}
              allowRegex
              placeholder="Search projects, models, measures… (Ctrl+K)"
              width={340}
              onFocus={() => setShowCommandPalette(true)}
            />
            <ThemeToggle />

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

      {/* ── Global Overlays ── */}
      <ToastContainer onOpenLogs={() => setShowLogs(true)} />
      <LiveValidator />

      <LogsPanel
        isOpen={showLogs}
        onClose={() => setShowLogs(false)}
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
