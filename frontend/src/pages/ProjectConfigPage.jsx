import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Save, Play, CalendarClock, CalendarDays, Settings2, FileCode2, SlidersHorizontal, Loader2, CheckCircle2 } from 'lucide-react';
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';
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
    const [cronValue, setCronValue] = useState('0 0 * * *');
  const [scheduleDate, setScheduleDate] = useState(() => toDateInputValue(new Date()));
    const [timeValue, setTimeValue] = useState('12:00');
    const [timezoneValue, setTimezoneValue] = useState('UTC');
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
    allow_models: '',
    block_models: '',
    auto_relationships: true,
    generate_descriptions: true,
  });
  const [fabricAccounts, setFabricAccounts] = useState([]);
  const [fabricWorkspaces, setFabricWorkspaces] = useState([]);
  const [fabricLoading, setFabricLoading] = useState(false);

  const [globalOpen, setGlobalOpen] = useState(false);
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
  const projectSyncStatus = String(
    (normalizedProjectId && projectStatusById?.[normalizedProjectId])
      || latestProjectRun?.status
      || (String(currentSyncId || '') === normalizedProjectId ? currentSyncStatus : '')
      || ''
  ).toLowerCase();
  const projectSyncProgress = Number(
    (normalizedProjectId && projectProgressById?.[normalizedProjectId])
      ?? latestProjectRun?.progress_pct
      ?? 0
  );
  const isProjectSyncing = projectSyncStatus === 'running'
    || (String(currentSyncId || '') === normalizedProjectId && currentSyncStatus === 'running');
  const isProjectSynced = !isProjectSyncing && projectSyncStatus === 'success';

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
          allow_models: '',
          block_models: '',
        },
      };
    }

    if (isLikelyBinaryOrGarbage(raw)) {
      throw new Error('Input is not valid semabridge.yaml content');
    }

    const parsed = parseYaml(raw);
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
        source_type: String(source.type || fallbackSource),
        target_type: String(target.type || fallbackTarget),
        output_format: String(ui.intermediate_format || ui.output_format || 'osi'),
        pbix_path: String(source.pbix_path || source.pbix_file_path || source.source_path || source.file_path || ''),
        pbix_folder: String(source.pbix_folder || ''),
        pbix_uploaded_path: String(projectMeta?.pbix_file_path || source.pbix_file_path || source.pbix_path || ''),
        identity_id: String(source.identity_id || ''),
        workspace_id: String(source.workspace_id || ''),
        database: String(source.database || ''),
        schema: String(source.schema || ''),
        target_database: String(target.database || ''),
        target_schema: String(target.schema || ''),
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
    } else {
      delete nextTree.target.database;
      delete nextTree.target.schema;
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
        const parsed = parseProjectYaml(nextYaml, project);
        setConfigTree(normalizeConfigTreeForApi(parsed.tree || {}));
        setConfigForm(parsed.form);
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
      setSaveInfo(`Invalid semabridge.yaml format: ${err?.message || 'Unable to parse YAML'}`);
      addLog('error', 'Project Config', `Save failed: ${err?.message || 'Invalid semabridge.yaml format'}`);
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

  const handleCreateJob = async () => {
    await handleSave();
    navigate('/jobs');
  };

  const handleScheduleSave = async () => {
    const scheduledTime = scheduleType === 'time' ? utcIsoFromLocalInputs(scheduleDate, timeValue) : '';
    const payload = {
      schedule_type: scheduleType,
      cron: scheduleType === 'cron' ? cronValue : '',
      date: scheduleType === 'time' ? scheduleDate || '' : '',
      time: scheduleType === 'time' ? timeValue || '' : '',
      scheduled_time: scheduledTime,
      timezone: timezoneValue,
    };

    try {
      const response = await api.saveProjectSchedule(id, payload);
      setSaveInfo(
        scheduleType === 'manual'
          ? (response?.message || 'Schedule cleared. Trigger remains on-demand.')
          : scheduleType === 'cron'
            ? `Schedule saved with cron "${cronValue}" (${timezoneValue}).`
            : `Schedule saved for ${scheduleDate || 'selected date'} at ${timeValue} (${timezoneValue}).`
      );
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
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', minHeight: 0 }}>
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

        <div style={{ display: 'flex', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
          <ModeButton active={viewMode === 'form'} onClick={() => handleViewModeChange('form')} icon={<SlidersHorizontal size={13} />} label="Form" />
          <ModeButton active={viewMode === 'yaml'} onClick={() => handleViewModeChange('yaml')} icon={<FileCode2 size={13} />} label="YAML" />
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
            {viewMode === 'form' ? (
              <FormEditor
                value={configForm}
                onChange={setConfigForm}
                fabricAccounts={fabricAccounts}
                fabricWorkspaces={fabricWorkspaces}
                fabricLoading={fabricLoading}
                onRefreshFabricWorkspaces={() => refreshFabricWorkspaces()}
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
                onClick={() => setScheduleType('cron')}
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
              <label style={{ fontWeight: 500, fontSize: 12 }}>Cron Expression</label>
              <input value={cronValue} onChange={e => setCronValue(e.target.value)} style={modalInputStyle} placeholder="0 0 * * *" />
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>e.g. 0 0 * * * (every day at midnight)</div>
            </div>
          )}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {scheduleType === 'time' && (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: 12, borderRadius: 8, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                Schedule regularly using the calendar below. This is frontend-only for now and does not create a backend scheduler yet.
              </div>
            )}

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
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




      <GlobalConfigModal open={globalOpen} onClose={() => setGlobalOpen(false)} />
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
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: 4, display: 'flex', flexDirection: 'column', gap: 14 }}>
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

      {value.source_type === 'fabric' && (
        <>
          <div>
            <label style={LABEL}>Fabric Account</label>
            <select
              value={value.identity_id}
              onChange={e => patch('identity_id', e.target.value)}
              style={{ ...INPUT, cursor: 'pointer' }}
            >
              <option value="">Select account</option>
              {fabricAccounts.map(acc => (
                <option key={acc.id} value={acc.id}>
                  {acc.tag || acc.identity_email || acc.id}
                </option>
              ))}
            </select>
          </div>

          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              <label style={{ ...LABEL, margin: 0 }}>Workspace ID</label>
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
              <select
                value={value.workspace_id}
                onChange={e => patch('workspace_id', e.target.value)}
                style={{ ...INPUT, cursor: 'pointer' }}
              >
                <option value="">Select workspace</option>
                {fabricWorkspaces.map(ws => (
                  <option key={ws.id || ws.workspace_id} value={ws.id || ws.workspace_id}>
                    {ws.name || ws.displayName || ws.id || ws.workspace_id}
                  </option>
                ))}
              </select>
            ) : (
              <input value={value.workspace_id} onChange={e => patch('workspace_id', e.target.value)} style={INPUT} placeholder="fabric workspace id" />
            )}
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

function ModeButton({ active, onClick, icon, label }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '7px 12px', border: 'none', cursor: 'pointer',
        background: active ? 'var(--accent-blue)' : 'transparent',
        color: active ? '#fff' : 'var(--text-secondary)',
        fontSize: 12, fontWeight: 600,
      }}
    >
      {icon} {label}
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
