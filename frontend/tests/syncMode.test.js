import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeSyncMode, resolveProjectSyncMode } from '../src/utils/syncMode.js';

test('normalizeSyncMode accepts only copy and upsert', () => {
  assert.equal(normalizeSyncMode('upsert'), 'upsert');
  assert.equal(normalizeSyncMode('COPY'), 'copy');
  assert.equal(normalizeSyncMode('anything-else'), 'copy');
});

test('resolveProjectSyncMode prefers the project record over fallback cache', () => {
  assert.equal(
    resolveProjectSyncMode({ id: 7, sync_mode: 'upsert' }, 'copy'),
    'upsert',
  );
  assert.equal(
    resolveProjectSyncMode({ id: 7, sync_mode: 'copy' }, 'upsert'),
    'copy',
  );
});