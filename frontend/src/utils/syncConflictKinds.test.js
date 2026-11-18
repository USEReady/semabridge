/**
 * Run: node --test src/utils/syncConflictKinds.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { syncConflictKind } from './duplicateSyncPrompt.js';

const err = (status, detail) => ({ status, payload: { detail }, message: '' });

test('each refused sync start maps to its dialog', () => {
  assert.equal(syncConflictKind(err(409, { status: 'duplicate' })), 'duplicate');
  assert.equal(syncConflictKind(err(409, { status: 'removed_fields', models: [] })), 'removed_fields');
  assert.equal(syncConflictKind(err(409, { status: 'already_running' })), 'already_running');
  assert.equal(syncConflictKind(err(409, { status: 'blocked' })), 'blocked');
  assert.equal(syncConflictKind(err(409, { status: 'duplicate_check_failed' })), 'duplicate_check_failed');
  assert.equal(
    syncConflictKind(err(409, { status: 'removed_fields', models: [], unverifiable: [{ model: 'Fin' }] })),
    'removed_fields',
  );
  assert.equal(syncConflictKind(err(500, 'boom')), '');
});
