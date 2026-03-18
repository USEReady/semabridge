import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Save, Play, CalendarClock, Settings2, FileCode2, SlidersHorizontal, Loader2 } from 'lucide-react';
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';
import CodeMirror from '@uiw/react-codemirror';
import { yaml as yamlLang } from '@codemirror/lang-yaml';

import GlobalConfigModal from '../components/projects/GlobalConfigModal';
import SearchableSelect from '../components/common/SearchableSelect';
import { useTheme } from '../context/ThemeProvider';
import { useLogs } from '../context/LogsContext';
import { api } from '../utils/api';

const INPUT = {
  display: 'block', width: '100%',
  background: 'var(--bg-input)', border: '1px solid var(--border-main)',
  borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px',
  fontSize: 13, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

const LABEL = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 };

/**
 * ProjectConfigPage — dedicated full page /projects/:id/config
 */
export default function ProjectConfigPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const { theme } = useTheme();
  const { addLog } = useLogs();
  const isInvalidProjectId = !id || id === 'null' || id === 'undefined';
  const yamlExtensions = useMemo(() => [yamlLang()], []);

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
    workspace_id: '',
    database: '',
    schema: '',
    target_database: '',
    target_schema: '',
    allow_models: '',
    block_models: '',
  });

  const [globalOpen, setGlobalOpen] = useState(false);

  useEffect(() => {
    const modeFromUrl = searchParams.get('mode');
    const storedMode = localStorage.getItem(`project_${id}_viewMode`);
    const preferred = modeFromUrl === 'yaml' || modeFromUrl === 'form'
      ? modeFromUrl
      : (storedMode === 'yaml' || storedMode === 'form' ? storedMode : 'form');
    setViewMode(preferred);
  }, [id, searchParams]);

  useEffect(() => {
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
        try {
          hydrateFormFromYaml(cfg?.config_yaml || '', p);
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
  }, [id, isInvalidProjectId, navigate]);

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

  const parseProjectYaml = (yaml, projectMeta) => {
    const raw = String(yaml || '').trim();
    const fallbackTarget = ['fabric', 'snowflake'].includes(projectMeta?.target_type) ? projectMeta.target_type : 'snowflake';
    const fallbackSource = projectMeta?.source || 'fabric';

    if (!raw) {
      return {
        tree: {},
        form: {
          source_type: fallbackSource,
          target_type: fallbackTarget,
          output_format: 'osi',
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
    const target = tree.target && typeof tree.target === 'object' ? tree.target : {};
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
        output_format: String(ui.output_format || 'osi'),
        workspace_id: String(source.workspace_id || ''),
        database: String(source.database || ''),
        schema: String(source.schema || ''),
        target_database: String(target.database || ''),
        target_schema: String(target.schema || ''),
        allow_models: allowModels.join(', '),
        block_models: blockedModels.join(', '),
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
      },
    };

    if (configForm.source_type === 'fabric') {
      nextTree.source.workspace_id = configForm.workspace_id || '';
      delete nextTree.source.database;
      delete nextTree.source.schema;
    } else if (configForm.source_type === 'snowflake') {
      nextTree.source.database = configForm.database || '';
      nextTree.source.schema = configForm.schema || '';
      delete nextTree.source.workspace_id;
    } else {
      delete nextTree.source.workspace_id;
      delete nextTree.source.database;
      delete nextTree.source.schema;
    }

    if (allow.length > 1) {
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

    const normalized = normalizeTreeForYaml(nextTree);
    return stringifyYaml(normalized, { lineWidth: 0 });
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveInfo('');
    try {
      let nextYaml = '';
      if (viewMode === 'yaml') {
        const parsed = parseProjectYaml(yamlText, project);
        setConfigTree(parsed.tree || {});
        setConfigForm(parsed.form);
        nextYaml = stringifyYaml(normalizeTreeForYaml(parsed.tree || {}), { lineWidth: 0 });
      } else {
        nextYaml = buildYamlFromForm();
        const parsed = parseProjectYaml(nextYaml, project);
        setConfigTree(parsed.tree || {});
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
    setSyncing(true);
    try {
      const saved = await handleSave();
      if (!saved) {
        addLog('error', 'Sync', 'Sync not started because config save/validation failed.');
        return;
      }

      const run = await api.runProjectNow(id);
      const status = String(run?.status || '').toLowerCase();

      if (status === 'success') {
        addLog('success', 'Sync', 'Sync completed successfully.');
        setSaveInfo('Sync completed successfully.');
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

  if (loading) {
    return <div style={{ padding: 36, color: 'var(--text-tertiary)', fontSize: 13 }}>Loading project configuration...</div>;
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

  const effectiveYaml = viewMode === 'yaml' ? yamlText : buildYamlFromForm();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <div style={{ padding: '18px 28px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <button
          onClick={() => navigate('/projects')}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
        >
          <ArrowLeft size={14} /> Projects
        </button>
        <span style={{ color: 'var(--border-main)' }}>|</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>{project.name}</div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Project Configuration</div>
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

      <div style={{ flex: 1, overflow: 'hidden', display: 'flex' }}>
        <div style={{ width: '58%', minWidth: 420, borderRight: '1px solid var(--border-main)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: 18, borderBottom: '1px solid var(--border-main)' }}>
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

          <div style={{ flex: 1, overflow: 'auto' }}>
            {viewMode === 'form' ? (
              <FormEditor value={configForm} onChange={setConfigForm} />
            ) : (
              <div style={{ padding: 12, height: '100%' }}>
                <div style={{ height: '100%', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden', background: 'var(--bg-input)', display: 'flex', flexDirection: 'column' }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '8px 10px',
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

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border-main)' }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Effective YAML Preview</div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>Includes current project-level configuration.</div>
          </div>
          <pre style={{
            margin: 0, padding: 18, flex: 1, overflow: 'auto', fontSize: 12, lineHeight: 1.55,
            color: 'var(--text-primary)', background: 'var(--bg-surface)',
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          }}>
            {effectiveYaml || '# Empty configuration'}
          </pre>
        </div>
      </div>

      <div style={{ padding: '14px 28px', borderTop: '1px solid var(--border-main)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button onClick={handleCreateJob} style={secondaryBtn}>
          <CalendarClock size={13} /> Create Job for Later
        </button>
        <button onClick={handleSave} disabled={saving || syncing} style={secondaryBtn}>
          {saving ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={13} />}
          Save Config
        </button>
        <button onClick={handleRunNow} disabled={saving || syncing} style={{ ...primaryBtn, opacity: (saving || syncing) ? 0.7 : 1, cursor: (saving || syncing) ? 'not-allowed' : 'pointer' }}>
          {syncing ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={13} />} {syncing ? 'Syncing…' : 'Sync Now'}
        </button>
      </div>

      <GlobalConfigModal open={globalOpen} onClose={() => setGlobalOpen(false)} />
    </div>
  );
}

function FormEditor({ value, onChange }) {
  const patch = (k, v) => onChange(prev => ({ ...prev, [k]: v }));

  return (
    <div style={{ padding: 18, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label style={LABEL}>Source Type</label>
          <select value={value.source_type} onChange={e => patch('source_type', e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
            <option value="fabric">fabric</option>
            <option value="snowflake">snowflake</option>
            <option value="databricks">databricks</option>
            <option value="postgresql">postgresql</option>
            <option value="salesforce">salesforce</option>
          </select>
        </div>
        <div>
          <label style={LABEL}>Target Connector</label>
          <select value={value.target_type} onChange={e => patch('target_type', e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
            <option value="snowflake">snowflake</option>
            <option value="fabric">fabric</option>
          </select>
        </div>
      </div>

      <div>
        <label style={LABEL}>Output Format</label>
        <select value={value.output_format} onChange={e => patch('output_format', e.target.value)} style={{ ...INPUT, cursor: 'pointer' }}>
          <option value="atscale">atscale</option>
          <option value="osi">osi</option>
          <option value="dbt">dbt</option>
          <option value="lookml">lookml</option>
        </select>
      </div>

      {value.source_type === 'fabric' && (
        <div>
          <label style={LABEL}>Workspace ID</label>
          <input value={value.workspace_id} onChange={e => patch('workspace_id', e.target.value)} style={INPUT} placeholder="fabric workspace id" />
        </div>
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