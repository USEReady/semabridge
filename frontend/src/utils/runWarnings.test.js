import test from 'node:test';
import assert from 'node:assert/strict';
import { getRunWarnings, isStructureOnlyWarning, runWarningBadge } from './runWarnings.js';

const STUB = 'Structure only (no data): 1 metric view(s) were published without data because their source tables do not exist in Databricks: workspace.default.sales_mv.';
const BINDING = 'Source table workspace.default.Sales does not exist. A table with the same name exists in workspace.bkp.Sales, but it was not used automatically.';

test('collects source and target warnings from the run summary and every model result, de-duplicated', () => {
  const run = {
    summary: { source_warnings: ['Report X could not be read'], target_warnings: [STUB] },
    results: [
      { summary: { target_warnings: [STUB, BINDING] } },
      { summary: { target_warnings: ['  '] } },
      null,
    ],
  };
  assert.deepEqual(getRunWarnings(run), ['Report X could not be read', STUB, BINDING]);
});

test('a run without warnings has none and no badge', () => {
  assert.deepEqual(getRunWarnings({ summary: { target_warnings: [] } }), []);
  assert.deepEqual(getRunWarnings(null), []);
  assert.equal(runWarningBadge({ status: 'success', summary: {} }), null);
});

test('structure-only deploys get a "Structure only" badge', () => {
  const badge = runWarningBadge({ summary: { target_warnings: [STUB, BINDING] } });
  assert.equal(badge.label, 'Structure only');
  assert.equal(badge.structureOnly, true);
  assert.equal(badge.count, 2);
  assert.ok(badge.title.includes('workspace.bkp.Sales'));
});

test('other warnings get a count badge', () => {
  assert.equal(runWarningBadge({ summary: { target_warnings: [BINDING] } }).label, '1 warning');
  assert.equal(runWarningBadge({ summary: { target_warnings: [BINDING, 'other'] } }).label, '2 warnings');
});

test('isStructureOnlyWarning matches only the backend structure-only text', () => {
  assert.equal(isStructureOnlyWarning(STUB), true);
  assert.equal(isStructureOnlyWarning(BINDING), false);
  assert.equal(isStructureOnlyWarning(undefined), false);
});
