import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Save, Play, CalendarClock, CalendarDays, Settings2, FileCode2, SlidersHorizontal, Loader2, CheckCircle2, RotateCcw, Camera } from 'lucide-react';
import { parseDocument as parseYamlDocument, stringify as stringifyYaml } from 'yaml';
import CodeMirror from '@uiw/react-codemirror';
import { yaml as yamlLang } from '@codemirror/lang-yaml';

import GlobalConfigModal from '../components/projects/GlobalConfigModal';

import SearchableSelect from '../components/common/SearchableSelect';
import { useTheme } from '../context/ThemeProvider';
import { useLogs } from '../context/LogsContext';
import { useSyncStatusStore } from '../context/SyncStatusContext';
import { api } from '../utils/api';
import { buildMockRunLogs, saveRunLogs } from '../utils/runLogs';
import { useUIStore } from '../store/uiStore';

import Modal from '../components/common/Modal';

const INPUT = {
  display: 'block', width: '100%',
  background: 'var(--bg-input)', border: '1px solid var(--border-main)',
  borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px',
  fontSize: 13, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

const LABEL = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 };

function pad2(value) {
  return String(value).padStart(2, '0');
}

function toDateInputValue(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function toTimeInputValue(date) {
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

function utcIsoFromLocalInputs(dateValue, timeValue) {
  if (!dateValue || !timeValue) return '';
  const localDate = new Date(`${dateValue}T${timeValue}:00`);
  return Number.isNaN(localDate.getTime()) ? '' : localDate.toISOString();
}

function localInputsFromUtcIso(isoValue) {
  if (!isoValue) return null;
  const parsed = new Date(isoValue);
  if (Number.isNaN(parsed.getTime())) return null;
  return {
    date: toDateInputValue(parsed),
    time: toTimeInputValue(parsed),
  };
}

/**
 * ProjectConfigPage — dedicated full page /projects/:id/config
 */
export default function ProjectConfigPage() {
    // --- Scheduler State (must be at top-level of component) ---
    const [schedulerOpen, setSchedulerOpen] = useState(false);
    const [scheduleType, setScheduleType] = useState('manual');
    const [recurrence, setRecurrence] = useState('once'); // once | daily | monthly
    const [cronValue, setCronValue] = useState('0 0 0 * * ?'); // Quartz default 6 fields
    const [scheduleDate, setScheduleDate] = useState(() => toDateInputValue(new Date()));
    const [timeValue, setTimeValue] = useState('12:00');
    const [timezoneValue, setTimezoneValue] = useState('UTC');
    const [isManualCron, setIsManualCron] = useState(false);
    const scheduleDateInputRef = useRef(null);
    const scheduleTimeInputRef = useRef(null);
  const navigate = useNavigate();
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const { theme } = useTheme();
  const { addLog } = useLogs();
  const { runs, currentSyncId, currentSyncStatus, projectStatusById, projectProgressById } = useSyncStatusStore();
  const lastToastStatusRef = useRef('');
  const setActiveProjectId = useUIStore(state => state.setActiveProjectId);
  const hasHydrated = useUIStore(state => state.hasHydrated);
  const projectConfigDrafts = useUIStore(state => state.projectConfigDrafts);
  const setProjectConfigDraft = useUIStore(state => state.setProjectConfigDraft);
  const isInvalidProjectId = !id || id === 'null' || id === 'undefined';
  const yamlExtensions = useMemo(() => [yamlLang()], []);
  const projectDraft = projectConfigDrafts?.[String(id)] || null;
  const stableDraftKey = useMemo(() => {
    if (!projectDraft || typeof projectDraft !== 'object') return '';
    return JSON.stringify({
      viewMode: projectDraft.viewMode,
      yamlText: projectDraft.yamlText,
      configForm: projectDraft.configForm,
    });
  }, [projectDraft]);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [project, setProject] = useState(null);
  const [allProjects, setAllProjects] = useState([]);
  const [selectedPresetProjectId, setSelectedPresetProjectId] = useState(null);
  const [yamlPath, setYamlPath] = useState('');
  const [saveInfo, setSaveInfo] = useState('');
  const [configTree, setConfigTree] = useState({});
  const [projectMappings, setProjectMappings] = useState([]);
  const [projectMappingsLoading, setProjectMappingsLoading] = useState(false);
  const [projectMappingsError, setProjectMappingsError] = useState('');

  const [viewMode, setViewMode] = useState('form'); // form | yaml
  const [yamlText, setYamlText] = useState('');
  const [configForm, setConfigForm] = useState({
    source_type: 'fabric',
    target_type: 'snowflake',
    output_format: 'osi',
    pbix_path: '',
    pbix_folder: '',
    pbix_uploaded_path: '',
    identity_id: '',
    workspace_id: '',
    database: '',
    schema: '',
    target_database: '',
    target_schema: '',
    target_identity_id: '',
    target_workspace_id: '',
    allow_models: '',
    block_models: '',
    auto_relationships: true,
    generate_descriptions: true,
  });
  const [fabricAccounts, setFabricAccounts] = useState([]);
  const [fabricWorkspaces, setFabricWorkspaces] = useState([]);
  const [fabricLoading, setFabricLoading] = useState(false);

  // Target Fabric state (independent from source)
  const [targetFabricAccounts, setTargetFabricAccounts] = useState([]);
  const [targetFabricWorkspaces, setTargetFabricWorkspaces] = useState([]);
  const [targetFabricLoading, setTargetFabricLoading] = useState(false);
  const [databricksAccounts, setDatabricksAccounts] = useState([]);

  const [globalOpen, setGlobalOpen] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);
  const [restoreLoading, setRestoreLoading] = useState(false);
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [restoreSnapshots, setRestoreSnapshots] = useState([]);
  const [selectedRestoreSnapshotId, setSelectedRestoreSnapshotId] = useState('');
  const [useCurrentRestoreDetails, setUseCurrentRestoreDetails] = useState(true);
  const [manualSnapOpen, setManualSnapOpen] = useState(false);
  const [manualSnapBusy, setManualSnapBusy] = useState(false);
  const [manualSnapLabel, setManualSnapLabel] = useState('');
  const [manualSnapFormat, setManualSnapFormat] = useState('');
  const [manualSnapIncludeSource, setManualSnapIncludeSource] = useState(true);
  const [manualSnapIncludeTargets, setManualSnapIncludeTargets] = useState(true);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareSnapshots, setCompareSnapshots] = useState([]);
  const [compareFromSnapshotId, setCompareFromSnapshotId] = useState('');
  const [compareToSnapshotId, setCompareToSnapshotId] = useState('');
  const [compareResult, setCompareResult] = useState(null);
  const [compareMaximized, setCompareMaximized] = useState(false);
  const timezoneOptions = useMemo(() => ([
    'UTC',
    'Asia/Kolkata',
    'America/New_York',
    'America/Chicago',
    'America/Denver',
    'America/Los_Angeles',
    'Europe/London',
  ]), []);
  const normalizedProjectId = String(project?.id || project?.project_id || id || '');
  const latestProjectRun = useMemo(() => {
    const projectRuns = runs.filter((run) => String(run?.project_id || '') === normalizedProjectId);
    if (!projectRuns.length) return null;

    const getRunTs = (run) => {
      const started = run?.started_at ? Date.parse(run.started_at) : NaN;
      if (!Number.isNaN(started) && started > 0) return started;
      const updated = run?.updated_at ? Date.parse(run.updated_at) : NaN;
      if (!Number.isNaN(updated) && updated > 0) return updated;
      const created = run?.created_at ? Date.parse(run.created_at) : NaN;
      if (!Number.isNaN(created) && created > 0) return created;
      const numericId = Number(run?.id || run?.run_id || 0);
      return Number.isNaN(numericId) ? 0 : numericId;
    };

    return projectRuns.sort((a, b) => getRunTs(b) - getRunTs(a))[0] || null;
  }, [runs, normalizedProjectId]);
  const hasLiveSyncState = String(currentSyncId || '') === normalizedProjectId;
  const projectSyncStatus = String(
    hasLiveSyncState
      ? currentSyncStatus
      : ''
  ).toLowerCase();
  const projectSyncProgress = Number(
    hasLiveSyncState
      ? (projectProgressById?.[normalizedProjectId] ?? latestProjectRun?.progress_pct ?? 0)
      : 0
  );
  const isProjectSyncing = projectSyncStatus === 'running';
  const isProjectSynced = hasLiveSyncState && !isProjectSyncing && projectSyncStatus === 'success';
  const targetCount = useMemo(() => {
    try {
      const parsed = parseProjectYaml(yamlText || '', project);
      const tree = parsed?.tree || {};
      if (Array.isArray(tree?.targets) && tree.targets.length > 0) return tree.targets.length;
      if (tree?.target && typeof tree.target === 'object') return 1;
    } catch {
      // Keep UI stable even if YAML is temporarily invalid while editing.
    }
    return 0;
  }, [yamlText, project]);

  useEffect(() => {
    if (!normalizedProjectId) return;

    if (projectSyncStatus === 'running') {
      lastToastStatusRef.current = 'running';
      return;
    }

    if (lastToastStatusRef.current === 'running' && projectSyncStatus === 'success') {
      addLog('success', 'Sync', `${project?.name || 'Project'} sync completed successfully.`);
      setSaveInfo('Sync completed successfully.');
      lastToastStatusRef.current = 'success';
      return;
    }

    if (lastToastStatusRef.current === 'running' && (projectSyncStatus === 'failed' || projectSyncStatus === 'warning')) {
      addLog('error', 'Sync', `${project?.name || 'Project'} sync failed. Check logs for details.`);
      lastToastStatusRef.current = projectSyncStatus;
    }
  }, [addLog, normalizedProjectId, project?.name, projectSyncStatus]);

  useEffect(() => {
    const modeFromUrl = searchParams.get('mode');
    const storedMode = localStorage.getItem(`project_${id}_viewMode`);
    const preferred = modeFromUrl === 'yaml' || modeFromUrl === 'form'
      ? modeFromUrl
      : (storedMode === 'yaml' || storedMode === 'form' ? storedMode : 'form');
    setViewMode(preferred);
  }, [id, searchParams]);

  useEffect(() => {
    if (!hasHydrated) return;

    if (isInvalidProjectId) {
      setProject(null);
      setLoading(false);
      navigate('/projects', { replace: true });
      return;
    }

    (async () => {
      try {
        const [p, cfg, all] = await Promise.all([
          api.getProject(id),
          api.getProjectConfig(id),
          api.listProjects(),
        ]);
        setProject(p);
        setYamlText(cfg?.config_yaml || '');
        setYamlPath(cfg?.yaml_path || '');
        setAllProjects(all.filter(x => String(x.id) !== String(id)));
        setActiveProjectId(p?.id || p?.project_id || id);
        try {
          const schedule = await api.getProjectSchedule(id);
          const nextScheduleType = String(schedule?.schedule_type || 'manual').toLowerCase();
          if (nextScheduleType === 'cron' || nextScheduleType === 'time' || nextScheduleType === 'manual') {
            setScheduleType(nextScheduleType);
          }
          if (typeof schedule?.cron === 'string' && schedule.cron) {
            setCronValue(schedule.cron);
          }
          const utcSchedule = localInputsFromUtcIso(schedule?.scheduled_time || schedule?.next_run_at);
          if (utcSchedule) {
            setScheduleDate(utcSchedule.date);
            setTimeValue(utcSchedule.time);
          } else {
            if (typeof schedule?.date === 'string' && schedule.date) {
              setScheduleDate(schedule.date);
            }
            if (typeof schedule?.time === 'string' && schedule.time) {
              setTimeValue(schedule.time);
            }
          }
          if (typeof schedule?.timezone === 'string' && schedule.timezone) {
            setTimezoneValue(schedule.timezone);
          }
        } catch {
          // Schedule is optional; keep default frontend state if fetch fails.
        }
        try {
          hydrateFormFromYaml(cfg?.config_yaml || '', p);

          const draft = useUIStore.getState().projectConfigDrafts?.[String(id)] || null;
          if (draft && typeof draft === 'object') {
            if (typeof draft.viewMode === 'string' && (draft.viewMode === 'form' || draft.viewMode === 'yaml')) {
              setViewMode(draft.viewMode);
            }
            if (typeof draft.yamlText === 'string') {
              setYamlText(draft.yamlText);
            }
            if (draft.configForm && typeof draft.configForm === 'object') {
              setConfigForm(prev => ({ ...prev, ...draft.configForm }));
            }
          }
        } catch (err) {
          setConfigTree({});
          setSaveInfo(`Invalid semabridge.yaml format loaded: ${err?.message || 'Unable to parse YAML'}`);
          setConfigForm(prev => ({
            ...prev,
            source_type: p?.source || 'fabric',
            target_type: p?.target_type || 'snowflake',
          }));
        }
      } catch {
        setProject(null);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasHydrated, id, isInvalidProjectId, navigate, setActiveProjectId]);

  // Dynamic Cron Generation Sync
  useEffect(() => {
    if (scheduleType === 'manual' || (scheduleType === 'cron' && isManualCron)) return;

    const [hh, mm] = (timeValue || '00:00').split(':');
    const h = parseInt(hh || 0);
    const m = parseInt(mm || 0);
    
    let generated = '';
    if (recurrence === 'daily') {
      generated = `0 ${m} ${h} * * ?`;
    } else if (recurrence === 'monthly') {
      const d = (scheduleDate ? new Date(scheduleDate).getDate() : 1) || 1;
      generated = `0 ${m} ${h} ${d} * ?`;
    } else {
      generated = `0 ${m} ${h} * * ?`;
    }

    if (generated && generated !== cronValue) {
      setCronValue(generated);
    }
  }, [scheduleType, recurrence, timeValue, scheduleDate, cronValue, isManualCron]);

  useEffect(() => {
    if (!id || isInvalidProjectId || loading) return;
    const nextDraft = {
      viewMode,
      yamlText,
      configForm,
    };

    const nextDraftKey = JSON.stringify(nextDraft);
    if (nextDraftKey === stableDraftKey) return;

    setProjectConfigDraft(id, nextDraft);
  }, [id, isInvalidProjectId, loading, viewMode, yamlText, configForm, setProjectConfigDraft, stableDraftKey]);

  useEffect(() => {
    if (!id || isInvalidProjectId || loading) return;

    let cancelled = false;
    setProjectMappingsLoading(true);
    setProjectMappingsError('');

    api.getMappings(id)
      .then((response) => {
        if (cancelled) return;
        const rows = Array.isArray(response?.mappings) ? response.mappings : [];
        setProjectMappings(rows);
      })
      .catch((err) => {
        if (cancelled) return;
        setProjectMappings([]);
        setProjectMappingsError(err?.message || 'Failed to load saved mappings.');
      })
      .finally(() => {
        if (!cancelled) setProjectMappingsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id, isInvalidProjectId, loading]);

  useEffect(() => {
    if (configForm.source_type !== 'fabric') {
      setFabricAccounts([]);
      setFabricWorkspaces([]);
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const res = await api.getAccounts('FABRIC');
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        if (!cancelled) {
          setFabricAccounts(list);
          if (!configForm.identity_id) {
            const preferred = list.find(acc => acc.id === project?.account_id);
            if (preferred?.id) {
              setConfigForm(prev => ({ ...prev, identity_id: prev.identity_id || preferred.id }));
            }
          }
        }
      } catch {
        if (!cancelled) setFabricAccounts([]);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [configForm.source_type, configForm.identity_id, project?.account_id]);

  const refreshFabricWorkspaces = async (identityId = configForm.identity_id) => {
    if (!identityId) {
      setFabricWorkspaces([]);
      return;
    }
    setFabricLoading(true);
    try {
      const data = await api.fabricListWorkspaces(identityId);
      const list = Array.isArray(data?.workspaces) ? data.workspaces : (Array.isArray(data) ? data : []);
      setFabricWorkspaces(list);
      if (!configForm.workspace_id && list[0]) {
        setConfigForm(prev => ({
          ...prev,
          workspace_id: prev.workspace_id || String(list[0].id || list[0].workspace_id || ''),
        }));
      }
    } catch {
      setFabricWorkspaces([]);
    } finally {
      setFabricLoading(false);
    }
  };

  useEffect(() => {
    if (configForm.source_type === 'fabric' && configForm.identity_id) {
      refreshFabricWorkspaces(configForm.identity_id);
    }
  }, [configForm.source_type, configForm.identity_id]);

  // ── Target Fabric: fetch accounts when target is fabric ──────────────────
  useEffect(() => {
    if (configForm.target_type !== 'fabric') {
      setTargetFabricAccounts([]);
      setTargetFabricWorkspaces([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getAccounts('FABRIC');
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        if (!cancelled) setTargetFabricAccounts(list);
      } catch {
        if (!cancelled) setTargetFabricAccounts([]);
      }
    })();
    return () => { cancelled = true; };
  }, [configForm.target_type]);

  const refreshTargetFabricWorkspaces = async (identityId = configForm.target_identity_id) => {
    if (!identityId) {
      setTargetFabricWorkspaces([]);
      return;
    }
    setTargetFabricLoading(true);
    try {
      const data = await api.fabricListWorkspaces(identityId);
      const list = Array.isArray(data?.workspaces) ? data.workspaces : (Array.isArray(data) ? data : []);
      setTargetFabricWorkspaces(list);
    } catch {
      setTargetFabricWorkspaces([]);
    } finally {
      setTargetFabricLoading(false);
    }
  };

  useEffect(() => {
    if (configForm.target_type === 'fabric' && configForm.target_identity_id) {
      refreshTargetFabricWorkspaces(configForm.target_identity_id);
    }
  }, [configForm.target_type, configForm.target_identity_id]);

  // ── Target Databricks: fetch accounts when target is databricks ──────────────────
  useEffect(() => {
    if (configForm.target_type !== 'databricks') {
      setDatabricksAccounts([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getAccounts('DATABRICKS');
        const list = Array.isArray(res) ? res : (res?.accounts || []);
        if (!cancelled) setDatabricksAccounts(list);
      } catch {
        if (!cancelled) setDatabricksAccounts([]);
      }
    })();
    return () => { cancelled = true; };
  }, [configForm.target_type]);

  const isLikelyBinaryOrGarbage = (text) => {
    const t = String(text || '').trim();
    if (!t) return false;
    if (t.startsWith('/9j/')) return true; // common JPEG base64 prefix
    const hasYamlShape = /(^|\n)\s*[a-zA-Z_][\w.-]*\s*:/.test(t);
    return !hasYamlShape && t.length > 300;
  };

  const normalizeTreeForYaml = (value) => {
    if (Array.isArray(value)) return value.map(normalizeTreeForYaml);
    if (value && typeof value === 'object') {
      const out = {};
      Object.entries(value).forEach(([k, v]) => {
        if (v === undefined) return;
        out[k] = normalizeTreeForYaml(v);
      });
      return out;
    }
    return value;
  };

  const normalizeConfigTreeForApi = (tree) => {
    const normalized = normalizeTreeForYaml(tree && typeof tree === 'object' ? tree : {});
    const source = normalized?.source && typeof normalized.source === 'object'
      ? { ...normalized.source }
      : {};
    const sourceType = String(source.type || '').toLowerCase();

    if (sourceType === 'fabric') {
      const list = [];
      const append = (raw) => {
        if (typeof raw !== 'string') return;
        raw
          .split(',')
          .map(part => part.trim())
          .filter(Boolean)
          .forEach(part => list.push(part));
      };

      if (Array.isArray(source.models)) {
        source.models
          .map(item => String(item || '').trim())
          .filter(Boolean)
          .forEach(item => list.push(item));
      } else if (typeof source.models === 'string') {
        append(source.models);
      }

      if (!list.length && source.model !== undefined && source.model !== null) {
        if (typeof source.model === 'string') {
          append(source.model);
        } else {
          const single = String(source.model).trim();
          if (single) list.push(single);
        }
      }

      if (list.length) {
        source.models = list;
      } else {
        delete source.models;
      }

      delete source.model;
      normalized.source = source;
    }

    return normalized;
  };

  const parseProjectYaml = (yaml, projectMeta) => {
    const raw = String(yaml || '').trim();
    const fallbackTarget = ['fabric', 'snowflake', 'databricks'].includes(projectMeta?.target_type) ? projectMeta.target_type : 'snowflake';
    const fallbackSource = projectMeta?.source || 'fabric';

    if (!raw) {
      return {
        tree: {},
        form: {
          source_type: fallbackSource,
          target_type: fallbackTarget,
          output_format: 'osi',
          pbix_path: '',
          pbix_folder: '',
          pbix_uploaded_path: '',
          identity_id: '',
          workspace_id: '',
          database: '',
          schema: '',
          target_database: '',
          target_schema: '',
          target_identity_id: '',
          target_workspace_id: '',
          allow_models: '',
          block_models: '',
        },
      };
    }

    if (isLikelyBinaryOrGarbage(raw)) {
      throw new Error('Input is not valid semabridge.yaml content');
    }

    const parsedDoc = parseYamlDocument(raw, {
      uniqueKeys: false,
      prettyErrors: true,
    });
    const parsed = parsedDoc.toJS ? parsedDoc.toJS() : {};
    const tree = parsed && typeof parsed === 'object' ? parsed : {};

    const source = tree.source && typeof tree.source === 'object' ? tree.source : {};
    const target = tree.target && typeof tree.target === 'object'
      ? tree.target
      : (Array.isArray(tree.targets) && tree.targets.length > 0 && typeof tree.targets[0] === 'object' ? tree.targets[0] : {});
    const ui = tree.ui && typeof tree.ui === 'object' ? tree.ui : {};
    const options = tree.options && typeof tree.options === 'object' ? tree.options : {};

    const sourceModels = Array.isArray(source.models) ? source.models.map(v => String(v).trim()).filter(Boolean) : [];
    const sourceModel = source.model ? String(source.model).trim() : '';
    const allowModels = sourceModels.length
      ? sourceModels
      : (sourceModel && sourceModel !== '*' ? [sourceModel] : []);

    const blockedModelsRaw = options.exclude_model;
    const blockedModels = Array.isArray(blockedModelsRaw)
      ? blockedModelsRaw.map(v => String(v).trim()).filter(Boolean)
      : (blockedModelsRaw ? [String(blockedModelsRaw).trim()] : []);

    return {
      tree,
      form: {
        source_type: String(source.type || fallbackSource).toLowerCase(),
        target_type: String(target.type || fallbackTarget).toLowerCase(),
        output_format: String(ui.intermediate_format || ui.output_format || 'osi').toLowerCase(),
        pbix_path: String(source.pbix_path || source.pbix_file_path || source.source_path || source.file_path || ''),
        pbix_folder: String(source.pbix_folder || ''),
        pbix_uploaded_path: String(projectMeta?.pbix_file_path || source.pbix_file_path || source.pbix_path || ''),
        identity_id: String(source.identity_id || ''),
        workspace_id: String(source.workspace_id || ''),
        database: String(source.database || ''),
        schema: String(source.schema || ''),
        target_database: String(target.database || ''),
        target_schema: String(target.schema || ''),
        target_identity_id: String(target.identity_id || ''),
        target_workspace_id: String(target.workspace_id || ''),
        allow_models: allowModels.join(', '),
        block_models: blockedModels.join(', '),
        auto_relationships: options.auto_relationships !== false,
        generate_descriptions: options.generate_descriptions !== false,
      },
    };
  };

  const hydrateFormFromYaml = (yaml, projectMeta) => {
    const parsed = parseProjectYaml(yaml, projectMeta);
    setConfigTree(parsed.tree || {});
    setConfigForm(parsed.form);
  };

  const buildYamlFromForm = () => {
    const allow = configForm.allow_models.split(',').map(s => s.trim()).filter(Boolean);
    const block = configForm.block_models.split(',').map(s => s.trim()).filter(Boolean);

    const nextTree = {
      ...(configTree && typeof configTree === 'object' ? configTree : {}),
      project_name: project?.name || '',
      source: {
        ...((configTree?.source && typeof configTree.source === 'object') ? configTree.source : {}),
        type: configForm.source_type,
      },
      target: {
        ...((configTree?.target && typeof configTree.target === 'object') ? configTree.target : {}),
        type: configForm.target_type,
      },
      ui: {
        ...((configTree?.ui && typeof configTree.ui === 'object') ? configTree.ui : {}),
        output_format: configForm.output_format,
      },
      options: {
        ...((configTree?.options && typeof configTree.options === 'object') ? configTree.options : {}),
        auto_relationships: Boolean(configForm.auto_relationships),
        generate_descriptions: Boolean(configForm.generate_descriptions),
      },
    };

    if (configForm.source_type === 'fabric') {
      if (configForm.identity_id) nextTree.source.identity_id = configForm.identity_id;
      else delete nextTree.source.identity_id;
      nextTree.source.workspace_id = configForm.workspace_id || '';
      delete nextTree.source.pbix_path;
      delete nextTree.source.pbix_folder;
      delete nextTree.source.source_path;
      delete nextTree.source.file_path;
      delete nextTree.source.database;
      delete nextTree.source.schema;
    } else if (configForm.source_type === 'snowflake') {
      delete nextTree.source.identity_id;
      nextTree.source.database = configForm.database || '';
      nextTree.source.schema = configForm.schema || '';
      delete nextTree.source.pbix_path;
      delete nextTree.source.pbix_folder;
      delete nextTree.source.source_path;
      delete nextTree.source.file_path;
      delete nextTree.source.workspace_id;
    } else if (configForm.source_type === 'pbix') {
      delete nextTree.source.identity_id;
      delete nextTree.source.workspace_id;
      delete nextTree.source.database;
      delete nextTree.source.schema;
      if (configForm.pbix_path?.trim()) {
        nextTree.source.pbix_path = configForm.pbix_path.trim();
        nextTree.source.pbix_file_path = configForm.pbix_path.trim();
      } else {
        delete nextTree.source.pbix_path;
        delete nextTree.source.pbix_file_path;
      }
      if (configForm.pbix_folder?.trim()) {
        nextTree.source.pbix_folder = configForm.pbix_folder.trim();
      } else {
        delete nextTree.source.pbix_folder;
      }
      delete nextTree.source.source_path;
      delete nextTree.source.file_path;
    } else {
      delete nextTree.source.identity_id;
      delete nextTree.source.workspace_id;
      delete nextTree.source.database;
      delete nextTree.source.schema;
      delete nextTree.source.pbix_path;
      delete nextTree.source.pbix_folder;
      delete nextTree.source.source_path;
      delete nextTree.source.file_path;
    }

    if (configForm.source_type === 'pbix') {
      // PBIX extraction resolves model from file path/folder; do not force model fields.
      delete nextTree.source.model;
      delete nextTree.source.models;
    } else if (configForm.source_type === 'fabric') {
      if (allow.length) {
        nextTree.source.models = allow;
      } else {
        delete nextTree.source.models;
      }
      delete nextTree.source.model;
    } else if (allow.length > 1) {
      nextTree.source.models = allow;
      delete nextTree.source.model;
    } else {
      nextTree.source.model = allow[0] || '*';
      delete nextTree.source.models;
    }

    if (configForm.target_type === 'snowflake') {
      if (configForm.target_database) nextTree.target.database = configForm.target_database;
      else delete nextTree.target.database;
      if (configForm.target_schema) nextTree.target.schema = configForm.target_schema;
      else delete nextTree.target.schema;
      delete nextTree.target.identity_id;
      delete nextTree.target.workspace_id;
    } else if (configForm.target_type === 'fabric') {
      if (configForm.target_identity_id) nextTree.target.identity_id = configForm.target_identity_id;
      else delete nextTree.target.identity_id;
      if (configForm.target_workspace_id) nextTree.target.workspace_id = configForm.target_workspace_id;
      else delete nextTree.target.workspace_id;
      delete nextTree.target.database;
      delete nextTree.target.schema;
    } else {
      delete nextTree.target.database;
      delete nextTree.target.schema;
      delete nextTree.target.identity_id;
      delete nextTree.target.workspace_id;
    }

    if (block.length) nextTree.options.exclude_model = block;
    else delete nextTree.options.exclude_model;

    if (Object.keys(nextTree.options).length === 0) delete nextTree.options;

    const normalized = normalizeConfigTreeForApi(nextTree);
    return stringifyYaml(normalized, { lineWidth: 0 });
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveInfo('');
    try {
      let nextYaml = '';
      if (viewMode === 'yaml') {
        const parsed = parseProjectYaml(yamlText, project);
        const normalizedTree = normalizeConfigTreeForApi(parsed.tree || {});
        setConfigTree(normalizedTree);
        setConfigForm(parsed.form);
        nextYaml = stringifyYaml(normalizedTree, { lineWidth: 0 });
      } else {
        nextYaml = buildYamlFromForm();
        try {
          const parsed = parseProjectYaml(nextYaml, project);
          setConfigTree(normalizeConfigTreeForApi(parsed.tree || {}));
          setConfigForm(parsed.form);
        } catch (parseErr) {
          console.warn('Form-generated project YAML could not be round-tripped locally:', parseErr);
          addLog(
            'warning',
            'Project Config',
            `Generated YAML skipped local round-trip validation: ${parseErr?.message || 'Unknown parse error'}`,
          );
        }
      }

      const response = await api.saveProjectConfig(id, nextYaml);
      setYamlText(nextYaml);
      if (response?.yaml_path) setYamlPath(response.yaml_path);
      if (Array.isArray(response?.warnings) && response.warnings.length) {
        setSaveInfo(response.warnings.join(' | '));
      } else {
        setSaveInfo('Config saved. Project semabridge.yaml updated.');
      }
      return true;
    } catch (err) {
      const message = err?.message || 'Unable to parse YAML';
      setSaveInfo(`Invalid semabridge.yaml format: ${message}`);
      addLog('error', 'Project Config', `Save failed: ${message}`);
      return false;
    } finally {
      setSaving(false);
    }
  };

  const handleCopyPreset = async (selectedPreset) => {
    const fromProjectId = selectedPreset && typeof selectedPreset === 'object'
      ? (selectedPreset.id ?? selectedPreset.project_id)
      : selectedPreset;

    setSelectedPresetProjectId(fromProjectId || null);
    if (!fromProjectId) return;

    try {
      const cfg = await api.getProjectConfig(fromProjectId);
      const next = cfg?.config_yaml || '';
      setYamlText(next);
      hydrateFormFromYaml(next, project);
      // Show copied preset immediately in YAML workspace, like classic editor flow.
      if (viewMode !== 'yaml') {
        localStorage.setItem(`project_${id}_viewMode`, 'yaml');
        setViewMode('yaml');
      }
      setSaveInfo(`Preset copied from project ${selectedPreset?.name || fromProjectId}.`);
    } catch (err) {
      setSaveInfo(`Failed to load preset: ${err?.message || 'Unknown error'}`);
    }
  };

  const handleRunNow = async () => {
    if (
      String(configForm.source_type || '').toLowerCase() === 'pbix'
      && !String(configForm.pbix_path || '').trim()
      && !String(configForm.pbix_folder || '').trim()
    ) {
      const msg = 'PBIX source requires PBIX File Path or PBIX Folder before Sync Now.';
      addLog('error', 'Sync', msg);
      setSaveInfo(msg);
      return;
    }

    setSyncing(true);
    try {
      const saved = await handleSave();
      if (!saved) {
        addLog('error', 'Sync', 'Sync not started because config save/validation failed.');
        return;
      }

      const run = await api.runProjectNow(id);
      const status = String(run?.status || '').toLowerCase();
      if (run?.id) {
        saveRunLogs(run.id, buildMockRunLogs(run));
      }

      // Sync Now is the primary execution entry point. Route users directly to Runs.
      if (run?.run_id || run?.id || status === 'running') {
        navigate('/jobs');
        return;
      }
      if (typeof window !== 'undefined' && window.dispatchEvent) {
        window.dispatchEvent(new CustomEvent('semabridge-sync-fallback', {
          detail: {
            projectId: id,
            status: status || 'running',
            progress: 5,
          },
        }));
      }

      if (status === 'running') {
        addLog('info', 'Sync', 'Sync started. You can continue using other screens while it runs.');
        setSaveInfo('Sync started. Progress is shown in the bottom status bar.');
      } else if (status === 'success') {
        addLog('success', 'Sync', 'Sync completed successfully.');
        setSaveInfo('Sync completed successfully.');
        // Fallback: force progress bar to 100% and status to 'success'
        if (typeof window !== 'undefined' && window.dispatchEvent) {
          window.dispatchEvent(new CustomEvent('semabridge-sync-fallback', { detail: { projectId: id, status: 'success', progress: 100 } }));
        }
      } else if (status === 'warning' || status === 'partial') {
        addLog('warning', 'Sync', `Sync completed with warnings${run?.error ? `: ${run.error}` : ''}`);
        setSaveInfo('Sync completed with warnings. Check logs for details.');
      } else {
        const msg = run?.error || 'Sync failed.';
        addLog('error', 'Sync', msg);
        setSaveInfo(`Sync failed: ${msg}`);
      }
    } catch (err) {
      const msg = err?.message || 'Sync failed due to unexpected error.';
      addLog('error', 'Sync', msg);
      setSaveInfo(`Sync failed: ${msg}`);
    } finally {
      setSyncing(false);
    }
  };

  const buildRestoreOverrides = () => {
    const source = {
      type: configForm.source_type || 'fabric',
      identity_id: configForm.identity_id || undefined,
      workspace_id: configForm.workspace_id || undefined,
      database: configForm.database || undefined,
      schema: configForm.schema || undefined,
      pbix_path: configForm.pbix_path || undefined,
      pbix_folder: configForm.pbix_folder || undefined,
    };

    const target = {
      type: configForm.target_type || 'snowflake',
      database: configForm.target_database || undefined,
      schema: configForm.target_schema || undefined,
    };

    return {
      source,
      target,
      targets: [target],
      ui: {
        output_format: configForm.output_format || 'osi',
        intermediate_format: configForm.output_format || 'osi',
      },
    };
  };

  const openRestoreModal = async () => {
    setRestoreOpen(true);
    setRestoreLoading(true);
    setSelectedRestoreSnapshotId('');

    try {
      const response = await api.listProjectSnapshots(id, { role: 'source', limit: 300 });
      const rows = Array.isArray(response?.snapshots) ? response.snapshots : [];
      setRestoreSnapshots(rows);
      if (rows.length > 0) {
        setSelectedRestoreSnapshotId(String(rows[0].snapshot_id || ''));
      }
    } catch (err) {
      setRestoreSnapshots([]);
      setSaveInfo(`Failed to load snapshots: ${err?.message || 'Unknown error'}`);
      addLog('error', 'Restore Version', `Failed to load snapshots: ${err?.message || 'Unknown error'}`);
    } finally {
      setRestoreLoading(false);
    }
  };

  const handleRestoreVersion = async () => {
    if (!selectedRestoreSnapshotId) {
      setSaveInfo('Select a snapshot version to restore.');
      return;
    }

    setRestoreBusy(true);
    try {
      const payload = {
        snapshot_id: selectedRestoreSnapshotId,
        overrides: useCurrentRestoreDetails ? buildRestoreOverrides() : {},
        allow_format_conversion: true,
        execute_restore_run: true,
      };

      const response = await api.restoreProjectVersion(id, payload);
      const restoredYaml = String(response?.config_yaml || '').trim();
      if (!restoredYaml) {
        throw new Error('Restore succeeded but no config payload was returned.');
      }

      setYamlText(restoredYaml);
      hydrateFormFromYaml(restoredYaml, project);
      setSaveInfo(`Version restored from snapshot ${selectedRestoreSnapshotId.slice(0, 12)}.`);
      const restoreRunId = String(response?.run_id || '').trim();
      if (restoreRunId) {
        addLog('success', 'Restore Version', `Project config restored and restore run ${restoreRunId.slice(0, 12)} started.`);
      } else {
        addLog('success', 'Restore Version', 'Project config restored from selected snapshot version.');
      }
      setRestoreOpen(false);
    } catch (err) {
      const msg = err?.message || 'Restore failed.';
      setSaveInfo(`Restore failed: ${msg}`);
      addLog('error', 'Restore Version', msg);
    } finally {
      setRestoreBusy(false);
    }
  };

  const openManualSnapshotModal = () => {
    setManualSnapLabel('');
    setManualSnapFormat('');
    setManualSnapIncludeSource(true);
    setManualSnapIncludeTargets(targetCount > 0);
    setManualSnapOpen(true);
  };

  const handleManualSnapshotCapture = async () => {
    if (!manualSnapIncludeSource && !manualSnapIncludeTargets) {
      setSaveInfo('Select source and/or targets for manual snapshot capture.');
      return;
    }

    setManualSnapBusy(true);
    try {
      const payload = {
        label: String(manualSnapLabel || '').trim() || undefined,
        format: manualSnapFormat || undefined,
        scope: {
          source: manualSnapIncludeSource,
          targets: manualSnapIncludeTargets ? 'all' : [],
        },
      };
      const response = await api.captureProjectSnapshot(id, payload);
      const groupId = String(response?.snapshot_group_id || '').trim();
      const count = Number(response?.count || 0);
      setSaveInfo(`Manual snapshot captured (${count} record${count === 1 ? '' : 's'}).`);
      addLog('success', 'Snapshot', `Manual snapshot group ${groupId || 'created'} with ${count} snapshot record(s).`);
      setManualSnapOpen(false);
    } catch (err) {
      const msg = err?.message || 'Manual snapshot capture failed.';
      setSaveInfo(`Snapshot capture failed: ${msg}`);
      addLog('error', 'Snapshot', msg);
    } finally {
      setManualSnapBusy(false);
    }
  };

  const openCompareModal = async () => {
    setCompareOpen(true);
    setCompareLoading(true);
    setCompareResult(null);
    setCompareMaximized(false);
    try {
      const response = await api.listProjectSnapshots(id, { role: 'source', include_state: false, limit: 400 });
      const rows = Array.isArray(response?.snapshots) ? response.snapshots : [];
      setCompareSnapshots(rows);

      const defaultFrom = selectedRestoreSnapshotId || String(rows?.[1]?.snapshot_id || rows?.[0]?.snapshot_id || '');
      const defaultTo = String(rows?.[0]?.snapshot_id || '');
      setCompareFromSnapshotId(defaultFrom);
      setCompareToSnapshotId(defaultTo && defaultTo !== defaultFrom ? defaultTo : String(rows?.[1]?.snapshot_id || ''));
    } catch (err) {
      setCompareSnapshots([]);
      setSaveInfo(`Failed to load snapshots for comparison: ${err?.message || 'Unknown error'}`);
      addLog('error', 'Snapshot Compare', err?.message || 'Failed to load snapshots for comparison.');
    } finally {
      setCompareLoading(false);
    }
  };

  const isPrimitive = (value) => (
    value === null || ['string', 'number', 'boolean'].includes(typeof value)
  );

  const formatScalar = (value) => {
    if (value === null) return 'null';
    if (typeof value === 'string') return value;
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    return String(value);
  };

  const buildYamlLikeLines = (value, path = '', indent = 0) => {
    const lines = [];
    const pad = ' '.repeat(indent);

    if (Array.isArray(value)) {
      value.forEach((item, idx) => {
        const itemPath = path ? `${path}[${idx}]` : `[${idx}]`;
        if (isPrimitive(item)) {
          lines.push({ path: itemPath, text: `${pad}- ${formatScalar(item)}` });
        } else {
          lines.push({ path: itemPath, text: `${pad}-` });
          lines.push(...buildYamlLikeLines(item, itemPath, indent + 2));
        }
      });
      return lines;
    }

    if (value && typeof value === 'object') {
      Object.keys(value).forEach((key) => {
        const nextPath = path ? `${path}.${key}` : key;
        const nextValue = value[key];
        if (isPrimitive(nextValue)) {
          lines.push({ path: nextPath, text: `${pad}${key}: ${formatScalar(nextValue)}` });
        } else {
          lines.push({ path: nextPath, text: `${pad}${key}:` });
          lines.push(...buildYamlLikeLines(nextValue, nextPath, indent + 2));
        }
      });
      return lines;
    }

    lines.push({ path, text: `${pad}${formatScalar(value)}` });
    return lines;
  };

  const normalizeChangePath = (rawPath) => {
    const text = String(rawPath || '').trim();
    if (!text || text === '$') return '';
    if (text.startsWith('$.')) return text.slice(2);
    if (text.startsWith('$')) return text.slice(1);
    return text;
  };

  const pathsRelated = (linePath, changePath) => {
    const a = String(linePath || '');
    const b = String(changePath || '');
    if (!a && !b) return true;
    if (!a || !b) return false;
    return (
      a === b
      || a.startsWith(`${b}.`)
      || a.startsWith(`${b}[`)
      || b.startsWith(`${a}.`)
      || b.startsWith(`${a}[`)
    );
  };

  const getLineDiffType = (linePath, side, changes) => {
    const normalizedLinePath = normalizeChangePath(linePath);
    const rows = Array.isArray(changes) ? changes : [];

    const hasType = (targetType) => rows.some((row) => {
      const rowType = String(row?.type || '').toLowerCase();
      if (rowType !== targetType) return false;
      const rowPath = normalizeChangePath(row?.path);
      return pathsRelated(normalizedLinePath, rowPath);
    });

    if (side === 'from') {
      if (hasType('removed')) return 'removed';
      if (hasType('modified')) return 'modified';
      return null;
    }

    if (hasType('added')) return 'added';
    if (hasType('modified')) return 'modified';
    return null;
  };

  const getLineHighlightStyle = (diffType) => {
    if (diffType === 'added') {
      return {
        background: 'var(--color-success-bg)',
        borderLeft: '3px solid var(--color-success)',
      };
    }
    if (diffType === 'removed') {
      return {
        background: 'var(--color-error-bg)',
        borderLeft: '3px solid var(--color-error)',
      };
    }
    if (diffType === 'modified') {
      return {
        background: 'var(--color-warning-bg)',
        borderLeft: '3px solid var(--color-warning)',
      };
    }
    return null;
  };

  const renderStateLines = (state) => {
    const lines = buildYamlLikeLines(state || {});
    return lines.length > 0 ? lines : [{ path: '', text: '{}' }];
  };

  const selectedCompareFromSnapshot = useMemo(
    () => compareSnapshots.find((row) => String(row?.snapshot_id || '') === String(compareFromSnapshotId || '')) || null,
    [compareSnapshots, compareFromSnapshotId],
  );

  const selectedCompareToSnapshot = useMemo(
    () => compareSnapshots.find((row) => String(row?.snapshot_id || '') === String(compareToSnapshotId || '')) || null,
    [compareSnapshots, compareToSnapshotId],
  );

  const compareLeftLabel = useMemo(() => {
    const timing = String(compareResult?.from_snapshot?.timing || selectedCompareFromSnapshot?.timing || '').toLowerCase();
    if (timing === 'before') return 'Before Sync Snapshot';
    if (timing === 'after') return 'After Sync Snapshot';
    return 'From Snapshot State';
  }, [compareResult?.from_snapshot?.timing, selectedCompareFromSnapshot?.timing]);

  const compareRightLabel = useMemo(() => {
    const timing = String(compareResult?.to_snapshot?.timing || selectedCompareToSnapshot?.timing || '').toLowerCase();
    if (timing === 'before') return 'Before Sync Snapshot';
    if (timing === 'after') return 'After Sync Snapshot';
    return 'To Snapshot State';
  }, [compareResult?.to_snapshot?.timing, selectedCompareToSnapshot?.timing]);

  const handleCompareSnapshots = async () => {
    if (!compareFromSnapshotId || !compareToSnapshotId) {
      setSaveInfo('Select both snapshots to compare.');
      return;
    }
    if (compareFromSnapshotId === compareToSnapshotId) {
      setSaveInfo('Choose two different snapshots for comparison.');
      return;
    }

    setCompareBusy(true);
    try {
      const report = await api.compareProjectSnapshots(id, compareFromSnapshotId, compareToSnapshotId, { max_changes: 150, include_states: true });
      setCompareResult(report || null);
      const total = Number(report?.summary?.total_changes || 0);
      if (total === 0) {
        addLog('success', 'Snapshot Compare', 'No differences found. Rollback matches selected snapshot state.');
      } else {
        addLog('warning', 'Snapshot Compare', `${total} difference(s) found between selected snapshots.`);
      }
    } catch (err) {
      const msg = err?.message || 'Snapshot comparison failed.';
      setSaveInfo(`Snapshot comparison failed: ${msg}`);
      addLog('error', 'Snapshot Compare', msg);
      setCompareResult(null);
    } finally {
      setCompareBusy(false);
    }
  };

  const handleCreateJob = async () => {
    await handleSave();
    navigate('/jobs');
  };

  const handleScheduleSave = async () => {
    let finalCron = '';
    
    if (scheduleType === 'cron') {
      finalCron = (cronValue || '').trim();
      const parts = finalCron.split(/\s+/).filter(Boolean);
      // Basic normalization to 6 fields if 5 are provided, though we should enforce 6
      if (parts.length === 5) {
        finalCron += ' ?';
      }
    } else if (scheduleType === 'time') {
      if (recurrence === 'daily') {
        const [hh, mm] = (timeValue || '00:00').split(':');
        finalCron = `0 ${parseInt(mm || 0)} ${parseInt(hh || 0)} * * ?`;
      } else if (recurrence === 'monthly') {
        const [hh, mm] = (timeValue || '00:00').split(':');
        const day = new Date(scheduleDate).getDate() || 1;
        finalCron = `0 ${parseInt(mm || 0)} ${parseInt(hh || 0)} ${day} * ?`;
      }
      // If recurrence === 'once', finalCron remains empty string
    }

    const scheduledTime = (scheduleType === 'time' || scheduleType === 'manual') ? utcIsoFromLocalInputs(scheduleDate, timeValue) : '';
    
    const payload = {
      schedule_type: scheduleType,
      cron: finalCron,
      date: scheduleType === 'time' ? scheduleDate || '' : '',
      time: scheduleType === 'time' ? timeValue || '' : '',
      scheduled_time: scheduledTime,
      timezone: timezoneValue,
      recurrence: scheduleType === 'time' ? recurrence : undefined,
    };

    try {
      const response = await api.saveProjectSchedule(id, payload);
      let successMsg = '';
      if (scheduleType === 'manual') {
        successMsg = response?.message || 'Schedule cleared. Trigger remains on-demand.';
      } else if (scheduleType === 'cron') {
        successMsg = `Schedule saved with cron "${finalCron}" (${timezoneValue}).`;
      } else if (scheduleType === 'time') {
        if (recurrence === 'once') {
          successMsg = `One-time schedule saved for ${scheduleDate} at ${timeValue}.`;
        } else {
          successMsg = `Recurring ${recurrence} schedule saved (Cron: ${finalCron}).`;
        }
      }

      setSaveInfo(successMsg);
      setSchedulerOpen(false);
    } catch (err) {
      setSaveInfo(`Failed to save schedule: ${err?.message || 'Unknown error'}`);
      addLog('error', 'Scheduler', `Schedule save failed: ${err?.message || 'Unknown error'}`);
    }
  };

  const scheduleTimingLabel = scheduleType === 'manual'
    ? 'Optional Planned Time'
    : scheduleType === 'cron'
      ? 'Default Run Time'
      : 'Schedule Time';

  const scheduleTimingHelper = scheduleType === 'manual'
    ? 'Optionally choose a date and time for planning purposes in this frontend mock.'
    : scheduleType === 'cron'
      ? 'Choose the preferred time window that goes with your cron schedule.'
      : 'Choose the exact date and time for the scheduled run.';

  const handleViewModeChange = (nextMode) => {
    if (nextMode === viewMode) return;

    if (nextMode === 'yaml') {
      const nextYaml = buildYamlFromForm();
      setYamlText(nextYaml);
      try {
        const parsed = parseProjectYaml(nextYaml, project);
        setConfigTree(parsed.tree || {});
      } catch {
        // no-op: form generated YAML should be valid, keep view switch resilient.
      }
    } else {
      try {
        hydrateFormFromYaml(yamlText, project);
      } catch (err) {
        setSaveInfo(`Invalid semabridge.yaml format: ${err?.message || 'Unable to parse YAML'}`);
        return;
      }
    }

    localStorage.setItem(`project_${id}_viewMode`, nextMode);
    setViewMode(nextMode);
  };

  const syncButtonStyle = {
    ...primaryBtn,
    opacity: (saving || syncing || isProjectSyncing) ? 0.85 : 1,
    cursor: (saving || syncing || isProjectSyncing) ? 'not-allowed' : 'pointer',
    background: isProjectSynced ? 'var(--color-success)' : primaryBtn.background,
  };

  const syncButtonLabel = isProjectSyncing
    ? `Syncing${projectSyncProgress > 0 ? ` ${projectSyncProgress}%` : '...'}`
    : isProjectSynced
      ? 'Sync Successful'
      : 'Sync Now';

  if (loading) {
    return (
      <div style={{ padding: 36, color: 'var(--text-tertiary)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
        <span>Loading project configuration...</span>
      </div>
    );
  }

  if (!project) {
    return (
      <div style={{ padding: 36 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Project not found</div>
        <button onClick={() => navigate('/projects')} style={{ marginTop: 12, ...secondaryBtn }}>
          <ArrowLeft size={13} /> Back to Projects
        </button>
      </div>
    );
  }

  return (
    <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }} className="md:px-10">
      <div style={{ padding: '18px 28px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button
          onClick={() => navigate('/projects')}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
        >
          <ArrowLeft size={14} /> Projects
        </button>
        <span style={{ color: 'var(--border-main)' }}>|</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>{project.name}</div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Edit Project</div>
        </div>

        <button onClick={() => setGlobalOpen(true)} style={secondaryBtn}>
          <Settings2 size={13} /> Global Config
        </button>

        <div style={{ display: 'inline-flex', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
          <ModeButton active={viewMode === 'form'} onClick={() => handleViewModeChange('form')} icon={<SlidersHorizontal size={13} />} ariaLabel="Form view" />
          <ModeButton active={viewMode === 'yaml'} onClick={() => handleViewModeChange('yaml')} icon={<FileCode2 size={13} />} ariaLabel="YAML view" />
        </div>
      </div>

      {(yamlPath || saveInfo) && (
        <div style={{ padding: '10px 28px', borderBottom: '1px solid var(--border-main)', fontSize: 12, color: 'var(--text-secondary)', display: 'flex', gap: 14, flexWrap: 'wrap' }}>
          {yamlPath && <span>Project semabridge.yaml: {yamlPath}</span>}
          {saveInfo && <span>{saveInfo}</span>}
        </div>
      )}

      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minHeight: 0 }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
            <label style={LABEL}>Copy Presets from Another Project</label>
            <SearchableSelect
              items={allProjects}
              displayKey="name"
              valueKey="id"
              searchFields={['name', 'description', 'source']}
              value={selectedPresetProjectId}
              placeholder="Search project presets..."
              onChange={handleCopyPreset}
              clearable
            />
          </div>

          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 16 }}>
            <MappingPreviewPanel
              mappings={projectMappings}
              loading={projectMappingsLoading}
              error={projectMappingsError}
            />
            {viewMode === 'form' ? (
              <FormEditor
                value={configForm}
                onChange={setConfigForm}
                fabricAccounts={fabricAccounts}
                fabricWorkspaces={fabricWorkspaces}
                fabricLoading={fabricLoading}
                onRefreshFabricWorkspaces={() => refreshFabricWorkspaces()}
                targetFabricAccounts={targetFabricAccounts}
                targetFabricWorkspaces={targetFabricWorkspaces}
                targetFabricLoading={targetFabricLoading}
                onRefreshTargetFabricWorkspaces={() => refreshTargetFabricWorkspaces()}
                databricksAccounts={databricksAccounts}
              />
            ) : (
              <div style={{ height: '100%', minHeight: 420 }}>
                <div style={{ height: '100%', border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden', background: 'var(--bg-input)', display: 'flex', flexDirection: 'column', boxShadow: '0 8px 24px rgba(0, 0, 0, 0.08)' }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '10px 12px',
                    borderBottom: '1px solid var(--border-main)',
                    background: 'var(--bg-surface)',
                    fontSize: 12,
                    color: 'var(--text-secondary)',
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                  }}>
                    <span style={{ color: 'var(--text-tertiary)' }}>YAML Workspace</span>
                    <span style={{ color: 'var(--border-main)' }}>•</span>
                    <span>semabridge.yaml</span>
                  </div>
                  <CodeMirror
                    value={yamlText}
                    height="100%"
                    theme={theme === 'dark' ? 'dark' : 'light'}
                    extensions={yamlExtensions}
                    onChange={(val) => setYamlText(val)}
                    basicSetup={{
                      lineNumbers: true,
                      foldGutter: true,
                      dropCursor: true,
                      allowMultipleSelections: true,
                      indentOnInput: true,
                      highlightActiveLine: true,
                      scrollPastEnd: true,
                    }}
                    style={{ fontSize: 12, lineHeight: 1.5 }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>

      </div>

      <div style={{ padding: '10px 28px', borderTop: '1px solid var(--border-main)', background: 'var(--bg-surface)' }} />


      {/* Action Buttons */}
      <div style={{ padding: '14px 28px', borderTop: '1px solid var(--border-main)', display: 'flex', justifyContent: 'flex-end', gap: 8, flexWrap: 'wrap', background: 'var(--bg-surface)', position: 'sticky', bottom: 0, zIndex: 2 }}>
        <button onClick={() => setSchedulerOpen(true)} style={secondaryBtn}>
          <CalendarClock size={13} /> Schedule
        </button>
        <button onClick={openManualSnapshotModal} disabled={saving || syncing || isProjectSyncing} style={secondaryBtn}>
          <Camera size={13} /> Capture Snapshot
        </button>
        <button onClick={openCompareModal} disabled={saving || syncing || isProjectSyncing} style={secondaryBtn}>
          <FileCode2 size={13} /> Compare States
        </button>
        <button onClick={openRestoreModal} disabled={saving || syncing || isProjectSyncing} style={secondaryBtn}>
          <RotateCcw size={13} /> Restore Version
        </button>
        <button onClick={handleCreateJob} style={secondaryBtn}>
          <CalendarClock size={13} /> Create Job for Later
        </button>
        <button onClick={handleSave} disabled={saving || syncing || isProjectSyncing} style={secondaryBtn}>
          {saving ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={13} />}
          Save Config
        </button>
        <button onClick={handleRunNow} disabled={saving || syncing || isProjectSyncing} style={syncButtonStyle}>
          {isProjectSyncing || syncing ? (
            <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />
          ) : isProjectSynced ? (
            <CheckCircle2 size={13} />
          ) : (
            <Play size={13} />
          )} {syncButtonLabel}
        </button>
      </div>

      {/* Scheduler Modal */}
      <Modal open={schedulerOpen} onClose={() => setSchedulerOpen(false)} title="Schedule Job" size="md">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div>
            <label style={{ fontWeight: 600, fontSize: 13, display: 'block', marginBottom: 10 }}>Schedule Type</label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              <ScheduleOptionCard
                active={scheduleType === 'manual'}
                title="Manual Trigger"
                description="Run only when someone starts it."
                onClick={() => setScheduleType('manual')}
              />
              <ScheduleOptionCard
                active={scheduleType === 'cron'}
                title="Cron Expression"
                description="Use cron syntax for recurring runs."
                onClick={() => {
                  setScheduleType('cron');
                  setIsManualCron(false); // Reset on switch to allow dynamic sync
                }}
              />
              <ScheduleOptionCard
                active={scheduleType === 'time'}
                title="Schedule Regularly"
                description="Pick a calendar date, time, and timezone."
                onClick={() => setScheduleType('time')}
              />
            </div>
          </div>

          <div>
            <label style={{ fontWeight: 500, fontSize: 12, display: 'block', marginBottom: 6 }}>Timezone</label>
            <select value={timezoneValue} onChange={e => setTimezoneValue(e.target.value)} style={modalInputStyle}>
              {timezoneOptions.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </div>

          {scheduleType === 'manual' && (
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: 12, borderRadius: 8, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
              Manual mode selected. The project stays unscheduled and can be triggered whenever needed.
            </div>
          )}

          {scheduleType === 'cron' && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <label style={{ fontWeight: 500, fontSize: 12 }}>Cron Expression</label>
                {isManualCron && (
                  <button 
                    onClick={() => setIsManualCron(false)}
                    style={{ background: 'none', border: 'none', color: 'var(--accent-blue)', fontSize: 10, cursor: 'pointer', fontWeight: 600 }}
                  >
                    Resync with pickers
                  </button>
                )}
              </div>
              <input 
                value={cronValue} 
                onChange={e => {
                  setIsManualCron(true);
                  setCronValue(e.target.value);
                }} 
                style={modalInputStyle} 
                placeholder="0 0 0 * * ?" 
              />
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                Quartz/Oracle format (6 fields): <strong>sec min hour dom month dow</strong>. 
                Use <strong>?</strong> in dom or dow.
              </div>
            </div>
          )}

          {scheduleType === 'time' && (
            <div>
              <label style={{ fontWeight: 500, fontSize: 12, display: 'block', marginBottom: 6 }}>Recurrence</label>
              <select 
                value={recurrence} 
                onChange={e => setRecurrence(e.target.value)} 
                style={modalInputStyle}
              >
                <option value="once">Once (One-time run)</option>
                <option value="daily">Daily Schedule</option>
                <option value="monthly">Monthly Schedule</option>
              </select>
            </div>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {(scheduleType !== 'time' || recurrence !== 'daily') && (
                <div>
                  <label style={{ fontWeight: 500, fontSize: 12, display: 'block', marginBottom: 6 }}>Schedule Date</label>
                <div style={{ position: 'relative' }}>
                  <input
                    ref={scheduleDateInputRef}
                    type="date"
                    value={scheduleDate}
                    min={toDateInputValue(new Date())}
                    onChange={e => setScheduleDate(e.target.value)}
                    style={{ ...modalInputStyle, paddingRight: 42 }}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (scheduleDateInputRef.current?.showPicker) {
                        scheduleDateInputRef.current.showPicker();
                      } else {
                        scheduleDateInputRef.current?.focus();
                      }
                    }}
                    style={{
                      position: 'absolute',
                      right: 8,
                      top: '50%',
                      transform: 'translateY(-50%)',
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer',
                      padding: 4,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                    aria-label="Open calendar"
                    title="Open calendar"
                  >
                    <CalendarDays size={16} />
                  </button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                  Click the calendar icon to pick a date.
                </div>
              </div>
            )}
            <div>
                <label style={{ fontWeight: 500, fontSize: 12, display: 'block', marginBottom: 6 }}>{scheduleTimingLabel}</label>
                <div style={{ position: 'relative' }}>
                  <input
                    ref={scheduleTimeInputRef}
                    type="time"
                    value={timeValue}
                    onChange={e => setTimeValue(e.target.value)}
                    style={{ ...modalInputStyle, paddingRight: 42 }}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      if (scheduleTimeInputRef.current?.showPicker) {
                        scheduleTimeInputRef.current.showPicker();
                      } else {
                        scheduleTimeInputRef.current?.focus();
                      }
                    }}
                    style={{
                      position: 'absolute',
                      right: 8,
                      top: '50%',
                      transform: 'translateY(-50%)',
                      border: 'none',
                      background: 'transparent',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer',
                      padding: 4,
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                    aria-label="Open time picker"
                    title="Open time picker"
                  >
                    <CalendarClock size={16} />
                  </button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                  {scheduleTimingHelper}
                </div>
              </div>
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 24 }}>
          <button onClick={() => setSchedulerOpen(false)} style={secondaryBtn}>Cancel</button>
          <button onClick={handleScheduleSave} style={primaryBtn}>Save</button>
        </div>
      </Modal>

      <Modal open={restoreOpen} onClose={() => !restoreBusy && setRestoreOpen(false)} title="Restore Version" size="md">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Restoring a version updates this project to a previously captured snapshot state. Please confirm before proceeding.
          </div>

          {restoreLoading ? (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading available snapshots...</div>
          ) : restoreSnapshots.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>No source snapshots found for this project yet.</div>
          ) : (
            <>
              <div>
                <label style={{ ...LABEL, marginBottom: 6 }}>Snapshot Version</label>
                <select
                  value={selectedRestoreSnapshotId}
                  onChange={(e) => setSelectedRestoreSnapshotId(e.target.value)}
                  style={{ ...INPUT, cursor: 'pointer' }}
                >
                  {restoreSnapshots.map((row) => {
                    const sid = String(row?.snapshot_id || '');
                    const created = row?.created_at ? new Date(row.created_at).toLocaleString() : 'Unknown time';
                    const format = String(row?.intermediate_format || 'sml').toUpperCase();
                    const origin = String(row?.snapshot_origin || row?.stage || 'snapshot').toUpperCase();
                    return (
                      <option key={sid} value={sid}>
                        {sid.slice(0, 12)}... | {origin} | {format} | {created}
                      </option>
                    );
                  })}
                </select>
              </div>

              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                <input
                  type="checkbox"
                  checked={useCurrentRestoreDetails}
                  onChange={(e) => setUseCurrentRestoreDetails(e.target.checked)}
                />
                Use current project connector/runtime details for this restored sync instance
              </label>
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                Restore will create a new run with source from repository snapshot and deploy using selected config details.
              </div>
            </>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
            <button onClick={openCompareModal} disabled={restoreBusy || restoreLoading || !selectedRestoreSnapshotId} style={secondaryBtn}>Compare</button>
            <button onClick={() => setRestoreOpen(false)} disabled={restoreBusy} style={secondaryBtn}>Cancel</button>
            <button
              onClick={handleRestoreVersion}
              disabled={restoreBusy || restoreLoading || !selectedRestoreSnapshotId}
              style={primaryBtn}
            >
              {restoreBusy ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <RotateCcw size={13} />} Restore
            </button>
          </div>
        </div>
      </Modal>

      <Modal
        open={compareOpen}
        onClose={() => !compareBusy && setCompareOpen(false)}
        title="Compare Snapshot States"
        size="lg"
        allowMaximize
        isMaximized={compareMaximized}
        onToggleMaximize={() => setCompareMaximized((prev) => !prev)}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Compare two source snapshots to verify rollback accuracy. If total changes is 0, states are identical.
          </div>

          {compareLoading ? (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading snapshots...</div>
          ) : compareSnapshots.length < 2 ? (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Need at least two source snapshots to compare.</div>
          ) : (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={{ ...LABEL, marginBottom: 6 }}>From Snapshot</label>
                  <select value={compareFromSnapshotId} onChange={(e) => setCompareFromSnapshotId(e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
                    {compareSnapshots.map((row) => {
                      const sid = String(row?.snapshot_id || '');
                      const created = row?.created_at ? new Date(row.created_at).toLocaleString() : 'Unknown time';
                      const origin = String(row?.snapshot_origin || row?.stage || 'snapshot').toUpperCase();
                      return (
                        <option key={`from-${sid}`} value={sid}>{sid.slice(0, 12)}... | {origin} | {created}</option>
                      );
                    })}
                  </select>
                </div>
                <div>
                  <label style={{ ...LABEL, marginBottom: 6 }}>To Snapshot</label>
                  <select value={compareToSnapshotId} onChange={(e) => setCompareToSnapshotId(e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
                    {compareSnapshots.map((row) => {
                      const sid = String(row?.snapshot_id || '');
                      const created = row?.created_at ? new Date(row.created_at).toLocaleString() : 'Unknown time';
                      const origin = String(row?.snapshot_origin || row?.stage || 'snapshot').toUpperCase();
                      return (
                        <option key={`to-${sid}`} value={sid}>{sid.slice(0, 12)}... | {origin} | {created}</option>
                      );
                    })}
                  </select>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[selectedCompareFromSnapshot, selectedCompareToSnapshot].map((snapshot, index) => (
                  <div
                    key={index === 0 ? 'selected-from-meta' : 'selected-to-meta'}
                    style={{ border: '1px solid var(--border-main)', borderRadius: 10, padding: 12, background: 'var(--bg-surface)' }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
                      {index === 0 ? 'From Snapshot Details' : 'To Snapshot Details'}
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
                      <InfoRow label="ID" value={String(snapshot?.snapshot_id || '-').slice(0, 18) || '-'} mono />
                      <InfoRow label="Origin" value={String(snapshot?.snapshot_origin || snapshot?.stage || '-').toUpperCase()} />
                      <InfoRow label="Role" value={String(snapshot?.role || snapshot?.system_role || '-').toUpperCase()} />
                      <InfoRow label="Format" value={String(snapshot?.intermediate_format || '-').toUpperCase()} />
                      <InfoRow label="Connector" value={String(snapshot?.connector_type || snapshot?.connector || '-')} />
                      <InfoRow label="Target" value={String(snapshot?.target_id || '-')} />
                      <InfoRow label="Group" value={String(snapshot?.snapshot_group_id || '-').slice(0, 18) || '-'} mono />
                      <InfoRow label="Created" value={snapshot?.created_at ? new Date(snapshot.created_at).toLocaleString() : '-'} />
                    </div>
                  </div>
                ))}
              </div>

              {compareResult && (
                <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, padding: 12, background: 'var(--bg-surface)' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 10 }}>
                    <div style={{ fontSize: 12 }}><strong>Exact Match:</strong> {compareResult?.exact_match ? 'Yes' : 'No'}</div>
                    <div style={{ fontSize: 12 }}><strong>Added:</strong> {Number(compareResult?.summary?.added || 0)}</div>
                    <div style={{ fontSize: 12 }}><strong>Removed:</strong> {Number(compareResult?.summary?.removed || 0)}</div>
                    <div style={{ fontSize: 12 }}><strong>Modified:</strong> {Number(compareResult?.summary?.modified || 0)}</div>
                  </div>

                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>Sample Changes</div>
                  <div style={{ maxHeight: 220, overflow: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
                    {(Array.isArray(compareResult?.changes) && compareResult.changes.length > 0) ? (
                      compareResult.changes.map((item, idx) => (
                        <div
                          key={`chg-${idx}`}
                          style={{
                            padding: '8px 10px',
                            borderBottom: idx === compareResult.changes.length - 1 ? 'none' : '1px solid var(--border-main)',
                            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                            fontSize: 11,
                            color: 'var(--text-secondary)',
                            display: 'flex',
                            gap: 8,
                          }}
                        >
                          <span style={{ minWidth: 62, textTransform: 'uppercase' }}>{String(item?.type || 'modified')}</span>
                          <span>{String(item?.path || '$')}</span>
                        </div>
                      ))
                    ) : (
                      <div style={{ padding: 10, fontSize: 12, color: 'var(--text-tertiary)' }}>No visible changes.</div>
                    )}
                  </div>

                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginTop: 12, marginBottom: 6 }}>
                    Side-by-Side State View
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 8 }}>
                    Changed lines are highlighted inside each pane: green = added (right), red = removed (left), yellow = modified (both).
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
                      <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-main)', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {compareLeftLabel}
                      </div>
                      <div style={{ maxHeight: 320, overflow: 'auto', background: 'var(--bg-primary)' }}>
                        {renderStateLines(compareResult?.from_state || {}).map((line, idx) => {
                          const diffType = getLineDiffType(line.path, 'from', compareResult?.changes || []);
                          const diffStyle = getLineHighlightStyle(diffType);
                          return (
                            <div
                              key={`from-line-${idx}-${line.path}`}
                              style={{
                                padding: '2px 10px',
                                fontSize: 11,
                                lineHeight: 1.45,
                                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                                color: 'var(--text-secondary)',
                                whiteSpace: 'pre',
                                ...(diffStyle || {}),
                              }}
                              title={line.path || '$'}
                            >
                              {line.text}
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
                      <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border-main)', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {compareRightLabel}
                      </div>
                      <div style={{ maxHeight: 320, overflow: 'auto', background: 'var(--bg-primary)' }}>
                        {renderStateLines(compareResult?.to_state || {}).map((line, idx) => {
                          const diffType = getLineDiffType(line.path, 'to', compareResult?.changes || []);
                          const diffStyle = getLineHighlightStyle(diffType);
                          return (
                            <div
                              key={`to-line-${idx}-${line.path}`}
                              style={{
                                padding: '2px 10px',
                                fontSize: 11,
                                lineHeight: 1.45,
                                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                                color: 'var(--text-secondary)',
                                whiteSpace: 'pre',
                                ...(diffStyle || {}),
                              }}
                              title={line.path || '$'}
                            >
                              {line.text}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  </div>

                </div>
              )}
            </>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
            <button onClick={() => setCompareOpen(false)} disabled={compareBusy} style={secondaryBtn}>Close</button>
            <button
              onClick={handleCompareSnapshots}
              disabled={compareBusy || compareLoading || !compareFromSnapshotId || !compareToSnapshotId || compareFromSnapshotId === compareToSnapshotId}
              style={primaryBtn}
            >
              {compareBusy ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <FileCode2 size={13} />} Compare
            </button>
          </div>
        </div>
      </Modal>

      <Modal open={manualSnapOpen} onClose={() => !manualSnapBusy && setManualSnapOpen(false)} title="Capture Manual Snapshot" size="md">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={{ ...LABEL, marginBottom: 6 }}>Snapshot Label (optional)</label>
            <input
              value={manualSnapLabel}
              onChange={(e) => setManualSnapLabel(e.target.value)}
              placeholder="e.g. before-q2-model-tuning"
              style={INPUT}
            />
          </div>

          <div>
            <label style={{ ...LABEL, marginBottom: 6 }}>Format</label>
            <select
              value={manualSnapFormat}
              onChange={(e) => setManualSnapFormat(e.target.value)}
              style={{ ...INPUT, cursor: 'pointer' }}
            >
              <option value="">Use project format</option>
              <option value="sml">SML</option>
              <option value="osi">OSI</option>
            </select>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={manualSnapIncludeSource}
                onChange={(e) => setManualSnapIncludeSource(e.target.checked)}
              />
              Include source state
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <input
                type="checkbox"
                checked={manualSnapIncludeTargets}
                onChange={(e) => setManualSnapIncludeTargets(e.target.checked)}
                disabled={targetCount <= 0}
              />
              Include target states ({targetCount})
            </label>
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
            <button onClick={() => setManualSnapOpen(false)} disabled={manualSnapBusy} style={secondaryBtn}>Cancel</button>
            <button
              onClick={handleManualSnapshotCapture}
              disabled={manualSnapBusy}
              style={primaryBtn}
            >
              {manualSnapBusy ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Camera size={13} />} Capture
            </button>
          </div>
        </div>
      </Modal>




      <GlobalConfigModal open={globalOpen} onClose={() => setGlobalOpen(false)} />
    </div>
  );
}

function MappingPreviewPanel({ mappings = [], loading = false, error = '' }) {
  if (loading) {
    return (
      <div style={{ marginBottom: 16, border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 14, fontSize: 12, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />
        Loading saved mappings...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ marginBottom: 16, border: '1px solid var(--color-error)', borderRadius: 10, background: 'var(--color-error-bg)', padding: 14, fontSize: 12, color: 'var(--text-primary)' }}>
        {error}
      </div>
    );
  }

  if (!Array.isArray(mappings) || mappings.length === 0) {
    return null;
  }

  const collisionCount = mappings.reduce((count, mapping) => {
    const tableCollision = mapping?.collision_detected ? 1 : 0;
    const columnCollisions = Array.isArray(mapping?.columns)
      ? mapping.columns.filter((column) => column?.collision_detected).length
      : 0;
    return count + tableCollision + columnCollisions;
  }, 0);

  return (
    <div style={{ marginBottom: 16, border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Saved Mappings</div>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 4 }}>
          {mappings.length} tables
        </span>
        {collisionCount > 0 && (
          <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent-orange)', background: 'rgba(245, 158, 11, 0.14)', padding: '2px 8px', borderRadius: 4 }}>
            {collisionCount} collisions resolved
          </span>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {mappings.map((mapping) => (
          <div key={mapping.id || mapping.source_path || mapping.source} style={{ border: '1px solid var(--border-main)', borderRadius: 8, padding: 12, background: 'var(--bg-main)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>{mapping.source}</div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>{mapping.target}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {mapping.collision_detected && (
                  <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--accent-orange)' }}>COLLISION</span>
                )}
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>{mapping.status || 'saved'}</span>
              </div>
            </div>

            {Array.isArray(mapping.columns) && mapping.columns.length > 0 && (
              <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
                {mapping.columns.slice(0, 6).map((column) => (
                  <div key={column.source_path || column.source} style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr auto', gap: 8, alignItems: 'center', fontSize: 11 }}>
                    <span style={{ color: 'var(--text-secondary)' }}>{column.source}</span>
                    <span style={{ color: 'var(--text-tertiary)' }}>{'->'}</span>
                    <span style={{ color: 'var(--text-primary)' }}>{column.target}</span>
                    <span style={{ color: column.collision_detected ? 'var(--accent-orange)' : 'var(--text-tertiary)', fontSize: 10 }}>
                      {column.collision_detected ? 'COLLISION' : (column.type || '')}
                    </span>
                  </div>
                ))}
                {mapping.columns.length > 6 && (
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    +{mapping.columns.length - 6} more columns
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function FormEditor({
  value,
  onChange,
  fabricAccounts = [],
  fabricWorkspaces = [],
  fabricLoading = false,
  onRefreshFabricWorkspaces,
  targetFabricAccounts = [],
  targetFabricWorkspaces = [],
  targetFabricLoading = false,
  onRefreshTargetFabricWorkspaces,
  databricksAccounts = [],
}) {
  const patch = (k, v) => onChange(prev => ({ ...prev, [k]: v }));
  const [overridePbixPath, setOverridePbixPath] = useState(false);
  const uploadedPbixPath = String(value.pbix_uploaded_path || '').trim();
  const pbixPathLocked = value.source_type === 'pbix' && Boolean(uploadedPbixPath) && !overridePbixPath;

  useEffect(() => {
    if (value.source_type !== 'pbix') {
      setOverridePbixPath(false);
    }
  }, [value.source_type]);

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: 4, paddingBottom: 320, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label style={LABEL}>Source Type</label>
          <select value={value.source_type} onChange={e => patch('source_type', e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
            <option value="pbix">pbix</option>
            <option value="fabric">fabric</option>
            <option value="snowflake">snowflake</option>
            <option value="databricks">databricks</option>
          </select>
        </div>
        <div>
          <label style={LABEL}>Target Connector</label>
          <select value={value.target_type} onChange={e => patch('target_type', e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
            <option value="snowflake">snowflake</option>
            <option value="fabric">fabric</option>
            <option value="databricks">databricks</option>
          </select>
        </div>
      </div>

      <div>
        <label style={LABEL}>Intermediate Format</label>
        <select value={value.output_format} onChange={e => patch('output_format', e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
          <option value="osi">OSI (Open Semantic Interchange)</option>
          <option value="sml">SML</option>
        </select>
      </div>

      {/* ── Source Configuration ── */}
      <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, padding: 16, background: 'var(--bg-surface)' }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
          1. Source Configuration
          <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--accent-blue)', marginLeft: 8 }}>
            {value.source_type === 'fabric' ? 'Microsoft Fabric' : value.source_type === 'snowflake' ? 'Snowflake' : value.source_type === 'databricks' ? 'Databricks' : 'PBIX File'}
          </span>
        </div>

      {value.source_type === 'fabric' && (
        <>
          <div style={{ marginBottom: 12 }}>
            <label style={LABEL}>Fabric Account</label>
            <select
              value={value.identity_id}
              onChange={e => patch('identity_id', e.target.value)}
              style={{ ...INPUT, cursor: 'pointer' }}
            >
              <option value="">Select account</option>
              {fabricAccounts.map(acc => (
                <option key={acc.id} value={acc.id}>
                  {acc.tag} ({acc.identity_email || acc.id})
                </option>
              ))}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
              Select an authenticated identity to use for discovery and synchronization.
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <label style={{ ...LABEL, margin: 0 }}>Fabric Workspace</label>
              <div style={{ flex: 1 }} />
              <button
                type="button"
                onClick={onRefreshFabricWorkspaces}
                disabled={fabricLoading || !value.identity_id}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-tertiary)',
                  cursor: fabricLoading || !value.identity_id ? 'not-allowed' : 'pointer',
                  fontSize: 11,
                  padding: 0,
                }}
              >
                {fabricLoading ? 'Loading...' : 'Refresh'}
              </button>
            </div>
            {fabricWorkspaces.length > 0 ? (
              <SearchableSelect
                items={fabricWorkspaces}
                displayKey="name"
                valueKey="id"
                value={value.workspace_id}
                placeholder="Choose a workspace"
                onChange={(ws) => {
                  const wsId = typeof ws === 'object' ? (ws?.id || ws?.workspace_id || '') : ws;
                  patch('workspace_id', String(wsId));
                }}
              />
            ) : (
              <input value={value.workspace_id} onChange={e => patch('workspace_id', e.target.value)} style={INPUT} placeholder="fabric workspace id" />
            )}
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
              Pick one workspace here. Step 3 will show models from this workspace only.
            </div>
          </div>
        </>
      )}

      {value.source_type === 'snowflake' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <label style={LABEL}>Database</label>
            <input value={value.database} onChange={e => patch('database', e.target.value)} style={INPUT} placeholder="ANALYTICS_DB" />
          </div>
          <div>
            <label style={LABEL}>Schema</label>
            <input value={value.schema} onChange={e => patch('schema', e.target.value)} style={INPUT} placeholder="PUBLIC" />
          </div>
        </div>
      )}

      {value.source_type === 'pbix' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <label style={{ ...LABEL, margin: 0 }}>PBIX File Path</label>
              {uploadedPbixPath && (
                <button
                  type="button"
                  onClick={() => setOverridePbixPath(prev => !prev)}
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--accent-blue)',
                    cursor: 'pointer',
                    fontSize: 11,
                    fontWeight: 600,
                    padding: 0,
                  }}
                >
                  {overridePbixPath ? 'Use uploaded path' : 'Edit manually'}
                </button>
              )}
            </div>
            <input
              value={value.pbix_path || ''}
              onChange={e => patch('pbix_path', e.target.value)}
              style={INPUT}
              readOnly={pbixPathLocked}
              placeholder="C:/models/Finance.pbix"
            />
            {uploadedPbixPath && (
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                {pbixPathLocked
                  ? 'Using uploaded file path from project creation. Click Edit manually to override.'
                  : 'Manual override enabled. You can point this to a permanent local/network path.'}
              </div>
            )}
          </div>
          <div>
            <label style={LABEL}>PBIX Folder (optional)</label>
            <input
              value={value.pbix_folder || ''}
              onChange={e => patch('pbix_folder', e.target.value)}
              style={INPUT}
              placeholder="C:/models"
            />
          </div>
        </div>
      )}
      </div>

      {/* ── Target Configuration ── */}
      <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, padding: 16, background: 'var(--bg-surface)', overflow: 'visible', position: 'relative', zIndex: 5 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
          2. Target Configuration
          <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--accent-blue)', marginLeft: 8 }}>
            {value.target_type === 'fabric' ? 'Microsoft Fabric' : value.target_type === 'snowflake' ? 'Snowflake' : 'Databricks'}
          </span>
        </div>

      {value.target_type === 'snowflake' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <label style={LABEL}>Target Database</label>
            <input value={value.target_database} onChange={e => patch('target_database', e.target.value)} style={INPUT} placeholder="ANALYTICS_DB" />
          </div>
          <div>
            <label style={LABEL}>Target Schema</label>
            <input value={value.target_schema} onChange={e => patch('target_schema', e.target.value)} style={INPUT} placeholder="PUBLIC" />
          </div>
        </div>
      )}

      {value.target_type === 'fabric' && (
        <>
          <div style={{ marginBottom: 12 }}>
            <label style={LABEL}>Target Fabric Account</label>
            <select
              value={value.target_identity_id}
              onChange={e => patch('target_identity_id', e.target.value)}
              style={{ ...INPUT, cursor: 'pointer' }}
            >
              <option value="">Select target account</option>
              {targetFabricAccounts.map(acc => (
                <option key={acc.id} value={acc.id}>
                  {acc.tag} ({acc.identity_email || acc.id})
                </option>
              ))}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
              Select the Fabric identity to use for deploying the semantic model.
            </div>
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <label style={{ ...LABEL, margin: 0 }}>Target Workspace</label>
              <div style={{ flex: 1 }} />
              <button
                type="button"
                onClick={onRefreshTargetFabricWorkspaces}
                disabled={targetFabricLoading || !value.target_identity_id}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-tertiary)',
                  cursor: targetFabricLoading || !value.target_identity_id ? 'not-allowed' : 'pointer',
                  fontSize: 11,
                  padding: 0,
                }}
              >
                {targetFabricLoading ? 'Loading...' : 'Refresh'}
              </button>
            </div>
            {targetFabricWorkspaces.length > 0 ? (
              <SearchableSelect
                items={targetFabricWorkspaces}
                displayKey="name"
                valueKey="id"
                value={value.target_workspace_id}
                placeholder="Choose target workspace"
                onChange={(ws) => {
                  const wsId = typeof ws === 'object' ? (ws?.id || ws?.workspace_id || '') : ws;
                  patch('target_workspace_id', String(wsId));
                }}
              />
            ) : (
              <input value={value.target_workspace_id} onChange={e => patch('target_workspace_id', e.target.value)} style={INPUT} placeholder="target workspace id" />
            )}
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
              The workspace where the semantic model will be deployed.
            </div>
          </div>
        </>
      )}

      {value.target_type === 'databricks' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {databricksAccounts.length > 0 && (
            <div>
              <label style={LABEL}>Target Databricks Account</label>
              <select
                value={value.target_identity_id || ''}
                onChange={e => patch('target_identity_id', e.target.value)}
                style={{ ...INPUT, cursor: 'pointer' }}
              >
                <option value="">Use global defaults</option>
                {databricksAccounts.map(acc => (
                  <option key={acc.id} value={acc.id}>
                    {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
                  </option>
                ))}
              </select>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                Select the Databricks identity to use for deployment.
              </div>
            </div>
          )}
          {databricksAccounts.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: 12, borderRadius: 8, background: 'var(--bg-input)', border: '1px solid var(--border-main)' }}>
              This target will use global connection defaults from Settings.
            </div>
          )}
        </div>
      )}
      </div>

      <div>
        <label style={LABEL}>Allow Models (comma-separated)</label>
        <input value={value.allow_models} onChange={e => patch('allow_models', e.target.value)} style={INPUT} placeholder="SalesModel, FinanceModel" />
      </div>
      <div>
        <label style={LABEL}>Block Models (comma-separated)</label>
        <input value={value.block_models} onChange={e => patch('block_models', e.target.value)} style={INPUT} placeholder="LegacyModel, TestModel" />
      </div>

      <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, padding: 12, background: 'var(--bg-surface)' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>Mapping Options</div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-secondary)', marginBottom: 8 }}>
          <input
            type="checkbox"
            checked={Boolean(value.auto_relationships)}
            onChange={e => patch('auto_relationships', e.target.checked)}
          />
          Auto-detect relationships
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={Boolean(value.generate_descriptions)}
            onChange={e => patch('generate_descriptions', e.target.checked)}
          />
          Generate AI descriptions (tables and fields)
        </label>
      </div>

      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
        Fields left blank inherit ghost defaults from global config where applicable.
      </div>
    </div>
  );
}

function ModeButton({ active, onClick, icon, ariaLabel }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      title={ariaLabel}
      style={{
        width: 38, height: 34,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        padding: 0, border: 'none', cursor: 'pointer',
        background: active ? 'var(--accent-blue)' : 'transparent',
        color: active ? '#fff' : 'var(--text-secondary)',
        fontSize: 12, fontWeight: 600,
      }}
    >
      {icon}
    </button>
  );
}

function ScheduleOptionCard({ active, title, description, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        border: `1px solid ${active ? 'var(--accent-blue)' : 'var(--border-main)'}`,
        borderRadius: 10,
        padding: 12,
        background: active ? 'rgba(59, 130, 246, 0.1)' : 'var(--bg-surface)',
        color: 'var(--text-primary)',
        cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 700 }}>{title}</div>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4, lineHeight: 1.45 }}>{description}</div>
    </button>
  );
}

function InfoRow({ label, value, mono = false }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 12, color: 'var(--text-primary)', fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' : 'inherit', wordBreak: 'break-word' }}>
        {value || '-'}
      </div>
    </div>
  );
}

const modalInputStyle = {
  width: '100%',
  padding: 8,
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  fontSize: 13,
  background: 'var(--bg-input)',
  color: 'var(--text-primary)',
  boxSizing: 'border-box',
};

const primaryBtn = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 14px', borderRadius: 8, border: 'none',
  background: 'var(--accent-blue)', color: '#fff',
  fontSize: 13, fontWeight: 600, cursor: 'pointer',
};

const secondaryBtn = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 14px', borderRadius: 8,
  border: '1px solid var(--border-main)',
  background: 'transparent', color: 'var(--text-secondary)',
  fontSize: 13, fontWeight: 600, cursor: 'pointer',
};
