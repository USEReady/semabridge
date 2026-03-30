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

export function buildMockRunLogs(run = {}) {
  const startedAt = timestampFromIso(run.started_at);
  const normalizedStatus = String(run.status || '').toLowerCase();
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
  const store = readStore();
  const stored = store[String(run.id)];
  if (Array.isArray(stored) && stored.length) return stored;
  return buildMockRunLogs(run);
}
