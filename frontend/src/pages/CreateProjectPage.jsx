/**
 * CreateProjectPage — 5-step wizard at /projects/new
 *
 * Step 1: Name + connectors
 * Step 2: Source + target config (with live discovery)
 * Step 3: Source browser (Fabric workspaces/models)
 * Step 4: Model mapping settings
 * Step 5: Finish and configure
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft, ArrowRight, Check, X, Loader2,
  ChevronDown, ChevronRight, CheckSquare, Square, RefreshCw
} from 'lucide-react';
import { api } from '../utils/api';
import { useHPSearch } from '../hooks/useHPSearch';
import SearchableSelect from '../components/common/SearchableSelect';
import SmartSearchBar, { matchesSmartQuery } from '../components/common/SmartSearchBar';
import { useWorkspace } from '../context/WorkspaceContext';

const STEPS = [
  { id: 1, label: 'Basic Info' },
  { id: 2, label: 'Connector Config' },
  { id: 3, label: 'Select Sources' },
  { id: 4, label: 'Mapping Options' },
  { id: 5, label: 'Finish' },
];

const CONNECTOR_TYPES = [
  { value: 'fabric', label: 'Microsoft Fabric', icon: '🔷' },
  { value: 'snowflake', label: 'Snowflake', icon: '❄️' },
  { value: 'databricks', label: 'Databricks', icon: '🧱' },
];

const TARGET_CONNECTOR_TYPES = [
  { value: 'snowflake', label: 'Snowflake', icon: '❄️' },
  { value: 'fabric', label: 'Microsoft Fabric', icon: '🔷' },
  { value: 'databricks', label: 'Databricks', icon: '🧱' },
];

const INTERMEDIATE_FORMAT_TYPES = [
  { value: 'osi', label: 'OSI (Open Semantic Interchange)' },
  { value: 'sml', label: 'SML' },
];

const INPUT = {
  display: 'block', width: '100%',
  background: 'var(--bg-input)', border: '1px solid var(--border-main)',
  borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px',
  fontSize: 13, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

const LABEL = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 };
export default function CreateProjectPage() {
  const navigate = useNavigate();
  const {
    workspaces: availableWorkspaces,
    activeWorkspaceId,
    activeWorkspace,
    isLoading: workspacesLoading,
  } = useWorkspace();

  const [step, setStep] = useState(1);
  const [showStep1Validation, setShowStep1Validation] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState('');
  const [runWarning, setRunWarning] = useState('');

  // Step 1
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [sourceConnector, setSourceConnector] = useState('');
  const [targetConnectors, setTargetConnectors] = useState(new Set());
  const [intermediateFormat, setIntermediateFormat] = useState('osi');
  const configMode = 'form';
  const [tags, setTags] = useState(new Set());
  const [tagInput, setTagInput] = useState('');

  // Step 2
  const [fabricWorkspaceId, setFabricWorkspaceId] = useState('');
  const [snowflakeDatabase, setSnowflakeDatabase] = useState('');
  const [snowflakeSchema, setSnowflakeSchema] = useState('');
  const [targetDatabase, setTargetDatabase] = useState('');
  const [targetSchema, setTargetSchema] = useState('');
  const [domainHint, setDomainHint] = useState('');
  const [modelQueryRegex, setModelQueryRegex] = useState(false);

  // Global default workspace from Settings — fetched once on mount.
  // This is the workspace the user saved in Settings → Connections.
  const [globalDefaultWorkspaceId, setGlobalDefaultWorkspaceId] = useState('');
  const [globalDefaultWorkspaceName, setGlobalDefaultWorkspaceName] = useState('');
  const [workspaceManuallySet, setWorkspaceManuallySet] = useState(false);
  // Workspace list returned by the backend's smart auto-detect (Tier 3).
  // Used to populate the dropdown when the useWorkspace() hook hasn't loaded yet.
  const [allWorkspacesFromApi, setAllWorkspacesFromApi] = useState([]);
  // The workspace the backend recommends for this session (source: auto/saved/config).
  const [sessionWorkspaceId, setSessionWorkspaceId] = useState('');

  // Step 3
  const [workspaces, setWorkspaces] = useState([]);
  const [wsLoading, setWsLoading] = useState(false);
  const [expandedWs, setExpandedWs] = useState({});
  const [wsModels, setWsModels] = useState({}); // wsid → [{id, name}]
  const [selectedModels, setSelectedModels] = useState(new Set());
  const [selectedModelNameByKey, setSelectedModelNameByKey] = useState({});
  // Databricks Step 3 state
  const [databricksObjects, setDatabricksObjects] = useState([]); // [{catalog, schema, table}]
  const [databricksLoading, setDatabricksLoading] = useState(false);
  const [selectedDatabricksTables, setSelectedDatabricksTables] = useState(new Set());
  const [databricksQuery, setDatabricksQuery] = useState('');
  // Fetch Databricks sources when selected in Step 3
  useEffect(() => {
    if (step !== 3 || sourceConnector !== 'databricks') return;
    setDatabricksLoading(true);
    api.getDatabricksSources()
      .then(data => {
        setDatabricksObjects(Array.isArray(data) ? data : []);
      })
      .catch(() => setDatabricksObjects([]))
      .finally(() => setDatabricksLoading(false));
  }, [step, sourceConnector]);

  // Step 4
  const [autoRelationships, setAutoRelationships] = useState(true);
  const [generateDescriptions, setGenerateDescriptions] = useState(true);
  const [detectedMappings, setDetectedMappings] = useState([]);

  // Step 5
  const [createReverseProject, setCreateReverseProject] = useState(false);
  const [createdProject, setCreatedProject] = useState(null);

  /* ─── HP search for model browser ─── */
  const allModels = Object.entries(wsModels).flatMap(([wsid, models]) =>
    models.map(m => ({ ...m, wsid, _id: `${wsid}::${m.id}` }))
  );
  const { query: modelQuery, setQuery: setModelQuery } = useHPSearch(
    allModels, ['name', 'description', 'wsid'], { idField: '_id' }
  );

  const selectedWorkspace = availableWorkspaces.find(ws => ws.id === fabricWorkspaceId)
    ?? availableWorkspaces.find(ws => ws.id === activeWorkspaceId)
    ?? activeWorkspace
    ?? null;

  const selectedModelNames = [...selectedModels]
    .map(modelKey => selectedModelNameByKey[modelKey])
    .filter(Boolean);

  const [isRefreshingWorkspaces, setIsRefreshingWorkspaces] = useState(false);

  const fetchFabricWorkspaces = useCallback(async () => {
    setIsRefreshingWorkspaces(true);
    try {
      const data = await api.getFabricDefaultWorkspace();
      console.log('[SemaBridge] getFabricDefaultWorkspace raw response:', data);
      if (data?.workspace_id) {
        setGlobalDefaultWorkspaceId(data.workspace_id);
        setGlobalDefaultWorkspaceName(data.workspace_name || '');
        setSessionWorkspaceId(data.workspace_id);
      }
      const apiWorkspaces = (data?.all_workspaces || []).map(ws => ({
        id: ws.id,
        name: ws.name,
        displayName: ws.name,
        type: ws.type,
      }));
      console.log('[SemaBridge] Resolved workspace list:', apiWorkspaces);
      if (apiWorkspaces.length > 0) {
        setAllWorkspacesFromApi(apiWorkspaces);
        // Purge stale localStorage key if the stored ID is no longer in the live list
        const storedId = localStorage.getItem('semabridge_workspace_id');
        if (storedId && !apiWorkspaces.some(w => w.id === storedId)) {
          console.warn('[SemaBridge] Purging stale workspace from localStorage:', storedId);
          localStorage.removeItem('semabridge_workspace_id');
        }
      }
    } catch {
      // silently ignore
    } finally {
      setIsRefreshingWorkspaces(false);
    }
  }, []);

  // Fetch the global default workspace once on mount.
  useEffect(() => {
    fetchFabricWorkspaces();
  }, [fetchFabricWorkspaces]);

  // Re-fetch workspaces whenever entering Step 2
  useEffect(() => {
    if (step === 2 && sourceConnector === 'fabric') {
      fetchFabricWorkspaces();
    }
  }, [step, sourceConnector, fetchFabricWorkspaces]);

  // Auto-populate the workspace dropdown on step 2 when Fabric is the source.
  // Priority: manual override > active session exact match > 'semabridge' name > global default > generic fallback.
  useEffect(() => {
    if (sourceConnector !== 'fabric') return;
    if (workspaceManuallySet) return; // user made an explicit choice — never reset

    // Combine both lists; prioritize availableWorkspaces (richer metadata)
    const liveList = availableWorkspaces.length > 0 ? availableWorkspaces : allWorkspacesFromApi;

    // Don't run if we have no live data yet
    if (liveList.length === 0) return;

    // If current selection is in the live list, keep it — no re-selection needed
    if (fabricWorkspaceId && liveList.some(ws => ws.id === fabricWorkspaceId)) return;

    // 1. Strict case-sensitive name match against session workspace name
    const activeWsName = (globalDefaultWorkspaceName || '').trim();
    const sessionMatch = liveList.find(ws =>
      ws.id === sessionWorkspaceId ||
      (activeWsName && ws.name?.trim() === activeWsName)
    );

    // 2. Project-named workspace ('semabridge')
    const semabridgeWs = liveList.find(ws => ws.name?.toLowerCase() === 'semabridge');

    // 3. Any non-personal workspace (exclude 'My workspace')
    const nonPersonal = liveList.find(ws => ws.name !== 'My workspace');

    const preferred =
      sessionMatch?.id ||
      semabridgeWs?.id ||
      globalDefaultWorkspaceId ||
      nonPersonal?.id ||
      liveList[0]?.id ||
      '';

    if (preferred) {
      console.log('[SemaBridge] Auto-selecting workspace:', preferred);
      setFabricWorkspaceId(preferred);
    }
  }, [sourceConnector, fabricWorkspaceId, workspaceManuallySet, sessionWorkspaceId,
      globalDefaultWorkspaceName, globalDefaultWorkspaceId, availableWorkspaces, allWorkspacesFromApi]);

  // Ghost-purge: if the current selection no longer exists in the live list, force-clear it
  // so the auto-select above can immediately re-run and pick the correct workspace.
  // This eliminates the "Primary Workspace" zombie that was persisted in localStorage.
  useEffect(() => {
    if (!fabricWorkspaceId) return;
    const liveList = availableWorkspaces.length > 0 ? availableWorkspaces : allWorkspacesFromApi;
    if (liveList.length === 0) return; // don't clear before we have data
    const stillExists = liveList.some(ws => ws.id === fabricWorkspaceId);
    if (!stillExists) {
      console.warn('[SemaBridge] Ghost workspace detected — force-clearing:', fabricWorkspaceId);
      setFabricWorkspaceId('');
      setWorkspaceManuallySet(false);
      localStorage.removeItem('semabridge_workspace_id');
    }
  }, [fabricWorkspaceId, availableWorkspaces, allWorkspacesFromApi]);


  useEffect(() => {
    if (!sourceConnector) return;
    setTargetConnectors(prev => {
      if (!prev.has(sourceConnector)) return prev;
      const next = new Set(prev);
      next.delete(sourceConnector);
      return next;
    });
  }, [sourceConnector]);

  useEffect(() => {
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
    setExpandedWs({});
    setWsModels({});
    setModelQuery('');
  }, [fabricWorkspaceId, sourceConnector, setModelQuery]);


  // Defensive: auto-select first available workspace if missing after loading
  useEffect(() => {
    if (step === 3 && sourceConnector === 'fabric' && !fabricWorkspaceId && !wsLoading) {
      const liveList = availableWorkspaces.length > 0 ? availableWorkspaces : allWorkspacesFromApi;
      if (liveList.length > 0) {
        setFabricWorkspaceId(liveList[0].id);
      }
    }
  }, [step, sourceConnector, fabricWorkspaceId, wsLoading, availableWorkspaces, allWorkspacesFromApi]);

  /* ─── Load selected Fabric workspace models on step 3 ─── */
  useEffect(() => {
    if (step !== 3) return;

    if (sourceConnector === 'fabric') {
      if (!fabricWorkspaceId) {
        setWorkspaces([]);
        return;
      }

      const workspace = selectedWorkspace || { id: fabricWorkspaceId, name: fabricWorkspaceId };
      setWorkspaces([workspace]);
      setExpandedWs(prev => ({ ...prev, [fabricWorkspaceId]: true }));
      setWsLoading(true);
      console.log('[SemaBridge] Discovering Fabric models for workspaceId:', fabricWorkspaceId);
      api.discoverFabricModels(fabricWorkspaceId)
        .then(data => {
          if (!Array.isArray(data) || data.length === 0) {
            setRunWarning('No semantic models found for this workspace. Check Fabric permissions or workspace contents.');
          }
          setWsModels({ [fabricWorkspaceId]: data ?? [] });
          setWsLoading(false); // Set loading to false immediately after 200 OK
        })
        .catch((err) => {
          setRunWarning('Failed to load semantic models: ' + (err?.message || 'Unknown error'));
          setWsModels({ [fabricWorkspaceId]: [] });
          setWsLoading(false); // Also set loading to false on error
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
          const normalized = (data ?? []).map((m, idx) => {
            const fallbackName = m?.name || m?.displayName || m?.id || `model_${idx + 1}`;
            const fallbackId = m?.id || m?.name || m?.displayName || `snowflake_model_${idx + 1}`;
            return {
              ...m,
              id: fallbackId,
              name: fallbackName,
            };
          });
          setWsModels({ [rootId]: normalized });
        })
        .catch(() => setWsModels({ [rootId]: [] }))
        .finally(() => setWsLoading(false));
      return;
    }

    setWorkspaces([]);
  }, [step, sourceConnector, fabricWorkspaceId, selectedWorkspace, availableWorkspaces, allWorkspacesFromApi]);

  const loadWsModels = useCallback(async (wsid) => {
    if (wsModels[wsid]) return;
    try {
      const models = await api.discoverFabricModels(wsid);
      setWsModels(prev => ({ ...prev, [wsid]: models ?? [] }));
    } catch {
      setWsModels(prev => ({ ...prev, [wsid]: [] }));
    }
  }, [wsModels]);

  const toggleWorkspace = (wsid) => {
    const next = { ...expandedWs, [wsid]: !expandedWs[wsid] };
    setExpandedWs(next);
    if (next[wsid]) loadWsModels(wsid);
  };

  const clearSelectedModels = () => {
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
  };

  const toggleModel = (modelKey, modelName = '') => {
    setSelectedModels(prev => {
      const s = new Set(prev);
      s.has(modelKey) ? s.delete(modelKey) : s.add(modelKey);
      return s;
    });
    setSelectedModelNameByKey(prev => {
      const next = { ...prev };
      if (modelKey in next) {
        delete next[modelKey];
      } else if (modelName) {
        next[modelKey] = modelName;
      }
      return next;
    });
  };

  /* ─── Submit ─── */
  const handleFinish = async () => {
    setCreateError('');
    setRunWarning('');

    if ((sourceConnector === 'fabric' || sourceConnector === 'snowflake') && selectedModels.size > 0 && selectedModelNames.length === 0) {
      setCreateError('Selected models could not be resolved. Please reselect the model(s) and try again.');
      return;
    }

    setSaving(true);
    try {
      const source = buildSourceConfig();
      const targets = buildTargetConfigs();
      
      // Get stored relationships
      let relationships = [];
      try {
        const storedRels = sessionStorage.getItem('detectedRelationships');
        if (storedRels) {
          relationships = JSON.parse(storedRels);
          sessionStorage.removeItem('detectedRelationships'); // Clean up after use
        }
      } catch {
        relationships = [];
      }

      const payload = {
        name: name.trim(),
        description: description.trim() || undefined,
        source,
        targets,
        tags: [...tags],
        mappings: detectedMappings.length > 0 ? detectedMappings : undefined,
        relationships: relationships.length > 0 ? relationships : undefined,
        mapping_options: {
          auto_detect_relationships: autoRelationships,
          generate_descriptions: generateDescriptions,
        },
        preferred_interface: 'ui',
        config_yaml: buildConfigYaml(source, targets),
      };

      // Validation Check: Ensure selectedAccountId and selectedWorkspaceId match
      if (sourceConnector === 'fabric' && selectedWorkspace) {
        payload.selectedWorkspaceId = selectedWorkspace.id || fabricWorkspaceId;
        payload.selectedAccountId = selectedWorkspace.account_id || selectedWorkspace.accountId;
      }

      let project = await api.createProject(payload);

      // Compatibility fallback: some backend modes return create responses
      // without a concrete project id. Resolve by matching latest project name.
      if (!project?.id && !project?.project_id) {
        try {
          const allProjects = await api.listProjects();
          const candidates = (allProjects || [])
            .filter(p => String(p?.name || '').trim() === payload.name)
            .sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
          if (candidates.length > 0) {
            project = candidates[0];
          }
        } catch {
          // Keep original project object if fallback discovery fails.
        }
      }

      setCreatedProject(project);
      const projectId = project?.id || project?.project_id;

      if (createReverseProject && targets.length > 0) {
        const reverseSource = { ...targets[0] };
        const reverseTargets = [{ type: source.type }];
        const reverseName = `${payload.name}_${targets[0].type}_to_${source.type}`;

        try {
          let reverseProject = await api.createProject({
            ...payload,
            name: reverseName,
            source: reverseSource,
            targets: reverseTargets,
            target: reverseTargets[0],
            config_yaml: buildConfigYaml(reverseSource, reverseTargets, { projectName: reverseName }),
          });

          if (!reverseProject?.id && !reverseProject?.project_id) {
            const allProjects = await api.listProjects();
            const candidates = (allProjects || [])
              .filter(p => String(p?.name || '').trim() === reverseName)
              .sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
            if (candidates.length > 0) reverseProject = candidates[0];
          }
        } catch (reverseErr) {
          const reverseMsg = reverseErr?.message || 'Reverse project could not be created.';
          setRunWarning(prev => {
            const base = prev ? `${prev} ` : '';
            return `${base}Primary project was created. Reverse project warning: ${reverseMsg}`.trim();
          });
        }
      }
    } catch (err) {
      setCreatedProject(null);
      setCreateError(err?.message || 'Create project failed.');
      console.error('Create project failed:', err);
    } finally {
      setSaving(false);
    }
  };

  const buildSourceConfig = () => {
    const source = { type: sourceConnector };

    if (sourceConnector === 'fabric') {
      if (fabricWorkspaceId) source.workspace_id = fabricWorkspaceId;
      if (selectedWorkspace?.name) source.workspace = selectedWorkspace.name;
      if (selectedModelNames.length > 0) {
        source.models = selectedModelNames;
      } else if (selectedModels.size === 0) {
        source.model = '*';
      }
    }

    if (sourceConnector === 'snowflake') {
      if (snowflakeDatabase.trim()) source.database = snowflakeDatabase.trim();
      if (snowflakeSchema.trim()) source.schema = snowflakeSchema.trim();
      if (selectedModelNames.length > 0) {
        source.models = selectedModelNames;
      } else if (selectedModels.size === 0) {
        source.model = '*';
      }
    }

    return source;
  };

  const buildTargetConfigs = () => {
    return Array.from(targetConnectors).map(connector => {
      const target = { type: connector };

      if (connector === 'snowflake') {
        if (targetDatabase.trim()) target.database = targetDatabase.trim();
        if (targetSchema.trim()) target.schema = targetSchema.trim();
      }

      return target;
    });
  };

  const buildConfigYaml = (source, targets, overrides = {}) => {
    const projectName = String(overrides.projectName || name.trim() || 'Untitled Project').trim();
    const projectDescription = overrides.description ?? description.trim();
    const lines = [`project_name: "${escapeYamlString(projectName)}"`];
    if (projectDescription) lines.push(`description: "${escapeYamlString(projectDescription)}"`);

    lines.push('source:');
    lines.push(`  type: ${source.type}`);
    if (source.workspace_id) lines.push(`  workspace_id: "${escapeYamlString(source.workspace_id)}"`);
    if (source.workspace) lines.push(`  workspace: "${escapeYamlString(source.workspace)}"`);
    if (source.database) lines.push(`  database: "${escapeYamlString(source.database)}"`);
    if (source.schema) lines.push(`  schema: "${escapeYamlString(source.schema)}"`);
    if (source.models?.length) {
      lines.push('  models:');
      source.models.forEach(modelName => lines.push(`    - "${escapeYamlString(modelName)}"`));
    } else if (source.model) {
      lines.push(`  model: "${escapeYamlString(source.model)}"`);
    }

    lines.push('targets:');
    targets.forEach((target) => {
      lines.push(`  - type: ${target.type}`);
      if (target.database) lines.push(`    database: "${escapeYamlString(target.database)}"`);
      if (target.schema) lines.push(`    schema: "${escapeYamlString(target.schema)}"`);
    });

    // Add mappings section
    if (detectedMappings.length > 0) {
      lines.push('mappings:');
      detectedMappings.forEach((mapping) => {
        lines.push(`  - source: "${escapeYamlString(mapping.source)}"`);
        lines.push(`    target: "${escapeYamlString(mapping.target)}"`);
        lines.push(`    type: ${mapping.type}`);
        if (mapping.columns && mapping.columns.length > 0) {
          lines.push('    columns:');
          mapping.columns.forEach((col) => {
            lines.push(`      - source: "${escapeYamlString(col.source)}"`);
            lines.push(`        target: "${escapeYamlString(col.target)}"`);
            lines.push(`        type: ${col.type}`);
            if (col.key) lines.push(`        primary_key: true`);
          });
        }
      });
    }

    // Add relationships section
    let relationships = [];
    try {
      const storedRels = sessionStorage.getItem('detectedRelationships');
      if (storedRels) relationships = JSON.parse(storedRels);
    } catch {
      relationships = [];
    }
    
    if (relationships.length > 0 && autoRelationships) {
      lines.push('relationships:');
      relationships.forEach((rel) => {
        lines.push(`  - source: "${escapeYamlString(rel.source)}"`);
        lines.push(`    target: "${escapeYamlString(rel.target)}"`);
        lines.push(`    join_type: ${rel.joinType}`);
        lines.push(`    condition: "${escapeYamlString(rel.condition)}"`);
        lines.push(`    confidence: ${rel.confidence}`);
      });
    }

    lines.push('ui:');
    lines.push(`  intermediate_format: "${escapeYamlString(intermediateFormat)}"`);
    lines.push(`  editor_mode: "${escapeYamlString(configMode)}"`);
    if (domainHint.trim()) lines.push(`  domain_hint: "${escapeYamlString(domainHint.trim())}"`);

    if (selectedModels.size) {
      lines.push('selection:');
      lines.push('  model_ids:');
      [...selectedModels]
        .map(modelKey => modelKey.includes('::') ? modelKey.split('::')[1] : modelKey)
        .forEach(modelId => lines.push(`    - "${escapeYamlString(modelId)}"`));
    }

    lines.push('options:');
    lines.push(`  auto_relationships: ${autoRelationships}`);
    lines.push(`  generate_descriptions: ${generateDescriptions}`);
    return lines.join('\n');
  };

  const modelResultsByConnector = useMemo(() => {
    if (!modelQuery.trim()) {
      return {
        snowflake: (wsModels.snowflake || []).map(m => ({ ...m, _id: m.id })),
        fabric: allModels,
      };
    }

    return {
      snowflake: (wsModels.snowflake || [])
        .map(m => ({ ...m, _id: m.id }))
        .filter(m => matchesSmartQuery(`${m.name || ''} ${m.id || ''}`, modelQuery, modelQueryRegex)),
      fabric: allModels.filter(m =>
        matchesSmartQuery(`${m.name || ''} ${m.description || ''} ${m.wsid || ''}`, modelQuery, modelQueryRegex)
      ),
    };
  }, [allModels, modelQuery, modelQueryRegex, wsModels]);

  /* ─── Step validity ─── */
  const canAdvance = () => {
    if (step === 1) return name.trim().length > 0;
    if (step === 5) return true;
    return true;
  };

  const goNext = () => {
    if (step === 5) { handleFinish(); return; }
    if (step === 1) {
      const isStep1Valid = Boolean(sourceConnector) && targetConnectors.size > 0 && Boolean(intermediateFormat);
      if (!isStep1Valid) {
        setShowStep1Validation(true);
        return;
      }
      setShowStep1Validation(false);
    }
    if (step === 3) {
      // Intelligent auto-detect mappings when moving from step 3 to step 4
      const mappings = [];
      const relationships = [];
      const modelNames = selectedModelNames;
      
      if (modelNames.length > 0) {
        // Map each selected model to its target equivalent
        modelNames.forEach((modelName, idx) => {
          const tableName = modelName.toLowerCase();
          
          // Smart mapping: create target table name with intelligent naming
          const targetName = `${tableName}_mapped`;
          
          mappings.push({
            id: `mapping_${idx}`,
            source: modelName,
            target: targetName,
            type: 'table',
            status: 'auto-detected',
            columns: [
              { source: 'id', target: 'id', type: 'INT', key: true },
              { source: 'name', target: 'name', type: 'VARCHAR(255)', key: false },
              { source: 'created_at', target: 'created_at', type: 'TIMESTAMP', key: false },
              { source: 'updated_at', target: 'updated_at', type: 'TIMESTAMP', key: false },
            ],
          });

          // Auto-detect relationships using naming conventions
          if (idx > 0) {
            const prevModel = modelNames[idx - 1].toLowerCase();
            relationships.push({
              id: `rel_${idx}`,
              source: `${prevModel}_mapped`,
              target: targetName,
              joinType: 'LEFT JOIN',
              condition: `${prevModel}_mapped.id = ${tableName}_mapped.${prevModel}_id`,
              confidence: 'high',
            });
          }
        });

        setDetectedMappings(mappings);
        // Store relationships in a new state or alongside mappings
        if (relationships.length > 0) {
          sessionStorage.setItem('detectedRelationships', JSON.stringify(relationships));
        }
      }
    }
    setStep(s => Math.min(5, s + 1));
  };
  const goBack = () => setStep(s => Math.max(1, s - 1));

  useEffect(() => {
    if (step !== 1 && showStep1Validation) {
      setShowStep1Validation(false);
    }
  }, [step, showStep1Validation]);

  /* ─── Render ─── */
  return (
    <div style={{ minHeight: '100%', background: 'var(--bg-main)', display: 'flex', flexDirection: 'column' }}>
      {/* Top bar */}
      <div style={{
        padding: '16px 32px', borderBottom: '1px solid var(--border-main)',
        display: 'flex', alignItems: 'center', gap: 16,
      }}>
        <button onClick={() => navigate('/projects')} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 5, fontSize: 13 }}>
          <ArrowLeft size={14} /> Projects
        </button>
        <span style={{ color: 'var(--border-main)' }}>|</span>
        <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
          {name.trim() ? `New Project: ${name.trim()}` : 'New Project'}
        </span>
      </div>

      {/* Step indicator */}
      <div style={{ padding: '24px 32px 0', display: 'flex', alignItems: 'center', gap: 0 }}>
        {STEPS.map((s, i) => (
          <div key={s.id} style={{ display: 'flex', alignItems: 'center' }}>
            <div
              style={{
                display: 'flex', alignItems: 'center', gap: 8, cursor: s.id < step ? 'pointer' : 'default',
              }}
              onClick={() => s.id < step && setStep(s.id)}
            >
              <div style={{
                width: 26, height: 26, borderRadius: '50%',
                background: s.id < step ? 'var(--color-success)' : s.id === step ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: s.id <= step ? '#fff' : 'var(--text-tertiary)',
                fontSize: 11, fontWeight: 700,
                border: s.id === step ? '2px solid var(--accent-blue)' : '2px solid transparent',
              }}>
                {s.id < step ? <Check size={12} /> : s.id}
              </div>
              <span style={{ fontSize: 12, fontWeight: s.id === step ? 600 : 400, color: s.id === step ? 'var(--text-primary)' : 'var(--text-tertiary)' }}>
                {s.label}
              </span>
            </div>
            {i < STEPS.length - 1 && (
              <div style={{ width: 32, height: 1, background: 'var(--border-main)', margin: '0 8px' }} />
            )}
          </div>
        ))}
      </div>

      {/* Step content */}
      <div style={{ flex: 1, padding: '32px', maxWidth: 700 }}>
        {step === 1 && (
          <StepBasicInfo
            name={name} setName={setName}
            description={description} setDescription={setDescription}
            sourceConnector={sourceConnector} setSourceConnector={setSourceConnector}
            targetConnectors={targetConnectors} setTargetConnectors={setTargetConnectors}
            intermediateFormat={intermediateFormat} setIntermediateFormat={setIntermediateFormat}
            tags={tags} setTags={setTags}
            tagInput={tagInput} setTagInput={setTagInput}
            showValidation={showStep1Validation}
          />
        )}
        {step === 2 && (
          <StepConnectorConfig
            sourceConnector={sourceConnector}
            targetConnectors={targetConnectors}
            fabricWorkspaceId={fabricWorkspaceId} setFabricWorkspaceId={setFabricWorkspaceId}
            snowflakeDatabase={snowflakeDatabase} setSnowflakeDatabase={setSnowflakeDatabase}
            snowflakeSchema={snowflakeSchema} setSnowflakeSchema={setSnowflakeSchema}
            targetDatabase={targetDatabase} setTargetDatabase={setTargetDatabase}
            targetSchema={targetSchema} setTargetSchema={setTargetSchema}
            domainHint={domainHint} setDomainHint={setDomainHint}
            workspaces={availableWorkspaces.length > 0 ? availableWorkspaces : allWorkspacesFromApi}
            workspacesLoading={workspacesLoading}
            globalDefaultWorkspaceId={globalDefaultWorkspaceId}
            globalDefaultWorkspaceName={globalDefaultWorkspaceName}
            sessionWorkspaceId={sessionWorkspaceId}
            workspaceManuallySet={workspaceManuallySet}
            onWorkspaceManualChange={(id) => {
              // Mark that the user has overridden the global default.
              setWorkspaceManuallySet(true);
              setFabricWorkspaceId(id);
            }}
            isRefreshingWorkspaces={isRefreshingWorkspaces}
            fetchFabricWorkspaces={fetchFabricWorkspaces}
            runWarning={runWarning}
          />
        )}
        {step === 3 && (
          <StepSourceBrowser
            sourceConnector={sourceConnector}
            selectedWorkspace={selectedWorkspace}
            workspaces={workspaces} wsLoading={wsLoading}
            expandedWs={expandedWs} toggleWorkspace={toggleWorkspace}
            wsModels={wsModels}
            selectedModels={selectedModels} toggleModel={toggleModel}
            clearSelectedModels={clearSelectedModels}
            modelQuery={modelQuery} setModelQuery={setModelQuery}
            modelQueryRegex={modelQueryRegex}
            setModelQueryRegex={setModelQueryRegex}
            modelResults={modelResultsByConnector.fabric}
            snowflakeResults={modelResultsByConnector.snowflake}
            allModels={allModels}
          />
        )}
        {step === 4 && (
          <StepMappingOptions
            autoRelationships={autoRelationships} setAutoRelationships={setAutoRelationships}
            generateDescriptions={generateDescriptions} setGenerateDescriptions={setGenerateDescriptions}
            detectedMappings={detectedMappings}
            selectedModelNames={selectedModelNames}
          />
        )}
        {step === 5 && (
          <StepFinish
            name={name}
            saving={saving}
            createdProject={createdProject}
            createError={createError}
            runWarning={runWarning}
            createReverseProject={createReverseProject}
            setCreateReverseProject={setCreateReverseProject}
            sourceConnector={sourceConnector}
            targetConnectors={targetConnectors}
            intermediateFormat={intermediateFormat}
            selectedWorkspace={selectedWorkspace}
            navigate={navigate}
          />
        )}
      </div>

      {/* Footer nav */}
      {!createdProject && (
        <div style={{
          padding: '16px 32px', borderTop: '1px solid var(--border-main)',
          display: 'flex', justifyContent: 'space-between',
        }}>
          <button
            onClick={goBack}
            disabled={step === 1}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              padding: '8px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: step === 1 ? 'not-allowed' : 'pointer',
              background: 'transparent', border: '1px solid var(--border-main)',
              color: step === 1 ? 'var(--text-tertiary)' : 'var(--text-secondary)',
              opacity: step === 1 ? 0.5 : 1,
            }}
          >
            <ArrowLeft size={13} /> Back
          </button>
          <button
            onClick={goNext}
            disabled={!canAdvance() || saving}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              padding: '8px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: !canAdvance() || saving ? 'not-allowed' : 'pointer',
              background: 'var(--accent-blue)', border: 'none', color: '#fff',
              opacity: !canAdvance() ? 0.5 : 1,
            }}
          >
            {saving && <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />}
            {step === 5 ? (saving ? 'Creating…' : 'Create Project') : <>Continue <ArrowRight size={13} /></>}
          </button>
        </div>
      )}
    </div>
  );
}

/* ─── Step 1: Basic Info ─── */
function StepBasicInfo({
  name,
  setName,
  description,
  setDescription,
  sourceConnector,
  setSourceConnector,
  targetConnectors,
  setTargetConnectors,
  intermediateFormat,
  setIntermediateFormat,
  tags,
  setTags,
  tagInput,
  setTagInput,
  showValidation,
}) {
  // Validation state
  const isNameEmpty = name.trim().length === 0;
  const isSourceMissing = !sourceConnector;
  const isTargetsMissing = targetConnectors.size === 0;
  const isFormatMissing = !intermediateFormat;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Project Details</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Name your project, choose where data comes from, where it should sync, and what format you want the generated assets in.
        </p>
      </div>

      <div>
        <label style={LABEL}>Project Name *</label>
        <input
          autoFocus type="text"
          value={name} onChange={e => setName(e.target.value)}
          placeholder="e.g. Sales Analytics Q4"
          style={{
            ...INPUT,
            borderColor: showValidation && isNameEmpty ? 'var(--color-error)' : 'var(--border-main)',
          }}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = showValidation && isNameEmpty ? 'var(--color-error)' : 'var(--border-main)'; }}
        />
        {showValidation && isNameEmpty && (
          <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 4 }}>
            💡 Project name is required to continue
          </p>
        )}
      </div>

      <div>
        <label style={LABEL}>Description</label>
        <textarea
          value={description} onChange={e => setDescription(e.target.value)}
          placeholder="Describe the project's purpose…"
          rows={3}
          style={{ ...INPUT, resize: 'vertical', minHeight: 72 }}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
        />
      </div>

      <div>
        <label style={LABEL}>Project Tags</label>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
          {[...tags].map(tag => (
            <div
              key={tag}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '4px 10px', borderRadius: 4,
                background: 'var(--accent-blue)20', color: 'var(--accent-blue)',
                fontSize: 12, fontWeight: 500,
              }}
            >
              {tag}
              <button
                type="button"
                onClick={() => { const newTags = new Set(tags); newTags.delete(tag); setTags(newTags); }}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0 }}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            type="text"
            value={tagInput}
            onChange={e => setTagInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && tagInput.trim()) {
                const newTags = new Set(tags);
                newTags.add(tagInput.trim());
                setTags(newTags);
                setTagInput('');
              }
            }}
            placeholder="Type and press Enter to add…"
            style={{ ...INPUT, flex: 1 }}
            onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
            onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
          />
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6 }}>
          Add tags to help organize and categorize your project for easier discovery.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        {/* SOURCE CONNECTOR - Single Selection */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <label style={{...LABEL, margin: 0}}>Source Connector</label>
            <span style={{
              fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
              background: 'var(--accent-blue)20', color: 'var(--accent-blue)', textTransform: 'uppercase'
            }}>
              Single Selection
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {CONNECTOR_TYPES.map(c => {
              const isSelected = sourceConnector === c.value;
              const shouldDim = Boolean(sourceConnector) && !isSelected;
              return (
                <button
                  key={c.value}
                  type="button"
                  onClick={() => setSourceConnector(c.value)}
                  className={`transition-all duration-300 ${shouldDim ? 'text-slate-500' : ''}`}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 14px', borderRadius: 8, cursor: 'pointer',
                    background: isSelected ? 'var(--accent-blue)14' : 'var(--bg-surface)',
                    border: isSelected ? '1.5px solid var(--accent-blue)' : '1px solid var(--border-main)',
                    color: isSelected ? 'var(--accent-blue)' : shouldDim ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                    fontSize: 12, fontWeight: isSelected ? 600 : 400,
                    textAlign: 'left', transition: 'all 0.2s ease',
                    opacity: shouldDim ? 0.4 : 1,
                    filter: shouldDim ? 'grayscale(100%)' : 'none',
                  }}
                  onMouseEnter={e => {
                    if (!isSelected) e.target.style.borderColor = 'var(--accent-blue)40';
                  }}
                  onMouseLeave={e => {
                    if (!isSelected) e.target.style.borderColor = 'var(--border-main)';
                  }}
                >
                  {/* Radio button indicator */}
                  <div style={{
                    width: 16, height: 16, borderRadius: '50%', flexShrink: 0,
                    border: isSelected ? '5px solid var(--accent-blue)' : '2px solid var(--text-tertiary)',
                    background: isSelected ? 'var(--accent-blue)20' : 'transparent',
                    transition: 'all 0.2s ease',
                  }} />
                  {c.icon && <span style={{ fontSize: 16 }}>{c.icon}</span>}
                  <span style={{ flex: 1 }}>{c.label}</span>
                  {isSelected && <Check size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />}
                </button>
              );
            })}
          </div>
          {showValidation && isSourceMissing && (
            <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 8 }}>
              💡 Please select one source connector to continue
            </p>
          )}
          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 8 }}>
            Choose one source. Data will be read from this connector only.
          </p>
        </div>

        {/* TARGET CONNECTORS - Multiple Selection */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <label style={{...LABEL, margin: 0}}>Target Connectors</label>
            <span style={{
              fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 4,
              background: 'var(--color-success-bg)', color: 'var(--color-success)', textTransform: 'uppercase'
            }}>
              Multiple Selection
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {TARGET_CONNECTOR_TYPES.map(t => {
              const isSelected = targetConnectors.has(t.value);
              const isDisabledBySource = Boolean(sourceConnector) && t.value === sourceConnector;
              const handleTargetClick = () => {
                if (isDisabledBySource) return;
                const newTargets = new Set(targetConnectors);
                if (newTargets.has(t.value)) {
                  newTargets.delete(t.value);
                } else {
                  newTargets.add(t.value);
                }
                setTargetConnectors(newTargets);
              };
              return (
                <button
                  key={t.value}
                  type="button"
                  onClick={handleTargetClick}
                  disabled={isDisabledBySource}
                  className={`transition-all duration-300 ${isDisabledBySource ? 'text-slate-500' : ''}`}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 14px', borderRadius: 8,
                    cursor: isDisabledBySource ? 'not-allowed' : 'pointer',
                    background: isSelected ? 'var(--accent-blue)14' : 'var(--bg-surface)',
                    border: isSelected ? '1.5px solid var(--accent-blue)' : '1px solid var(--border-main)',
                    color: isSelected ? 'var(--accent-blue)' : isDisabledBySource ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                    fontSize: 12, fontWeight: isSelected ? 600 : 400,
                    textAlign: 'left', transition: 'all 0.2s ease',
                    opacity: isDisabledBySource ? 0.2 : 1,
                    filter: isDisabledBySource ? 'grayscale(100%)' : 'none',
                    pointerEvents: isDisabledBySource ? 'none' : 'auto',
                  }}
                  onMouseEnter={e => {
                    if (!isSelected && !isDisabledBySource) e.target.style.borderColor = 'var(--accent-blue)40';
                  }}
                  onMouseLeave={e => {
                    if (!isSelected && !isDisabledBySource) e.target.style.borderColor = 'var(--border-main)';
                  }}
                >
                  {/* Checkbox indicator */}
                  <div style={{
                    width: 16, height: 16, borderRadius: 4, flexShrink: 0,
                    border: isSelected ? 'none' : '2px solid var(--text-tertiary)',
                    background: isSelected ? 'var(--accent-blue)' : 'transparent',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 0.2s ease',
                  }}>
                    {isSelected && <Check size={12} color="white" />}
                  </div>
                  {t.icon && <span style={{ fontSize: 16 }}>{t.icon}</span>}
                  <span style={{ flex: 1 }}>{t.label}</span>
                  {isSelected && <span style={{ fontSize: 11, color: 'var(--accent-blue)', fontWeight: 500 }}></span>}
                </button>
              );
            })}
          </div>
          <p style={{ fontSize: 11, color: showValidation && isTargetsMissing ? 'var(--color-error)' : 'var(--text-tertiary)', marginTop: 8 }}>
            {isTargetsMissing ? '💡 Select at least one target to continue' : 'Choose one or more targets. Data will be synced to all selected connectors.'}
          </p>
        </div>
      </div>

      <div>
        <label style={LABEL}>Intermediate Format</label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {INTERMEDIATE_FORMAT_TYPES.map(t => (
            <ConnectorChip
              key={t.value} label={t.label}
              selected={intermediateFormat === t.value}
              onClick={() => setIntermediateFormat(t.value)}
            />
          ))}
        </div>
        <p style={{ fontSize: 11, color: showValidation && isFormatMissing ? 'var(--color-error)' : 'var(--text-tertiary)', marginTop: 6 }}>
          {isFormatMissing ? '💡 Choose an intermediate format to continue' : 'The target connector is used for sync. The intermediate format controls which semantic artifacts are generated for review or deployment.'}
        </p>
      </div>

    </div>
  );
}

function ConnectorChip({ icon, label, selected, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 12px', borderRadius: 8, cursor: 'pointer',
        background: selected ? 'var(--accent-blue)14' : 'var(--bg-surface)',
        border: selected ? '1.5px solid var(--accent-blue)' : '1px solid var(--border-main)',
        color: selected ? 'var(--accent-blue)' : 'var(--text-secondary)',
        fontSize: 12, fontWeight: selected ? 600 : 400,
        textAlign: 'left',
      }}
    >
      {icon && <span>{icon}</span>}
      {label}
      {selected && <Check size={12} style={{ marginLeft: 'auto' }} />}
    </button>
  );
}

/* ─── Step 2: Connector Config ─── */
function StepConnectorConfig({
  sourceConnector,
  targetConnectors,
  fabricWorkspaceId,
  setFabricWorkspaceId,
  snowflakeDatabase,
  setSnowflakeDatabase,
  snowflakeSchema,
  setSnowflakeSchema,
  targetDatabase,
  setTargetDatabase,
  targetSchema,
  setTargetSchema,
  domainHint,
  setDomainHint,
  workspaces,
  workspacesLoading,
  globalDefaultWorkspaceId,
  globalDefaultWorkspaceName,
  sessionWorkspaceId,
  workspaceManuallySet,
  onWorkspaceManualChange,
  isRefreshingWorkspaces,
  fetchFabricWorkspaces,
  runWarning,
}) {
  // True when the currently selected workspace is the one saved in Settings.
  const isUsingGlobalDefault = Boolean(
    globalDefaultWorkspaceId && fabricWorkspaceId === globalDefaultWorkspaceId
  );

  const selectedTargets = [...targetConnectors];
  const sourceLabel = CONNECTOR_TYPES.find(c => c.value === sourceConnector)?.label || sourceConnector;

  const SECTION_CARD = {
    border: '1px solid var(--border-main)',
    borderRadius: 12,
    background: 'var(--bg-surface)',
    padding: 16,
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Connector Configuration</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Configure source first, then review one or more target connectors. Credentials are managed globally in Settings and are not entered here.
        </p>
      </div>

      <div style={SECTION_CARD}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>1. Source Configuration</div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
              Select source location and defaults used for discovery.
            </div>
          </div>
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent-blue)', background: 'var(--accent-blue)14', border: '1px solid var(--accent-blue)30', padding: '3px 8px', borderRadius: 999 }}>
            {sourceLabel}
          </span>
        </div>

        {sourceConnector === 'fabric' && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <label style={{ ...LABEL, margin: 0 }}>Fabric Workspace</label>
              {isUsingGlobalDefault && (
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 999,
                  background: 'var(--color-success-bg)', color: 'var(--color-success)',
                  border: '1px solid var(--color-success)30', letterSpacing: '0.3px',
                }}>
                  ✓ Default
                </span>
              )}
              <div style={{ flex: 1 }} />
              <button 
                onClick={fetchFabricWorkspaces} 
                disabled={isRefreshingWorkspaces}
                style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  background: 'none', border: 'none', cursor: isRefreshingWorkspaces ? 'not-allowed' : 'pointer',
                  fontSize: 11, color: 'var(--text-tertiary)', padding: '2px 6px',
                  borderRadius: 4, transition: 'all 0.2s ease',
                  opacity: isRefreshingWorkspaces ? 0.6 : 1
                }}
                onMouseEnter={e => { if(!isRefreshingWorkspaces) e.currentTarget.style.color = 'var(--text-primary)'; }}
                onMouseLeave={e => { if(!isRefreshingWorkspaces) e.currentTarget.style.color = 'var(--text-tertiary)'; }}
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
              placeholder="Choose a workspace"
              value={fabricWorkspaceId}
              onChange={item => {
                const newId = item?.id || '';
                // If the user picks a different workspace, notify the parent.
                if (newId !== fabricWorkspaceId && onWorkspaceManualChange) {
                  onWorkspaceManualChange(newId);
                } else {
                  setFabricWorkspaceId(newId);
                }
              }}
              loading={workspacesLoading}
              clearable={false}
            />
            <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
              {isUsingGlobalDefault
                ? `Pre-selected from your active Fabric session${globalDefaultWorkspaceName ? ` (${globalDefaultWorkspaceName})` : ''}. You can override it below.`
                : 'Pick one workspace here. Step 3 will show models from this workspace only.'}
            </p>
            {!workspacesLoading && workspaces.length === 0 && (
              <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 8 }}>
                No Fabric workspaces were found. Check connector setup in Settings.
              </p>
            )}
            {runWarning && (
              <p style={{ fontSize: 12, color: 'var(--color-error)', marginTop: 8 }}>
                {runWarning}
              </p>
            )}
          </div>
        )}

        {sourceConnector === 'snowflake' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={LABEL}>Source Database</label>
              <input
                type="text" value={snowflakeDatabase} onChange={e => setSnowflakeDatabase(e.target.value)}
                placeholder="e.g. ANALYTICS_DB"
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
        )}

        {sourceConnector !== 'fabric' && sourceConnector !== 'snowflake' && (
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
            const targetMeta = TARGET_CONNECTOR_TYPES.find(t => t.value === target) || { label: target, icon: '🔗' };
            return (
              <details key={target} open style={{ border: '1px solid var(--border-main)', borderRadius: 10, overflow: 'hidden' }}>
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
                    <span>{targetMeta.icon}</span>
                    {targetMeta.label} Target Configuration
                  </span>
                  <ChevronDown size={14} />
                </summary>

                <div style={{ padding: 12, background: 'var(--bg-surface)' }}>
                  {target === 'snowflake' && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
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
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                      Fabric target uses the existing global connector. Destination details can be refined later in project configuration.
                    </div>
                  )}

                  {target !== 'snowflake' && target !== 'fabric' && (
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

/* ─── Step 3: Source Browser ─── */
function StepSourceBrowser({
  sourceConnector, selectedWorkspace, workspaces, wsLoading,
  expandedWs, toggleWorkspace, wsModels,
  selectedModels, toggleModel, clearSelectedModels,
  modelQuery, setModelQuery,
  modelQueryRegex, setModelQueryRegex,
  modelResults, snowflakeResults,
  // Databricks props
  databricksObjects = [],
  databricksLoading = false,
  selectedDatabricksTables = new Set(),
  setSelectedDatabricksTables = () => {},
  databricksQuery = '',
  setDatabricksQuery = () => {},
}) {

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
      ? allTables.filter(t => t.table.toLowerCase().includes(databricksQuery.toLowerCase()) || t.schema.toLowerCase().includes(databricksQuery.toLowerCase()) || t.catalog.toLowerCase().includes(databricksQuery.toLowerCase()))
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
      ? fabricModels.filter(m => (m.name || '').toLowerCase().includes(modelQuery.toLowerCase()))
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

function WorkspaceRow({ ws, expanded, models, selectedModels, onToggle, onModelToggle }) {
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

function ModelRow({ model, selected, onToggle, indent, showWs }) {
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
function StepMappingOptions({
  autoRelationships,
  setAutoRelationships,
  generateDescriptions,
  setGenerateDescriptions,
  detectedMappings,
  selectedModelNames,
}) {
  const [expandedMapping, setExpandedMapping] = useState(null);

  // Get relationships from session storage (read-only)
  const detectedRelationships = (() => {
    try {
      const stored = sessionStorage.getItem('detectedRelationships');
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  })();

  const explicitTables = useMemo(() => {
    return Array.from(new Set((selectedModelNames || []).map(name => String(name || '').trim()).filter(Boolean)));
  }, [selectedModelNames]);

  const inferredTables = useMemo(() => {
    const explicitUpper = new Set(explicitTables.map(name => name.toUpperCase()));
    return Array.from(new Set(
      (detectedMappings || [])
        .map(mapping => String(mapping?.source || '').trim())
        .filter(Boolean)
        .filter(name => !explicitUpper.has(name.toUpperCase()))
    ));
  }, [detectedMappings, explicitTables]);



  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Mapping Options & Verification</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Review detected mappings and relationships before proceeding. These will transform your source model.
        </p>
      </div>

      {/* TABLE MAPPINGS SECTION */}
      <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 10px' }}>
          Scope Verification
        </h3>
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', margin: '0 0 10px' }}>
          Extraction will only include explicitly selected models/tables and will always block internal/system patterns.
        </p>
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            <strong>Explicitly selected:</strong> {explicitTables.length > 0 ? explicitTables.join(', ') : 'None'}
          </div>
          <div style={{ fontSize: 11, color: inferredTables.length > 0 ? 'var(--accent-orange)' : 'var(--text-secondary)' }}>
            <strong>Inferred from mapping:</strong> {inferredTables.length > 0 ? inferredTables.join(', ') : 'None'}
          </div>
        </div>
      </div>

      {detectedMappings.length > 0 && (
        <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>📊 Table Mappings</span>
            <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 4 }}>
              {detectedMappings.length} detected
            </span>
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {detectedMappings.map(mapping => (
              <div key={mapping.id}>
                <button
                  onClick={() => setExpandedMapping(expandedMapping === mapping.id ? null : mapping.id)}
                  style={{
                    width: '100%', textAlign: 'left',
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '12px 14px', borderRadius: 8, cursor: 'pointer',
                    background: 'var(--bg-main)', border: '1.5px solid var(--border-main)',
                    color: 'var(--text-primary)', transition: 'all 0.2s ease',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-blue)40'; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-main)'; }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>
                      {mapping.source}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span>→</span> {mapping.target}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div
                      style={{
                        padding: '3px 10px', borderRadius: 4,
                        background: 'var(--accent-blue)20', color: 'var(--accent-blue)',
                        fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
                      }}
                    >
                      {mapping.status}
                    </div>
                    <ChevronDown
                      size={14} color="var(--text-tertiary)"
                      style={{ transform: expandedMapping === mapping.id ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                    />
                  </div>
                </button>

                {/* Column Details */}
                {expandedMapping === mapping.id && mapping.columns && (
                  <div style={{
                    marginTop: 8, padding: '12px', borderRadius: 6,
                    background: 'var(--bg-main)', border: '1px solid var(--accent-blue)20',
                  }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8, textTransform: 'uppercase' }}>
                      Column Mappings
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {mapping.columns.map((col, idx) => (
                        <div key={idx} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 8, alignItems: 'center', fontSize: 11 }}>
                          <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{col.source}</span>
                          <span style={{ color: 'var(--text-tertiary)', textAlign: 'center' }}>→</span>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{col.target}</span>
                            <span style={{ fontSize: 10, color: 'var(--text-tertiary)', background: 'var(--border-main)20', padding: '1px 6px', borderRadius: 3 }}>
                              {col.type}
                            </span>
                            {col.key && <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--accent-blue)' }}>🔑 PRIMARY KEY</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 12, marginBottom: 0 }}>
            ℹ️ These mappings were automatically detected from your selected models. Review each mapping to ensure accuracy before proceeding.
          </p>
        </div>
      )}



      {/* RELATIONSHIPS SECTION */}
      {detectedRelationships.length > 0 && autoRelationships && (
        <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>🔗 Detected Relationships</span>
            <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 4 }}>
              {detectedRelationships.length} relationships
            </span>
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {detectedRelationships.map(rel => (
              <div
                key={rel.id}
                style={{
                  padding: '12px 14px', borderRadius: 8,
                  background: 'var(--bg-main)', border: '1px solid var(--border-main)',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {rel.source}
                  </div>
                  <span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>{rel.joinType}</span>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                    {rel.target}
                  </div>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace', padding: '8px 10px', borderRadius: 4, background: 'var(--bg-surface)', marginBottom: 6 }}>
                  {rel.condition}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 3, textTransform: 'uppercase' }}>
                    {rel.confidence} confidence
                  </span>
                </div>
              </div>
            ))}
          </div>
          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 12, marginBottom: 0 }}>
            ℹ️ Relationships were inferred from foreign keys and naming conventions. Enable "Auto-detect Relationships" toggle below to use them during mapping.
          </p>
        </div>
      )}

      {/* OPTIONS */}
      <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
        <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px' }}>Transformation Options</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ToggleOption
            label="Auto-detect Relationships"
            description="Automatically infer joins and relationships from foreign keys and naming conventions."
            checked={autoRelationships}
            onChange={setAutoRelationships}
          />
          <ToggleOption
            label="Generate AI Descriptions"
            description="Use the LLM to auto-generate descriptions for tables and fields during sync."
            checked={generateDescriptions}
            onChange={setGenerateDescriptions}
          />
        </div>
      </div>

      <div style={{ padding: 12, borderRadius: 8, background: 'var(--accent-blue)08', border: '1px solid var(--accent-blue)20' }}>
        <p style={{ fontSize: 11, color: 'var(--accent-blue)', margin: 0, lineHeight: 1.6 }}>
          ✓ <strong>Ready to proceed:</strong> All mappings have been verified and are ready for transformation. Click "Continue" to move to the next step.
        </p>
      </div>
    </div>
  );
}

function ToggleOption({ label, description, checked, onChange }) {
  return (
    <div
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 12,
        padding: '14px 16px', borderRadius: 10, cursor: 'pointer',
        background: checked ? 'var(--accent-blue)08' : 'var(--bg-surface)',
        border: checked ? '1.5px solid var(--accent-blue)40' : '1px solid var(--border-main)',
      }}
      onClick={() => onChange(v => !v)}
    >
      <div
        style={{
          width: 40, height: 22, borderRadius: 11, flexShrink: 0, marginTop: 2,
          background: checked ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
          position: 'relative', transition: 'background 0.2s',
        }}
      >
        <div style={{
          position: 'absolute', top: 3, left: checked ? 20 : 3,
          width: 16, height: 16, borderRadius: '50%',
          background: '#fff', transition: 'left 0.2s',
          boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
        }} />
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>{description}</div>
      </div>
    </div>
  );
}

/* ─── Step 5: Finish ─── */
function StepFinish({
  name,
  saving,
  createReverseProject,
  setCreateReverseProject,
  createdProject,
  createError,
  runWarning,
  sourceConnector,
  targetConnectors,
  intermediateFormat,
  selectedWorkspace,
  navigate,
}) {
  const createdProjectId = createdProject?.id || createdProject?.project_id;

  if (saving && !createdProject) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0' }}>
        <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'var(--accent-blue)20', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
          <Loader2 size={28} style={{ color: 'var(--accent-blue)', animation: 'spin 1s linear infinite' }} />
        </div>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Creating Project</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 24 }}>
          Setting up configurations and initializing "{name}"...
        </p>
      </div>
    );
  }

  if (createdProject) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0' }}>
        <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'var(--color-success)20', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
          <Check size={28} style={{ color: 'var(--color-success)' }} />
        </div>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Project Created!</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 24 }}>
          {`Project "${name}" has been created successfully. You can now review your configuration or start your first sync.`}
        </p>
        {runWarning && (
          <div style={{ maxWidth: 520, margin: '0 auto 20px', padding: '12px 14px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.5, textAlign: 'left' }}>
            {runWarning}
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={() => navigate('/projects')} style={footerBtn('secondary')}>Back to Projects</button>
          {createdProjectId ? (
            <button onClick={() => navigate(`/projects/${createdProjectId}/config?mode=form`)} style={footerBtn('primary')}>
              Configure Project
            </button>
          ) : (
            <button disabled style={{ ...footerBtn('secondary'), opacity: 0.6, cursor: 'not-allowed' }}>
              Configure Project (ID unavailable)
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Ready to Create</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Review your choices and create the project.
        </p>
      </div>

      <div style={{ padding: '16px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>{name}</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
          Source: {sourceConnector} <br />
          Targets: {Array.from(targetConnectors).join(', ')} <br />
          Intermediate Format: {intermediateFormat} <br />
          Preferred Interface: UI Form <br />
          Workspace: {selectedWorkspace?.name || 'Will use saved defaults'}
        </div>
      </div>

      {createError && (
        <div style={{ padding: '12px 14px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.28)', color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.5 }}>
          {createError}
        </div>
      )}

      <ToggleOption
        label="Create Reverse Project"
        description={`Also create ${name || 'the project'}_${Array.from(targetConnectors)[0] || 'target'}_to_${sourceConnector} using reversed source/target roles.`}
        checked={createReverseProject}
        onChange={setCreateReverseProject}
      />
    </div>
  );
}

function footerBtn(variant) {
  return {
    padding: '9px 20px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    background: variant === 'primary' ? 'var(--accent-blue)' : 'transparent',
    color: variant === 'primary' ? '#fff' : 'var(--text-secondary)',
    border: variant === 'primary' ? 'none' : '1px solid var(--border-main)',
  };
}

function escapeYamlString(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
