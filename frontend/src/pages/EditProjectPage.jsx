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

import { api } from '../utils/api';
import { parseConfigYamlToInitialData } from '../utils/parseConfigYamlToInitialData';
import { useUIStore } from '../store/uiStore';
import CreateProjectPage from './CreateProjectPage';

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
    // Clear stale draft so ProjectConfigPage loads fresh data from backend
    useUIStore.getState().setProjectConfigDraft(id, null);
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
