/**
 * Shared utilities for sync status and progress calculation.
 */

export function normalizeStatus(status) {
  const s = String(status || 'draft').toLowerCase();
  if (s === 'running' || s === 'success' || s === 'failed' || s === 'draft') return s;
  if (s === 'warning' || s === 'partial' || s === 'completed') return 'success';
  if (s === 'error' || s === 'cancelled') return 'failed';
  return 'draft';
}

export function deriveProgressFromRun(run) {
  if (!run) return 0;
  const explicit = Number(run?.progress_pct);
  if (!Number.isNaN(explicit) && explicit >= 0) {
    return Math.max(0, Math.min(100, Math.round(explicit)));
  }

  const total = Number(run?.total_items ?? run?.total_models ?? 0);
  const synced = Number(run?.completed_items ?? run?.models_synced ?? 0);
  if (total > 0) {
    const ratio = Math.max(0, Math.min(1, synced / total));
    return Math.round(ratio * 100);
  }
  return 0;
}
