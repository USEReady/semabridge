import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDuplicateSyncError, useDuplicateSyncPrompt } from './duplicateSyncPrompt.js';

test('isDuplicateSyncError recognizes 409 target_view_exists', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        status: 'target_view_exists',
        view_name: 'SEMABRIDGE.TEST.SALES_VIEW',
        upsert_available: true,
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), true);
});

test('isDuplicateSyncError recognizes 409 with view_name even without explicit status', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        view_name: 'ANALYTICS.PUBLIC.REVENUE',
        owner_project_id: 'proj-123',
        upsert_available: false,
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), true);
});

test('isDuplicateSyncError recognizes 409 legacy duplicates', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        status: 'duplicate',
        duplicates: [{ model: 'Sales', view_name: 'SALES_VIEW' }],
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), true);
});

test('isDuplicateSyncError TRAP AVOIDANCE: ignores 409 project busy', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        status: 'busy',
        message: 'Project is currently syncing.',
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), false, 'Must not open dialog for a busy project');
});

test('isDuplicateSyncError TRAP AVOIDANCE: ignores 409 PROJECT_BUSY code', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        code: 'PROJECT_BUSY',
        status: 'running',
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), false);
});

test('isDuplicateSyncError ignores non-409 errors', () => {
  assert.equal(isDuplicateSyncError({ status: 500, payload: { detail: { view_name: 'V' } } }), false);
  assert.equal(isDuplicateSyncError({ status: 400 }), false);
  assert.equal(isDuplicateSyncError(null), false);
});

test('isDuplicateSyncError recognizes 409 Match A (identical file)', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        match_type: 'A',
        reason: 'identical, nothing to update',
        matched_project_id: 'proj-hua-man',
        project_name: 'Hua=man',
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), true);
});

test('isDuplicateSyncError recognizes 409 Match B (same source, changed file)', () => {
  const err = {
    status: 409,
    payload: {
      detail: {
        match_type: 'B',
        reason: 'same source identity with different content',
        matched_project_id: 'proj-sales',
      },
    },
  };
  assert.equal(isDuplicateSyncError(err), true);
});

test('useDuplicateSyncPrompt stores detail and notifies resolve callback', () => {
  let resolvedVal = null;
  const mockResolve = (val) => { resolvedVal = val; };

  useDuplicateSyncPrompt.getState().show({
    view_name: 'DB.SCHEMA.VIEW',
    owner_project_id: 'proj-other',
    upsert_available: true,
  }, mockResolve);

  const state = useDuplicateSyncPrompt.getState();
  assert.equal(state.open, true);
  assert.equal(state.detail?.view_name, 'DB.SCHEMA.VIEW');
  assert.equal(state.detail?.upsert_available, true);

  // Invoke resolve with user choice
  state.resolve?.({ sync_mode: 'upsert' });
  assert.deepEqual(resolvedVal, { sync_mode: 'upsert' });

  useDuplicateSyncPrompt.getState().close();
  assert.equal(useDuplicateSyncPrompt.getState().open, false);
});
