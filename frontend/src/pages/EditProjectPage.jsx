/**
 * EditProjectPage — /projects/:id/edit
 *
 * Loads an existing project by ID and feeds it into the shared
 * 5-step CreateProjectPage wizard with editMode=true.
 *
 * All the "SAVED" tags, diff highlights, and review changes logic
 * live inside CreateProjectPage and work automatically in edit mode.
 */
import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Loader2, ArrowLeft } from 'lucide-react';
import { parseDocument as parseYamlDocument } from 'yaml';

import { api } from '../utils/api';
import CreateProjectPage from './CreateProjectPage';

/** Parse a semabridge.yaml string into a flat form object matching initialData shape */
function parseConfigYamlToInitialData(yamlText, projectMeta) {
  const raw = String(yamlText || '').trim();
  const fallback = {
    source_type: projectMeta?.source || 'fabric',
    target_type: projectMeta?.target_type || 'snowflake',
    output_format: 'osi',
    identity_id: '',
    workspace_id: '',
    database: '',
    schema: '',
    target_database: '',
    target_schema: '',
    target_account: '',
    target_warehouse: '',
    target_identity_id: '',
    target_workspace_id: '',
    models: [],
  };

  if (!raw) return fallback;

  try {
    const doc = parseYamlDocument(raw, { uniqueKeys: false, prettyErrors: true });
    const tree = doc.toJS ? doc.toJS() : {};

    const source = tree?.source && typeof tree.source === 'object' ? tree.source : {};
    const targets = Array.isArray(tree?.targets) ? tree.targets : (tree?.target ? [tree.target] : []);
    const firstTarget = targets[0] && typeof targets[0] === 'object' ? targets[0] : {};
    const ui = tree?.ui && typeof tree.ui === 'object' ? tree.ui : {};

    // Models — could be source.models (array), source.model (string or '*')
    let models = [];
    if (Array.isArray(source.models)) {
      models = source.models.filter(m => m && m !== '*');
    } else if (typeof source.model === 'string' && source.model && source.model !== '*') {
      models = [source.model];
    }

    // Also check selection block (written by the wizard itself)
    const selectionModelIds = Array.isArray(tree?.selection?.model_ids)
      ? tree.selection.model_ids
      : [];
    if (selectionModelIds.length > 0 && models.length === 0) {
      models = selectionModelIds;
    }

    return {
      source_type: String(source.type || fallback.source_type),
      target_type: String(firstTarget.type || fallback.target_type),
      output_format: String(ui.intermediate_format || fallback.output_format),
      identity_id: String(source.identity_id || ''),
      workspace_id: String(source.workspace_id || ''),
      database: String(source.database || ''),
      schema: String(source.schema || ''),
      target_database: String(firstTarget.database || ''),
      target_schema: String(firstTarget.schema || ''),
      target_account: String(firstTarget.account || ''),
      target_warehouse: String(firstTarget.warehouse || ''),
      target_identity_id: String(firstTarget.identity_id || ''),
      target_workspace_id: String(firstTarget.workspace_id || ''),
      models,
    };
  } catch {
    return fallback;
  }
}

export default function EditProjectPage() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [project, setProject] = useState(null);
  const [configYaml, setConfigYaml] = useState('');
  const [mappings, setMappings] = useState([]);

  useEffect(() => {
    if (!id || id === 'null' || id === 'undefined') {
      navigate('/projects', { replace: true });
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError('');

    Promise.all([
      api.getProject(id),
      api.getProjectConfig(id).catch(() => ({ config_yaml: '' })),
      api.getMappings(id).catch(() => ({ mappings: [] })),
    ])
      .then(([p, cfg, mapRes]) => {
        if (cancelled) return;
        setProject(p);
        setConfigYaml(cfg?.config_yaml || '');
        setMappings(Array.isArray(mapRes?.mappings) ? mapRes.mappings : []);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.message || 'Failed to load project.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [id, navigate]);

  // Build the initialData object that feeds into the wizard
  const initialData = useMemo(() => {
    if (!project) return null;
    const parsed = parseConfigYamlToInitialData(configYaml, project);
    return {
      id: project.id || project.project_id,
      name: project.name || '',
      description: project.description || '',
      tags: Array.isArray(project.tags) ? project.tags : [],
      mappings,
      ...parsed,
    };
  }, [project, configYaml, mappings]);

  // Called by the wizard on Step 5 "Save" — receives YAML string + meta object
  const handleSave = async (yamlText, meta = {}) => {
    await api.updateProject(id, {
      name: meta?.name || project?.name,
      description: meta?.description ?? project?.description,
      tags: meta?.tags || project?.tags || [],
    });
    await api.saveProjectConfig(id, yamlText);
    // Navigation is handled after createdProject state is set in the wizard
  };

  // --- Loading state ---
  if (loading) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        justifyContent: 'center', height: '100vh', gap: 16,
        color: 'var(--text-tertiary)', fontSize: 14,
      }}>
        <Loader2 size={32} style={{ animation: 'spin 1s linear infinite', color: 'var(--accent-blue)' }} />
        <span>Loading project…</span>
      </div>
    );
  }

  // --- Error / not found state ---
  if (error || !project) {
    return (
      <div style={{ padding: 40, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
          {error || 'Project not found'}
        </div>
        <button
          onClick={() => navigate('/projects')}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, border: '1px solid var(--border-main)',
            background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 13,
          }}
        >
          <ArrowLeft size={14} /> Back to Projects
        </button>
      </div>
    );
  }

  // --- Render the wizard in edit mode ---
  return (
    <CreateProjectPage
      editMode={true}
      initialData={initialData}
      onSaveConfig={handleSave}
    />
  );
}
