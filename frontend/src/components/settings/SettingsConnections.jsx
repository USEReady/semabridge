import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  PlugZap, RefreshCw, Cloud, Snowflake, Plus, Database, FolderOpen, Server, Loader2,
} from 'lucide-react';
import ConnectionsPanel from '../ConnectionsPanel';
import LocalFoldersManager from './LocalFoldersManager';
import { api } from '../../utils/api';
import {
  SectionHeader, Card, CardGrid, Pill, ActionButton,
} from './settingsUi';

/** Map anything that names a connector (project.source, adapter, account type…) to a stable key. */
function normalizeConnectorKey(value) {
  if (!value) return '';
  let key = '';
  if (typeof value === 'string') {
    key = value.toLowerCase().trim();
  } else if (typeof value === 'object') {
    key = String(
      value.id || value.type || value.adapter || value.source || value.source_type || value.connector || value.name || ''
    ).toLowerCase().trim();
  }
  if (!key) return '';
  if (key.includes('pbix') || key.includes('powerbi') || key.includes('power bi') || key === 'pbi') return 'pbix';
  if (key.includes('fabric')) return 'fabric';
  if (key.includes('snowflake')) return 'snowflake';
  if (key.includes('databricks')) return 'databricks';
  return key;
}

const CONNECTORS = [
  { id: 'snowflake', name: 'Snowflake', icon: Snowflake, tone: '#38bdf8' },
  { id: 'fabric', name: 'Microsoft Fabric', icon: Cloud, tone: '#3b82f6' },
  { id: 'databricks', name: 'Databricks', icon: Database, tone: '#f97316' },
];

function connectorDetail(conn) {
  if (!conn) return 'Not configured';
  const creds = conn.credentials || {};
  const bits = [];
  if (conn.account_tag) bits.push(conn.account_tag);
  const host = creds.account || creds.host || creds.server_hostname || creds.workspace_url;
  if (host) bits.push(String(host).replace(/^https?:\/\//, ''));
  if (creds.warehouse) bits.push(creds.warehouse);
  if (!bits.length && conn.identity_email) bits.push(conn.identity_email);
  return bits.join(' · ') || 'Configured';
}

function isExpiredMessage(msg) {
  const m = String(msg || '').toLowerCase();
  return m.includes('expired') || m.includes('invalid_grant') || (m.includes('token') && m.includes('invalid'));
}

/**
 * Settings › Connections — where Semabridge reads from and deploys to.
 * Cards per connector with status, usage count and Test / Edit / Reconnect / Connect,
 * plus Power BI file folders and the backend service health.
 */
export default function SettingsConnections() {
  const [status, setStatus] = useState(null);
  const [health, setHealth] = useState(null);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [manage, setManage] = useState({ open: false, connector: null });
  const [tests, setTests] = useState({}); // id -> { running, result }
  const [foldersOpen, setFoldersOpen] = useState(false);
  const [folders, setFolders] = useState([]);

  const refresh = useCallback(async () => {
    setLoading(true);
    const [s, h, p, f] = await Promise.allSettled([
      api.getConnectionsStatus(),
      api.getHealth(),
      api.listProjects({ limit: 200 }),
      api.listLocalFolders(true),
    ]);
    setStatus(s.status === 'fulfilled' ? s.value : null);
    setHealth(h.status === 'fulfilled' ? h.value : { status: 'error' });
    setProjects(p.status === 'fulfilled' && Array.isArray(p.value) ? p.value : []);
    setFolders(f.status === 'fulfilled' && Array.isArray(f.value) ? f.value : []);
    setLoading(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const usage = useMemo(() => {
    const counts = {};
    projects.forEach((p) => {
      const keys = new Set([p.source, p.adapter, p.source_type, p.target_type].map(normalizeConnectorKey).filter(Boolean));
      keys.forEach((k) => { counts[k] = (counts[k] || 0) + 1; });
    });
    return counts;
  }, [projects]);

  const openManage = (connector = null) => setManage({ open: true, connector });

  const runTest = async (id) => {
    setTests((t) => ({ ...t, [id]: { running: true, result: null } }));
    try {
      const result = await api.testConnection(id);
      setTests((t) => ({ ...t, [id]: { running: false, result } }));
    } catch (err) {
      setTests((t) => ({ ...t, [id]: { running: false, result: { status: 'failed', message: err?.message || 'Test failed' } } }));
    }
  };

  const healthy = health && (health.status === 'ok' || health.status === 'healthy');
  const dbLabel = health?.db_dialect
    ? (health.db_dialect === 'postgresql' ? 'PostgreSQL' : health.db_dialect)
    : null;

  return (
    <div>
      <SectionHeader
        title="Connections"
        description="Where Semabridge reads from and deploys to"
        action={(
          <div style={{ display: 'flex', gap: 8 }}>
            <ActionButton onClick={refresh} disabled={loading} title="Refresh status" aria-label="Refresh status">
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </ActionButton>
            <ActionButton primary onClick={() => openManage(null)}>
              <Plus size={13} /> Add connection
            </ActionButton>
          </div>
        )}
      />

      <CardGrid>
        {CONNECTORS.map((c) => {
          const conn = status?.[c.id];
          const connected = conn?.configured === true && conn?.status === 'connected';
          const test = tests[c.id];
          const testFailed = test?.result && test.result.status !== 'success';
          const expired = connected && ((testFailed && isExpiredMessage(test.result.message)) || String(conn?.status || '').includes('expired'));
          const pill = !conn && loading
            ? { tone: 'neutral', label: 'Checking…' }
            : expired ? { tone: 'warning', label: 'Token expired' }
              : connected ? { tone: 'success', label: 'Connected' }
                : { tone: 'neutral', label: 'Not set up' };
          const used = usage[c.id] || 0;
          const Icon = c.icon;
          return (
            <Card key={c.id}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <IconTile><Icon size={18} color={c.tone} /></IconTile>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)' }}>{c.name}</span>
                    <Pill tone={pill.tone}>{pill.label}</Pill>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3, wordBreak: 'break-word' }}>
                    {connected ? connectorDetail(conn) : 'Not configured'}
                  </div>
                  {used > 0 && (
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 6 }}>
                      Used by {used} project{used === 1 ? '' : 's'}
                    </div>
                  )}
                  {test?.result && (
                    <div style={{
                      fontSize: 11.5, marginTop: 6, wordBreak: 'break-word',
                      color: test.result.status === 'success' ? 'var(--color-success)' : 'var(--color-error)',
                    }}>
                      {test.result.status === 'success' ? 'Test passed' : 'Test failed'}
                      {test.result.message ? ` — ${test.result.message}` : ''}
                    </div>
                  )}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                {connected && !expired && (
                  <>
                    <ActionButton onClick={() => runTest(c.id)} disabled={test?.running}>
                      {test?.running ? <Loader2 size={12} className="animate-spin" /> : null}
                      {test?.running ? 'Testing…' : 'Test'}
                    </ActionButton>
                    <ActionButton onClick={() => openManage(c.id)}>Edit</ActionButton>
                  </>
                )}
                {expired && (
                  <ActionButton primary onClick={() => openManage(c.id)}>Reconnect</ActionButton>
                )}
                {!connected && (
                  <ActionButton primary onClick={() => openManage(c.id)}>
                    <PlugZap size={12} /> Connect
                  </ActionButton>
                )}
              </div>
            </Card>
          );
        })}

        {/* Power BI files — registered local folders */}
        <Card>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
            <IconTile><FolderOpen size={18} color="#eab308" /></IconTile>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)' }}>Power BI files</span>
                <Pill tone={folders.length ? 'success' : 'neutral'}>{folders.length ? 'Ready' : 'Not set up'}</Pill>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
                {folders.length
                  ? `${folders.length} folder${folders.length === 1 ? '' : 's'} with .pbix files`
                  : 'Register folders that hold .pbix files'}
              </div>
              {usage.pbix > 0 && (
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 6 }}>
                  Used by {usage.pbix} project{usage.pbix === 1 ? '' : 's'}
                </div>
              )}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <ActionButton onClick={() => setFoldersOpen((v) => !v)}>
              {foldersOpen ? 'Hide folders' : 'Manage folders'}
            </ActionButton>
          </div>
        </Card>
      </CardGrid>

      {foldersOpen && (
        <div style={{ marginTop: 12 }}>
          <LocalFoldersManager folders={folders} onChanged={refresh} />
        </div>
      )}

      {/* Backend service */}
      <div style={{
        marginTop: 20, padding: '12px 16px', borderRadius: 10,
        border: '1px solid var(--border-main)', background: 'var(--bg-surface)',
        display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}>
        <Server size={16} style={{ color: 'var(--text-tertiary)' }} />
        <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-primary)' }}>Backend service</span>
        <Pill tone={!health ? 'neutral' : healthy ? 'success' : 'error'}>
          {!health ? 'Checking…' : healthy ? 'Healthy' : 'Unreachable'}
        </Pill>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          {[
            health?.version ? `v${health.version}` : null,
            dbLabel,
            health?.database && health.database !== 'connected' ? `database ${health.database}` : null,
          ].filter(Boolean).join(' · ')}
        </span>
      </div>

      <ConnectionsPanel
        isOpen={manage.open}
        onClose={() => {
          setManage({ open: false, connector: null });
          refresh();
        }}
        selectedConnector={manage.connector}
      />
    </div>
  );
}

function IconTile({ children }) {
  return (
    <div style={{
      width: 38, height: 38, borderRadius: 10, flexShrink: 0,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
    }}>
      {children}
    </div>
  );
}
