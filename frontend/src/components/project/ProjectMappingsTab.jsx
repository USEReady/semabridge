/**
 * Mappings tab of the project page: the same "Saved Mappings" panel the
 * project settings page shows (MappingPreviewPanel), fed by GET /api/.../mappings.
 */
import React, { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { MappingPreviewPanel } from '../../pages/ProjectConfigPage';
import { card, muted } from './projectStyles';

export default function ProjectMappingsTab({ projectId }) {
  // Result is keyed by project id so "loading" is derived, not set in the effect.
  const [result, setResult] = useState({ id: null, mappings: [], error: '' });

  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    api.getMappings(projectId)
      .then((response) => {
        if (!cancelled) setResult({ id: projectId, mappings: Array.isArray(response?.mappings) ? response.mappings : [], error: '' });
      })
      .catch((err) => {
        if (!cancelled) setResult({ id: projectId, mappings: [], error: err?.message || 'Failed to load saved mappings.' });
      });
    return () => { cancelled = true; };
  }, [projectId]);

  const loading = result.id !== projectId;
  const mappings = loading ? [] : result.mappings;
  const error = loading ? '' : result.error;

  if (!loading && !error && mappings.length === 0) {
    return (
      <div style={card}>
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Saved Mappings</span>
        <span style={muted}>No saved mappings for this project yet. Mappings are saved when you edit the project and choose tables.</span>
      </div>
    );
  }
  return <MappingPreviewPanel mappings={mappings} loading={loading} error={error} />;
}
