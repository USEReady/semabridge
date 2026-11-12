import { useState } from 'react';
import { Clock, Search, Check, Globe, Sun, Moon, Monitor } from 'lucide-react';
import Modal from '../common/Modal';
import { useTheme } from '../../context/ThemeProvider';
import { SectionHeader, Card, ActionButton } from './settingsUi';

const TIMEZONES = [
  'UTC',
  'Asia/Kolkata',
  'Asia/Dubai',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Sao_Paulo',
  'Australia/Sydney',
];

const systemTimeZone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
};

function readTimezone() {
  try { return localStorage.getItem('semabridge-timezone') || systemTimeZone(); } catch { return systemTimeZone(); }
}

function Row({ icon, title, description, last = false, children }) {
  const Icon = icon;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '14px 0', borderBottom: last ? 'none' : '1px solid var(--border-main)', flexWrap: 'wrap' }}>
      <Icon size={16} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{title}</div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>{description}</div>
      </div>
      {children}
    </div>
  );
}

/** Settings › Preferences — timezone (stored per browser) and theme. */
export default function SettingsPreferences() {
  const { theme, setTheme } = useTheme();
  const [timezone, setTimezone] = useState(readTimezone);
  const [modalOpen, setModalOpen] = useState(false);
  const [query, setQuery] = useState('');

  const pickTimezone = (tz) => {
    setTimezone(tz);
    try { localStorage.setItem('semabridge-timezone', tz); } catch { /* storage unavailable */ }
    setModalOpen(false);
  };

  const themes = [
    { id: 'light', label: 'Light', icon: Sun },
    { id: 'dark', label: 'Dark', icon: Moon },
    { id: 'system', label: 'System', icon: Monitor },
  ];

  return (
    <div>
      <SectionHeader title="Preferences" description="How Semabridge looks and shows times. Saved in this browser." />
      <Card style={{ padding: '4px 16px' }}>
        <Row
          icon={Clock}
          title="Timezone"
          description="Used for run history, schedules and timestamps across the app."
        >
          <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{timezone}</span>
          <ActionButton onClick={() => { setQuery(''); setModalOpen(true); }}>Change</ActionButton>
        </Row>
        <Row
            last
            icon={Monitor}
            title="Theme"
            description="System follows your operating system. Also available from the theme button in the top bar."
          >
            <div role="radiogroup" aria-label="Theme" style={{ display: 'inline-flex', padding: 2, borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)' }}>
              {themes.map((t) => {
                const Icon = t.icon;
                const active = theme === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setTheme(t.id)}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      border: 'none', borderRadius: 6, padding: '5px 12px', cursor: 'pointer',
                      fontSize: 12, fontWeight: 600,
                      background: active ? 'var(--bg-surface)' : 'transparent',
                      color: active ? 'var(--text-primary)' : 'var(--text-tertiary)',
                      boxShadow: active ? 'var(--shadow-sm)' : 'none',
                    }}
                  >
                    <Icon size={13} /> {t.label}
                  </button>
                );
              })}
            </div>
          </Row>
      </Card>
      <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '10px 2px 0' }}>
        Some screens pick up a new timezone after a page reload.
      </p>

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title="Timezone" size="md">
        <div>
          <div style={{ position: 'relative', marginBottom: 12 }}>
            <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
            <input
              type="text"
              placeholder="Search timezones…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ width: '100%', padding: '8px 12px 8px 32px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-input)', color: 'var(--text-primary)', outline: 'none', fontSize: 13 }}
            />
          </div>
          <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
            {TIMEZONES.filter((tz) => tz.toLowerCase().includes(query.toLowerCase())).map((tz) => (
              <button
                key={tz}
                type="button"
                onClick={() => pickTimezone(tz)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '10px 14px', fontSize: 13, textAlign: 'left', cursor: 'pointer',
                  border: 'none', borderBottom: '1px solid var(--border-main)',
                  background: timezone === tz ? 'var(--bg-surface-hover)' : 'transparent',
                  color: 'var(--text-primary)',
                }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <Globe size={14} style={{ color: 'var(--text-tertiary)' }} /> {tz}
                </span>
                {timezone === tz && <Check size={14} style={{ color: 'var(--accent-blue)' }} />}
              </button>
            ))}
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', textAlign: 'center', margin: '12px 0 0' }}>
            Detected from this computer: {systemTimeZone()}
          </p>
        </div>
      </Modal>
    </div>
  );
}
