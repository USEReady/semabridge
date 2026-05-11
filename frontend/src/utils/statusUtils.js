/**
 * Status priority mapping for aggregation.
 * Lower index = Higher priority.
 */
const STATUS_PRIORITY = [
  'failed',
  'error',
  'disconnected',
  'warning',
  'configured',
  'running',
  'success',
  'active',
  'connected',
  'draft',
  'idle',
];

/**
 * Aggregates project statuses into a single representative status.
 * @param {Array} projects - List of project objects.
 * @returns {string} - The highest priority status.
 */
export function aggregateStatus(projects) {
  if (!projects || projects.length === 0) return 'idle';

  let highestPriorityIndex = STATUS_PRIORITY.length - 1;
  let result = 'idle';

  for (const project of projects) {
    const status = (project.status || 'idle').toLowerCase();
    const index = STATUS_PRIORITY.indexOf(status);
    
    // If we find a failed status, we can stop immediately as it's top priority
    if (index === 0) return 'failed';

    if (index !== -1 && index < highestPriorityIndex) {
      highestPriorityIndex = index;
      result = status;
    }
  }

  return result;
}

/**
 * Maps a status string to a HEX color or CSS variable.
 */
export function getStatusColor(status) {
  const s = (status || 'idle').toLowerCase();
  if (s === 'failed' || s === 'error' || s === 'disconnected') return 'var(--color-error)';
  if (s === 'warning' || s === 'configured') return 'var(--color-warning)';
  if (s === 'running') return 'var(--accent-blue)';
  if (s === 'success' || s === 'active' || s === 'connected') return 'var(--color-success)';
  return 'var(--text-tertiary)';
}

/**
 * Checks if any project in a list is in a 'failed' or 'error' state.
 */
export function hasFailures(projects) {
  return projects.some(p => {
    const s = (p.status || '').toLowerCase();
    return s === 'failed' || s === 'error';
  });
}
