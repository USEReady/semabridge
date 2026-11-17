import { create } from 'zustand';

/**
 * Global prompt for the pre-sync duplicate check. The backend refuses a
 * sync with 409 { detail: { status: 'duplicate', duplicates: [...] } } when
 * a model is already synced by another project; api.runProjectNow awaits
 * requestDuplicateSyncConfirmation() and retries with
 * acknowledge_duplicates only if the user confirms.
 */
export const useDuplicateSyncPrompt = create((set) => ({
  open: false,
  duplicates: [],
  message: '',
  resolve: null,
  show: (detail, resolve) => set({
    open: true,
    duplicates: Array.isArray(detail?.duplicates) ? detail.duplicates : [],
    message: String(detail?.message || ''),
    resolve,
  }),
  close: () => set({ open: false, duplicates: [], message: '', resolve: null }),
}));

export function isDuplicateSyncError(err) {
  return err?.status === 409 && err?.payload?.detail?.status === 'duplicate';
}

export function requestDuplicateSyncConfirmation(detail) {
  return new Promise((resolve) => {
    useDuplicateSyncPrompt.getState().show(detail, resolve);
  });
}
