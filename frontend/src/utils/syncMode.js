const ALLOWED_SYNC_MODES = new Set(['copy', 'upsert']);

export function normalizeSyncMode(value, fallback = 'copy') {
  const candidate = String(value || '').trim().toLowerCase();
  if (ALLOWED_SYNC_MODES.has(candidate)) {
    return candidate;
  }
  const normalizedFallback = String(fallback || '').trim().toLowerCase();
  return ALLOWED_SYNC_MODES.has(normalizedFallback) ? normalizedFallback : 'copy';
}

export function resolveProjectSyncMode(project, fallback = 'copy') {
  if (!project || typeof project !== 'object') {
    return normalizeSyncMode(fallback);
  }

  return normalizeSyncMode(
    project.sync_mode ?? project.write_strategy ?? fallback,
    fallback,
  );
}