import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Database } from 'lucide-react';
import RepositoryMap from '../components/RepositoryMap/RepositoryMap';
import { authFetch } from '../utils/api';

function resolveTargetPlatform(project, explicitTarget) {
  const raw =
    explicitTarget ||
    project?.target_type ||
    (typeof project?.target === 'string' ? project.target : project?.target?.type) ||
    (Array.isArray(project?.targets) &&
      (typeof project.targets[0] === 'string' ? project.targets[0] : project.targets[0]?.type)) ||
    '';
  const norm = String(raw).toLowerCase();
  if (norm.includes('databricks')) return 'databricks';
  if (norm.includes('snowflake')) return 'snowflake';
  return '';
}

/**
 * ExplorePage — "Model map" (/map).
 * Wraps RepositoryMap (React Flow diagram, schema table and detail panel).
 * Supports `?model=<id or name>`, `?project=<id>`, and `?target=<databricks|snowflake>` deep links.
 */
export default function ExplorePage() {
  const [searchParams] = useSearchParams();
  const initialModel = searchParams.get('model');
  const projectId = searchParams.get('project');
  const explicitTarget = searchParams.get('target');
  const [projectInfo, setProjectInfo] = useState(null);

  useEffect(() => {
    if (!projectId) {
      setProjectInfo(null);
      return;
    }
    let cancelled = false;
    authFetch('/api/projects?limit=200&offset=0')
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (cancelled) return;
        const list = Array.isArray(data) ? data : (data?.projects || []);
        const match = list.find((p) => p.id === projectId || p.name === projectId);
        setProjectInfo(match || { id: projectId, name: projectId });
      })
      .catch(() => {
        if (!cancelled) setProjectInfo({ id: projectId, name: projectId });
      });
    return () => { cancelled = true; };
  }, [projectId]);

  const targetPlatform = resolveTargetPlatform(projectInfo, explicitTarget);
  const isDatabricks = targetPlatform === 'databricks';

  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      {(projectId || targetPlatform) && (
        <div style={{
          padding: '8px 16px',
          borderBottom: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          fontSize: 12,
          color: 'var(--text-secondary)',
          flexShrink: 0,
        }}>
          <Database size={13} style={{ color: isDatabricks ? '#ff5c45' : 'var(--accent-blue)' }} />
          {projectInfo?.name && (
            <strong style={{ color: 'var(--text-primary)' }}>{projectInfo.name}</strong>
          )}
          <span style={{
            fontSize: 10.5,
            fontWeight: 700,
            padding: '2px 7px',
            borderRadius: 4,
            background: isDatabricks ? 'rgba(255, 54, 33, 0.12)' : 'rgba(56, 189, 248, 0.12)',
            border: isDatabricks ? '1px solid rgba(255, 54, 33, 0.35)' : '1px solid rgba(56, 189, 248, 0.35)',
            color: isDatabricks ? '#ff5c45' : '#38bdf8',
          }}>
            {isDatabricks ? 'Databricks Metric View' : 'Snowflake Semantic View'}
          </span>
          <span style={{ color: 'var(--text-tertiary)' }}>
            {isDatabricks ? 'Unity Catalog semantic layer' : 'Snowflake semantic layer'}
          </span>
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <RepositoryMap initialModel={initialModel} />
      </div>
    </div>
  );
}
