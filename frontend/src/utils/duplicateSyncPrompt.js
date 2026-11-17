import { create } from 'zustand';

/**
 * Global prompt for the pre-sync target view exists and duplicate checks.
 * The backend returns 409 when the target semantic view already exists or a model
 * is already synced by another project. Callers await requestDuplicateSyncConfirmation()
 * and retry with the user's chosen sync_mode and confirmations.
 */
export const useDuplicateSyncPrompt = create((set) => ({
  open: false,
  detail: null,
  duplicates: [],
  message: '',
  resolve: null,
  show: (detail, resolve) => set({
    open: true,
    detail: detail || null,
    duplicates: Array.isArray(detail?.duplicates) ? detail.duplicates : [],
    message: String(detail?.message || detail?.reason || ''),
    resolve,
  }),
  close: () => set({ open: false, detail: null, duplicates: [], message: '', resolve: null }),
}));

export function isDuplicateSyncError(err) {
  if (Number(err?.status) !== 409) return false;
  const detail = err?.payload?.detail || err?.detail || err?.payload;
  if (!detail || typeof detail !== 'object') return false;

  // Trap avoidance: never open for project busy or run in progress
  const statusStr = String(detail.status || '').toLowerCase();
  if (statusStr === 'busy' || statusStr === 'running' || detail.code === 'PROJECT_BUSY') {
    return false;
  }

  return (
    statusStr === 'duplicate' ||
    statusStr === 'target_view_exists' ||
    Boolean(detail.view_name) ||
    typeof detail.upsert_available === 'boolean' ||
    Boolean(detail.match_type)
  );
}

export function requestDuplicateSyncConfirmation(detail) {
  return new Promise((resolve) => {
    useDuplicateSyncPrompt.getState().show(detail, resolve);
  });
}
