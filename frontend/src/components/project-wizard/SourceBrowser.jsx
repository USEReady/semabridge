import { useState } from 'react';
import { ChevronDown, ChevronRight, CheckSquare, Square, Loader2 } from 'lucide-react';
import SmartSearchBar, { matchesSmartQuery } from '../common/SmartSearchBar';
export function StepSourceBrowser({
  sourceConnector, selectedWorkspace, workspaces, wsLoading,
  expandedWs, toggleWorkspace, wsModels,
  selectedModels, toggleModel, clearSelectedModels,
  modelQuery, setModelQuery,
  modelQueryRegex, setModelQueryRegex,
  modelResults, snowflakeResults,
  pbixSourceMode = 'TAG',
  selectedLocalFolderTag = '',
  pbixFiles = [],
  pbixFilesLoading = false,
  pbixFilesError = '',
  selectedPbixFilePath = '',
  onSelectPbixFile = () => {},
  // Databricks props
  databricksObjects = [],
  databricksLoading = false,
  selectedDatabricksTables = new Set(),
  setSelectedDatabricksTables = () => {},
  databricksQuery = '',
  setDatabricksQuery = () => {},
  databricksQueryRegex = false,
  setDatabricksQueryRegex = () => {},
}) {

  if (sourceConnector === 'pbix') {
    if (pbixSourceMode === 'MANUAL') {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>PBIX Source Ready</h2>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
              PBIX sync skips model discovery. Continue to the finish step to launch the pipeline and monitor Extraction, OSI Conversion, SML Generation, and Snowflake Deployment.
            </p>
          </div>
          <div style={{ padding: '18px 20px', borderRadius: 12, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Upload the `.pbix` file in Connector Config, then use Start Sync after the project is created.
            </div>
          </div>
        </div>
      );
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select PBIX File</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            Choose a file from the tagged folder before continuing. The selected row will populate the final `pbix_file_path`.
          </p>
        </div>

        <div style={{ padding: '14px 16px', borderRadius: 12, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Folder tag: <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{selectedLocalFolderTag || 'Not selected'}</span>
          </div>
        </div>

        {pbixFilesError && (
          <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-error)30', background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
            {pbixFilesError}
          </div>
        )}

        <div className="custom-scrollbar" style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {pbixFilesLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering PBIX files…
            </div>
          ) : pbixFiles.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No `.pbix` files were found in the selected folder.
            </div>
          ) : pbixFiles.map(file => {
            const isSelected = selectedPbixFilePath === file.path;
            return (
              <div
                key={file.path}
                onClick={() => onSelectPbixFile(file.path)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '12px 14px',
                  cursor: 'pointer',
                  borderBottom: '1px solid var(--border-subtle)',
                  background: isSelected ? 'var(--accent-blue)0a' : 'transparent',
                }}
                onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
              >
                <input type="radio" checked={isSelected} readOnly style={{ accentColor: 'var(--accent-blue)' }} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{file.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
                    {file.modified_at ? `Last modified ${new Date(file.modified_at).toLocaleString()}` : file.path}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (sourceConnector === 'fabric') {
    // Conditional rendering for Fabric step
    if (!selectedWorkspace) {
      return (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
          Choose a Fabric workspace in Connector Config before selecting models.
        </div>
      );
    }

    const fabricModels = wsModels[selectedWorkspace.id] || [];
    const displayModels = modelQuery
      ? fabricModels.filter(m =>
          matchesSmartQuery(
            `${m.name || ''} ${m.id || ''} ${m.description || ''}`,
            modelQuery,
            modelQueryRegex,
          )
        )
      : fabricModels.map(m => ({ ...m, _id: m.id }));

    // Show loader if loading
    if (wsLoading) {
      return (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
            <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
            Discovering Fabric semantic models…
          </div>
        </div>
      );
    }

    // Show models if available
    if (displayModels.length > 0) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Models</h2>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
              Choose which Fabric semantic models to include from {selectedWorkspace.name}. Leave all unchecked to include everything in this workspace.
            </p>
          </div>
          <SmartSearchBar
            value={modelQuery}
            onChange={setModelQuery}
            useRegex={modelQueryRegex}
            onToggleRegex={setModelQueryRegex}
            placeholder={`Search models in ${selectedWorkspace.name}`}
          />
          {selectedModels.size > 0 && (
            <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
              {selectedModels.size} model{selectedModels.size !== 1 ? 's' : ''} selected
              <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
                Clear
              </button>
            </div>
          )}
          <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
            {displayModels.map(m => {
              const modelId = m._id || m.id;
              return (
                <ModelRow
                  key={modelId}
                  model={{ ...m, _id: modelId }}
                  selected={selectedModels.has(modelId)}
                  onToggle={() => toggleModel(modelId, m.name || m.id)}
                />
              );
            })}
          </div>
        </div>
      );
    }

    // Show no models found placeholder
    return (
      <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
        <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
          No Fabric semantic models found. Check connector setup in Settings.
        </div>
      </div>
    );
  }

  if (sourceConnector === 'databricks') {
    // Flatten all tables for search
    const allTables = (databricksObjects || []).flatMap(obj =>
      (obj.tables || []).map(tbl => ({
        catalog: obj.catalog,
        schema: obj.schema,
        table: tbl,
        key: `${obj.catalog}.${obj.schema}.${tbl}`
      }))
    );
    const filteredTables = databricksQuery
      ? allTables.filter(t =>
          matchesSmartQuery(
            `${t.table || ''} ${t.schema || ''} ${t.catalog || ''}`,
            databricksQuery,
            databricksQueryRegex,
          )
        )
      : allTables;

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Databricks Tables</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            Choose which Databricks tables to include. Leave all unchecked to include everything.
          </p>
        </div>
        <SmartSearchBar
          value={databricksQuery}
          onChange={setDatabricksQuery}
          useRegex={databricksQueryRegex}
          onToggleRegex={setDatabricksQueryRegex}
          placeholder="Search Databricks tables"
        />
        {selectedDatabricksTables.size > 0 && (
          <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
            {selectedDatabricksTables.size} table{selectedDatabricksTables.size !== 1 ? 's' : ''} selected
            <button onClick={() => setSelectedDatabricksTables(new Set())} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
              Clear
            </button>
          </div>
        )}
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {databricksLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering Databricks tables…
            </div>
          ) : filteredTables.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No Databricks tables found. Check connector setup in Settings.
            </div>
          ) : (
            filteredTables.map(t => (
              <div
                key={t.key}
                onClick={() => {
                  setSelectedDatabricksTables(prev => {
                    const s = new Set(prev);
                    s.has(t.key) ? s.delete(t.key) : s.add(t.key);
                    return s;
                  });
                }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 12px', cursor: 'pointer', userSelect: 'none',
                  background: selectedDatabricksTables.has(t.key) ? 'var(--accent-blue)0a' : 'transparent',
                  borderBottom: '1px solid var(--border-subtle)'
                }}
                onMouseEnter={e => { if (!selectedDatabricksTables.has(t.key)) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                onMouseLeave={e => { if (!selectedDatabricksTables.has(t.key)) e.currentTarget.style.background = 'transparent'; }}
              >
                {selectedDatabricksTables.has(t.key)
                  ? <CheckSquare size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
                  : <Square size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
                <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{t.table}</span>
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>{t.catalog}.{t.schema}</span>
              </div>
            ))
          )}
        </div>
      </div>
    );
  }

  if (sourceConnector === 'fabric') {
    // Conditional rendering for Fabric step
    if (!selectedWorkspace) {
      return (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
          Choose a Fabric workspace in Connector Config before selecting models.
        </div>
      );
    }

    const fabricModels = wsModels[selectedWorkspace.id] || [];
    const displayModels = modelQuery
      ? fabricModels.filter(m =>
          matchesSmartQuery(
            `${m.name || ''} ${m.id || ''} ${m.description || ''}`,
            modelQuery,
            modelQueryRegex,
          )
        )
      : fabricModels.map(m => ({ ...m, _id: m.id }));

    // Show loader if loading
    if (wsLoading) {
      return (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
            <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
            Discovering Fabric semantic models…
          </div>
        </div>
      );
    }

    // Show models if available
    if (displayModels.length > 0) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Models</h2>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
              Choose which Fabric semantic models to include from {selectedWorkspace.name}. Leave all unchecked to include everything in this workspace.
            </p>
          </div>
          <SmartSearchBar
            value={modelQuery}
            onChange={setModelQuery}
            useRegex={modelQueryRegex}
            onToggleRegex={setModelQueryRegex}
            placeholder={`Search models in ${selectedWorkspace.name}`}
          />
          {selectedModels.size > 0 && (
            <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
              {selectedModels.size} model{selectedModels.size !== 1 ? 's' : ''} selected
              <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
                Clear
              </button>
            </div>
          )}
          <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
            {displayModels.map(m => {
              const modelId = m._id || m.id;
              return (
                <ModelRow
                  key={modelId}
                  model={{ ...m, _id: modelId }}
                  selected={selectedModels.has(modelId)}
                  onToggle={() => toggleModel(modelId, m.name || m.id)}
                />
              );
            })}
          </div>
        </div>
      );
    }

    // Show no models found placeholder
    return (
      <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
        <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
          No Fabric semantic models found. Check connector setup in Settings.
        </div>
      </div>
    );
  }

  if (sourceConnector === 'snowflake') {
    const snowflakeModels = wsModels.snowflake || [];
    const displayModels = modelQuery ? snowflakeResults : snowflakeModels.map(m => ({ ...m, _id: m.id }));

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Sources</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            Choose which Snowflake semantic objects to include. Leave all unchecked to include everything.
          </p>
        </div>

        <SmartSearchBar
          value={modelQuery}
          onChange={setModelQuery}
          useRegex={modelQueryRegex}
          onToggleRegex={setModelQueryRegex}
          placeholder="Search Snowflake semantic objects"
        />

        {selectedModels.size > 0 && (
          <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
            {selectedModels.size} object{selectedModels.size !== 1 ? 's' : ''} selected
            <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
              Clear
            </button>
          </div>
        )}

        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {wsLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering Snowflake semantic objects…
            </div>
          ) : displayModels.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No Snowflake semantic objects found. Check connector setup in Settings.
            </div>
          ) : (
            displayModels.map(m => {
              const modelId = m._id || m.id;
              return (
                <ModelRow
                  key={modelId}
                  model={{ ...m, _id: modelId }}
                  selected={selectedModels.has(modelId)}
                  onToggle={() => toggleModel(modelId, m.name || m.id)}
                />
              );
            })
          )}
        </div>
      </div>
    );
  }

  const displayModels = modelQuery ? modelResults : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Models</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Choose which Fabric semantic models to include from {selectedWorkspace.name}. Leave all unchecked to include everything in this workspace.
        </p>
      </div>

      {/* Search */}
      <SmartSearchBar
        value={modelQuery}
        onChange={setModelQuery}
        useRegex={modelQueryRegex}
        onToggleRegex={setModelQueryRegex}
        placeholder={`Search models in ${selectedWorkspace.name}`}
      />

      {selectedModels.size > 0 && (
        <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
          {selectedModels.size} model{selectedModels.size !== 1 ? 's' : ''} selected
          <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
            Clear
          </button>
        </div>
      )}

      {/* Flat search results */}
      {displayModels && (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {displayModels.map(m => (
            <ModelRow key={m._id} model={m} selected={selectedModels.has(m._id)} onToggle={() => toggleModel(m._id, m.name || m.id)} showWs />
          ))}
        </div>
      )}

      {/* Tree view when no search */}
      {!displayModels && (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
          {wsLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering workspaces…
            </div>
          ) : workspaces.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No workspaces found. Check your Fabric connector credentials in Settings.
            </div>
          ) : workspaces.map((ws) => {
              const wsid = ws.workspace_id || ws.id;
              const wsName = ws.display_name || ws.name || wsid;
              if (!wsid) return null;
              return (
            <WorkspaceRow
              key={wsid}
              ws={{ ...ws, id: wsid, name: wsName }}
              expanded={!!expandedWs[wsid]}
              models={wsModels[wsid]}
              selectedModels={selectedModels}
              onToggle={() => toggleWorkspace(wsid)}
              onModelToggle={(mid, modelName) => toggleModel(`${wsid}::${mid}`, modelName)}
            />
              );
          })}
        </div>
      )}
    </div>
  );
}

export function WorkspaceRow({ ws, expanded, models, selectedModels, onToggle, onModelToggle }) {
  return (
    <div>
      <div
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '9px 12px', cursor: 'pointer', userSelect: 'none',
          borderBottom: '1px solid var(--border-subtle)',
          background: expanded ? 'var(--bg-surface-raised)' : 'transparent',
        }}
        onMouseEnter={e => { if (!expanded) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
        onMouseLeave={e => { if (!expanded) e.currentTarget.style.background = 'transparent'; }}
      >
        {expanded ? <ChevronDown size={13} style={{ color: 'var(--text-tertiary)' }} /> : <ChevronRight size={13} style={{ color: 'var(--text-tertiary)' }} />}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{ws.name || ws.id}</span>
        {models && (
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', marginLeft: 4 }}>({models.length} models)</span>
        )}
      </div>
      {expanded && models && (
        <div className="custom-scrollbar" style={{ maxHeight: 250, overflowY: 'auto' }}>
          {models.map(m => (
            <ModelRow
              key={m.id}
              model={{ ...m, _id: `${ws.id}::${m.id}` }}
              selected={selectedModels.has(`${ws.id}::${m.id}`)}
              onToggle={() => onModelToggle(m.id, m.name || m.id)}
              indent
            />
          ))}
        </div>
      )}
      {expanded && !models && (
        <div style={{ padding: '8px 32px', fontSize: 12, color: 'var(--text-tertiary)' }}>
          <Loader2 size={12} style={{ animation: 'spin 1s linear infinite', display: 'inline', marginRight: 6 }} />
          Loading models…
        </div>
      )}
    </div>
  );
}

export function ModelRow({ model, selected, onToggle, indent, showWs }) {
  return (
    <div
      onClick={onToggle}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: `7px ${indent ? 32 : 12}px`,
        cursor: 'pointer', userSelect: 'none',
        background: selected ? 'var(--accent-blue)0a' : 'transparent',
        borderBottom: '1px solid var(--border-subtle)',
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
    >
      {selected
        ? <CheckSquare size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
        : <Square size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
      <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{model.name || model.id}</span>
      {showWs && <span style={{ fontSize: 10, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>{model.wsid}</span>}
    </div>
  );
}

/* ─── Step 4: Mapping Options ─── */
