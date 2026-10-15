import { cleanPbixLabel } from './pbixLabel.js';

const STORAGE_KEY = 'semabridge_run_logs_v1';

function safeParse(value) {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function readStore() {
  if (typeof window === 'undefined') return {};
  return safeParse(window.localStorage.getItem(STORAGE_KEY));
}

function writeStore(store) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
}

function timestampFromIso(iso) {
  const date = iso ? new Date(iso) : new Date();
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  const ss = String(date.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}

function normalizeStatus(status) {
  return String(status || '').trim().toLowerCase();
}

function levelFromStatus(status) {
  const normalized = normalizeStatus(status);
  if (normalized === 'failed') return 'ERROR';
  if (normalized === 'skipped') return 'SKIP';
  if (normalized === 'running') return 'LIVE';
  if (normalized === 'success') return 'SUCCESS';
  return 'INFO';
}

const LIVE_RUN_STAGES = [
  'Validating source format...',
  'Converting metadata to SML...',
  'Parsing semantic view...',
  'Persisting generated artifacts...',
  'Generating TMSL payload...',
  'Deploying semantic model to Fabric...',
];

function buildProgressiveRunLogs(run = {}, seedLines = []) {
  if (normalizeStatus(run.status) !== 'running') return [];

  const startedAtMs = run.started_at ? Date.parse(run.started_at) : NaN;
  const elapsedMs = Number.isFinite(startedAtMs) ? Math.max(0, Date.now() - startedAtMs) : 0;
  const visibleStageCount = Math.min(LIVE_RUN_STAGES.length, Math.max(1, Math.floor(elapsedMs / 3500) + 1));
  const lines = Array.isArray(seedLines) && seedLines.length
    ? seedLines.map((line) => String(line))
    : [`LIVE ${timestampFromIso(run.started_at)} Run queued. Waiting for execution engine...`];

  LIVE_RUN_STAGES.slice(0, visibleStageCount).forEach((stage, index) => {
    if (lines.some((line) => line.includes(stage))) return;
    const timestamp = timestampFromIso((startedAtMs || Date.now()) + ((index + 1) * 3500));
    lines.push(`LIVE ${timestamp} ${stage}`);
  });

  return lines;
}

function collectStepLogs(summary, prefix = '') {
  const steps = Array.isArray(summary?.steps_completed) ? summary.steps_completed : [];
  const errors = Array.isArray(summary?.errors) ? summary.errors : [];
  const lines = [];

  steps.forEach((step) => {
    const timestamp = timestampFromIso(step?.completed_at || step?.started_at || summary?.started_at);
    const level = levelFromStatus(step?.status);
    const scope = prefix ? `${prefix} ` : '';
    const detail = step?.message ? ` - ${step.message}` : '';
    lines.push(
      `${level} ${timestamp} ${scope}Stage ${step?.step_number ?? '?'}: ${step?.step_name || 'Unknown'}${detail}`
    );
  });

  errors.forEach((error) => {
    const timestamp = timestampFromIso(summary?.completed_at || summary?.started_at);
    const scope = prefix ? `${prefix} ` : '';
    const stepPart = error?.step_number ? `Stage ${error.step_number}` : 'Run';
    const stepName = error?.step_name ? ` (${error.step_name})` : '';
    const message = error?.message || 'Unknown error';
    lines.push(`ERROR ${timestamp} ${scope}${stepPart}${stepName} - ${message}`);
  });

  return lines;
}

function buildSummaryRunLogs(run = {}) {
  const lines = [];

  // The backend (_build_run_logs in run_service.py) already emits one
  // complete, timestamped, correctly-scoped line per stage. Re-deriving the
  // same lines here from run.summary / run.results independently used to
  // double-log (and with the bracket/no-bracket split, quadruple-log) every
  // stage. Only fall back to local reconstruction when the backend response
  // doesn't include logs at all.
  if (Array.isArray(run.logs) && run.logs.length) {
    lines.push(...run.logs.map((line) => String(line)));
  } else {
    const topLevelSummaryLines = collectStepLogs(run.summary);
    if (topLevelSummaryLines.length) {
      lines.push(...topLevelSummaryLines);
    }

    const results = Array.isArray(run.results) ? run.results : [];
    results.forEach((result) => {
      const modelName = result?.model ? `[${result.model}]` : '[Model]';
      lines.push(...collectStepLogs(result?.summary, modelName));
    });
  }

  if (lines.length) {
    if (run.message) {
      lines.push(`INFO ${timestampFromIso(run.started_at)} ${String(run.message)}`);
    }
    if (run.error) {
      lines.push(`ERROR ${timestampFromIso(run.completed_at || run.started_at)} ${String(run.error)}`);
    }
    return lines;
  }

  return [];
}

export function buildMockRunLogs(run = {}) {
  const startedAt = timestampFromIso(run.started_at);
  const normalizedStatus = normalizeStatus(run.status);
  const lines = [
    `INFO ${startedAt} Validating source format...`,
    `INFO ${startedAt} Converting metadata to SML...`,
    `INFO ${startedAt} Parsing semantic view...`,
    `INFO ${startedAt} Persisting generated artifacts...`,
    `INFO ${startedAt} Generating TMSL payload...`,
  ];

  if (normalizedStatus === 'failed') {
    lines.push(`ERROR ${startedAt} Deploy to Fabric failed during execution.`);
  } else if (normalizedStatus === 'running') {
    lines.push(`INFO ${startedAt} Deploying semantic model to Fabric...`);
  } else {
    lines.push(`INFO ${startedAt} Deploying semantic model to Fabric...`);
    lines.push(`SUCCESS ${startedAt} Sync completed successfully.`);
  }

  return lines;
}

export function saveRunLogs(runId, logs) {
  if (!runId || !Array.isArray(logs) || !logs.length) return;
  const store = readStore();
  store[String(runId)] = logs;
  writeStore(store);
}

export function getRunLogs(run) {
  if (!run) return [];
  const summaryLogs = buildSummaryRunLogs(run);
  if (summaryLogs.length) return summaryLogs;
  const store = readStore();
  const stored = store[String(run.id)];
  if (Array.isArray(stored) && stored.length) return stored;
  return [];
}

// ---------------------------------------------------------------------------
// Per-model run breakdown (multi-model runs only)
// ---------------------------------------------------------------------------

function consoleLinesForResult(result) {
  const consoleData = result?.console;
  if (Array.isArray(consoleData?.lines) && consoleData.lines.length) {
    return consoleData.lines.map((line) => String(line));
  }
  if (typeof consoleData?.text === 'string' && consoleData.text.trim()) {
    return consoleData.text.replace(/\r\n/g, '\n').split('\n').filter((line) => line.length);
  }
  if (Array.isArray(result?.logs) && result.logs.length) {
    return result.logs.map((line) => String(line));
  }
  return collectStepLogs(result?.summary);
}

function durationMsForResult(result) {
  const direct = Number(result?.duration_ms ?? result?.summary?.duration_ms);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const started = Date.parse(result?.summary?.started_at || result?.started_at || '');
  const completed = Date.parse(result?.summary?.completed_at || result?.completed_at || '');
  if (Number.isFinite(started) && Number.isFinite(completed) && completed >= started) {
    return completed - started;
  }
  return null;
}

/**
 * Splits a multi-model run into one display entry per model, merging
 * run.results[] (status/logs/dropped fields) with run.model_reports[]
 * (per-model report availability) by model label. Raw labels are matched
 * first, then their cleanPbixLabel() form (so a path vs. basename mismatch
 * still pairs up).
 *
 * Returns [] for single-model runs (results <= 1 AND model_reports <= 1) so
 * callers keep rendering the combined run view unchanged.
 *
 * Entry shape:
 *   { key, model, label, status, viewName, targetArtifactPath, durationMs,
 *     logs[], reportEntry, reportModel, droppedEntities[], reconciliation,
 *     hasResult }
 */
export function getPerModelRunEntries(run) {
  if (!run || typeof run !== 'object') return [];

  const results = (Array.isArray(run?.results) ? run.results : [])
    .filter((r) => r && typeof r === 'object');
  const reports = (Array.isArray(run?.model_reports) ? run.model_reports : [])
    .filter((e) => e && typeof e === 'object' && e.model);

  // If no per-model results or reports were provided, synthesize a single-model
  // entry from the run-level fields so that single-model runs render their
  // status, report preview, logs, reconciliation, and dropped fields under
  // the model cleanly.
  if (results.length === 0 && reports.length === 0) {
    const rawModel = run.source_model_name || run.model || run.view_name || run.summary?.view_name || run.project_name || 'Target Model';
    const droppedEntities = (
      (Array.isArray(run.dropped_entities) && run.dropped_entities.length > 0 && run.dropped_entities) ||
      (Array.isArray(run.summary?.dropped_entities) && run.summary.dropped_entities.length > 0 && run.summary.dropped_entities) ||
      (Array.isArray(run.summary?.drop_ledger) && run.summary.drop_ledger.length > 0 && run.summary.drop_ledger) ||
      []
    );
    const reconciliation = run.reconciliation || run.summary?.reconciliation || null;
    return [{
      key: 'model-single-1',
      model: rawModel,
      label: cleanPbixLabel(rawModel) || rawModel,
      status: normalizeStatus(run.status) || 'unknown',
      viewName: run.view_name || run.summary?.view_name || null,
      targetArtifactPath: run.target_artifact_path || null,
      durationMs: Number(run.duration_ms) || null,
      logs: buildSummaryRunLogs(run),
      reportEntry: null,
      reportModel: (run.has_report || run.report_path) ? rawModel : null,
      has_report: Boolean(run.has_report || run.report_path),
      has_summary: Boolean(run.has_summary),
      has_log: true,
      droppedEntities,
      reconciliation: reconciliation && typeof reconciliation === 'object' ? reconciliation : null,
      upsertDelta: run.upsert_delta || run.summary?.upsert_delta || null,
      hasResult: true,
    }];
  }

  const usedReports = new Set();
  const findReport = (rawModel) => {
    if (!rawModel) return null;
    const raw = String(rawModel);
    let idx = reports.findIndex((e, i) => !usedReports.has(i) && String(e.model) === raw);
    if (idx < 0) {
      const clean = cleanPbixLabel(raw);
      idx = reports.findIndex((e, i) => !usedReports.has(i) && cleanPbixLabel(e.model) === clean);
    }
    if (idx < 0) return null;
    usedReports.add(idx);
    return reports[idx];
  };

  const usedKeys = new Set();
  const uniqueKey = (base) => {
    let key = base;
    let n = 2;
    while (usedKeys.has(key)) key = `${base}#${n++}`;
    usedKeys.add(key);
    return key;
  };

  const entries = results.map((result, index) => {
    const model = result.model ? String(result.model) : '';
    const reportEntry = findReport(model);
    const reconciliation = result.reconciliation || result.summary?.reconciliation || null;
    const hasReport = result.has_report !== undefined ? Boolean(result.has_report) : Boolean(reportEntry);
    const hasSummary = result.has_summary !== undefined ? Boolean(result.has_summary) : hasReport;
    const hasLog = result.has_log !== undefined ? Boolean(result.has_log) : (result.logs?.length > 0 || (result.console?.lines?.length > 0));
    const droppedEntities = (
      (Array.isArray(result.dropped_entities) && result.dropped_entities.length > 0 && result.dropped_entities) ||
      (Array.isArray(result.summary?.dropped_entities) && result.summary.dropped_entities.length > 0 && result.summary.dropped_entities) ||
      (Array.isArray(result.summary?.drop_ledger) && result.summary.drop_ledger.length > 0 && result.summary.drop_ledger) ||
      (results.length === 1 && Array.isArray(run?.dropped_entities) && run.dropped_entities.length > 0 && run.dropped_entities) ||
      (results.length === 1 && Array.isArray(run?.summary?.dropped_entities) && run.summary.dropped_entities.length > 0 && run.summary.dropped_entities) ||
      (results.length === 1 && Array.isArray(run?.summary?.drop_ledger) && run.summary.drop_ledger.length > 0 && run.summary.drop_ledger) ||
      []
    );

    return {
      key: uniqueKey(model || `model-${index + 1}`),
      model: model || null,
      label: cleanPbixLabel(model) || `Model ${index + 1}`,
      status: normalizeStatus(result.status || result.summary?.status) || 'unknown',
      viewName: result.view_name || result.summary?.view_name || null,
      targetArtifactPath: result.target_artifact_path || null,
      durationMs: durationMsForResult(result),
      logs: consoleLinesForResult(result),
      reportEntry,
      reportModel: reportEntry ? String(reportEntry.model) : (hasReport && model ? model : null),
      has_report: hasReport,
      has_summary: hasSummary,
      has_log: hasLog,
      droppedEntities,
      reconciliation: reconciliation && typeof reconciliation === 'object' ? reconciliation : null,
      upsertDelta: result.upsert_delta || result.summary?.upsert_delta || null,
      hasResult: true,
    };
  });

  reports.forEach((reportEntry, index) => {
    if (usedReports.has(index)) return;
    const model = String(reportEntry.model);
    entries.push({
      key: uniqueKey(model),
      model,
      label: cleanPbixLabel(model) || model,
      status: 'unknown',
      viewName: null,
      targetArtifactPath: null,
      durationMs: null,
      logs: [],
      reportEntry,
      reportModel: model,
      droppedEntities: [],
      reconciliation: null,
      upsertDelta: null,
      hasResult: false,
    });
  });

  return entries;
}

export { collectStepLogs };
