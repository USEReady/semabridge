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
  const topLevelSummaryLines = collectStepLogs(run.summary);
  if (topLevelSummaryLines.length) {
    lines.push(...topLevelSummaryLines);
  }

  const results = Array.isArray(run.results) ? run.results : [];
  results.forEach((result) => {
    const modelName = result?.model ? `[${result.model}]` : '[Model]';
    lines.push(...collectStepLogs(result?.summary, modelName));
  });

  if (lines.length) {
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
  return buildMockRunLogs(run);
}
