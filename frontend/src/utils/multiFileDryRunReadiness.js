/**
 * resolveMultiFileDryRunReadiness — pure classification of a multi-file
 * dry-run job's poll status into the three states StepMappingOptions.jsx's
 * readiness banner and Proceed/Continue buttons must gate on.
 *
 * Extracted out of StepMappingOptions.jsx so the exact bug this fixes (the
 * "READY TO PROCEED" banner and Proceed/Continue enabling the instant a
 * multi-file background job is CREATED, rather than once every file has
 * actually reached a final state) has a direct, non-component-rendering
 * regression test — see multiFileDryRunReadiness.test.js and the sibling
 * MultiFileDryRunStatus.regression.test.js's own note on why this project's
 * frontend tests are data-transform tests, not jsdom/RTL component tests.
 *
 * `jobStatus` is api.getDryRunJobStatus()'s raw response,
 * `{ status, files: [...] }`, where `status` is the backend's
 * _aggregate_job_status() result: "pending" | "running" | "success" |
 * "failed" | "partial". `null` means no poll has landed yet (job was just
 * created, or a brand new job replaced a previous one).
 *
 * The per-model helpers further down (buildMultiModelSections /
 * resolveMultiModelReadiness / collectMappingOverrides / row edit helpers)
 * are what the multi-model dry-run page actually gates on: Proceed requires
 * every model to have succeeded, its result to be loaded, and no model to
 * have unresolved blocking rows.
 */
import { cleanPbixLabel } from './pbixLabel.js';

export function resolveMultiFileDryRunReadiness(jobStatus) {
  const status = jobStatus?.status ?? null;
  const running = status === null || status === 'running' || status === 'pending';
  const succeeded = status === 'success';
  const hasFailures = status === 'failed' || status === 'partial';
  return { running, succeeded, hasFailures };
}

// ─── Per-model (multi-file / multi-model) dry-run helpers ────────────────────

/** Minimum compatibility score a model may have and still allow Proceed (same as the single-model gate). */
export const MIN_PROCEED_COMPAT_SCORE = 70;

/** Normalizes a backend per-file status into pending | running | success | failed. */
export function normalizeFileStatus(status) {
  const s = String(status || '').trim().toLowerCase();
  if (s === 'success' || s === 'succeeded' || s === 'completed' || s === 'complete' || s === 'done') return 'success';
  if (s === 'failed' || s === 'error' || s === 'errored' || s === 'cancelled' || s === 'canceled') return 'failed';
  if (s === 'running' || s === 'in_progress' || s === 'started') return 'running';
  return 'pending';
}

export function isTerminalFileStatus(status) {
  const s = normalizeFileStatus(status);
  return s === 'success' || s === 'failed';
}

/** True once every file in the job reached a terminal state (polling can stop). */
export function isJobTerminal(jobStatus) {
  const files = Array.isArray(jobStatus?.files) ? jobStatus.files : [];
  if (files.length > 0) return files.every((file) => isTerminalFileStatus(file?.status));
  const agg = String(jobStatus?.status || '').toLowerCase();
  return agg === 'success' || agg === 'failed' || agg === 'partial';
}

/** Human label for one job file (Fabric model name or cleaned PBIX file name). */
export function fileLabel(file) {
  const label = String(file?.label || '').trim();
  if (label) return label;
  const raw = file?.filename || file?.pbix_path || file?.source_ref || file?.file_id || '';
  return cleanPbixLabel(raw) || String(raw);
}

/** The value that identifies this model in the project config's source.models list. */
export function fileSourceRef(file) {
  return String(file?.source_ref || file?.pbix_path || file?.label || file?.filename || '').trim();
}

/** Same blocking rule the single-model page uses (utils/projectHelpers.jsx isBlockingRow). */
export function isReadinessBlockingRow(row) {
  const status = String(row?.status || '').toLowerCase();
  if (status === 'collision' || status === 'unmapped') return true;
  const validationStatus = String(row?.validation_status || '').toLowerCase();
  if (validationStatus === 'invalid' || validationStatus === 'collision') return true;
  const validationCode = String(row?.validation_code || '').toUpperCase();
  return Boolean(validationCode && validationCode !== 'OK');
}

function isMetricRow(row) {
  const kind = String(row?.field_type || row?.entity_kind || '').toLowerCase();
  return kind === 'measure' || kind === 'metric';
}

/** Attempt token for one file run -- changes when the file is re-run, so a stale result is never reused. */
export function fileAttemptKey(file) {
  return `${file?.file_id || ''}@${file?.completed_at || ''}`;
}

/**
 * buildMultiModelSections — groups a job's per-file statuses and the loaded
 * per-file results into one section per model, in job order.
 *
 * fileStates: { [file_id]: { attempt, rows, result, loadError } } -- rows are
 * that model's own (possibly user-edited) NormalizedRow[].
 */
export function buildMultiModelSections(jobStatus, fileStates = {}) {
  const files = Array.isArray(jobStatus?.files) ? jobStatus.files : [];
  return files.map((file, index) => {
    const fileId = String(file?.file_id || `file-${index}`);
    const status = normalizeFileStatus(file?.status);
    const state = fileStates?.[fileId] || null;
    const attempt = fileAttemptKey(file);
    const stateIsCurrent = Boolean(state) && state.attempt === attempt;
    const loaded = status === 'success' && stateIsCurrent && Array.isArray(state?.rows);
    const loadError = status === 'success' && stateIsCurrent && !loaded ? String(state?.loadError || '') : '';
    const rows = loaded ? state.rows : [];
    const result = loaded ? (state.result || null) : null;
    const metricCount = rows.filter(isMetricRow).length;
    const compat = typeof result?.compatibility_score === 'number' ? result.compatibility_score : null;
    return {
      fileId,
      index,
      label: fileLabel(file),
      sourceRef: fileSourceRef(file),
      previewProjectId: String(file?.preview_project_id || '').trim(),
      status,
      error: String(file?.error_message || file?.error_summary || file?.error || '').trim(),
      loadError,
      loaded,
      loading: status === 'success' && !loaded && !loadError,
      attempt,
      rows,
      result,
      columnCount: rows.length - metricCount,
      metricCount,
      blockingCount: rows.filter(isReadinessBlockingRow).length,
      compatibilityScore: compat,
      schemaConflictCount: Array.isArray(result?.schema_conflicts) ? result.schema_conflicts.length : 0,
    };
  });
}

/**
 * resolveMultiModelReadiness — Proceed is enabled only when EVERY model's dry
 * run succeeded, every model's result is loaded, and no model has unresolved
 * blocking rows or a compatibility score under the single-model threshold.
 */
export function resolveMultiModelReadiness(sections = [], { minCompat = MIN_PROCEED_COMPAT_SCORE } = {}) {
  const list = Array.isArray(sections) ? sections : [];
  const total = list.length;
  const pendingCount = list.filter((s) => s.status === 'pending' || s.status === 'running').length;
  const failedCount = list.filter((s) => s.status === 'failed' || Boolean(s.loadError)).length;
  const loadingCount = list.filter((s) => s.loading).length;
  const succeededCount = list.filter((s) => s.status === 'success' && s.loaded).length;
  const totalBlocking = list.reduce((sum, s) => sum + (s.blockingCount || 0), 0);
  const totalSchemaConflicts = list.reduce((sum, s) => sum + (s.schemaConflictCount || 0), 0);
  const scores = list.map((s) => s.compatibilityScore).filter((v) => typeof v === 'number');
  const minCompatibilityScore = scores.length ? Math.min(...scores) : null;
  const blockedModels = list.filter((s) => (s.blockingCount || 0) > 0).map((s) => s.label);
  const lowCompatModels = list
    .filter((s) => typeof s.compatibilityScore === 'number' && s.compatibilityScore < minCompat)
    .map((s) => s.label);

  const running = total === 0 || pendingCount > 0 || loadingCount > 0;
  const allSucceeded = total > 0 && succeededCount === total;
  const ready = allSucceeded && totalBlocking === 0 && lowCompatModels.length === 0;

  return {
    total,
    running,
    allSucceeded,
    hasFailures: failedCount > 0,
    ready,
    pendingCount,
    failedCount,
    loadingCount,
    succeededCount,
    totalBlocking,
    totalSchemaConflicts,
    minCompatibilityScore,
    blockedModels,
    lowCompatModels,
  };
}

/** All rows of all loaded models, each tagged with its model's file_id and source ref. */
export function flattenSectionRows(sections = []) {
  return (Array.isArray(sections) ? sections : []).flatMap((s) => (
    (s.rows || []).map((row) => ({ ...row, dry_run_file_id: s.fileId, model_source: s.sourceRef }))
  ));
}

/**
 * collectMappingOverrides — the project config's `mappings_overrides` list.
 * Single-model: exactly the existing shape ({ source_path, target_name }).
 * Multi-model: the same entries, each additionally keyed by `model` (that
 * model's entry in source.models -- PBIX path or Fabric model name) so one
 * model's rename can never be applied to another model's same-path field.
 */
export function collectMappingOverrides({ rows = [], sections = null } = {}) {
  const toOverride = (row) => ({
    source_path: String(row?.source_path || '').trim(),
    target_name: String(row?.target_field || row?.target_name || row?.target || '').trim(),
  });
  if (Array.isArray(sections) && sections.length > 0) {
    return sections.flatMap((s) => (s.rows || [])
      .map((row) => ({ model: s.sourceRef || s.label, ...toOverride(row) }))
      .filter((o) => o.source_path && o.target_name));
  }
  return (Array.isArray(rows) ? rows : []).map(toOverride).filter((o) => o.source_path && o.target_name);
}

/**
 * buildDryRunMigrationFields — which dry run / preview project(s) the user's
 * dry-run-time edits (e.g. synonyms) were saved against, sent on project
 * create / edit-save so the backend can migrate them to the real project.
 * Single: { preview_project_id } from the /dry-run response.
 * Multi: { dry_run_job_id, preview_project_ids } (every model's preview id).
 * The literal 'preview' placeholder is never sent.
 */
export function buildDryRunMigrationFields({ multiJobId = '', sections = [], singlePreviewProjectId = '' } = {}) {
  const clean = (id) => {
    const text = String(id || '').trim();
    return text && text !== 'preview' ? text : '';
  };
  if (multiJobId) {
    const ids = [...new Set((sections || [])
      .map((section) => clean(section.previewProjectId || section.result?.project_id))
      .filter(Boolean))];
    return { dry_run_job_id: multiJobId, ...(ids.length > 0 ? { preview_project_ids: ids } : {}) };
  }
  const single = clean(singlePreviewProjectId);
  return single ? { preview_project_id: single } : {};
}
