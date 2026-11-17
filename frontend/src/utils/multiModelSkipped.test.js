/**
 * A model with no tables (thin report) is "skipped": it does not block the
 * other models, but a failed model still blocks and a project where every
 * model was skipped has nothing to deploy. Same rule as the server gate
 * (dry_run_job_service.validate_multi_model_dry_run_readiness).
 *
 * Run: node --test src/utils/multiModelSkipped.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isTerminalFileStatus, normalizeFileStatus, resolveMultiModelReadiness } from './multiFileDryRunReadiness.js';

const ok = (label) => ({ label, status: 'success', loaded: true, blockingCount: 0 });
const skipped = (label) => ({ label, status: 'skipped', loaded: false, blockingCount: 0 });
const failed = (label) => ({ label, status: 'failed', loaded: false, blockingCount: 0 });

test('skipped is its own terminal status', () => {
  assert.equal(normalizeFileStatus('skipped'), 'skipped');
  assert.equal(isTerminalFileStatus('skipped'), true);
});

test('a skipped model does not block the others', () => {
  const r = resolveMultiModelReadiness([ok('Sales'), skipped('Thin')]);
  assert.equal(r.ready, true);
  assert.deepEqual(r.skippedModels, ['Thin']);
});

test('a failed model still blocks', () => {
  assert.equal(resolveMultiModelReadiness([ok('Sales'), skipped('Thin'), failed('Broken')]).ready, false);
});

test('nothing to deploy when every model was skipped', () => {
  assert.equal(resolveMultiModelReadiness([skipped('A'), skipped('B')]).ready, false);
});
