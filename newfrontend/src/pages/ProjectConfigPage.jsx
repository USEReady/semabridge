import { useState, useEffect } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, Save, Play, CalendarClock, Settings2, FileCode2, SlidersHorizontal, Loader2 } from 'lucide-react';

import GlobalConfigModal from '../components/projects/GlobalConfigModal';
import SearchableSelect from '../components/common/SearchableSelect';
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
  const isInvalidProjectId = !id || id === 'null' || id === 'undefined';

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [project, setProject] = useState(null);
  const [allProjects, setAllProjects] = useState([]);
  const [selectedPresetProjectId, setSelectedPresetProjectId] = useState(null);
  const [yamlPath, setYamlPath] = useState('');
  const [saveInfo, setSaveInfo] = useState('');

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
        hydrateFormFromYaml(cfg?.config_yaml || '', p);
      } catch {
        setProject(null);
      } finally {
        setLoading(false);
      }
    })();
  }, [id, isInvalidProjectId, navigate]);

  const hydrateFormFromYaml = (yaml, projectMeta) => {
    const get = (re, fallback = '') => (yaml.match(re)?.[1] ?? fallback).trim();
    const getBlock = (key) => yaml.match(new RegExp(`^${key}:\\s*$([\\s\\S]*?)(?=^\\S|\\Z)`, 'm'))?.[1] ?? '';
    const getFromBlock = (block, key, fallback = '') => (
      block.match(new RegExp(`^\\s{2}${key}:\\s*(.+)$`, 'm'))?.[1] ?? fallback
    ).trim().replace(/["']/g, '');
    const getListFromBlock = (block, key) => {
      const listBlock = block.match(new RegExp(`^\\s{2}${key}:\\s*$([\\s\\S]*?)(?=^\\s{2}\\S|^\\S|\\Z)`, 'm'))?.[1] ?? '';
      return listBlock
        .split('\n')
        .map(line => line.trim())
        .filter(line => line.startsWith('-'))
        .map(line => line.replace(/^[-\s]+/, '').replace(/["']/g, ''));
    };

    const sourceBlock = getBlock('source');
    const targetBlock = getBlock('target');
    const uiBlock = getBlock('ui');
    const optionsBlock = getBlock('options');
    const sourceModels = getListFromBlock(sourceBlock, 'models');
    const sourceModel = getFromBlock(sourceBlock, 'model', get(/^\s*model:\s*(.+)$/m, '')).replace(/['"]/g, '');
    const allowModelValue = sourceModels.length
      ? sourceModels.join(', ')
      : (sourceModel && sourceModel !== '*' ? sourceModel : '');
    const legacyTarget = get(/^target_type:\s*(.+)$/m, projectMeta?.target_type || '').replace(/["']/g, '');
    const targetConnector = getFromBlock(targetBlock, 'type', ['fabric', 'snowflake'].includes(legacyTarget) ? legacyTarget : 'snowflake');
    const outputFormat = getFromBlock(uiBlock, 'output_format', ['fabric', 'snowflake'].includes(legacyTarget) ? 'osi' : (legacyTarget || 'osi'));

    setConfigForm({
      source_type: getFromBlock(sourceBlock, 'type', get(/^source_type:\s*(.+)$/m, projectMeta?.source || 'fabric')).replace(/["']/g, ''),
      target_type: targetConnector,
      output_format: outputFormat,
      workspace_id: getFromBlock(sourceBlock, 'workspace_id', get(/^\s*workspace_id:\s*(.+)$/m, '')).replace(/["']/g, ''),
      database: getFromBlock(sourceBlock, 'database', get(/^\s*database:\s*(.+)$/m, '')).replace(/["']/g, ''),
      schema: getFromBlock(sourceBlock, 'schema', get(/^\s*schema:\s*(.+)$/m, '')).replace(/["']/g, ''),
      target_database: getFromBlock(targetBlock, 'database', '').replace(/["']/g, ''),
      target_schema: getFromBlock(targetBlock, 'schema', '').replace(/["']/g, ''),
      allow_models: allowModelValue,
      block_models: getListFromBlock(optionsBlock, 'exclude_model').join(', '),
    });
  };

  const buildYamlFromForm = () => {
    const allow = configForm.allow_models.split(',').map(s => s.trim()).filter(Boolean);
    const block = configForm.block_models.split(',').map(s => s.trim()).filter(Boolean);

    const lines = [
      `project_name: "${project?.name || ''}"`,
    ];

    lines.push('source:');
    lines.push(`  type: ${configForm.source_type}`);

    if (configForm.source_type === 'fabric') {
      lines.push(`  workspace_id: "${configForm.workspace_id || ''}"`);
    }
    if (configForm.source_type === 'snowflake') {
      lines.push(`  database: "${configForm.database || ''}"`);
      lines.push(`  schema: "${configForm.schema || ''}"`);
    }

    if (allow.length > 1) {
      lines.push('  models:');
      allow.forEach(v => lines.push(`    - "${v}"`));
    } else if (allow.length === 1) {
      lines.push(`  model: "${allow[0]}"`);
    } else {
      lines.push('  model: "*"');
    }

    lines.push('target:');
    lines.push(`  type: ${configForm.target_type}`);
    if (configForm.target_type === 'snowflake') {
      if (configForm.target_database) lines.push(`  database: "${configForm.target_database}"`);
      if (configForm.target_schema) lines.push(`  schema: "${configForm.target_schema}"`);
    }

    lines.push('ui:');
    lines.push(`  output_format: "${configForm.output_format}"`);

    if (block.length) {
      lines.push('options:');
      lines.push('  exclude_model:');
      block.forEach(v => lines.push(`    - "${v}"`));
    }

    return lines.join('\n');
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveInfo('');
    try {
      const nextYaml = viewMode === 'yaml' ? yamlText : buildYamlFromForm();
      const response = await api.saveProjectConfig(id, nextYaml);
      setYamlText(nextYaml);
      if (response?.yaml_path) setYamlPath(response.yaml_path);
      if (Array.isArray(response?.warnings) && response.warnings.length) {
        setSaveInfo(response.warnings.join(' | '));
      } else {
        setSaveInfo('Config saved. Project semabridge.yaml updated.');
      }
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
      setSaveInfo(`Preset copied from project ${selectedPreset?.name || fromProjectId}.`);
    } catch (err) {
      setSaveInfo(`Failed to load preset: ${err?.message || 'Unknown error'}`);
    }
  };

  const handleRunNow = async () => {
    await handleSave();
    await api.runProjectNow(id);
  };

  const handleCreateJob = async () => {
    await handleSave();
    navigate('/jobs');
  };

  const handleViewModeChange = (nextMode) => {
    if (nextMode === viewMode) return;

    if (nextMode === 'yaml') {
      setYamlText(buildYamlFromForm());
    } else {
      hydrateFormFromYaml(yamlText, project);
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
                <textarea
                  value={yamlText}
                  onChange={e => setYamlText(e.target.value)}
                  style={{
                    width: '100%', height: '100%', background: 'var(--bg-input)',
                    color: 'var(--text-primary)', border: '1px solid var(--border-main)', borderRadius: 8,
                    padding: 12, fontSize: 12, lineHeight: 1.5,
                    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                    outline: 'none', boxSizing: 'border-box',
                  }}
                />
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
        <button onClick={handleSave} disabled={saving} style={secondaryBtn}>
          {saving ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={13} />}
          Save Config
        </button>
        <button onClick={handleRunNow} style={primaryBtn}>
          <Play size={13} /> Sync Now
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