import { useState, useEffect } from 'react';
import {
  PlugZap, RefreshCw, CheckCircle2, AlertCircle, Clock,
  Settings2, Snowflake, Cloud, Plus, ExternalLink, Database,
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';
import DataTable from '../components/common/DataTable';
import Modal from '../components/common/Modal';
import ConnectionsPanel from '../components/ConnectionsPanel';
import ConfigEditor from '../components/ConfigEditor';
import { ConfigurationProvider } from '../context/ConfigurationContext';
import { api } from '../utils/api';

const ENVIRONMENTS = ['Dev', 'Staging', 'Prod'];

const RECOMMENDED_SYSTEMS = [
  { id: 'snowflake',   label: 'Snowflake',   icon: '❄️',  desc: 'Data Warehouse' },
  { id: 'databricks',  label: 'Databricks',  icon: '🧱',  desc: 'Data Engineering' },
  { id: 'fabric',      label: 'MS Fabric',   icon: '🔷',  desc: 'Analytics Platform' },
  { id: 'bigquery',    label: 'BigQuery',    icon: '🔵',  desc: 'Cloud Data Warehouse' },
  { id: 'postgres',    label: 'PostgreSQL',  icon: '🐘',  desc: 'Relational DB' },
  { id: 'salesforce',  label: 'Salesforce',  icon: '☁️',  desc: 'CRM Platform' },
];

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
        });

        setConnectors(rows);
      } catch {
        setConnectors([
          { id: 'fabric',        name: 'Microsoft Fabric', type: 'Analytics Platform', icon: '🔷', status: 'disconnected', last_sync: null, detail: 'Not configured' },
          { id: 'snowflake',     name: 'Snowflake',        type: 'Data Warehouse',     icon: '❄️', status: 'disconnected', last_sync: null, detail: 'Not configured' },
          { id: 'semabridge_api', name: 'SemaBridge API',  type: 'Backend Service',    icon: '⚡', status: 'error',        last_sync: null, detail: 'Unreachable' },
        ]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const columns = [
    {
      key: 'name',
      label: 'Connector',
      render: (val, row) => (
        <div className="flex items-center gap-3">
          <div
            className="flex items-center justify-center rounded-lg flex-shrink-0"
            style={{ width: 34, height: 34, background: 'var(--color-accent-faint)', fontSize: 18 }}
          >
            {row.icon}
          </div>
          <div>
            <p className="text-primary font-semibold text-sm">{val}</p>
            <p className="text-tertiary" style={{ fontSize: 11 }}>{row.type}</p>
          </div>
        </div>
      ),
    },
    {
      key: 'detail',
      label: 'Details',
      render: (val) => <span className="text-secondary" style={{ fontSize: 12 }}>{val}</span>,
    },
    {
      key: 'status',
      label: 'Status',
      align: 'center',
      render: (val) => <StatusBadge status={val === 'connected' ? 'connected' : val === 'error' ? 'error' : 'disconnected'} label={val === 'connected' ? 'Connected' : val === 'error' ? 'Error' : 'Disconnected'} />,
    },
    {
      key: 'last_sync',
      label: 'Last Sync',
      render: (val) => (
        <div className="flex items-center gap-1.5">
          <Clock size={11} style={{ color: 'var(--text-tertiary)' }} />
          <span className="text-tertiary" style={{ fontSize: 12 }}>{formatRelative(val)}</span>
        </div>
      ),
    },
    {
      key: 'id',
      label: 'Actions',
      align: 'right',
      render: (val, row) => (
        <button
          onClick={() => setManageOpen(true)}
          className="flex items-center gap-1.5 rounded-lg text-xs font-semibold px-3 py-1.5 theme-transition"
          style={{
            background: row.status === 'disconnected' || row.status === 'error'
              ? 'var(--accent-blue)' : 'transparent',
            color: row.status === 'disconnected' || row.status === 'error'
              ? '#fff' : 'var(--accent-blue)',
            border: row.status === 'connected'
              ? '1px solid var(--accent-blue)' : 'none',
            cursor: 'pointer',
          }}
        >
          {row.status === 'connected' ? <Settings2 size={12} /> : <PlugZap size={12} />}
          {row.status === 'connected' ? 'Configure' : 'Connect'}
        </button>
      ),
    },
  ];

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
                boxShadow: env === e ? '0 1px 4px rgba(99,102,241,0.3)' : 'none',
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

      {/* Active connectors */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-primary font-semibold" style={{ fontSize: 15, margin: 0 }}>Active Connectors</h2>
          <button
            onClick={() => window.location.reload()}
            className="flex items-center gap-1.5 text-tertiary text-xs rounded-md px-2 py-1 theme-transition"
            style={{ background: 'transparent', border: '1px solid var(--border-main)', cursor: 'pointer' }}
          >
            <RefreshCw size={11} /> Refresh
          </button>
        </div>

        <DataTable
          columns={columns}
          data={connectors}
          loading={loading}
          emptyText="No connectors configured yet."
          pageSize={10}
        />
      </div>

      {/* Recommended systems */}
      <div>
        <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: '0 0 16px' }}>
          Recommended Systems
        </h2>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
            gap: 12,
          }}
        >
          {RECOMMENDED_SYSTEMS.map(sys => (
            <button
              key={sys.id}
              onClick={() => setManageOpen(true)}
              className="flex flex-col items-center gap-2 rounded-xl theme-transition"
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-main)',
                padding: '20px 12px',
                cursor: 'pointer',
                transition: 'border-color 0.2s, box-shadow 0.2s',
                textAlign: 'center',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; e.currentTarget.style.boxShadow = '0 2px 12px rgba(99,102,241,0.12)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-main)'; e.currentTarget.style.boxShadow = 'none'; }}
            >
              <span style={{ fontSize: 28 }}>{sys.icon}</span>
              <div>
                <p className="text-primary font-semibold" style={{ fontSize: 13, marginBottom: 2 }}>{sys.label}</p>
                <p className="text-tertiary" style={{ fontSize: 11 }}>{sys.desc}</p>
              </div>
            </button>
          ))}
        </div>
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
