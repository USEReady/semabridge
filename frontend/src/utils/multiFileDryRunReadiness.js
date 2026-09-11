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
 * `jobStatus` is exactly what MultiFileDryRunStatus.jsx's poll loop passes
 * to its onStatusChange callback -- api.getDryRunJobStatus()'s raw
 * response, `{ status, files: [...] }`, where `status` is the backend's
 * _aggregate_job_status() result: "pending" | "running" | "success" |
 * "failed" | "partial". `null` means no poll has landed yet (job was just
 * created, or a brand new job replaced a previous one).
 */
export function resolveMultiFileDryRunReadiness(jobStatus) {
  const status = jobStatus?.status ?? null;
  const running = status === null || status === 'running' || status === 'pending';
  const succeeded = status === 'success';
  const hasFailures = status === 'failed' || status === 'partial';
  return { running, succeeded, hasFailures };
}
