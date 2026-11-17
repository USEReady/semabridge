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
import {
  resolveMultiFileDryRunReadiness,
  formatZeroTablesReason,
  resolveMultiModelReadiness,
  getSyncButtonProps,
  normalizeFileStatus,
  isTerminalFileStatus,
} from './multiFileDryRunReadiness.js';

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

test('normalizeFileStatus and isTerminalFileStatus support skipped_no_tables', () => {
  assert.equal(normalizeFileStatus('skipped_no_tables'), 'skipped_no_tables');
  assert.equal(normalizeFileStatus('SKIPPED'), 'skipped_no_tables');
  assert.equal(isTerminalFileStatus('skipped_no_tables'), true);
});

test('formatZeroTablesReason formats all 5 reason codes correctly', () => {
  // 1. LIVE_CONNECTION
  assert.equal(
    formatZeroTablesReason({ reasonCode: 'LIVE_CONNECTION', modelReference: 'RemoteModel' }),
    'Points to RemoteModel on Power BI Service'
  );
  // 2. ONLY_AUTO_DATE_TABLES
  assert.equal(
    formatZeroTablesReason({ reasonCode: 'ONLY_AUTO_DATE_TABLES' }),
    'Only auto date/time tables found (excluded by design)'
  );
  // 3. ALL_TABLES_FAILED
  assert.equal(
    formatZeroTablesReason({ reasonCode: 'ALL_TABLES_FAILED' }),
    'All tables failed extraction or validation'
  );
  // 4. MEASURES_ONLY
  assert.equal(
    formatZeroTablesReason({ reasonCode: 'MEASURES_ONLY', measures: [{ name: 'Total Sales' }, { name: 'Profit Margin' }] }),
    'Model contains measures but no physical tables: Total Sales, Profit Margin'
  );
  assert.equal(
    formatZeroTablesReason({ reasonCode: 'MEASURES_ONLY', measures: [] }),
    'Model contains measures but no physical tables'
  );
  // 5. EMPTY_MODEL
  assert.equal(
    formatZeroTablesReason({ reasonCode: 'EMPTY_MODEL' }),
    'Model has no tables or fields'
  );
});

test('resolveMultiModelReadiness handles skipped_no_tables models and computes N of M', () => {
  const sections = [
    { fileId: 'f1', label: 'Sales', status: 'success', loaded: true, rows: [{ id: '1', status: 'auto' }], blockingCount: 0, compatibilityScore: 95 },
    { fileId: 'f2', label: 'HR Live', status: 'skipped_no_tables', reasonCode: 'LIVE_CONNECTION', modelReference: 'HR Live Model' },
  ];

  const readiness = resolveMultiModelReadiness(sections);
  assert.equal(readiness.total, 2);
  assert.equal(readiness.validCount, 1);
  assert.equal(readiness.skippedCount, 1);
  assert.equal(readiness.failedCount, 0);
  assert.equal(readiness.allProcessed, true);
  assert.equal(readiness.ready, true, 'Ready because 1 model is valid and the other is safely skipped');

  const btnProps = getSyncButtonProps(readiness);
  assert.equal(btnProps.label, 'Create 1 of 2 semantic views');
  assert.equal(btnProps.disabled, false);
});

test('getSyncButtonProps returns "Nothing to create" and disabled when N = 0', () => {
  const sections = [
    { fileId: 'f1', label: 'LiveOnly', status: 'skipped_no_tables', reasonCode: 'LIVE_CONNECTION' },
  ];

  const readiness = resolveMultiModelReadiness(sections);
  assert.equal(readiness.validCount, 0);
  assert.equal(readiness.ready, false);

  const btnProps = getSyncButtonProps(readiness);
  assert.equal(btnProps.label, 'Nothing to create');
  assert.equal(btnProps.disabled, true);
});
