import { useState, useEffect } from 'react';
import {
  PlugZap, RefreshCw, CheckCircle2, AlertCircle, Clock,
  Settings2, Snowflake, Cloud, Plus, ExternalLink, Database, ChevronDown,
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';

import ConnectionsPanel from '../components/ConnectionsPanel';
import ConfigEditor from '../components/ConfigEditor';
import { ConfigurationProvider } from '../context/ConfigurationContext';
import { api } from '../utils/api';

const ENVIRONMENTS = ['Dev', 'Staging', 'Prod'];

function formatRelative(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

export default function SettingsPage() {
  const [env, setEnv] = useState('Dev');
  const [connectors, setConnectors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [manageOpen, setManageOpen] = useState(false);
  const [expandedSections, setExpandedSections] = useState({ source: true, targets: true });

  const toggleSection = (section) => {
    setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }));
  };

  // Load connection status
  useEffect(() => {
    (async () => {
      try {
        const [healthData, statusData] = await Promise.allSettled([
          api.getHealth(),
          api.getConnectionsStatus(),
        ]);

        const health = healthData.status === 'fulfilled' ? healthData.value : null;
        const status = statusData.status === 'fulfilled' ? statusData.value : null;

        const rows = [];

        // Fabric connector
        const fabricConn = status?.fabric;
        rows.push({
          id: 'fabric',
          name: 'Microsoft Fabric',
          type: 'Analytics Platform',
          icon: '🔷',
          status: fabricConn?.logged_in || fabricConn?.has_credentials ? 'connected' : 'disconnected',
          last_sync: fabricConn?.last_sync ?? null,
          detail: fabricConn?.workspace_name ? `Workspace: ${fabricConn.workspace_name}` : 'Not configured',
          tags: ['production', 'analytics'],
        });

        // Snowflake connector
        const snowConn = status?.snowflake;
        rows.push({
          id: 'snowflake',
          name: 'Snowflake',
          type: 'Data Warehouse',
          icon: '❄️',
          status: snowConn?.connected ? 'connected' : 'disconnected',
          last_sync: snowConn?.last_sync ?? null,
          detail: snowConn?.account ? `Account: ${snowConn.account}` : 'Not configured',
          tags: ['warehouse', 'target'],
        });

        // API health as a meta-connector
        rows.push({
          id: 'semabridge_api',
          name: 'SemaBridge API',
          type: 'Backend Service',
          icon: '⚡',
          status: health?.status === 'ok' || health?.status === 'healthy' ? 'connected' : 'error',
          last_sync: null,
          detail: health ? `v${health.version ?? '—'} · port 8000` : 'Unreachable',
          tags: ['backend'],
        });

        setConnectors(rows);
      } catch {
        setConnectors([
          { id: 'fabric',        name: 'Microsoft Fabric', type: 'Analytics Platform', icon: '🔷', status: 'disconnected', last_sync: null, detail: 'Not configured', tags: ['production'] },
          { id: 'snowflake',     name: 'Snowflake',        type: 'Data Warehouse',     icon: '❄️', status: 'disconnected', last_sync: null, detail: 'Not configured', tags: ['warehouse'] },
          { id: 'semabridge_api', name: 'SemaBridge API',  type: 'Backend Service',    icon: '⚡', status: 'error',        last_sync: null, detail: 'Unreachable', tags: ['backend'] },
        ]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  
  return (
    <ConfigurationProvider>
      <div style={{ padding: '28px 32px', minHeight: '100%' }}>
        <PageHeader
        title="Settings"
        description="Manage connectors, integrations, and environment configuration."
        action={{
          label: 'Add Connector',
          icon: <Plus size={14} />,
          onClick: () => setManageOpen(true),
        }}
      />

      {/* Environment toggle */}
      <div className="flex items-center gap-3 mb-8">
        <span className="text-secondary text-sm font-semibold">Environment:</span>
        <div
          className="flex rounded-lg overflow-hidden"
          style={{ border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}
        >
          {ENVIRONMENTS.map(e => (
            <button
              key={e}
              onClick={() => setEnv(e)}
              className="text-sm font-medium px-4 py-1.5 theme-transition"
              style={{
                background: env === e ? 'var(--accent-blue)' : 'transparent',
                color: env === e ? '#fff' : 'var(--text-secondary)',
                border: 'none',
                cursor: 'pointer',
                boxShadow: env === e ? 'var(--shadow-focus)' : 'none',
              }}
            >
              {e}
            </button>
          ))}
        </div>
        <span className="text-tertiary text-xs">({env} environment active)</span>
      </div>

      {/* Configuration Syncing Dual-View */}
      <div className="mb-8">
        <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: '0 0 16px' }}>Project Configuration</h2>
        <ConfigEditor />
      </div>

      {/* Source & Target Configuration */}
      <div className="mb-8">
        <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: '0 0 16px' }}>Connector Configuration</h2>
        
        {/* Source Configuration Section */}
        <div className="mb-6">
          <button
            onClick={() => toggleSection('source')}
            className="w-full flex items-center justify-between rounded-lg p-4 theme-transition"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--bg-surface)'; }}
          >
            <div className="flex items-center gap-3">
              <Cloud size={16} style={{ color: 'var(--accent-blue)' }} />
              <div style={{ textAlign: 'left' }}>
                <h3 className="text-primary font-semibold" style={{ fontSize: 13, margin: 0 }}>Source Configuration</h3>
                <p className="text-tertiary" style={{ fontSize: 11, margin: 0, marginTop: 2 }}>Analytics platform where data originates</p>
              </div>
            </div>
            <ChevronDown 
              size={14} 
              style={{
                transform: expandedSections.source ? 'rotate(0deg)' : 'rotate(-90deg)',
                transition: 'transform 0.2s',
                color: 'var(--text-tertiary)',
              }}
            />
          </button>
          
          {expandedSections.source && (
            <div style={{ marginTop: 12, padding: '16px', background: 'var(--bg-surface-raised)', borderRadius: 8 }}>
              {(() => {
                const fabricConn = connectors.find(c => c.id === 'fabric');
                return fabricConn ? (
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <div
                        style={{
                          width: 34, height: 34,
                          background: 'var(--color-accent-faint)',
                          borderRadius: 8,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: 18,
                        }}
                      >
                        {fabricConn.icon}
                      </div>
                      <div>
                        <p className="text-primary" style={{ fontWeight: 500, fontSize: 12, margin: 0 }}>{fabricConn.name}</p>
                        <p className="text-secondary" style={{ fontSize: 11, margin: 0, marginTop: 2 }}>{fabricConn.detail}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge status={fabricConn.status === 'connected' ? 'connected' : 'disconnected'} label={fabricConn.status === 'connected' ? 'Connected' : 'Disconnected'} />
                      <button
                        onClick={() => setManageOpen(true)}
                        className="flex items-center gap-1.5 rounded-lg text-xs font-semibold px-3 py-1.5 theme-transition"
                        style={{
                          background: fabricConn.status === 'disconnected' ? 'var(--accent-blue)' : 'transparent',
                          color: fabricConn.status === 'disconnected' ? '#fff' : 'var(--accent-blue)',
                          border: fabricConn.status === 'connected' ? '1px solid var(--accent-blue)' : 'none',
                          cursor: 'pointer',
                        }}
                      >
                        {fabricConn.status === 'connected' ? <Settings2 size={12} /> : <PlugZap size={12} />}
                        {fabricConn.status === 'connected' ? 'Configure' : 'Connect'}
                      </button>
                    </div>
                  </div>
                ) : null;
              })()}
            </div>
          )}
        </div>

        {/* Target Configuration Section */}
        <div>
          <button
            onClick={() => toggleSection('targets')}
            className="w-full flex items-center justify-between rounded-lg p-4 theme-transition"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--bg-surface)'; }}
          >
            <div className="flex items-center gap-3">
              <Database size={16} style={{ color: 'var(--accent-blue)' }} />
              <div style={{ textAlign: 'left' }}>
                <h3 className="text-primary font-semibold" style={{ fontSize: 13, margin: 0 }}>Target Configuration</h3>
                <p className="text-tertiary" style={{ fontSize: 11, margin: 0, marginTop: 2 }}>Data warehouse destinations</p>
              </div>
            </div>
            <ChevronDown 
              size={14} 
              style={{
                transform: expandedSections.targets ? 'rotate(0deg)' : 'rotate(-90deg)',
                transition: 'transform 0.2s',
                color: 'var(--text-tertiary)',
              }}
            />
          </button>
          
          {expandedSections.targets && (
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
              {(() => {
                const snowConn = connectors.find(c => c.id === 'snowflake');
                return snowConn ? (
                  <div style={{ padding: '16px', background: 'var(--bg-surface-raised)', borderRadius: 8 }}>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div
                          style={{
                            width: 34, height: 34,
                            background: 'var(--color-accent-faint)',
                            borderRadius: 8,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontSize: 18,
                          }}
                        >
                          {snowConn.icon}
                        </div>
                        <div>
                          <p className="text-primary" style={{ fontWeight: 500, fontSize: 12, margin: 0 }}>{snowConn.name}</p>
                          <p className="text-secondary" style={{ fontSize: 11, margin: 0, marginTop: 2 }}>{snowConn.detail}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <StatusBadge status={snowConn.status === 'connected' ? 'connected' : 'disconnected'} label={snowConn.status === 'connected' ? 'Connected' : 'Disconnected'} />
                        <button
                          onClick={() => setManageOpen(true)}
                          className="flex items-center gap-1.5 rounded-lg text-xs font-semibold px-3 py-1.5 theme-transition"
                          style={{
                            background: snowConn.status === 'disconnected' ? 'var(--accent-blue)' : 'transparent',
                            color: snowConn.status === 'disconnected' ? '#fff' : 'var(--accent-blue)',
                            border: snowConn.status === 'connected' ? '1px solid var(--accent-blue)' : 'none',
                            cursor: 'pointer',
                          }}
                        >
                          {snowConn.status === 'connected' ? <Settings2 size={12} /> : <PlugZap size={12} />}
                          {snowConn.status === 'connected' ? 'Configure' : 'Connect'}
                        </button>
                      </div>
                    </div>
                  </div>
                ) : null;
              })()}
            </div>
          )}
        </div>
      </div>

      {/* API Health / Backend Service */}
      <div className="mb-8">
        <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: '0 0 16px' }}>Backend Service</h2>
        {(() => {
          const apiConn = connectors.find(c => c.id === 'semabridge_api');
          return apiConn ? (
            <div style={{ padding: '16px', background: 'var(--bg-surface)', borderRadius: 8, border: '1px solid var(--border-main)' }}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div
                    style={{
                      width: 34, height: 34,
                      background: 'var(--color-accent-faint)',
                      borderRadius: 8,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 18,
                    }}
                  >
                    {apiConn.icon}
                  </div>
                  <div>
                    <p className="text-primary" style={{ fontWeight: 500, fontSize: 12, margin: 0 }}>{apiConn.name}</p>
                    <p className="text-secondary" style={{ fontSize: 11, margin: 0, marginTop: 2 }}>{apiConn.detail}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <StatusBadge status={apiConn.status === 'connected' ? 'connected' : apiConn.status === 'error' ? 'error' : 'disconnected'} label={apiConn.status === 'connected' ? 'Healthy' : apiConn.status === 'error' ? 'Error' : 'Unavailable'} />
                </div>
              </div>
            </div>
          ) : null;
        })()}
      </div>

      {/* ConnectionsPanel as drawer */}
      <ConnectionsPanel
        isOpen={manageOpen}
        onClose={() => setManageOpen(false)}
      />
      </div>
    </ConfigurationProvider>
  );
}