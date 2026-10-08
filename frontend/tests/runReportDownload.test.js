import test from 'node:test';
import assert from 'node:assert/strict';

import { buildRunReportUrl, resolveReportFilename } from '../src/utils/runReportDownload.js';

// These back api.downloadRunReport(), which VersionControlPage.jsx's
// "Download Report" action calls with the currently selected run's
// project/run IDs. See run_service.py's download_run_report route
// (GET /api/projects/{project_id}/runs/{run_id}/report) for the backend
// side this must match exactly.

test('buildRunReportUrl targets the exact project and run', () => {
  assert.equal(
    buildRunReportUrl('/api', 'proj-summary-test', 'run-1786987828903'),
    '/api/projects/proj-summary-test/runs/run-1786987828903/report',
  );
});

test('buildRunReportUrl differs for different project/run IDs -- never a shared/generic endpoint', () => {
  const url = buildRunReportUrl('/api', 'proj-a', 'run-1');
  assert.notEqual(url, buildRunReportUrl('/api', 'proj-b', 'run-1'));
  assert.notEqual(url, buildRunReportUrl('/api', 'proj-a', 'run-2'));
});

test('buildRunReportUrl honors a custom API base URL', () => {
  assert.equal(
    buildRunReportUrl('https://example.test/api', 'proj-1', 'run-1'),
    'https://example.test/api/projects/proj-1/runs/run-1/report',
  );
});

test('resolveReportFilename prefers the server Content-Disposition filename', () => {
  const cd = 'attachment; filename="summary-test_run-1786987828903_report.md"';
  assert.equal(
    resolveReportFilename(cd, 'proj-summary-test', 'run-1786987828903'),
    'summary-test_run-1786987828903_report.md',
  );
});

test('resolveReportFilename falls back to a project/run-scoped name when the header is missing', () => {
  assert.equal(
    resolveReportFilename('', 'proj-summary-test', 'run-1786987828903'),
    'proj-summary-test_run-1786987828903_report.md',
  );
  assert.equal(
    resolveReportFilename(null, 'proj-summary-test', 'run-1786987828903'),
    'proj-summary-test_run-1786987828903_report.md',
  );
});

test('resolveReportFilename falls back when Content-Disposition has no filename= directive', () => {
  assert.equal(
    resolveReportFilename('attachment', 'proj-x', 'run-y'),
    'proj-x_run-y_report.md',
  );
});

// This is the case the whole feature exists to fix: a run that FAILED (or
// finished with warnings/partial success) still has a real report on disk
// -- write_run_report is unconditional in _perform_project_run's `finally`
// block -- so the download action must resolve the identical URL shape
// regardless of the run's outcome. There is no separate "failure" endpoint
// or filename shape; the same function serves both.
test('the same URL/filename shape applies to a failed run as to a successful one', () => {
  const successUrl = buildRunReportUrl('/api', 'proj-summary-test', 'run-success-1');
  const failedUrl = buildRunReportUrl('/api', 'proj-summary-test', 'run-failed-1');
  assert.equal(successUrl, '/api/projects/proj-summary-test/runs/run-success-1/report');
  assert.equal(failedUrl, '/api/projects/proj-summary-test/runs/run-failed-1/report');

  const successName = resolveReportFilename(
    'attachment; filename="summary-test_run-success-1_report.md"', 'proj-summary-test', 'run-success-1',
  );
  const failedName = resolveReportFilename(
    'attachment; filename="summary-test_run-failed-1_report.md"', 'proj-summary-test', 'run-failed-1',
  );
  assert.equal(successName, 'summary-test_run-success-1_report.md');
  assert.equal(failedName, 'summary-test_run-failed-1_report.md');
});
