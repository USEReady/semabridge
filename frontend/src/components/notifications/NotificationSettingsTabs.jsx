import { NavLink } from 'react-router-dom';

const TABS = [
  { to: '/settings/notifications/channels', label: 'Channels' },
  { to: '/settings/notifications/routing', label: 'Rules' },
  { to: '/settings/notifications/templates', label: 'Templates' },
  { to: '/settings/notifications/logs', label: 'Logs' },
  { to: '/settings/notifications/analytics', label: 'Analytics' },
];

export default function NotificationSettingsTabs() {
  return (
    <nav
      aria-label="Notification settings"
      style={{
        display: 'flex',
        gap: 4,
        borderBottom: '1px solid var(--border-main)',
        overflowX: 'auto',
      }}
    >
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          style={({ isActive }) => ({
            padding: '10px 12px',
            color: isActive ? 'var(--accent-blue)' : 'var(--text-secondary)',
            borderBottom: isActive ? '2px solid var(--accent-blue)' : '2px solid transparent',
            fontSize: 13,
            fontWeight: 600,
            textDecoration: 'none',
            whiteSpace: 'nowrap',
          })}
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
}
