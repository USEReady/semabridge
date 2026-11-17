import { create } from 'zustand';

/**
 * Global prompt for every reason the backend refuses to START a sync:
 *
 *  - 409 { detail: { status: 'duplicate', duplicates: [...] } }
 *      a model is already synced by another project. The user can confirm;
 *      api.runProjectNow then retries with acknowledge_duplicates.
 *  - 409 { detail: { status: 'already_running', run_id } }
 *      this project already has a sync in progress. Options: view that run.
 *  - 409 { detail: { status: 'blocked', blockers: [...] } }, 503
 *    { detail: { status: 'readiness_timeout' } }, or the multi-model gate's
 *    "cannot be synced ... dry run" error: the dry run has unresolved
 *    blockers (or none was run). Options: open the dry run to resolve them.
 *
 *  - 409 { detail: { status: 'removed_fields', models: [{ model, removed }] } }
 *      a COPY sync would remove fields that are in the deployed view but no
 *      longer in the source. Options: remove them ('confirm'), keep them by
 *      syncing as an upsert ('upsert'), or cancel.
 *
 * The dialog (DuplicateSyncDialog) is mounted once in DashboardLayout.
 * `requestSyncConflictDecision` resolves with the user's choice:
 * 'confirm' (duplicate / removed_fields), 'upsert' (removed_fields),
 * 'view_run', 'open_dry_run' or 'cancel'.
 */
export const useDuplicateSyncPrompt = create((set) => ({
  open: false,
  kind: 'duplicate',
  projectId: '',
  detail: {},
  duplicates: [],
  message: '',
  resolve: null,
  show: (kind, projectId, detail, resolve) => set({
    open: true,
    kind,
    projectId: String(projectId || ''),
    detail: detail && typeof detail === 'object' ? detail : {},
    duplicates: Array.isArray(detail?.duplicates) ? detail.duplicates : [],
    message: String(detail?.message || ''),
    resolve,
  }),
  close: () => set({ open: false, kind: 'duplicate', projectId: '', detail: {}, duplicates: [], message: '', resolve: null }),
}));

const MULTI_MODEL_GATE_RE = /cannot be synced|dry[- ]run.*(blocker|not (been )?run|no dry-run job|failed|stale|out of date)/i;

/** Which sync-start conflict an API error is, or '' when it is none. */
export function syncConflictKind(err) {
  const detail = err?.payload?.detail;
  const status = typeof detail === 'object' && detail ? String(detail.status || '') : '';
  if (err?.status === 409 && status === 'duplicate') return 'duplicate';
  if (err?.status === 409 && (status === 'already_running' || status === 'running')) return 'already_running';
  if (err?.status === 409 && status === 'blocked') return 'blocked';
  if (err?.status === 409 && status === 'removed_fields') return 'removed_fields';
  if (err?.status === 503 && status === 'readiness_timeout') return 'blocked';
  // Multi-model gate (ValidationError): plain-text detail.
  if (err?.status && err.status >= 400 && err.status < 500 && MULTI_MODEL_GATE_RE.test(String(err?.message || ''))) {
    return 'blocked';
  }
  return '';
}

export function isDuplicateSyncError(err) {
  return syncConflictKind(err) === 'duplicate';
}

/** Detail object for the dialog; plain-text errors become { message }. */
export function syncConflictDetail(err) {
  const detail = err?.payload?.detail;
  if (detail && typeof detail === 'object') return detail;
  const message = String(err?.message || '').replace(/^API Error \d+:\s*/, '');
  return { message };
}

export function requestSyncConflictDecision(kind, projectId, detail) {
  return new Promise((resolve) => {
    useDuplicateSyncPrompt.getState().show(kind, projectId, detail, resolve);
  });
}

export function requestDuplicateSyncConfirmation(detail, projectId = '') {
  return requestSyncConflictDecision('duplicate', projectId, detail).then(choice => choice === 'confirm');
}
