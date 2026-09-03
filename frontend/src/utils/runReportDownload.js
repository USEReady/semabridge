// Pure, DOM/fetch-free helpers backing api.js's downloadRunReport() --
// extracted the same way buildDryRunPayload.js / syncMode.js are, so the
// URL/filename logic that matters (which project+run a download actually
// targets) is unit-testable without needing a browser fetch/DOM
// environment, matching this project's existing frontend test style
// (plain node:test against pure functions, see frontend/tests/).

/**
 * Builds the run-report download endpoint URL for one specific
 * project/run pair. Identical for every run outcome (success, warning,
 * partial, or failed) -- the backend's write_run_report() is called
 * unconditionally from _perform_project_run's `finally` block, so this URL
 * is always the right one to fetch regardless of how the run ended.
 */
export function buildRunReportUrl(apiBaseUrl, projectId, runId) {
    return `${apiBaseUrl}/projects/${projectId}/runs/${runId}/report`;
}

/**
 * Same as buildRunReportUrl(), but for one model's own report from a
 * multi-PBIX batch run (see run_report_service.py's
 * write_per_model_run_reports() / the backend's
 * .../runs/{run_id}/report/{model_label} endpoint).
 */
export function buildRunModelReportUrl(apiBaseUrl, projectId, runId, modelLabel) {
    return `${apiBaseUrl}/projects/${projectId}/runs/${runId}/report/${encodeURIComponent(modelLabel)}`;
}

/**
 * JSON counterpart to buildRunReportUrl() -- the accordion-style summary
 * view's data source (RunReportSummary.jsx), built fresh server-side from
 * the run's own snapshot/drop-ledger data rather than downloading the
 * rendered Markdown file.
 */
export function buildRunReportSummaryUrl(apiBaseUrl, projectId, runId) {
    return `${apiBaseUrl}/projects/${projectId}/runs/${runId}/report-summary`;
}

/**
 * Per-model counterpart to buildRunReportSummaryUrl(), for one file's own
 * scoped summary in a multi-PBIX batch run.
 */
export function buildRunModelReportSummaryUrl(apiBaseUrl, projectId, runId, modelLabel) {
    return `${apiBaseUrl}/projects/${projectId}/runs/${runId}/report-summary/${encodeURIComponent(modelLabel)}`;
}

/**
 * Resolves the filename a downloaded report should be saved as: prefers
 * the server's Content-Disposition header (which encodes the project's
 * display name, see run_service.py's get_run_report_compat), falling back
 * to a plain `${projectId}_${runId}_report.md` if the header is missing or
 * unparseable -- never throws, never returns an empty string.
 */
export function resolveReportFilename(contentDispositionHeader, projectId, runId) {
    const cd = contentDispositionHeader || '';
    const match = cd.match(/filename="?([^";]+)"?/);
    return match ? match[1] : `${projectId}_${runId}_report.md`;
}
