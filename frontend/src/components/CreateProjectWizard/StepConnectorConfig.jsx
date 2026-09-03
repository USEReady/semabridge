import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Check, ChevronDown, RefreshCw, Loader2, CheckCircle2 } from 'lucide-react';
import { api } from '../../utils/api';
import SourceIcon from '../common/SourceIcon';
import SearchableSelect from '../common/SearchableSelect';
import MultiPbixUpload from './MultiPbixUpload';
import { uploadStatusColors } from './uploadStatusColors';
import { RemoveFileButton } from './uploadStatusUI';
import { CONNECTOR_TYPES, TARGET_CONNECTOR_TYPES, PBIX_SOURCE_MODES } from '../../utils/constants';

const SECTION_CARD = {
  border: '1px solid var(--border-main)',
  borderRadius: 16,
  padding: '32px 36px',
  background: 'var(--bg-surface)',
  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.08)',
  position: 'relative',
  overflow: 'visible'
};

function getAccountLabel(account) {
  const primary = account?.tag || account?.name || account?.identity_email || account?.account || account?.id;
  const secondary = account?.identity_email || account?.account || account?.locator || account?.id;
  if (!primary) return 'Saved account';
  if (!secondary || String(primary) === String(secondary)) return String(primary);
  return `${primary} (${secondary})`;
}

function getAccountId(account) {
  return String(account?.id || account?.identity_id || account?.account_id || '');
}

export function StepConnectorConfig({
  sourceConnector,
  targetConnectors,
  fabricAccountId,
  setFabricAccountId,
  fabricAccounts,
  fabricWorkspaceId,
  setFabricWorkspaceId,
  snowflakeAccountId,
  setSnowflakeAccountId,
  snowflakeAccounts,
  databricksAccountId,
  setDatabricksAccountId,
  databricksAccounts,
  snowflakeDatabase,
  setSnowflakeDatabase,
  snowflakeSchema,
  setSnowflakeSchema,
  targetDatabase,
  setTargetDatabase,
  targetSchema,
  setTargetSchema,
  targetAccount,
  setTargetAccount,
  targetWarehouse,
  setTargetWarehouse,
  pbixFile,
  setPbixFile,
  pbixUploadPath,
  pbixUploading,
  setPbixUploading,
  pbixSourceMode,
  setPbixSourceMode,
  localFolders,
  localFoldersLoading,
  selectedLocalFolderId,
  setSelectedLocalFolderId,
  onUploadSuccess,
  onClearPbix = () => {},
  pbixMultiFileMode = false,
  setPbixMultiFileMode = () => {},
  setPbixFilePaths = () => {},
  workspaces,
  workspacesLoading,
  isRefreshingWorkspaces,
  fetchFabricWorkspaces,
  workspaceDiscoveryError,
  runWarning,
}) {
  const [pbixDragOver, setPbixDragOver] = useState(false);
  const [pbixUploadError, setPbixUploadError] = useState('');
  
  // Dynamic Snowflake discovery states
  const [snowflakeWarehouses, setSnowflakeWarehouses] = useState([]);
  const [snowflakeDatabases, setSnowflakeDatabases] = useState([]);
  const [snowflakeSchemas, setSnowflakeSchemas] = useState([]);
  const [snowflakeDiscoveryLoading, setSnowflakeDiscoveryLoading] = useState(false);
  const [snowflakeSchemaLoading, setSnowflakeSchemaLoading] = useState(false);
  const [snowflakeDiscoveryError, setSnowflakeDiscoveryError] = useState('');

  // Race condition protection refs
  const latestAccountIdRef = useRef(snowflakeAccountId);
  const activeSnowflakeDatabase = sourceConnector === 'snowflake' ? snowflakeDatabase : targetDatabase;
  const latestDatabaseRef = useRef(activeSnowflakeDatabase);

  useEffect(() => {
    latestAccountIdRef.current = snowflakeAccountId;
  }, [snowflakeAccountId]);

  useEffect(() => {
    latestDatabaseRef.current = activeSnowflakeDatabase;
  }, [activeSnowflakeDatabase]);

  const handleSnowflakeAccountSelect = (value, role = 'target') => {
    setSnowflakeAccountId(value);
    if (role === 'target') {
      setTargetAccount('');
    }
  };

  const normalizeDiscoveryItems = useCallback((items) => (
    (Array.isArray(items) ? items : [])
      .map((item) => {
        const id = String(item?.id || item?.name || item || '').trim();
        const name = String(item?.name || item?.id || item || '').trim();
        return id ? { id, name } : null;
      })
      .filter(Boolean)
  ), []);

  const uploadPbixFile = async (file) => {
    if (!file) return;
    if (!String(file.name || '').toLowerCase().endsWith('.pbix')) {
      setPbixUploadError('Only .pbix files are supported.');
      return;
    }

    setPbixUploadError('');
    setPbixUploading(true);
    try {
      const response = await api.uploadPbix(file);
      const uploadedPath = String(response?.path || '').trim();
      setPbixFile(file);
      onUploadSuccess?.({ file, path: uploadedPath });
      if (!uploadedPath) {
        setPbixUploadError('Upload succeeded but server did not return a file path.');
      }
    } catch (err) {
      setPbixUploadError(err?.message || 'PBIX upload failed.');
    } finally {
      setPbixUploading(false);
    }
  };

  const handlePbixDrop = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    setPbixDragOver(false);
    const droppedFile = event.dataTransfer?.files?.[0] || null;
    if (droppedFile) await uploadPbixFile(droppedFile);
  };
  
  const selectedTargets = [...targetConnectors];
  const sourceLabel = CONNECTOR_TYPES.find(c => c.value === sourceConnector)?.label || sourceConnector;
  const activeLocalFolders = useMemo(
    () => (localFolders || []).filter(folder => folder?.is_active !== false),
    [localFolders],
  );

  // Auto-select folder if pbix source mode is folder tags
  useEffect(() => {
    if (sourceConnector !== 'pbix' || pbixSourceMode !== 'TAG') return;
    if (localFoldersLoading || activeLocalFolders.length === 0) return;

    const hasSelection = activeLocalFolders.some(folder => String(folder.id) === String(selectedLocalFolderId || ''));
    if (!hasSelection) {
      setSelectedLocalFolderId(String(activeLocalFolders[0]?.id || ''));
    }
  }, [
    activeLocalFolders,
    localFoldersLoading,
    pbixSourceMode,
    selectedLocalFolderId,
    setSelectedLocalFolderId,
    sourceConnector,
  ]);

  // Effect to load warehouses and databases on Snowflake Account selection
  useEffect(() => {
    const needsSnowflake = sourceConnector === 'snowflake' || targetConnectors.has('snowflake');
    if (!needsSnowflake || !snowflakeAccountId) {
      setSnowflakeWarehouses([]);
      setSnowflakeDatabases([]);
      setSnowflakeSchemas([]);
      setSnowflakeDiscoveryError('');
      return;
    }

    let active = true;
    setSnowflakeDiscoveryLoading(true);
    setSnowflakeDiscoveryError('');

    Promise.all([
      api.discoverSnowflakeWarehouses(snowflakeAccountId),
      api.discoverSnowflakeDatabases(snowflakeAccountId),
    ])
      .then(([warehouseData, databaseData]) => {
        // Safe check: ignore responses if account ID changed since request started
        if (!active || snowflakeAccountId !== latestAccountIdRef.current) return;
        
        const warehouses = normalizeDiscoveryItems(warehouseData);
        const databases = normalizeDiscoveryItems(databaseData);
        setSnowflakeWarehouses(warehouses);
        setSnowflakeDatabases(databases);

        const hasWarehouse = warehouses.some(item => item.id === targetWarehouse);
        if (!hasWarehouse) {
          setTargetWarehouse(warehouses[0]?.id || '');
        }

        const hasSourceDatabase = databases.some(item => item.id === snowflakeDatabase);
        if (sourceConnector === 'snowflake' && !hasSourceDatabase) {
          setSnowflakeDatabase(databases[0]?.id || '');
        }

        const hasTargetDatabase = databases.some(item => item.id === targetDatabase);
        if (targetConnectors.has('snowflake') && !hasTargetDatabase) {
          setTargetDatabase(databases[0]?.id || '');
        }
      })
      .catch((err) => {
        if (!active || snowflakeAccountId !== latestAccountIdRef.current) return;
        setSnowflakeWarehouses([]);
        setSnowflakeDatabases([]);
        setSnowflakeSchemas([]);
        setSnowflakeDiscoveryError(err?.message || 'Could not load Snowflake account metadata.');
      })
      .finally(() => {
        if (active && snowflakeAccountId === latestAccountIdRef.current) {
          setSnowflakeDiscoveryLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [
    normalizeDiscoveryItems,
    snowflakeAccountId,
    sourceConnector,
    targetConnectors,
    setSnowflakeDatabase,
    setTargetDatabase,
    setTargetWarehouse,
  ]);

  // Effect to load schemas on Database selection
  useEffect(() => {
    const needsSnowflake = sourceConnector === 'snowflake' || targetConnectors.has('snowflake');
    if (!needsSnowflake || !snowflakeAccountId || !activeSnowflakeDatabase) {
      setSnowflakeSchemas([]);
      return;
    }

    let active = true;
    setSnowflakeSchemaLoading(true);
    
    api.discoverSnowflakeSchemas(activeSnowflakeDatabase, snowflakeAccountId)
      .then((schemaData) => {
        // Safe check: ignore responses if database or account ID has changed
        if (!active || activeSnowflakeDatabase !== latestDatabaseRef.current || snowflakeAccountId !== latestAccountIdRef.current) return;
        
        const schemas = normalizeDiscoveryItems(schemaData);
        setSnowflakeSchemas(schemas);

        if (sourceConnector === 'snowflake') {
          const hasSchema = schemas.some(item => item.id === snowflakeSchema);
          if (!hasSchema) setSnowflakeSchema(schemas[0]?.id || '');
        }

        if (targetConnectors.has('snowflake')) {
          const hasSchema = schemas.some(item => item.id === targetSchema);
          if (!hasSchema) setTargetSchema(schemas[0]?.id || '');
        }
      })
      .catch((err) => {
        if (!active || activeSnowflakeDatabase !== latestDatabaseRef.current || snowflakeAccountId !== latestAccountIdRef.current) return;
        setSnowflakeSchemas([]);
        setSnowflakeDiscoveryError(err?.message || 'Could not load Snowflake schemas.');
      })
      .finally(() => {
        if (active && activeSnowflakeDatabase === latestDatabaseRef.current && snowflakeAccountId === latestAccountIdRef.current) {
          setSnowflakeSchemaLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [
    activeSnowflakeDatabase,
    normalizeDiscoveryItems,
    snowflakeAccountId,
    sourceConnector,
    targetConnectors,
    setSnowflakeSchema,
    setTargetSchema,
  ]);

  const LABEL = {
    display: 'block', fontSize: 11, fontWeight: 700,
    color: 'var(--text-tertiary)', marginBottom: 6,
    textTransform: 'uppercase', letterSpacing: '0.05em'
  };
  const INPUT = {
    width: '100%', padding: '10px 14px', borderRadius: 8,
    background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
    color: 'var(--text-primary)', fontSize: 13, outline: 'none',
    transition: 'all 0.2s ease',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, paddingBottom: 40 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Connector Configuration</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Configure the source and target connectors for your project.
        </p>
      </div>

      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>1. Source Configuration: {sourceLabel}</div>
        </div>

        {sourceConnector === 'snowflake' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={LABEL}>Source Snowflake Account</label>
              <select
                value={snowflakeAccountId}
                onChange={e => handleSnowflakeAccountSelect(e.target.value, 'source')}
                style={INPUT}
              >
                {snowflakeAccounts.length === 0 && <option value="">No saved Snowflake accounts</option>}
                {snowflakeAccounts.map(acc => (
                  <option key={getAccountId(acc)} value={getAccountId(acc)}>
                    {getAccountLabel(acc)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label style={LABEL}>Source Warehouse</label>
              <select
                value={targetWarehouse}
                onChange={e => setTargetWarehouse(e.target.value)}
                style={INPUT}
                disabled={snowflakeDiscoveryLoading || snowflakeWarehouses.length === 0}
              >
                {snowflakeDiscoveryLoading && <option value="">Loading warehouses...</option>}
                {!snowflakeDiscoveryLoading && snowflakeWarehouses.length === 0 && <option value="">No warehouses found</option>}
                {snowflakeWarehouses.map(item => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </div>
            <div style={{ gridColumn: 'span 2', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label style={LABEL}>Source Database</label>
                <select
                  value={snowflakeDatabase}
                  onChange={e => setSnowflakeDatabase(e.target.value)}
                  style={INPUT}
                  disabled={snowflakeDiscoveryLoading || snowflakeDatabases.length === 0}
                >
                  {snowflakeDiscoveryLoading && <option value="">Loading databases...</option>}
                  {!snowflakeDiscoveryLoading && snowflakeDatabases.length === 0 && <option value="">No databases found</option>}
                  {snowflakeDatabases.map(item => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={LABEL}>Source Schema</label>
                <select
                  value={snowflakeSchema}
                  onChange={e => setSnowflakeSchema(e.target.value)}
                  style={INPUT}
                  disabled={snowflakeSchemaLoading || snowflakeSchemas.length === 0}
                >
                  {snowflakeSchemaLoading && <option value="">Loading schemas...</option>}
                  {!snowflakeSchemaLoading && snowflakeSchemas.length === 0 && <option value="">No schemas found</option>}
                  {snowflakeSchemas.map(item => (
                    <option key={item.id} value={item.id}>{item.name}</option>
                  ))}
                </select>
              </div>
            </div>
            {snowflakeDiscoveryError && (
              <div style={{ gridColumn: 'span 2', color: 'var(--color-error)', fontSize: 11 }}>
                {snowflakeDiscoveryError}
              </div>
            )}
          </div>
        )}

        {sourceConnector === 'fabric' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label style={LABEL}>Fabric Account</label>
              <select
                value={fabricAccountId}
                onChange={e => {
                  const nextAccountId = e.target.value;
                  setFabricAccountId(nextAccountId);
                  setFabricWorkspaceId('');
                  fetchFabricWorkspaces(nextAccountId);
                }}
                style={INPUT}
              >
                <option value="" disabled>Select Fabric connection</option>
                {fabricAccounts.length === 0 && <option value="" disabled>No accounts available</option>}
                {fabricAccounts.length > 0 && fabricAccounts.map(acc => (
                  <option key={getAccountId(acc)} value={getAccountId(acc)}>
                    {getAccountLabel(acc)}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <label style={{ ...LABEL, margin: 0 }}>Source Fabric Workspace</label>
                <div style={{ flex: 1 }} />
                <button
                  onClick={() => fetchFabricWorkspaces(fabricAccountId)}
                  disabled={isRefreshingWorkspaces || !fabricAccountId}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 4,
                    background: 'none', border: 'none', cursor: (isRefreshingWorkspaces || !fabricAccountId) ? 'not-allowed' : 'pointer',
                    fontSize: 11, color: 'var(--text-tertiary)', padding: '2px 6px',
                    borderRadius: 4, transition: 'all 0.2s ease',
                    opacity: (isRefreshingWorkspaces || !fabricAccountId) ? 0.6 : 1
                  }}
                  title="Refresh workspaces"
                >
                  <RefreshCw size={12} style={{ animation: isRefreshingWorkspaces ? 'spin 1s linear infinite' : 'none' }} />
                  Refresh
                </button>
              </div>
              <SearchableSelect
                key={`fabric-source-${fabricAccountId || 'none'}-${workspaces.length}-${isRefreshingWorkspaces ? 'loading' : 'ready'}`}
                items={workspaces}
                displayKey="name"
                valueKey="id"
                searchFields={['name', 'id', 'workspace_id']}
                placeholder={isRefreshingWorkspaces ? 'Refreshing...' : "Choose a workspace"}
                value={fabricWorkspaceId}
                onChange={item => setFabricWorkspaceId(item?.id || '')}
                loading={workspacesLoading || isRefreshingWorkspaces}
                clearable={false}
                defaultOpen={sourceConnector === 'fabric' && workspaces.length > 0}
              />
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                Select the workspace containing the semantic models you want to migrate.
              </p>
              {workspaceDiscoveryError && (
                <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 6 }}>
                  {workspaceDiscoveryError}
                </p>
              )}
            </div>
          </div>
        )}

        {sourceConnector === 'pbix' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label style={LABEL}>PBIX Source Mode</label>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {PBIX_SOURCE_MODES.map(mode => {
                  const active = pbixSourceMode === mode.value;
                  return (
                    <button
                      key={mode.value}
                      type="button"
                      onClick={() => setPbixSourceMode(mode.value)}
                      style={{
                        border: `1px solid ${active ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                        background: active ? 'var(--accent-blue)14' : 'var(--bg-surface)',
                        color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
                        borderRadius: 999,
                        padding: '7px 12px',
                        cursor: 'pointer',
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >
                      {mode.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {pbixSourceMode === 'TAG' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div>
                  <label style={LABEL}>Folder Tag</label>
                  <select
                    value={selectedLocalFolderId}
                    onChange={(event) => setSelectedLocalFolderId(event.target.value)}
                    style={INPUT}
                  >
                    <option value="">Select Folder Tag...</option>
                    {activeLocalFolders.map(folder => (
                      <option key={folder.id} value={folder.id}>
                        {folder.tag_name} ({folder.absolute_path})
                      </option>
                    ))}
                  </select>
                  <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                    {localFoldersLoading
                      ? 'Loading trusted local folders…'
                      : activeLocalFolders.length === 0
                        ? 'No active local folders are registered in Settings yet.'
                        : 'Step 3 will show the PBIX files inside the selected tagged folder.'}
                  </p>
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <label style={LABEL}>PBIX Upload</label>
                  <div style={{ display: 'flex', gap: 6 }}>
                    {[
                      { value: false, label: 'Single file' },
                      { value: true, label: 'Multiple files' },
                    ].map((opt) => {
                      const active = pbixMultiFileMode === opt.value;
                      return (
                        <button
                          key={String(opt.value)}
                          type="button"
                          onClick={() => setPbixMultiFileMode(opt.value)}
                          style={{
                            border: `1px solid ${active ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                            background: active ? 'var(--accent-blue)14' : 'var(--bg-surface)',
                            color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
                            borderRadius: 999,
                            padding: '4px 10px',
                            cursor: 'pointer',
                            fontSize: 11,
                            fontWeight: 700,
                          }}
                        >
                          {opt.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {pbixMultiFileMode ? (
                  <MultiPbixUpload onFilesChange={setPbixFilePaths} maxFiles={10} />
                ) : (() => {
                  const hasUploadedPbix = Boolean(pbixUploadPath) && !pbixUploading && !pbixUploadError;
                  const doneStyle = uploadStatusColors(hasUploadedPbix ? 'done' : null);
                  return (
                <label
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setPbixDragOver(true);
                  }}
                  onDragLeave={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setPbixDragOver(false);
                  }}
                  onDrop={handlePbixDrop}
                  style={{
                    position: 'relative',
                    border: `1px dashed ${doneStyle?.border || 'var(--accent-blue)'}`,
                    borderRadius: 12,
                    padding: 18,
                    background: doneStyle?.background || (pbixDragOver ? 'var(--accent-blue)14' : 'var(--accent-blue)08'),
                    color: 'var(--text-secondary)',
                    cursor: pbixUploading ? 'progress' : 'pointer',
                  }}
                >
                  <input
                    type="file"
                    accept=".pbix"
                    style={{ display: 'none' }}
                    disabled={pbixUploading}
                    onChange={async (event) => {
                      const nextFile = event.target.files?.[0] || null;
                      if (nextFile) await uploadPbixFile(nextFile);
                    }}
                  />
                  {hasUploadedPbix && (
                    <div style={{ position: 'absolute', top: 10, right: 10 }}>
                      <RemoveFileButton
                        title="Remove file"
                        onClick={() => {
                          setPbixUploadError('');
                          setPbixDragOver(false);
                          onClearPbix();
                        }}
                      />
                    </div>
                  )}
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                    Drag and drop a `.pbix` file here or click to browse
                    {pbixUploading && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
                    {hasUploadedPbix && (
                      <span
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: 4,
                          fontSize: 11, fontWeight: 700, color: 'var(--color-success)',
                          background: 'var(--color-success-bg)', border: '1px solid var(--color-success)',
                          borderRadius: 999, padding: '2px 8px',
                        }}
                      >
                        <CheckCircle2 size={12} /> Uploaded Successfully
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: hasUploadedPbix ? 'var(--color-success)' : 'var(--text-tertiary)' }}>
                    {pbixUploading
                      ? 'Saving file to server...'
                      : pbixUploadPath
                        ? `Saved path: ${pbixUploadPath}`
                        : pbixFile
                          ? `Selected file: ${pbixFile.name}`
                          : 'The file is uploaded temporarily and passed to LocalPBIXConnector during sync.'}
                  </div>
                  {pbixUploading && (
                    <div style={{ marginTop: 10, width: '100%', height: 6, borderRadius: 999, background: 'var(--border-main)', overflow: 'hidden' }}>
                      <div style={{ width: '100%', height: '100%', background: 'var(--accent-blue)', animation: 'pulse 1.2s ease-in-out infinite' }} />
                    </div>
                  )}
                  {pbixUploadError && (
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-error)' }}>
                      {pbixUploadError}
                    </div>
                  )}
                </label>
                  );
                })()}
              </div>
            )}
          </div>
        )}

        {sourceConnector !== 'fabric' && sourceConnector !== 'snowflake' && sourceConnector !== 'pbix' && (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
            Source connector defaults will be resolved from the saved global connection.
          </div>
        )}
      </div>

      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>2. Target Configuration</div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
            Configure each selected target separately. Leave fields empty to use global defaults.
          </div>
        </div>

        {selectedTargets.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            No target connector selected. Go back to Basic Info and choose at least one target.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {selectedTargets.map(target => {
            const targetMeta = TARGET_CONNECTOR_TYPES.find(t => t.value === target) || { label: target };
            return (
              <details key={target} open style={{ border: '1px solid var(--border-main)', borderRadius: 10 }}>
                <summary
                  style={{
                    listStyle: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    padding: '10px 12px',
                    background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <SourceIcon source={targetMeta.value ?? target} size={16} />
                    {targetMeta.label} Target Configuration
                  </span>
                  <ChevronDown size={14} />
                </summary>

                <div style={{ padding: 12, background: 'var(--bg-surface)' }}>
                  {target === 'snowflake' && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                      <div>
                        <label style={LABEL}>Snowflake Account</label>
                        <select
                          value={snowflakeAccountId}
                          onChange={e => handleSnowflakeAccountSelect(e.target.value, 'target')}
                          style={INPUT}
                        >
                          {snowflakeAccounts.length === 0 && <option value="">No saved Snowflake accounts</option>}
                          {snowflakeAccounts.map(acc => (
                            <option key={getAccountId(acc)} value={getAccountId(acc)}>
                              {getAccountLabel(acc)}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Warehouse</label>
                        <select
                          value={targetWarehouse}
                          onChange={e => setTargetWarehouse(e.target.value)}
                          style={INPUT}
                          disabled={snowflakeDiscoveryLoading || snowflakeWarehouses.length === 0}
                        >
                          {snowflakeDiscoveryLoading && <option value="">Loading warehouses...</option>}
                          {!snowflakeDiscoveryLoading && snowflakeWarehouses.length === 0 && <option value="">No warehouses found</option>}
                          {snowflakeWarehouses.map(item => (
                            <option key={item.id} value={item.id}>{item.name}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Database</label>
                        <select
                          value={targetDatabase}
                          onChange={e => setTargetDatabase(e.target.value)}
                          style={INPUT}
                          disabled={snowflakeDiscoveryLoading || snowflakeDatabases.length === 0}
                        >
                          {snowflakeDiscoveryLoading && <option value="">Loading databases...</option>}
                          {!snowflakeDiscoveryLoading && snowflakeDatabases.length === 0 && <option value="">No databases found</option>}
                          {snowflakeDatabases.map(item => (
                            <option key={item.id} value={item.id}>{item.name}</option>
                          ))}
                        </select>
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Schema</label>
                        <select
                          value={targetSchema}
                          onChange={e => setTargetSchema(e.target.value)}
                          style={INPUT}
                          disabled={snowflakeSchemaLoading || snowflakeSchemas.length === 0}
                        >
                          {snowflakeSchemaLoading && <option value="">Loading schemas...</option>}
                          {!snowflakeSchemaLoading && snowflakeSchemas.length === 0 && <option value="">No schemas found</option>}
                          {snowflakeSchemas.map(item => (
                            <option key={item.id} value={item.id}>{item.name}</option>
                          ))}
                        </select>
                      </div>
                      {snowflakeDiscoveryError && (
                        <div style={{ gridColumn: 'span 2', color: 'var(--color-error)', fontSize: 11 }}>
                          {snowflakeDiscoveryError}
                        </div>
                      )}
                    </div>
                  )}

                  {target === 'fabric' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div>
                        <label style={LABEL}>Fabric Account</label>
                        <select
                          value={fabricAccountId}
                          onChange={e => {
                            const nextAccountId = e.target.value;
                            setFabricAccountId(nextAccountId);
                            setFabricWorkspaceId('');
                            fetchFabricWorkspaces(nextAccountId);
                          }}
                          style={INPUT}
                        >
                          {fabricAccounts.length === 0 && <option value="" disabled>No accounts available</option>}
                          {fabricAccounts.length > 0 && fabricAccounts.map(acc => (
                            <option key={getAccountId(acc)} value={getAccountId(acc)}>
                              {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
                            </option>
                          ))}
                        </select>
                        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                          Choose the Fabric identity for target deployment.
                        </p>
                      </div>

                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <label style={{ ...LABEL, margin: 0 }}>Target Fabric Workspace</label>
                          <div style={{ flex: 1 }} />
                          <button
                            onClick={() => fetchFabricWorkspaces(fabricAccountId)}
                            disabled={isRefreshingWorkspaces || !fabricAccountId}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 4,
                              background: 'none', border: 'none', cursor: (isRefreshingWorkspaces || !fabricAccountId) ? 'not-allowed' : 'pointer',
                              fontSize: 11, color: 'var(--text-tertiary)', padding: '2px 6px',
                              borderRadius: 4, transition: 'all 0.2s ease',
                              opacity: (isRefreshingWorkspaces || !fabricAccountId) ? 0.6 : 1
                            }}
                            title="Refresh workspaces"
                          >
                            <RefreshCw size={12} style={{ animation: isRefreshingWorkspaces ? 'spin 1s linear infinite' : 'none' }} />
                            Refresh
                          </button>
                        </div>

                        <SearchableSelect
                          key={`fabric-target-${fabricAccountId || 'none'}-${workspaces.length}-${isRefreshingWorkspaces ? 'loading' : 'ready'}`}
                          items={workspaces}
                          displayKey="name"
                          valueKey="id"
                          searchFields={['name', 'id', 'workspace_id']}
                          placeholder={isRefreshingWorkspaces ? 'Refreshing...' : 'Choose a target workspace'}
                          value={fabricWorkspaceId}
                          onChange={item => {
                            const newId = item?.id || '';
                            setFabricWorkspaceId(newId);
                          }}
                          loading={workspacesLoading || isRefreshingWorkspaces}
                          clearable={false}
                          defaultOpen={targetConnectors.has('fabric') && workspaces.length > 0}
                        />

                        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                          Choose the destination Fabric workspace for this target sync.
                        </p>
                        {workspaceDiscoveryError && (
                          <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 6 }}>
                            {workspaceDiscoveryError}
                          </p>
                        )}
                      </div>
                    </div>
                  )}

                  {target === 'databricks' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      {databricksAccounts.length > 0 && (
                        <div>
                          <label style={LABEL}>Databricks Account</label>
                          <select
                            value={databricksAccountId}
                            onChange={e => setDatabricksAccountId(e.target.value)}
                            style={INPUT}
                          >
                            {databricksAccounts.map(acc => (
                              <option key={getAccountId(acc)} value={getAccountId(acc)}>
                                {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
                              </option>
                            ))}
                          </select>
                          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                            Select which Databricks identity to use for deployment.
                          </p>
                        </div>
                      )}
                      {databricksAccounts.length === 0 && (
                        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                          No Databricks accounts configured. This target will use global connection defaults from Settings.
                        </div>
                      )}
                    </div>
                  )}

                  {target !== 'snowflake' && target !== 'fabric' && target !== 'databricks' && (
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                      This target will use global connection defaults from Settings.
                    </div>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      </div>

    </div>
  );
}
