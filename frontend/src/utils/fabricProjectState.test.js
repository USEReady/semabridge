/**
 * Batch 5 (frontend, Fabric only): edit-mode restore, separate source/target
 * state, the model picker, and the 401 -> "Sign in again" state.
 * Model listings below are recorded discovery responses (ids scrubbed).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classifyModelDiscovery,
  hydrateFabricEditState,
  isReauthPayload,
  modelPickerOptions,
  selectedModelNames,
} from './fabricProjectState.js';

// GET /api/discovery/fabric/workspaces/{ws}/models (recorded)
const RECORDED_MODELS = [
  { id: '11111111-1111-1111-1111-111111111111', name: 'Sales', displayName: 'Sales' },
  { id: '22222222-2222-2222-2222-222222222222', name: 'Finance', displayName: 'Finance' },
];
// 401 body when the Fabric sign-in must be refreshed (recorded)
const RECORDED_REAUTH = { detail: { error: 'reauth_required', message: 'Fabric token expired' } };

test('edit mode restores the saved connection and keeps source and target apart', () => {
  const state = hydrateFabricEditState({
    source_type: 'fabric', identity_id: 'acct-src', workspace_id: 'ws-src',
    target_type: 'fabric', target_identity_id: 'acct-tgt', target_workspace_id: 'ws-tgt',
  });
  assert.deepEqual(state.source, { fabricAccountId: 'acct-src', selectedConnectionId: 'acct-src', fabricWorkspaceId: 'ws-src' });
  assert.deepEqual(state.target, { targetFabricAccountId: 'acct-tgt', targetFabricWorkspaceId: 'ws-tgt' });
  // the target never lands in a source field (that used to overwrite the source workspace)
  assert.ok(!Object.values(state.source).includes('ws-tgt') && !Object.values(state.source).includes('acct-tgt'));
  const noTarget = hydrateFabricEditState({ identity_id: 'acct-src', workspace_id: 'ws-src' });
  assert.deepEqual(noTarget.target, { targetFabricAccountId: '', targetFabricWorkspaceId: '' });
});

test('model picker lists the workspace models and restores the saved selection exactly', () => {
  // saved by display name (one model) and by GUID (the other), plus a model no longer in the workspace
  const rows = modelPickerOptions(RECORDED_MODELS, 'sales, 22222222-2222-2222-2222-222222222222, Retired Model');
  assert.deepEqual(rows.map((r) => [r.name, r.checked, r.missing]), [
    ['Sales', true, false],
    ['Finance', true, false],
    ['Retired Model', true, true],
  ]);
  assert.deepEqual(selectedModelNames(rows.map((r) => (r.name === 'Finance' ? { ...r, checked: false } : r))), ['Sales', 'Retired Model']);
  // no fuzzy matching: a partial name is not a match
  assert.deepEqual(modelPickerOptions(RECORDED_MODELS, 'Sale').map((r) => [r.name, r.checked, r.missing]),
    [['Sales', false, false], ['Finance', false, false], ['Sale', true, true]]);
});

test('a 401 during model discovery becomes the "Sign in again" state, not "Failed to load"', () => {
  const err = Object.assign(new Error('Session expired. Please log in again.'), { status: 401 });
  assert.equal(classifyModelDiscovery({ error: err }).state, 'reauth');
  assert.ok(isReauthPayload(RECORDED_REAUTH));
  const fromPayload = classifyModelDiscovery({ result: RECORDED_REAUTH }); // handleResponse returns this body
  assert.equal(fromPayload.state, 'reauth');
  assert.ok(!fromPayload.message.includes('Failed to load'));
  assert.equal(classifyModelDiscovery({ error: Object.assign(new Error('boom'), { status: 500 }) }).state, 'error');
  assert.equal(classifyModelDiscovery({ result: RECORDED_MODELS }).state, 'ok');
  assert.equal(classifyModelDiscovery({ result: [] }).state, 'empty');
});
