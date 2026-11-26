/**
 * Deploy preview / run helpers (src/utils/databricksDeploy.js).
 *
 * Run: node --test src/utils/databricksDeploy.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { attention, blockingReasons, canConfirm, previewCounts, runFinished } from './databricksDeploy.js';

const preview = {
  model_blockers: [],
  views: [
    { name: 'a', action: 'create', readiness: 'verified_structure', reasons: [] },
    { name: 'b', action: 'update', readiness: 'incomplete_unverified', reasons: [] },
    { name: 'c', action: 'unchanged', readiness: 'verified_structure', reasons: [] },
    { name: 'd', action: 'blocked', readiness: 'verified_structure',
      reasons: [{ code: 'manual_drift', message: 'changed outside SemaBridge' }] },
  ],
  stub_tables: [{ name: 'Store', action: 'create', reasons: [] }],
  sources: [{ name: 'Sales', action: 'blocked', reasons: [{ code: 'source_replaced', message: 'recreated' }] }],
};

test('preview counts', () => {
  assert.deepEqual(previewCounts(preview), { create: 1, update: 1, unchanged: 1, blocked: 1, stubs: 1, incomplete: 1 });
});

test('blocking reasons by scope', () => {
  const r = blockingReasons({ ...preview, model_blockers: [{ code: 'proof_not_valid', message: 'run probes' }] });
  assert.deepEqual(r.model.map((x) => x.code), ['proof_not_valid']);
  assert.deepEqual(r.items.map((x) => `${x.scope}:${x.code}`), ['d:manual_drift', 'Sales:source_replaced']);
});

test('confirm needs an intent and, when asked, the access acknowledgement', () => {
  assert.equal(canConfirm({ intent: null }, true), false);
  assert.equal(canConfirm({ intent: { requires_access_acknowledgement: true } }, false), false);
  assert.equal(canConfirm({ intent: { requires_access_acknowledgement: true } }, true), true);
  assert.equal(canConfirm({ intent: { requires_access_acknowledgement: false } }, false), true);
});

test('run state and attention', () => {
  assert.equal(runFinished({ status: 'running' }), false);
  assert.equal(runFinished({ status: 'partial' }), true);
  const a = attention({ status: 'uncertain', summary: { results: [
    { outcome: 'created' }, { outcome: 'applied_unverified' }, { outcome: 'failed' }] } });
  assert.equal(a.unverified.length, 1);
  assert.equal(a.failed.length, 1);
  assert.equal(a.needsReconcile, true);
});
