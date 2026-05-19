import React, { useState } from 'react';
import { Check, ChevronDown, RefreshCw, Loader2 } from 'lucide-react';
import { api } from '../../utils/api';
import SourceIcon from '../common/SourceIcon';
import SearchableSelect from '../common/SearchableSelect';
import { CONNECTOR_TYPES, TARGET_CONNECTOR_TYPES, PBIX_SOURCE_MODES } from '../../utils/constants';

const SECTION_CARD = {
  border: '1px solid var(--border-main)',
  borderRadius: 16,
  padding: '32px 36px',
  background: 'var(--bg-surface)',
  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.08)',
  position: 'relative',
  overflow: 'hidden'
};

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
  domainHint,
  setDomainHint,
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
  workspaces,
  workspacesLoading,
  isRefreshingWorkspaces,
  fetchFabricWorkspaces,
  runWarning,
}) {
  const [pbixDragOver, setPbixDragOver] = useState(false);
  const [pbixUploadError, setPbixUploadError] = useState('');
  
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
  const activeLocalFolders = (localFolders || []).filter(folder => folder?.is_active !== false);

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
              <label style={LABEL}>Source Account (override)</label>
              <input
                type="text" value={snowflakeAccountId} onChange={e => setSnowflakeAccountId(e.target.value)}
                placeholder="Use saved Snowflake account"
                style={INPUT}
                onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
            </div>
            <div>
              <label style={LABEL}>Source Warehouse (override)</label>
              <input
                type="text" value={targetWarehouse} onChange={e => setTargetWarehouse(e.target.value)}
                placeholder="Use saved Snowflake warehouse"
                style={INPUT}
                onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
            </div>
            <div style={{ gridColumn: 'span 2', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label style={LABEL}>Source Database</label>
                <input
                  type="text" value={snowflakeDatabase} onChange={e => setSnowflakeDatabase(e.target.value)}
                  placeholder="e.g. SNOWFLAKE_SAMPLE_DATA"
                  style={INPUT}
                  onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                  onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                />
              </div>
              <div>
                <label style={LABEL}>Source Schema</label>
                <input
                  type="text" value={snowflakeSchema} onChange={e => setSnowflakeSchema(e.target.value)}
                  placeholder="e.g. PUBLIC"
                  style={INPUT}
                  onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                  onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                />
              </div>
            </div>
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
                  <option key={acc.id} value={acc.id}>
                    {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
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
                items={workspaces}
                displayKey="name"
                valueKey="id"
                searchFields={['name', 'id', 'workspace_id']}
                placeholder={isRefreshingWorkspaces ? 'Refreshing...' : "Choose a workspace"}
                value={fabricWorkspaceId}
                onChange={item => setFabricWorkspaceId(item?.id || '')}
                loading={workspacesLoading || isRefreshingWorkspaces}
                clearable={false}
              />
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                Select the workspace containing the semantic models you want to migrate.
              </p>
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
                <label style={LABEL}>PBIX Upload</label>
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
                    border: '1px dashed var(--accent-blue)',
                    borderRadius: 12,
                    padding: 18,
                    background: pbixDragOver ? 'var(--accent-blue)14' : 'var(--accent-blue)08',
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
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                    Drag and drop a `.pbix` file here or click to browse
                    {pbixUploading && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
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
                        <label style={LABEL}>Snowflake Account (override)</label>
                        <input
                          type="text" value={targetAccount} onChange={e => setTargetAccount(e.target.value)}
                          placeholder="Use saved Snowflake account"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Warehouse (override)</label>
                        <input
                          type="text" value={targetWarehouse} onChange={e => setTargetWarehouse(e.target.value)}
                          placeholder="Use saved Snowflake warehouse"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Database (optional)</label>
                        <input
                          type="text" value={targetDatabase} onChange={e => setTargetDatabase(e.target.value)}
                          placeholder="Use global Snowflake database"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Schema (optional)</label>
                        <input
                          type="text" value={targetSchema} onChange={e => setTargetSchema(e.target.value)}
                          placeholder="Use global Snowflake schema"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
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
                          }}
                          style={INPUT}
                        >
                          {fabricAccounts.length === 0 && <option value="" disabled>No accounts available</option>}
                          {fabricAccounts.length > 0 && fabricAccounts.map(acc => (
                            <option key={acc.id} value={acc.id}>
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
                            disabled={isRefreshingWorkspaces}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 4,
                              background: 'none', border: 'none', cursor: isRefreshingWorkspaces ? 'not-allowed' : 'pointer',
                              fontSize: 11, color: 'var(--text-tertiary)', padding: '2px 6px',
                              borderRadius: 4, transition: 'all 0.2s ease',
                              opacity: isRefreshingWorkspaces ? 0.6 : 1
                            }}
                            title="Refresh workspaces"
                          >
                            <RefreshCw size={12} style={{ animation: isRefreshingWorkspaces ? 'spin 1s linear infinite' : 'none' }} />
                            Refresh
                          </button>
                        </div>

                        <SearchableSelect
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
                        />

                        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                          Choose the destination Fabric workspace for this target sync.
                        </p>
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
                              <option key={acc.id} value={acc.id}>
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

      <div style={SECTION_CARD}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>Optional Metadata</div>
        <label style={LABEL}>Domain Hint (optional)</label>
        <input
          type="text" value={domainHint} onChange={e => setDomainHint(e.target.value)}
          placeholder="e.g. finance, sales, hr — helps AI generate better names"
          style={INPUT}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
        />
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
          A domain hint improves generated labels and descriptions. It does not change connector behavior.
        </p>
      </div>
    </div>
  );
}
