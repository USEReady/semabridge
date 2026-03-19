/**
 * CreateProjectPage — 5-step wizard at /projects/new
 *
 * Step 1: Name + connectors
 * Step 2: Source + target config (with live discovery)
 * Step 3: Source browser (Fabric workspaces/models)
 * Step 4: Model mapping settings
 * Step 5: Schedule / run now
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowLeft, ArrowRight, Check, Search, X, Loader2,
  ChevronDown, ChevronRight, CheckSquare, Square,
} from 'lucide-react';
import { api } from '../utils/api';
import { useHPSearch } from '../hooks/useHPSearch';
import SearchableSelect from '../components/common/SearchableSelect';
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
  { value: 'postgresql', label: 'PostgreSQL', icon: '🐘' },
  { value: 'salesforce', label: 'Salesforce', icon: '☁️' },
];

const TARGET_CONNECTOR_TYPES = [
  { value: 'snowflake', label: 'Snowflake', icon: '❄️' },
  { value: 'fabric', label: 'Microsoft Fabric', icon: '🔷' },
];

const OUTPUT_FORMAT_TYPES = [
  { value: 'atscale', label: 'AtScale' },
  { value: 'osi', label: 'OSI (Open Schema Interface)' },
  { value: 'dbt', label: 'dbt (yaml)' },
  { value: 'lookml', label: 'LookML' },
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
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState('');
  const [runWarning, setRunWarning] = useState('');

  // Step 1
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [sourceConnector, setSourceConnector] = useState('fabric');
  const [targetConnector, setTargetConnector] = useState('snowflake');
  const [outputFormat, setOutputFormat] = useState('osi');
  const [configMode, setConfigMode] = useState('form');

  // Step 2
  const [fabricWorkspaceId, setFabricWorkspaceId] = useState('');
  const [snowflakeDatabase, setSnowflakeDatabase] = useState('');
  const [snowflakeSchema, setSnowflakeSchema] = useState('');
  const [targetDatabase, setTargetDatabase] = useState('');
  const [targetSchema, setTargetSchema] = useState('');
  const [domainHint, setDomainHint] = useState('');

  // Step 3
  const [workspaces, setWorkspaces] = useState([]);
  const [wsLoading, setWsLoading] = useState(false);
  const [expandedWs, setExpandedWs] = useState({});
  const [wsModels, setWsModels] = useState({}); // wsid → [{id, name}]
  const [selectedModels, setSelectedModels] = useState(new Set());
  const [selectedModelNameByKey, setSelectedModelNameByKey] = useState({});

  // Step 4
  const [autoRelationships, setAutoRelationships] = useState(true);
  const [includeHiddenFields, setIncludeHiddenFields] = useState(false);
  const [generateDescriptions, setGenerateDescriptions] = useState(true);

  // Step 5
  const [runNow, setRunNow] = useState(true);
  const [createdProject, setCreatedProject] = useState(null);

  /* ─── HP search for model browser ─── */
  const allModels = Object.entries(wsModels).flatMap(([wsid, models]) =>
    models.map(m => ({ ...m, wsid, _id: `${wsid}::${m.id}` }))
  );
  const { results: modelResults, query: modelQuery, setQuery: setModelQuery } = useHPSearch(
    allModels, ['name', 'description', 'wsid'], { idField: '_id' }
  );

  const selectedWorkspace = availableWorkspaces.find(ws => ws.id === fabricWorkspaceId)
    ?? availableWorkspaces.find(ws => ws.id === activeWorkspaceId)
    ?? activeWorkspace
    ?? null;

  const selectedModelNames = [...selectedModels]
    .map(modelKey => selectedModelNameByKey[modelKey])
    .filter(Boolean);

  useEffect(() => {
    if (sourceConnector !== 'fabric' || fabricWorkspaceId) return;

    const fallbackWorkspaceId = activeWorkspaceId || availableWorkspaces[0]?.id || '';
    if (fallbackWorkspaceId) setFabricWorkspaceId(fallbackWorkspaceId);
  }, [sourceConnector, fabricWorkspaceId, activeWorkspaceId, availableWorkspaces]);

  useEffect(() => {
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
    setExpandedWs({});
    setWsModels({});
    setModelQuery('');
  }, [fabricWorkspaceId, sourceConnector, setModelQuery]);

  /* ─── Load selected Fabric workspace models on step 3 ─── */
  useEffect(() => {
    if (step !== 3 || sourceConnector !== 'fabric' || !fabricWorkspaceId) {
      if (step === 3 && sourceConnector === 'fabric' && !fabricWorkspaceId) {
        setWorkspaces([]);
      }
      return;
    }

    const workspace = selectedWorkspace || { id: fabricWorkspaceId, name: fabricWorkspaceId };
    setWorkspaces([workspace]);
    setExpandedWs(prev => ({ ...prev, [fabricWorkspaceId]: true }));
    setWsLoading(true);
    api.discoverFabricModels(fabricWorkspaceId)
      .then(data => setWsModels({ [fabricWorkspaceId]: data ?? [] }))
      .catch(() => setWsModels({ [fabricWorkspaceId]: [] }))
      .finally(() => setWsLoading(false));
  }, [step, sourceConnector, fabricWorkspaceId, selectedWorkspace]);

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

    if (sourceConnector === 'fabric' && selectedModels.size > 0 && selectedModelNames.length === 0) {
      setCreateError('Selected models could not be resolved. Please reselect the model(s) and try again.');
      return;
    }

    setSaving(true);
    try {
      const source = buildSourceConfig();
      const target = buildTargetConfig();
      const payload = {
        name: name.trim(),
        description: description.trim() || undefined,
        source,
        target,
        config_yaml: buildConfigYaml(source, target),
      };
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
      if (runNow && projectId) {
        try {
          await api.runProjectNow(projectId);
        } catch (err) {
          setRunWarning(err?.message || 'Project created, but the first sync could not be started.');
        }
      } else if (runNow) {
        setRunWarning('Project created, but the first sync could not be started because no project id was returned.');
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
    }

    return source;
  };

  const buildTargetConfig = () => {
    const target = { type: targetConnector };

    if (targetConnector === 'snowflake') {
      if (targetDatabase.trim()) target.database = targetDatabase.trim();
      if (targetSchema.trim()) target.schema = targetSchema.trim();
    }

    return target;
  };

  const buildConfigYaml = (source, target) => {
    const lines = [`project_name: "${name.trim()}"`];
    if (description.trim()) lines.push(`description: "${escapeYamlString(description.trim())}"`);

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

    lines.push('target:');
    lines.push(`  type: ${target.type}`);
    if (target.database) lines.push(`  database: "${escapeYamlString(target.database)}"`);
    if (target.schema) lines.push(`  schema: "${escapeYamlString(target.schema)}"`);

    lines.push('ui:');
    lines.push(`  output_format: "${escapeYamlString(outputFormat)}"`);
    lines.push(`  editor_mode: "${escapeYamlString(configMode)}"`);
    if (domainHint.trim()) lines.push(`  domain_hint: "${escapeYamlString(domainHint.trim())}"`);

    if (selectedModels.size) {
      lines.push('selection:');
      lines.push('  model_ids:');
      [...selectedModels]
        .map(modelKey => modelKey.split('::')[1])
        .forEach(modelId => lines.push(`    - "${escapeYamlString(modelId)}"`));
    }

    lines.push('options:');
    lines.push(`  auto_relationships: ${autoRelationships}`);
    lines.push(`  include_hidden_fields: ${includeHiddenFields}`);
    lines.push(`  generate_descriptions: ${generateDescriptions}`);
    return lines.join('\n');
  };

  /* ─── Step validity ─── */
  const canAdvance = () => {
    if (step === 1) return name.trim().length > 0 && !!sourceConnector && !!targetConnector && !!outputFormat;
    if (step === 2 && sourceConnector === 'fabric') return !!fabricWorkspaceId;
    if (step === 5) return true;
    return true;
  };

  const goNext = () => {
    if (step === 5) { handleFinish(); return; }
    setStep(s => Math.min(5, s + 1));
  };
  const goBack = () => setStep(s => Math.max(1, s - 1));

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
        <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>New Project</span>
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
            targetConnector={targetConnector} setTargetConnector={setTargetConnector}
            outputFormat={outputFormat} setOutputFormat={setOutputFormat}
            configMode={configMode} setConfigMode={setConfigMode}
          />
        )}
        {step === 2 && (
          <StepConnectorConfig
            sourceConnector={sourceConnector}
            targetConnector={targetConnector}
            fabricWorkspaceId={fabricWorkspaceId} setFabricWorkspaceId={setFabricWorkspaceId}
            snowflakeDatabase={snowflakeDatabase} setSnowflakeDatabase={setSnowflakeDatabase}
            snowflakeSchema={snowflakeSchema} setSnowflakeSchema={setSnowflakeSchema}
            targetDatabase={targetDatabase} setTargetDatabase={setTargetDatabase}
            targetSchema={targetSchema} setTargetSchema={setTargetSchema}
            domainHint={domainHint} setDomainHint={setDomainHint}
            workspaces={availableWorkspaces}
            workspacesLoading={workspacesLoading}
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
            modelResults={modelResults} allModels={allModels}
          />
        )}
        {step === 4 && (
          <StepMappingOptions
            autoRelationships={autoRelationships} setAutoRelationships={setAutoRelationships}
            includeHiddenFields={includeHiddenFields} setIncludeHiddenFields={setIncludeHiddenFields}
            generateDescriptions={generateDescriptions} setGenerateDescriptions={setGenerateDescriptions}
          />
        )}
        {step === 5 && (
          <StepFinish
            name={name}
            saving={saving}
            runNow={runNow} setRunNow={setRunNow}
            createdProject={createdProject}
            createError={createError}
            runWarning={runWarning}
            sourceConnector={sourceConnector}
            targetConnector={targetConnector}
            outputFormat={outputFormat}
            configMode={configMode}
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
  targetConnector,
  setTargetConnector,
  outputFormat,
  setOutputFormat,
  configMode,
  setConfigMode,
}) {
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
          style={INPUT}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
        />
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

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div>
          <label style={LABEL}>Source Connector</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {CONNECTOR_TYPES.map(c => (
              <ConnectorChip
                key={c.value}
                icon={c.icon} label={c.label}
                selected={sourceConnector === c.value}
                onClick={() => setSourceConnector(c.value)}
              />
            ))}
          </div>
        </div>
        <div>
          <label style={LABEL}>Target Connector</label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {TARGET_CONNECTOR_TYPES.map(t => (
              <ConnectorChip
                key={t.value}
                icon={t.icon}
                label={t.label}
                selected={targetConnector === t.value}
                onClick={() => setTargetConnector(t.value)}
              />
            ))}
          </div>
        </div>
      </div>

      <div>
        <label style={LABEL}>Output Format</label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {OUTPUT_FORMAT_TYPES.map(t => (
            <ConnectorChip
              key={t.value} label={t.label}
              selected={outputFormat === t.value}
              onClick={() => setOutputFormat(t.value)}
            />
          ))}
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6 }}>
          The target connector is used for sync. The output format controls which semantic artifacts are generated for review or deployment.
        </p>
      </div>

      <div>
        <label style={LABEL}>Project Configuration Experience</label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          <ConnectorChip
            label="UI Friendly Form"
            selected={configMode === 'form'}
            onClick={() => setConfigMode('form')}
          />
          <ConnectorChip
            label="YAML First"
            selected={configMode === 'yaml'}
            onClick={() => setConfigMode('yaml')}
          />
        </div>
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6 }}>
          You can switch between Form and YAML later from the project configuration page.
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
  targetConnector,
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
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Connector Configuration</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Choose the source workspace and any defaults needed for the first sync. Global credentials are set in Settings.
        </p>
      </div>

      {sourceConnector === 'fabric' && (
        <div>
          <label style={LABEL}>Fabric Workspace</label>
          <SearchableSelect
            items={workspaces}
            displayKey="name"
            valueKey="id"
            searchFields={['name', 'id', 'workspace_id']}
            placeholder="Choose a workspace"
            value={fabricWorkspaceId}
            onChange={item => setFabricWorkspaceId(item?.id || '')}
            loading={workspacesLoading}
            clearable={false}
          />
          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
            Pick the workspace once here. The next step will show models from this workspace only.
          </p>
          {!workspacesLoading && workspaces.length === 0 && (
            <p style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 8 }}>
              No Fabric workspaces were found. Check the connector credentials in Settings before creating the project.
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

      {targetConnector === 'snowflake' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          <div>
            <label style={LABEL}>Target Database (optional)</label>
            <input
              type="text" value={targetDatabase} onChange={e => setTargetDatabase(e.target.value)}
              placeholder="Use global target defaults"
              style={INPUT}
              onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
              onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
            />
          </div>
          <div>
            <label style={LABEL}>Target Schema (optional)</label>
            <input
              type="text" value={targetSchema} onChange={e => setTargetSchema(e.target.value)}
              placeholder="Use global target defaults"
              style={INPUT}
              onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
              onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
            />
          </div>
        </div>
      )}

      {targetConnector === 'fabric' && (
        <div style={{ padding: '14px 16px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
            Microsoft Fabric target
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
            Fabric target sync will use the saved global connector settings. You can fine-tune destination details later in Configure if needed.
          </div>
        </div>
      )}

      <div>
        <label style={LABEL}>Domain Hint (optional)</label>
        <input
          type="text" value={domainHint} onChange={e => setDomainHint(e.target.value)}
          placeholder="e.g. finance, sales, hr — helps AI generate better names"
          style={INPUT}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
        />
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
          A domain hint helps the AI produce more accurate semantic descriptions.
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
  modelQuery, setModelQuery, modelResults, allModels,
}) {
  if (sourceConnector !== 'fabric') {
    return (
      <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
        Source browsing is currently available for Microsoft Fabric.<br />
        All available objects will be included automatically.
      </div>
    );
  }

  if (!selectedWorkspace) {
    return (
      <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
        Choose a Fabric workspace in Connector Config before selecting models.
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
      <div style={{ position: 'relative' }}>
        <Search size={13} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)', pointerEvents: 'none' }} />
        <input
          value={modelQuery} onChange={e => setModelQuery(e.target.value)}
          placeholder={`Search models in ${selectedWorkspace.name}…`}
          style={{ ...INPUT, paddingLeft: 30 }}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
        />
      </div>

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
function StepMappingOptions({ autoRelationships, setAutoRelationships, includeHiddenFields, setIncludeHiddenFields, generateDescriptions, setGenerateDescriptions }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Mapping Options</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Configure how SemaBridge transforms your semantic models.
        </p>
      </div>

      <ToggleOption
        label="Auto-detect Relationships"
        description="Automatically infer joins and relationships from foreign keys and naming conventions."
        checked={autoRelationships}
        onChange={setAutoRelationships}
      />
      <ToggleOption
        label="Include Hidden Fields"
        description="Include fields marked as hidden in the source model."
        checked={includeHiddenFields}
        onChange={setIncludeHiddenFields}
      />
      <ToggleOption
        label="Generate AI Descriptions"
        description="Use the LLM to auto-generate descriptions for fields, measures, and hierarchies."
        checked={generateDescriptions}
        onChange={setGenerateDescriptions}
      />
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
  runNow,
  setRunNow,
  createdProject,
  createError,
  runWarning,
  sourceConnector,
  targetConnector,
  outputFormat,
  configMode,
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
          "{name}" has been created{runNow && !runWarning ? ' and the first run has been triggered' : ''}.
        </p>
        {runWarning && (
          <div style={{ maxWidth: 520, margin: '0 auto 20px', padding: '12px 14px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.5, textAlign: 'left' }}>
            {runWarning}
          </div>
        )}
        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={() => navigate('/projects')} style={footerBtn('secondary')}>Back to Projects</button>
          {createdProjectId ? (
            <>
              <button onClick={() => navigate(`/projects/${createdProjectId}/config?mode=form`)} style={footerBtn(configMode === 'form' ? 'primary' : 'secondary')}>Open UI Form</button>
              <button onClick={() => navigate(`/projects/${createdProjectId}/config?mode=yaml`)} style={footerBtn(configMode === 'yaml' ? 'primary' : 'secondary')}>Open YAML</button>
            </>
          ) : (
            <button disabled style={{ ...footerBtn('secondary'), opacity: 0.6, cursor: 'not-allowed' }}>
              Open YAML (ID unavailable)
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
          Target: {targetConnector} <br />
          Output format: {outputFormat} <br />
          Config mode: {configMode === 'yaml' ? 'YAML' : 'UI Form'} <br />
          Workspace: {selectedWorkspace?.name || 'Will use saved defaults'}
        </div>
      </div>

      {createError && (
        <div style={{ padding: '12px 14px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.28)', color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.5 }}>
          {createError}
        </div>
      )}

      <ToggleOption
        label="Run Now"
        description="Trigger the first sync immediately after creation."
        checked={runNow}
        onChange={setRunNow}
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
