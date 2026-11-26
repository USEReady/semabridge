/**
 * databricksPlan -- reading the backend's Databricks metric-view plan
 * (databricks_view_plan) on the screen. The planner is authoritative for
 * final names and clashes: nothing here derives a name; it only matches plan
 * entries to dry-run rows by source identity (table + column, or measure).
 */
import { collectMappingOverrides } from './multiFileDryRunReadiness.js';

const fieldPath = (dataset, column) => `datasets.${dataset}.columns.${column}`;
const measurePath = (name) => `metrics.${name}`;

/** Map source_path -> [{ view, name, kind }] (a field can be in several views). */
export function plannedNamesBySourcePath(plan) {
  const out = new Map();
  const add = (path, entry) => {
    if (!out.has(path)) out.set(path, []);
    out.get(path).push(entry);
  };
  (plan?.views || []).forEach((view) => {
    (view.fields || []).forEach((f) => add(fieldPath(f.dataset, f.column), { view: view.name, name: f.name, kind: 'field' }));
    (view.measures || []).forEach((m) => add(measurePath(m.source_name), { view: view.name, name: m.name, kind: 'measure' }));
  });
  return out;
}

/** source_path -> the plan's outcome for a saved name (name_requests). */
export function nameRequestsBySourcePath(plan) {
  const out = new Map();
  (plan?.name_requests || []).forEach((r) => out.set(String(r.source_path), r));
  return out;
}

/** Measures the user can untick: every approximate placed measure, plus the
 * ones already excluded (so they can be ticked back). */
export function approximateMeasureChoices(plan) {
  const excluded = new Set(plan?.settings?.excluded_measures || []);
  const items = [];
  (plan?.views || []).forEach((view) => (view.measures || []).forEach((m) => {
    if (m.semantic_status === 'approximate') {
      items.push({ source_name: m.source_name, name: m.name, view: view.name, comment: m.comment || '', excluded: false });
    }
  }));
  (plan?.left_out || []).forEach((lo) => {
    if (excluded.has(lo.measure)) items.push({ source_name: lo.measure, name: '', view: '', comment: lo.detail || '', excluded: true });
  });
  return items.sort((a, b) => a.source_name.localeCompare(b.source_name));
}

/** Measures left out only because a measure they depend on was unticked. */
export function excludedDependents(plan) {
  const excluded = new Set(plan?.settings?.excluded_measures || []);
  return (plan?.left_out || []).filter((lo) => !excluded.has(lo.measure)
    && (lo.reason === 'depends_on' || lo.reason === 'user_excluded') && (lo.root_cause || lo.detail));
}

export function planStatus(plan) {
  const meta = plan?.meta || {};
  return { status: meta.status || 'valid', revision: Number(meta.revision || 0), error: meta.error || '',
    basis: meta.basis || {}, cacheRef: meta.cache_ref || null };
}

/** Body for POST /projects/{id}/databricks/replan. */
export function buildReplanBody({ plan, rows = [], sections = null, planner = null, revision = Date.now() }) {
  const ref = plan?.meta?.cache_ref || {};
  return {
    cache_project_id: ref.project_id || undefined,
    model_subdir: ref.model_subdir || undefined,
    mappings_overrides: collectMappingOverrides(sections ? { sections, targetType: 'databricks' } : { rows, targetType: 'databricks' }),
    ...(planner ? { planner } : {}),
    revision,
  };
}

/** The project id to address a plan request to: the real project, else the wizard ("preview"). */
export function planRequestProjectId(projectId) {
  const id = String(projectId || '').trim();
  return !id || id === 'preview' || id.startsWith('preview-') ? 'preview' : id;
}

/** Keeps only the newest response: an older revision never replaces a newer plan. */
export function newerPlan(current, incoming) {
  if (!incoming) return current;
  if (!current) return incoming;
  return Number(incoming?.meta?.revision || 0) >= Number(current?.meta?.revision || 0) ? incoming : current;
}
