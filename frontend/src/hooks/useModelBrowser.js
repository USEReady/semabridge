import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../utils/api';

/**
 * useModelBrowser
 * Owns Step 3 logic: loading workspace/snowflake models, toggling selections,
 * and loading PBIX files from tagged folders.
 */
export function useModelBrowser({
  step,
  sourceConnector,
  fabricWorkspaceId,
  selectedConnectionId,
  selectedWorkspace,
  liveFabricWorkspaces,
  selectedModels,
  setSelectedModels,
  setSelectedModelNameByKey,
  expandedWs,
  setExpandedWs,
  setWizardState,
  pbixSourceMode,
  selectedLocalFolderTag,
  setSelectedPbixFilePath,
  setRunWarning,
  editMode,
  initialData,
  isHydratedRef,
}) {
  const [workspaces,      setWorkspaces]      = useState([]);
  const [wsLoading,       setWsLoading]       = useState(false);
  const [wsModels,        setWsModels]        = useState({});
  const [databricksObjects, setDatabricksObjects] = useState([]);
  const [databricksLoading, setDatabricksLoading] = useState(false);
  const [pbixFiles,       setPbixFiles]       = useState([]);
  const [pbixFilesLoading, setPbixFilesLoading] = useState(false);
  const [pbixFilesError,  setPbixFilesError]  = useState('');

  const prevWorkspaceIdRef      = useRef(null);
  const prevSourceConnectorRef  = useRef(null);

  /* ── Ghost-purge: clear stale fabricWorkspaceId ── */
  useEffect(() => {
    if (!fabricWorkspaceId || liveFabricWorkspaces.length === 0) return;
    if (!liveFabricWorkspaces.some(ws => ws.id === fabricWorkspaceId)) {
      console.warn('[SemaBridge] Ghost workspace detected — force-clearing:', fabricWorkspaceId);
      setWizardState({ fabricWorkspaceId: '' });
      localStorage.removeItem('semabridge_workspace_id');
    }
  }, [fabricWorkspaceId, liveFabricWorkspaces, setWizardState]);

  /* ── Defensive auto-select first workspace if empty on Step 3 ── */
  useEffect(() => {
    if (step === 3 && sourceConnector === 'fabric' && !fabricWorkspaceId && !wsLoading) {
      if (liveFabricWorkspaces.length > 0) {
        setWizardState({ fabricWorkspaceId: liveFabricWorkspaces[0].id });
      }
    }
  }, [step, sourceConnector, fabricWorkspaceId, wsLoading, liveFabricWorkspaces, setWizardState]);

  /* ── Clear model selections when workspace or source changes ── */
  useEffect(() => {
    const wsChanged  = fabricWorkspaceId !== prevWorkspaceIdRef.current;
    const scChanged  = sourceConnector   !== prevSourceConnectorRef.current;

    if (!isHydratedRef.current || (!wsChanged && !scChanged)) {
      if (fabricWorkspaceId) prevWorkspaceIdRef.current = fabricWorkspaceId;
      if (sourceConnector)   prevSourceConnectorRef.current = sourceConnector;
      return;
    }
    if (editMode && wsChanged && fabricWorkspaceId === initialData?.workspace_id) {
      prevWorkspaceIdRef.current = fabricWorkspaceId;
      return;
    }

    console.log('[SemaBridge] Source/Workspace changed, clearing selections');
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
    setExpandedWs({});
    setWsModels({});

    prevWorkspaceIdRef.current     = fabricWorkspaceId;
    prevSourceConnectorRef.current = sourceConnector;
  }, [fabricWorkspaceId, sourceConnector, editMode, initialData?.workspace_id]); // eslint-disable-line

  /* ── Load models for the selected workspace when entering Step 3 ── */
  useEffect(() => {
    if (step !== 3) return;

    if (sourceConnector === 'fabric') {
      if (!fabricWorkspaceId) { setWorkspaces([]); return; }

      const workspace = selectedWorkspace || { id: fabricWorkspaceId, name: fabricWorkspaceId };
      setWorkspaces([workspace]);
      setExpandedWs(prev => ({ ...prev, [fabricWorkspaceId]: true }));
      setWsLoading(true);

      api.discoverFabricModels(fabricWorkspaceId, selectedConnectionId)
        .then(data => {
          if (!Array.isArray(data) || data.length === 0) {
            setRunWarning('No semantic models found for this workspace.');
          } else {
            setRunWarning('');
          }
          const models = data ?? [];
          setWsModels({ [fabricWorkspaceId]: models });

          if (models.length > 0 && selectedModels.size > 0) {
            setSelectedModelNameByKey(prev => {
              const next = { ...prev };
              let changed = false;
              selectedModels.forEach(key => {
                const id = key.includes('::') ? key.split('::')[1] : key;
                const match = models.find(m => m.id === id);
                if (match?.name && next[key] !== match.name) { next[key] = match.name; changed = true; }
              });
              return changed ? next : prev;
            });
          }
          setWsLoading(false);
        })
        .catch(err => {
          setRunWarning('Failed to load semantic models: ' + (err?.message || 'Unknown error'));
          setWsModels({ [fabricWorkspaceId]: [] });
          setWsLoading(false);
        });
      return;
    }

    if (sourceConnector === 'snowflake') {
      const rootId = 'snowflake';
      setWorkspaces([{ id: rootId, name: 'Snowflake' }]);
      setExpandedWs(prev => ({ ...prev, [rootId]: true }));
      setWsLoading(true);
      api.discoverSnowflakeModels()
        .then(data => {
          const normalized = (data ?? []).map((m, idx) => ({
            ...m,
            id:   m?.id   || m?.name || m?.displayName || `snowflake_model_${idx + 1}`,
            name: m?.name || m?.displayName || m?.id || `model_${idx + 1}`,
          }));
          setWsModels({ [rootId]: normalized });
        })
        .catch(() => setWsModels({ snowflake: [] }))
        .finally(() => setWsLoading(false));
      return;
    }

    setWorkspaces([]);
  }, [step, sourceConnector, fabricWorkspaceId, selectedConnectionId, selectedWorkspace]); // eslint-disable-line

  /* ── PBIX file discovery for TAG mode ── */
  useEffect(() => {
    if (pbixSourceMode !== 'TAG' || sourceConnector !== 'pbix' || !selectedLocalFolderTag || step !== 3) {
      setPbixFiles([]);
      setPbixFilesError('');
      setSelectedPbixFilePath('');
      return;
    }

    let active = true;
    setPbixFilesLoading(true);
    setPbixFilesError('');

    api.getLocalFolderFiles(selectedLocalFolderTag)
      .then(data => {
        if (!active) return;
        const files = Array.isArray(data?.files) ? data.files : [];
        setPbixFiles(files);
        if (!files.length) { setSelectedPbixFilePath(''); return; }
        setSelectedPbixFilePath(cur => (cur && files.some(f => f.path === cur)) ? cur : (files[0]?.path || ''));
      })
      .catch(err => {
        if (!active) return;
        setPbixFiles([]);
        setSelectedPbixFilePath('');
        setPbixFilesError(err?.message || 'Failed to discover PBIX files.');
      })
      .finally(() => { if (active) setPbixFilesLoading(false); });

    return () => { active = false; };
  }, [pbixSourceMode, selectedLocalFolderTag, sourceConnector, step]); // eslint-disable-line

  /* ── Workspace lazy-load ── */
  const loadWsModels = useCallback(async (wsid) => {
    if (wsModels[wsid]) return;
    try {
      const models = await api.discoverFabricModels(wsid, selectedConnectionId);
      setWsModels(prev => ({ ...prev, [wsid]: models ?? [] }));
    } catch {
      setWsModels(prev => ({ ...prev, [wsid]: [] }));
    }
  }, [wsModels, selectedConnectionId]);

  const toggleWorkspace = (wsid) => {
    const next = { ...expandedWs, [wsid]: !expandedWs[wsid] };
    setExpandedWs(next);
    if (next[wsid]) loadWsModels(wsid);
  };

  const toggleModel = (modelKey, modelName = '') => {
    setSelectedModels(prev => {
      const s = new Set(prev);
      s.has(modelKey) ? s.delete(modelKey) : s.add(modelKey);
      return s;
    });
    setSelectedModelNameByKey(prev => {
      const next = { ...prev };
      if (modelKey in next) { delete next[modelKey]; }
      else if (modelName) { next[modelKey] = modelName; }
      return next;
    });
  };

  const clearSelectedModels = () => {
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
  };

  return {
    workspaces,
    wsLoading,
    wsModels,
    databricksObjects,
    databricksLoading,
    pbixFiles,
    pbixFilesLoading,
    pbixFilesError,
    toggleWorkspace,
    toggleModel,
    clearSelectedModels,
  };
}
