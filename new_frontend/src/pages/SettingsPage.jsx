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
  const [selectedConnectorId, setSelectedConnectorId] = useState(null);

  const openManage = (connectorId = null) => {
    setSelectedConnectorId(connectorId);
    setManageOpen(true);
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
        // Backend returns { configured, has_auth, auth_method, credentials, missing_fields }
        const fabricConn = status?.fabric;
        const fabricConfigured = fabricConn?.configured === true;
        const fabricHasAuth = fabricConn?.has_auth === true;
        const fabricHasWorkspace = !!fabricConn?.credentials?.workspace_id;
        // 3-state: connected (workspace + auth), configured (workspace only), disconnected
        const fabricStatus = fabricConfigured
          ? 'connected'
          : fabricHasWorkspace
            ? 'configured'
            : 'disconnected';
        rows.push({
          id: 'fabric',
          name: 'Microsoft Fabric',
          type: 'Analytics Platform',
          icon: '🔷',
          status: fabricStatus,
          last_sync: null,
          detail: fabricStatus === 'connected'
            ? `Workspace: ${fabricConn.credentials.workspace_id}`
            : fabricStatus === 'configured'
              ? `Workspace set · ${fabricHasAuth ? '' : 'Auth required'}`
              : 'Not configured',
          tags: ['production', 'analytics'],
        });

        // Snowflake connector
        // Backend returns { configured, auth_type, credentials, missing_fields }
        const snowConn = status?.snowflake;
        const snowConfigured = snowConn?.configured === true;
        const snowHasFields = (snowConn?.fields_stored ?? 0) > 0;
        const snowAuthType = snowConn?.credentials?.auth_type ?? '';
        const snowAccount  = snowConn?.credentials?.account ?? '';
        const snowMissing  = snowConn?.missing_fields ?? [];
        // 3-state: connected (all required), configured (partial), disconnected
        const snowStatus = snowConfigured
          ? 'connected'
          : snowHasFields
            ? 'configured'
            : 'disconnected';
        rows.push({
          id: 'snowflake',
          name: 'Snowflake',
          type: 'Data Warehouse',
          icon: '❄️',
          status: snowStatus,
          last_sync: null,
          detail: snowStatus === 'connected'
            ? `${snowAccount}${snowAuthType ? ` · ${snowAuthType}` : ''}`
            : snowStatus === 'configured'
              ? `Missing: ${snowMissing.join(', ')}`
              : 'Not configured',
          tags: ['warehouse', 'target'],
        });

        // Databricks connector
        const dbConn = status?.databricks;
        const dbConfigured = dbConn?.configured === true;
        const dbStatus = dbConfigured ? 'connected' : 'disconnected';
        rows.push({
          id: 'databricks',
          name: 'Databricks',
          type: 'Data Intelligence Platform',
          icon: '🧱',
          status: dbStatus,
          last_sync: null,
          detail: dbConfigured ? 'Configured' : 'Not configured',
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
          { id: 'databricks',    name: 'Databricks',       type: 'Data Intelligence Platform', icon: '🧱', status: 'disconnected', last_sync: null, detail: 'Not configured', tags: ['warehouse'] },
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
          onClick: () => openManage(null),
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

      {/* Connector Configuration (Flattened) */}
      <div className="mb-8">
        <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: '0 0 16px' }}>Connector Configuration</h2>
        
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {connectors.filter(c => c.id !== 'semabridge_api').map(conn => (
            <div key={conn.id} style={{ padding: '16px', background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 8 }}>
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
                    {conn.icon}
                  </div>
                  <div style={{ textAlign: 'left' }}>
                    <p className="text-primary" style={{ fontWeight: 600, fontSize: 13, margin: 0 }}>{conn.name}</p>
                    <p className="text-secondary" style={{ fontSize: 11, margin: 0, marginTop: 2 }}>{conn.detail}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={conn.status} label={conn.status === 'connected' ? 'Connected' : conn.status === 'configured' ? 'Configured' : 'Disconnected'} />
                  <button
                    onClick={() => openManage(conn.id)}
                    className="flex items-center gap-1.5 rounded-lg text-xs font-semibold px-3 py-1.5 theme-transition"
                    style={{
                      background: conn.status === 'disconnected' ? 'var(--accent-blue)' : 'transparent',
                      color: conn.status === 'disconnected' ? '#fff' : 'var(--accent-blue)',
                      border: conn.status !== 'disconnected' ? '1px solid var(--accent-blue)' : 'none',
                      cursor: 'pointer',
                    }}
                  >
                    {conn.status === 'disconnected' ? <PlugZap size={12} /> : <Settings2 size={12} />}
                    {conn.status === 'disconnected' ? 'Connect' : 'Configure'}
                  </button>
                </div>
              </div>
            </div>
          ))}
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

      <ConnectionsPanel
        isOpen={manageOpen}
        onClose={() => setManageOpen(false)}
        selectedConnector={selectedConnectorId}
      />
      </div>
    </ConfigurationProvider>
  );
}