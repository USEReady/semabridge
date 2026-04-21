import { useState, useEffect, useCallback } from 'react';
import {
  PlugZap, RefreshCw, CheckCircle2, AlertCircle, Clock,
  Settings2, Cloud, Snowflake, Plus, ExternalLink, Database, ChevronDown, FolderOpen,
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';

import ConnectionsPanel from '../components/ConnectionsPanel';
import ConfigEditor from '../components/ConfigEditor';
import Modal from '../components/common/Modal';
import { ConfigurationProvider } from '../context/ConfigurationContext';
import { api } from '../utils/api';
import { useUIStore } from '../store/uiStore';

const ENVIRONMENTS = ['Dev', 'Staging', 'Prod'];

function isLikelyAbsolutePath(value) {
  const normalized = String(value || '').trim().replace(/\\/g, '/');
  return /^(?:[A-Za-z]:\/|\/\/|\/)/.test(normalized);
}

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
  if (key.includes('semabridge') || key.includes('api')) return 'semabridge_api';
  return key;
}

function renderConnectorIcon(connectorId, size = 18) {
  const key = normalizeConnectorKey(connectorId);
  if (key.includes('fabric')) return <Cloud size={size} color="#3b82f6" />;
  if (key.includes('snowflake')) return <Snowflake size={size} color="#38bdf8" />;
  if (key.includes('databricks')) return <Database size={size} color="#f97316" />;
  if (key.includes('api') || key.includes('semabridge')) return <PlugZap size={size} color="#22c55e" />;
  return <PlugZap size={size} color="var(--text-secondary)" />;
}

export default function SettingsPage() {
  const isAdvancedMode = useUIStore(state => state.isAdvancedMode);
  const setIsAdvancedMode = useUIStore(state => state.setIsAdvancedMode);
  
  const [env, setEnv] = useState('Dev');
  const [connectors, setConnectors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [manageOpen, setManageOpen] = useState(false);
  const [selectedConnectorId, setSelectedConnectorId] = useState(null);
  const [localFolders, setLocalFolders] = useState([]);
  const [localFoldersLoading, setLocalFoldersLoading] = useState(false);
  const [localFolderModalOpen, setLocalFolderModalOpen] = useState(false);
  const [localFolderTagInput, setLocalFolderTagInput] = useState('');
  const [localFolderPathInput, setLocalFolderPathInput] = useState('');
  const [localFolderSubmitting, setLocalFolderSubmitting] = useState(false);
  const [localFolderError, setLocalFolderError] = useState('');
  const [localFolderSuccess, setLocalFolderSuccess] = useState('');
  const [directoryPickerOpen, setDirectoryPickerOpen] = useState(false);
  const [directoryPath, setDirectoryPath] = useState('');
  const [directoryEntries, setDirectoryEntries] = useState([]);
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [directoryError, setDirectoryError] = useState('');

  const openManage = (connectorId = null) => {
    setSelectedConnectorId(connectorId);
    setManageOpen(true);
  };

  // Utility for case-insensitive status check
  const normalizeStatus = (status) => typeof status === 'string' ? status.toLowerCase() : status;

  const refreshLocalFolders = useCallback(async () => {
    setLocalFoldersLoading(true);
    try {
      const data = await api.listLocalFolders(true);
      setLocalFolders(Array.isArray(data) ? data : []);
    } catch {
      setLocalFolders([]);
    } finally {
      setLocalFoldersLoading(false);
    }
  }, []);

  // Load connection status (and allow refresh)
  const refreshConnectors = useCallback(async () => {
    setLoading(true);
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
      const fabricConfigured = fabricConn?.configured === true;
      const fabricHasAuth = fabricConn?.has_auth === true;
      const fabricHasWorkspace = !!fabricConn?.credentials?.workspace_id;
      const fabricStatus = fabricConfigured
        ? 'connected'
        : fabricHasWorkspace
          ? 'configured'
          : 'disconnected';
      rows.push({
        id: 'fabric',
        name: 'Microsoft Fabric',
        type: 'Analytics Platform',
        status: normalizeStatus(fabricStatus),
        last_sync: null,
        detail: fabricStatus === 'connected'
          ? `Workspace: ${fabricConn.credentials.workspace_id}`
          : fabricStatus === 'configured'
            ? `Workspace set · ${fabricHasAuth ? '' : 'Auth required'}`
            : 'Not configured',
        tags: ['production', 'analytics'],
      });

      // Snowflake connector
      const snowConn = status?.snowflake;
      const snowConfigured = snowConn?.configured === true;
      const snowHasFields = (snowConn?.fields_stored ?? 0) > 0;
      const snowAuthType = snowConn?.credentials?.auth_type ?? '';
      const snowAccount  = snowConn?.credentials?.account ?? '';
      const snowMissing  = snowConn?.missing_fields ?? [];
      const snowStatus = snowConfigured
        ? 'connected'
        : snowHasFields
          ? 'configured'
          : 'disconnected';
      rows.push({
        id: 'snowflake',
        name: 'Snowflake',
        type: 'Data Warehouse',
        status: normalizeStatus(snowStatus),
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
        status: normalizeStatus(dbStatus),
        last_sync: null,
        detail: dbConfigured ? 'Configured' : 'Not configured',
        tags: ['warehouse', 'target'],
      });

      // API health as a meta-connector
      rows.push({
        id: 'semabridge_api',
        name: 'SemaBridge API',
        type: 'Backend Service',
        status: normalizeStatus(health?.status === 'ok' || health?.status === 'healthy' ? 'connected' : 'error'),
        last_sync: null,
        detail: health ? `v${health.version ?? '—'} · port 8000` : 'Unreachable',
        tags: ['backend'],
      });

      setConnectors(rows);
    } catch {
      setConnectors([
        { id: 'fabric',        name: 'Microsoft Fabric', type: 'Analytics Platform', status: 'disconnected', last_sync: null, detail: 'Not configured', tags: ['production'] },
        { id: 'snowflake',     name: 'Snowflake',        type: 'Data Warehouse',     status: 'disconnected', last_sync: null, detail: 'Not configured', tags: ['warehouse'] },
        { id: 'databricks',    name: 'Databricks',       type: 'Data Intelligence Platform', status: 'disconnected', last_sync: null, detail: 'Not configured', tags: ['warehouse'] },
        { id: 'semabridge_api', name: 'SemaBridge API',  type: 'Backend Service',    status: 'error',        last_sync: null, detail: 'Unreachable', tags: ['backend'] },
      ]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refreshConnectors(); }, [refreshConnectors]);
  useEffect(() => { refreshLocalFolders(); }, [refreshLocalFolders]);

  const openLocalFolderModal = () => {
    setLocalFolderTagInput('');
    setLocalFolderPathInput('');
    setLocalFolderError('');
    setLocalFolderSuccess('');
    setDirectoryPickerOpen(false);
    setDirectoryPath('');
    setDirectoryEntries([]);
    setDirectoryError('');
    setLocalFolderModalOpen(true);
  };

  const loadDirectoryEntries = useCallback(async (nextPath = '') => {
    setDirectoryLoading(true);
    setDirectoryError('');
    try {
      const rows = await api.browseDirectory(nextPath);
      const folders = Array.isArray(rows) ? rows : [];
      setDirectoryEntries(folders);

      if (nextPath) {
        setDirectoryPath(nextPath.replace(/\\/g, '/'));
      } else if (folders.length > 0) {
        const firstPath = String(folders[0].path || '').replace(/\\/g, '/');
        const homeLikePath = firstPath.includes('/') ? firstPath.slice(0, firstPath.lastIndexOf('/')) : firstPath;
        setDirectoryPath(homeLikePath);
      } else {
        setDirectoryPath('');
      }
    } catch (err) {
      setDirectoryEntries([]);
      setDirectoryError(err?.message || 'Failed to browse directories.');
    } finally {
      setDirectoryLoading(false);
    }
  }, []);

  const openDirectoryPicker = useCallback(async () => {
    const basePath = isLikelyAbsolutePath(localFolderPathInput) ? localFolderPathInput.trim() : '';
    setDirectoryPickerOpen(true);
    await loadDirectoryEntries(basePath);
  }, [loadDirectoryEntries, localFolderPathInput]);

  const buildBreadcrumbs = useCallback((fullPath) => {
    const normalized = String(fullPath || '').replace(/\\/g, '/').trim();
    if (!normalized) return [{ label: 'Home', path: '' }];

    const crumbs = [];
    const windowsDriveMatch = normalized.match(/^([A-Za-z]:)(\/.*)?$/);
    if (windowsDriveMatch) {
      const drive = windowsDriveMatch[1];
      crumbs.push({ label: drive, path: `${drive}/` });
      const rest = (windowsDriveMatch[2] || '').split('/').filter(Boolean);
      let current = `${drive}/`;
      rest.forEach((part) => {
        current = `${current}${part}/`;
        crumbs.push({ label: part, path: current });
      });
      return crumbs;
    }

    const parts = normalized.split('/').filter(Boolean);
    let current = normalized.startsWith('/') ? '/' : '';
    if (normalized.startsWith('/')) {
      crumbs.push({ label: '/', path: '/' });
    }
    parts.forEach((part) => {
      current = current ? `${current.replace(/\/$/, '')}/${part}` : part;
      crumbs.push({ label: part, path: current });
    });
    return crumbs;
  }, []);

  const selectCurrentDirectory = useCallback(() => {
    if (!directoryPath) return;
    setLocalFolderPathInput(directoryPath.replace(/\\/g, '/').replace(/\/+$/, ''));
    setDirectoryPickerOpen(false);
  }, [directoryPath]);

  const saveLocalFolder = async () => {
    const tag = localFolderTagInput.trim();
    const absolutePath = localFolderPathInput.trim();

    if (!tag) {
      setLocalFolderError('Folder tag is required.');
      return;
    }

    if (!isLikelyAbsolutePath(absolutePath)) {
      setLocalFolderError('Enter an absolute folder path such as C:/Models/Finance.');
      return;
    }

    setLocalFolderSubmitting(true);
    setLocalFolderError('');
    try {
      await api.saveLocalFolder({
        tag_name: tag,
        absolute_path: absolutePath,
        is_active: true,
      });
      setLocalFolderSuccess(`Saved ${tag}.`);
      setLocalFolderModalOpen(false);
      await refreshLocalFolders();
    } catch (err) {
      setLocalFolderError(err?.message || 'Failed to save local folder.');
    } finally {
      setLocalFolderSubmitting(false);
    }
  };

  // Pass refreshConnectors to child components and call after Connect

  
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


      {/* Configuration Syncing Dual-View */}
      <div className="mb-8">
        <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: '0 0 16px' }}>Project Configuration</h2>
        <ConfigEditor />
      </div>

      {/* Advanced Experience */}
      <div className="mb-8" style={{ background: 'var(--bg-surface)', padding: 24, borderRadius: 12, border: '1px solid var(--border-main)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
           <div>
             <h2 className="text-primary font-semibold mb-1" style={{ fontSize: 15, margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
               Advanced Experience
             </h2>
             <p className="text-secondary" style={{ fontSize: 12, margin: '4px 0 0' }}>
               Enable advanced technical tools such as Semantic Comparator for debugging definitions.
             </p>
           </div>
           
           <label style={{ position: 'relative', display: 'inline-block', width: 44, height: 24 }}>
              <input 
                 type="checkbox" 
                 checked={isAdvancedMode} 
                 onChange={(e) => setIsAdvancedMode(e.target.checked)} 
                 style={{ opacity: 0, width: 0, height: 0 }} 
              />
              <span style={{ 
                 position: 'absolute', cursor: 'pointer', top: 0, left: 0, right: 0, bottom: 0, 
                 backgroundColor: isAdvancedMode ? 'var(--accent-blue)' : 'var(--bg-subtle)', 
                 border: `1px solid ${isAdvancedMode ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                 transition: '.4s', borderRadius: 34 
              }}>
                <span style={{
                   position: 'absolute', content: '""', height: 16, width: 16, left: 4, bottom: 3,
                   backgroundColor: 'white', transition: '.4s', borderRadius: '50%',
                   transform: isAdvancedMode ? 'translateX(18px)' : 'translateX(0)'
                }}></span>
              </span>
           </label>
        </div>
      </div>

      {/* Local Folder Management */}
      <div className="mb-8">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
          <div>
            <h2 className="text-primary font-semibold mb-1" style={{ fontSize: 15, margin: 0 }}>Local Folder Management</h2>
            <p className="text-secondary" style={{ fontSize: 12, margin: '4px 0 0' }}>
              Register trusted local directories once and reuse them across project wizards.
            </p>
          </div>
          <button
            onClick={openLocalFolderModal}
            className="flex items-center gap-1.5 rounded-lg text-xs font-semibold px-3 py-1.5 theme-transition"
            style={{
              background: 'var(--accent-blue)',
              color: '#fff',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            <Plus size={12} />
            Add Folder
          </button>
        </div>

        {localFolderSuccess && (
          <div style={{ marginBottom: 12, padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-success)30', background: 'var(--color-success-bg)', color: 'var(--color-success)', fontSize: 12 }}>
            {localFolderSuccess}
          </div>
        )}

        <div style={{ border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden', background: 'var(--bg-surface)' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--bg-surface-raised)' }}>
                <th style={{ textAlign: 'left', padding: '12px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Tag</th>
                <th style={{ textAlign: 'left', padding: '12px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Absolute Path</th>
                <th style={{ textAlign: 'left', padding: '12px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {localFoldersLoading ? (
                <tr>
                  <td colSpan="3" style={{ padding: '20px 16px', color: 'var(--text-tertiary)', fontSize: 12 }}>Loading local folders…</td>
                </tr>
              ) : localFolders.length === 0 ? (
                <tr>
                  <td colSpan="3" style={{ padding: '20px 16px', color: 'var(--text-tertiary)', fontSize: 12 }}>No local folders have been registered yet.</td>
                </tr>
              ) : (
                localFolders.map(folder => (
                  <tr key={folder.id} style={{ borderTop: '1px solid var(--border-main)' }}>
                    <td style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-primary)', fontWeight: 600 }}>{folder.tag_name}</td>
                    <td style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{folder.absolute_path}</td>
                    <td style={{ padding: '12px 16px', fontSize: 12 }}>
                      <StatusBadge
                        status={folder.is_active ? 'connected' : 'draft'}
                        label={folder.is_active ? 'Active' : 'Inactive'}
                      />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Connector Configuration (Flattened) */}
      <div className="mb-8">
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
          <h2 className="text-primary font-semibold mb-4" style={{ fontSize: 15, margin: 0 }}>Connector Configuration</h2>
          <button
            aria-label="Refresh Connectors"
            onClick={refreshConnectors}
            style={{ marginLeft: 10, background: 'none', border: 'none', cursor: 'pointer', padding: 0, display: 'flex', alignItems: 'center' }}
            disabled={loading}
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} style={{ color: 'var(--accent-blue)' }} />
          </button>
        </div>
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
                    {renderConnectorIcon(conn.id, 18)}
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
                    {renderConnectorIcon(apiConn.id, 18)}
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
        onClose={() => {
          setManageOpen(false);
          // Always refresh connectors after closing the modal
          refreshConnectors();
        }}
        selectedConnector={selectedConnectorId}
      />

      <Modal
        open={localFolderModalOpen}
        onClose={() => setLocalFolderModalOpen(false)}
        title="Add Local Folder"
        size="md"
        footer={(
          <>
            <button
              onClick={() => setLocalFolderModalOpen(false)}
              style={{ background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', borderRadius: 8, padding: '8px 12px', cursor: 'pointer' }}
            >
              Cancel
            </button>
            <button
              onClick={saveLocalFolder}
              disabled={localFolderSubmitting}
              style={{ background: 'var(--accent-blue)', border: 'none', color: '#fff', borderRadius: 8, padding: '8px 12px', cursor: localFolderSubmitting ? 'not-allowed' : 'pointer', opacity: localFolderSubmitting ? 0.7 : 1 }}
            >
              {localFolderSubmitting ? 'Saving…' : 'Save Folder'}
            </button>
          </>
        )}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label className="text-secondary" style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Folder Tag</label>
            <input
              value={localFolderTagInput}
              onChange={(event) => setLocalFolderTagInput(event.target.value)}
              placeholder="Finance_PBIX"
              style={{ width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-input)', color: 'var(--text-primary)', outline: 'none' }}
            />
          </div>
          <div>
            <label className="text-secondary" style={{ display: 'block', fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Absolute Path</label>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
              <input
                value={localFolderPathInput}
                onChange={(event) => setLocalFolderPathInput(event.target.value)}
                placeholder="C:/Models/Finance"
                style={{ flex: 1, width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-input)', color: 'var(--text-primary)', outline: 'none' }}
              />
              <button
                type="button"
                onClick={openDirectoryPicker}
                style={{
                  border: '1px solid var(--border-main)',
                  borderRadius: 8,
                  background: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  fontSize: 12,
                  fontWeight: 600,
                  padding: '10px 12px',
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  whiteSpace: 'nowrap',
                }}
              >
                <FolderOpen size={14} />
                Browse
              </button>
            </div>
            <p style={{ margin: '6px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>
              The path must already exist on the machine running the backend.
            </p>
          </div>
          {localFolderError && (
            <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-error)30', background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
              {localFolderError}
            </div>
          )}
        </div>
      </Modal>

      <Modal
        open={directoryPickerOpen}
        onClose={() => setDirectoryPickerOpen(false)}
        title="Select Directory"
        size="md"
        footer={(
          <>
            <button
              type="button"
              onClick={() => setDirectoryPickerOpen(false)}
              style={{ background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', borderRadius: 8, padding: '8px 12px', cursor: 'pointer' }}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={selectCurrentDirectory}
              disabled={!directoryPath}
              style={{ background: 'var(--accent-blue)', border: 'none', color: '#fff', borderRadius: 8, padding: '8px 12px', cursor: !directoryPath ? 'not-allowed' : 'pointer', opacity: !directoryPath ? 0.7 : 1 }}
            >
              Select This Folder
            </button>
          </>
        )}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Current Path</div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {buildBreadcrumbs(directoryPath).map((crumb, index) => (
              <button
                key={`${crumb.path || 'home'}-${index}`}
                type="button"
                onClick={() => loadDirectoryEntries(crumb.path)}
                style={{
                  border: '1px solid var(--border-main)',
                  background: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  borderRadius: 999,
                  padding: '4px 10px',
                  fontSize: 11,
                  cursor: 'pointer',
                }}
              >
                {crumb.label}
              </button>
            ))}
          </div>

          <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, maxHeight: 280, overflow: 'auto', background: 'var(--bg-surface)' }}>
            {directoryLoading ? (
              <div style={{ padding: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>Loading directories...</div>
            ) : directoryEntries.length === 0 ? (
              <div style={{ padding: 12, fontSize: 12, color: 'var(--text-tertiary)' }}>No subdirectories found.</div>
            ) : directoryEntries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                onClick={() => loadDirectoryEntries(entry.path)}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  border: 'none',
                  borderBottom: '1px solid var(--border-subtle)',
                  background: 'transparent',
                  color: 'var(--text-primary)',
                  padding: '10px 12px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <FolderOpen size={14} />
                <span style={{ fontSize: 12 }}>{entry.name}</span>
              </button>
            ))}
          </div>

          {directoryError && (
            <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-error)30', background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
              {directoryError}
            </div>
          )}
        </div>
      </Modal>
      </div>
    </ConfigurationProvider>
  );
}