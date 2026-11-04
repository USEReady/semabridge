const ALLOWED_SYNC_MODES = new Set(['copy', 'upsert']);

export function normalizeSyncMode(value, fallback = 'copy') {
  const candidate = String(value || '').trim().toLowerCase();
  if (ALLOWED_SYNC_MODES.has(candidate)) {
    return candidate;
  }
  const normalizedFallback = String(fallback || '').trim().toLowerCase();
  return ALLOWED_SYNC_MODES.has(normalizedFallback) ? normalizedFallback : 'copy';
}

/**
 * Determines whether a project has had at least one prior deployment/run,
 * which is a prerequisite for executing an UPSERT (incremental merge)
 * against existing target schemas and baseline models.
 */
export function canProjectUpsert(project) {
  if (!project || typeof project !== 'object') return false;
  const runsCount = Number(project.runs_count || project.run_count || 0);
  if (runsCount > 0) return true;
  if (project.last_run_at || project.last_deployed_at) return true;
  if (project.has_deployed || project.has_prior_run) return true;
  const status = String(project.status || '').toLowerCase();
  if (status === 'success' || status === 'active') return true;
  return false;
}

export function resolveProjectSyncMode(project, fallback = 'copy') {
  if (!project || typeof project !== 'object') {
    return normalizeSyncMode(fallback);
  }

  const requested = normalizeSyncMode(
    project.sync_mode ?? project.write_strategy ?? fallback,
    fallback,
  );

  // If the project is brand new and has never been deployed, force 'copy'
  if (requested === 'upsert' && !canProjectUpsert(project)) {
    return 'copy';
  }

  return requested;
}