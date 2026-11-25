/**
 * Non-fatal run warnings (source_warnings / target_warnings from the backend
 * RunSummary) for the Runs page. A run can succeed and still carry warnings,
 * e.g. Databricks metric views published structure-only (no data) because
 * their source tables are missing. Collected from the run-level summary and
 * every per-model result, de-duplicated, in order.
 */

function warningsOf(summary) {
  if (!summary || typeof summary !== 'object') return [];
  return [
    ...(Array.isArray(summary.source_warnings) ? summary.source_warnings : []),
    ...(Array.isArray(summary.target_warnings) ? summary.target_warnings : []),
  ];
}

export function getRunWarnings(run) {
  if (!run || typeof run !== 'object') return [];
  const all = [...warningsOf(run.summary)];
  (Array.isArray(run.results) ? run.results : []).forEach((r) => {
    if (r && typeof r === 'object') all.push(...warningsOf(r.summary));
  });
  const seen = new Set();
  const out = [];
  all.forEach((w) => {
    const text = String(w ?? '').trim();
    if (!text || seen.has(text)) return;
    seen.add(text);
    out.push(text);
  });
  return out;
}

export function isStructureOnlyWarning(text) {
  return String(text || '').startsWith('Structure only (no data)');
}

/** Short badge label for the Runs table, or null when there is nothing to flag. */
export function runWarningBadge(run) {
  const warnings = getRunWarnings(run);
  if (!warnings.length) return null;
  const structureOnly = warnings.some(isStructureOnlyWarning);
  return {
    label: structureOnly ? 'Structure only' : `${warnings.length} warning${warnings.length === 1 ? '' : 's'}`,
    title: warnings.join('\n\n'),
    count: warnings.length,
    structureOnly,
  };
}
