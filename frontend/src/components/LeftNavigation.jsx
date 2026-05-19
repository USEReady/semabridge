import { useState, useEffect } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  Map, FolderOpen, PlayCircle, Settings,
  Split, PanelLeftClose, PanelLeftOpen,
  History as HistoryIcon, Bell,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import ContextBadge from './ContextBadge';
import './Sidebar.css';

function SidebarNavIcon({ icon: Icon, active }) {
  return (
    <span
      aria-hidden="true"
      className="sidebar-icon-container"
      style={{
        color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
      }}
    >
      <Icon
        size={20}
        strokeWidth={active ? 2.5 : 2}
      />
    </span>
  );
}

export default function LeftNavigation({ onEmergencyReset, searchQuery = '' }) {
  const { user } = useAuth();
  const navigate = useNavigate();

  // 1. Initialize state from localStorage (default to floating/false for a cleaner first impression)
  const [isPinned, setIsPinned] = useState(() => {
    const saved = localStorage.getItem('semabridge_sidebar_pinned');
    return saved !== null ? JSON.parse(saved) : false;
  });

  // 2. Persist to localStorage whenever it changes
  useEffect(() => {
    localStorage.setItem('semabridge_sidebar_pinned', JSON.stringify(isPinned));
  }, [isPinned]);

  const NAV_ITEMS = [
    { to: '/projects',      label: 'Projects',        icon: FolderOpen },
    { to: '/jobs',          label: 'Runs',            icon: PlayCircle },
    { to: '/explore',       label: 'Explore',         icon: Map },
    { to: '/version-control', label: 'Version Control', icon: HistoryIcon },
    { to: '/comparator',    label: 'Comparator',      icon: Split },
    { to: '/settings/notifications/channels', label: 'Notifications', icon: Bell },
    { to: '/settings',      label: 'Settings',        icon: Settings },
  ];

  return (
    <>
      {/* 
        THE SPACER: 
        When unpinned (floating), we need a 64px spacer to prevent 
        the main content from sliding under the collapsed sidebar.
      */}
      <aside className={`sidebar ${isPinned ? 'pinned' : 'unpinned'}`}>
        {/* Top Logo Area */}
        <div className="nav-item logo-area">
          <div className="sidebar-icon-container">
             <img src="/favicon.svg" alt="SemaBridge" style={{ width: 28, height: 28 }} />
          </div>
          <span className="nav-label font-black text-primary tracking-tight" style={{ fontSize: 16 }}>SemaBridge</span>
        </div>

        {/* Navigation Links */}

        {/* Navigation Links */}
        <nav className="sidebar-nav">
          {NAV_ITEMS.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `nav-item ${isActive ? 'active' : ''}`
              }
            >
              {({ isActive }) => (
                <>
                  <SidebarNavIcon icon={icon} active={isActive} />
                  <span className="nav-label">{label}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Bottom Area */}
        <div className="sidebar-footer">
          <ContextBadge 
            variant="footer" 
            isCollapsed={!isPinned} 
            searchQuery={searchQuery}
            onOpenSettings={() => navigate('/settings')}
          />
          <button 
            onClick={() => setIsPinned(!isPinned)}
            className={`
              toggle-button flex items-center w-full py-4 transition-colors duration-200
              ${isPinned ? 'px-5 bg-surface-raised/50 text-primary' : 'px-0 justify-center text-secondary'}
              hover:bg-surface-hover hover:text-primary
            `}
            title={isPinned ? 'Unpin sidebar' : 'Pin sidebar'}
          >
            <div className="flex-shrink-0 flex items-center justify-center" style={{ width: 24, height: 24 }}>
              {isPinned ? <PanelLeftClose size={20} /> : <PanelLeftOpen size={20} />}
            </div>
            <span className="ml-4 text-sm font-medium whitespace-nowrap nav-label">
              {isPinned ? 'Collapse Menu' : 'Pin Menu'}
            </span>
          </button>

          {/* Emergency Reset */}
          <button
            onClick={onEmergencyReset}
            title="Emergency Reset State"
            className="nav-item reset-button"
          >
            <span className="reset-icon">R</span>
            <span className="nav-label">Reset State</span>
          </button>
        </div>
      </aside>
      {!isPinned && <div className="sidebar-spacer" />}
    </>
  );
}
