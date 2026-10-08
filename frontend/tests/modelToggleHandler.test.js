/**
 * Model toggle handler (Step 3 model picker) — tests the REAL logic in
 * src/utils/modelSelection.js that StepSourceBrowser/CreateProjectPage use,
 * instead of re-implementing it inline (the previous version of this file was
 * a Jest-style copy of the handler and never ran under node --test).
 *
 * Regression covered: edit mode seeded a saved model under BOTH its name and
 * its GUID, so it was listed twice and one click only removed one key (the
 * row stayed checked). Also: Fabric selection is capped at 10.
 *
 * Run: node --test tests/modelToggleHandler.test.js   (from frontend/)
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  toggleModelSelection,
  isModelSelected,
  resolveSelectedModelNames,
  dedupeModelSelection,
  seedSelectionFromSavedConfig,
  selectionIdentities,
  MAX_SELECTED_MODELS,
} from '../src/utils/modelSelection.js';

const SALES = { id: '11111111-2222-3333-4444-555555555555', name: 'Sales' };
const COST = { id: '66666666-7777-8888-9999-000000000000', name: 'Cost' };

test('adding a model keys it by id and records its name', () => {
  const r = toggleModelSelection({ selectedKeys: [], nameByKey: {}, model: SALES });
  assert.deepEqual(r.selectedKeys, [SALES.id]);
  assert.deepEqual(r.nameByKey, { [SALES.id]: 'Sales' });
  assert.equal(r.selected, true);
});

test('a single click unchecks a model selected under BOTH its name and its GUID (edit-mode duplicate)', () => {
  const selectedKeys = ['Sales', SALES.id];
  const nameByKey = { Sales: 'Sales', [SALES.id]: 'Sales' };
  assert.equal(isModelSelected(SALES, selectedKeys, nameByKey), true);
  const r = toggleModelSelection({ selectedKeys, nameByKey, model: SALES });
  assert.deepEqual(r.selectedKeys, []);
  assert.deepEqual(r.nameByKey, {});
  assert.equal(isModelSelected(SALES, r.selectedKeys, r.nameByKey), false);
});

test('legacy workspace-qualified keys are matched too', () => {
  const r = toggleModelSelection({ selectedKeys: [`ws1::${SALES.id}`], nameByKey: { [`ws1::${SALES.id}`]: 'Sales' }, model: SALES });
  assert.deepEqual(r.selectedKeys, []);
});

test('toggling one model never touches another', () => {
  const r = toggleModelSelection({ selectedKeys: [SALES.id, COST.id], nameByKey: { [SALES.id]: 'Sales', [COST.id]: 'Cost' }, model: SALES });
  assert.deepEqual(r.selectedKeys, [COST.id]);
  assert.deepEqual(r.nameByKey, { [COST.id]: 'Cost' });
});

test('selection is capped at MAX_SELECTED_MODELS (10); removing still works at the cap', () => {
  assert.equal(MAX_SELECTED_MODELS, 10);
  const keys = Array.from({ length: 10 }, (_, i) => `id-${i}`);
  const names = Object.fromEntries(keys.map((k, i) => [k, `Model ${i}`]));
  const blocked = toggleModelSelection({ selectedKeys: keys, nameByKey: names, model: { id: 'id-new', name: 'New' } });
  assert.equal(blocked.limitReached, true);
  assert.equal(blocked.selectedKeys.length, 10);
  const removed = toggleModelSelection({ selectedKeys: keys, nameByKey: names, model: { id: 'id-3', name: 'Model 3' } });
  assert.equal(removed.limitReached, false);
  assert.equal(removed.selectedKeys.length, 9);
});

test('duplicate name+GUID keys count once toward the cap', () => {
  assert.equal(selectionIdentities(['Sales', SALES.id], { Sales: 'Sales', [SALES.id]: 'Sales' }).length, 1);
});

test('resolveSelectedModelNames dedupes names sent to the backend', () => {
  assert.deepEqual(resolveSelectedModelNames(['Sales', SALES.id, COST.id], { Sales: 'Sales', [SALES.id]: 'Sales', [COST.id]: 'Cost' }), ['Sales', 'Cost']);
});

test('edit-mode seed uses saved names only (never names AND GUIDs)', () => {
  const seed = seedSelectionFromSavedConfig({ models: ['Sales', 'Cost'], selectionModelIds: [SALES.id, COST.id] });
  assert.deepEqual(seed.selectedKeys, ['Sales', 'Cost']);
  const idsOnly = seedSelectionFromSavedConfig({ models: [], selectionModelIds: [SALES.id] });
  assert.deepEqual(idsOnly.selectedKeys, [SALES.id]);
});

test('dedupeModelSelection collapses name and GUID keys onto one GUID key once models load', () => {
  const r = dedupeModelSelection(['Sales', SALES.id, 'Gone'], { Sales: 'Sales', [SALES.id]: SALES.id, Gone: 'Gone' }, [SALES, COST]);
  assert.deepEqual(r.selectedKeys, [SALES.id, 'Gone']);
  assert.deepEqual(r.nameByKey, { [SALES.id]: 'Sales', Gone: 'Gone' });
});
