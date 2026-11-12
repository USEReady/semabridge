import { useSearchParams, Link } from 'react-router-dom';
import { PlugZap, Sparkles, SlidersHorizontal, HardDrive, Wrench, ExternalLink, AlertTriangle } from 'lucide-react';
import SettingsConnections from '../components/settings/SettingsConnections';
import SettingsPreferences from '../components/settings/SettingsPreferences';
import SettingsStorage from '../components/settings/SettingsStorage';
import { SectionHeader } from '../components/settings/settingsUi';
import LlmProvidersPanel from '../components/LlmProvidersPanel';
import GlobalConfigPage from './GlobalConfigPage';

const SECTIONS = [
  { id: 'connections', label: 'Connections', icon: PlugZap },
  { id: 'ai', label: 'AI providers', icon: Sparkles },
  { id: 'preferences', label: 'Preferences', icon: SlidersHorizontal },
  { id: 'storage', label: 'Storage & history', icon: HardDrive },
  { id: 'advanced', label: 'Advanced', icon: Wrench },
];

function AdvancedSection() {
  return (
    <div>
      <SectionHeader
        title="Advanced"
        description="Global configuration file (~/.semabridge/config.yaml): environment variables, default credentials and system-wide settings."
        action={(
          <Link
            to="/global-config"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 600, color: 'var(--accent-blue)', textDecoration: 'none' }}
          >
            Open as full page <ExternalLink size={12} />
          </Link>
        )}
      />
      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 16,
        padding: '10px 14px', borderRadius: 10,
        border: '1px solid var(--color-warning)', background: 'var(--color-warning-muted, rgba(245,158,11,.08))',
        fontSize: 12.5, color: 'var(--text-primary)',
      }}>
        <AlertTriangle size={15} style={{ color: 'var(--color-warning)', flexShrink: 0, marginTop: 1 }} />
        <span>Changes here apply to every project on this server. Most settings are easier to change in Connections and AI providers.</span>
      </div>
      <GlobalConfigPage embedded />
    </div>
  );
}

function AiSection() {
  return (
    <div>
      <SectionHeader
        title="AI providers"
        description="Models Semabridge uses to translate formulas it can't convert by rule, and to answer questions in Ask AI."
      />
      <LlmProvidersPanel />
    </div>
  );
}

/**
 * SettingsPage (/settings, admin only) — one page with a left sub-menu.
 * The active section lives in ?section= so it can be linked
 * (e.g. /settings?section=advanced replaces the old Global Config entry point).
 */
export default function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get('section');
  const active = SECTIONS.some((s) => s.id === requested) ? requested : 'connections';

  const select = (id) => {
    const next = new URLSearchParams(searchParams);
    next.set('section', id);
    setSearchParams(next, { replace: true });
  };

  return (
    <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1280, margin: '0 auto' }} className="md:px-10">
      <h1 style={{ margin: '0 0 20px', fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>Settings</h1>

      <div style={{ display: 'flex', gap: 28, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <nav aria-label="Settings sections" style={{ width: 200, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 2, position: 'sticky', top: 0 }}>
          {SECTIONS.map((s) => {
            const Icon = s.icon;
            const isActive = s.id === active;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => select(s.id)}
                aria-current={isActive ? 'page' : undefined}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 12px', borderRadius: 8, border: 'none',
                  textAlign: 'left', cursor: 'pointer',
                  fontSize: 13, fontWeight: isActive ? 600 : 500,
                  background: isActive ? 'var(--color-accent-faint, var(--bg-surface-hover))' : 'transparent',
                  color: isActive ? 'var(--accent-blue)' : 'var(--text-secondary)',
                }}
              >
                <Icon size={15} />
                {s.label}
              </button>
            );
          })}
        </nav>

        <main style={{ flex: 1, minWidth: 0, flexBasis: 480 }}>
          {active === 'connections' && <SettingsConnections />}
          {active === 'ai' && <AiSection />}
          {active === 'preferences' && <SettingsPreferences />}
          {active === 'storage' && <SettingsStorage />}
          {active === 'advanced' && <AdvancedSection />}
        </main>
      </div>
    </div>
  );
}
