/**
 * multiFileDryRunReadiness.test.js
 *
 * Regression test for the bug: StepMappingOptions.jsx's "READY TO
 * PROCEED — MAPPING VALIDATION COMPLETE" banner and Proceed/Continue
 * buttons were gated on dryRunStatus, which CreateProjectPage.jsx sets to
 * 'success' the instant a multi-file background dry-run job is CREATED --
 * not once every file has actually finished. Confirmed via screenshot:
 * two files both still showing "Running" while the banner and both
 * buttons were already active.
 *
 * Run: node --test src/utils/multiFileDryRunReadiness.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resolveMultiFileDryRunReadiness } from './multiFileDryRunReadiness.js';

test('no poll has landed yet (job just created) -> running, not ready', () => {
  const result = resolveMultiFileDryRunReadiness(null);
  assert.equal(result.running, true);
  assert.equal(result.succeeded, false);
  assert.equal(result.hasFailures, false);
});

test('backend aggregate status "pending" -> running, not ready (this is the reported bug\'s exact shape)', () => {
  const result = resolveMultiFileDryRunReadiness({
    status: 'pending',
    files: [{ file_id: 'a', status: 'pending' }, { file_id: 'b', status: 'pending' }],
  });
  assert.equal(result.running, true);
  assert.equal(result.succeeded, false, 'must never report ready while files are still pending');
});

test('backend aggregate status "running" with files still Running -> running, not ready', () => {
  const result = resolveMultiFileDryRunReadiness({
    status: 'running',
    files: [{ file_id: 'a', status: 'running' }, { file_id: 'b', status: 'running' }],
  });
  assert.equal(result.running, true);
  assert.equal(result.succeeded, false, 'must never report ready while any file is still Running');
  assert.equal(result.hasFailures, false);
});

test('backend aggregate status "success" (every file succeeded) -> ready, not running, no failures', () => {
  const result = resolveMultiFileDryRunReadiness({
    status: 'success',
    files: [{ file_id: 'a', status: 'success' }, { file_id: 'b', status: 'success' }],
  });
  assert.equal(result.running, false);
  assert.equal(result.succeeded, true);
  assert.equal(result.hasFailures, false);
});

test('backend aggregate status "failed" (every file failed) -> not running, not ready, has failures', () => {
  const result = resolveMultiFileDryRunReadiness({
    status: 'failed',
    files: [{ file_id: 'a', status: 'failed' }, { file_id: 'b', status: 'failed' }],
  });
  assert.equal(result.running, false);
  assert.equal(result.succeeded, false, 'must never report ready when every file failed');
  assert.equal(result.hasFailures, true);
});

test('backend aggregate status "partial" (mixed success/failed) -> not running, not ready, has failures', () => {
  const result = resolveMultiFileDryRunReadiness({
    status: 'partial',
    files: [{ file_id: 'a', status: 'success' }, { file_id: 'b', status: 'failed' }],
  });
  assert.equal(result.running, false);
  assert.equal(result.succeeded, false, 'a mixed success/failed outcome must never claim blanket readiness');
  assert.equal(result.hasFailures, true);
});
