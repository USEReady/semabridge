/**
 * Reading the Databricks plan on the screen (src/utils/databricksPlan.js).
 *
 * Run: node --test src/utils/databricksPlan.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  approximateMeasureChoices,
  buildReplanBody,
  excludedDependents,
  nameRequestsBySourcePath,
  newerPlan,
  plannedNamesBySourcePath,
  planRequestProjectId,
  planStatus,
} from './databricksPlan.js';

const plan = {
  settings: { calculate_mode: 'approximate', excluded_measures: ['Old LY'] },
  meta: { revision: 100, status: 'valid', cache_ref: { project_id: 'preview-abc', model_subdir: null },
    basis: { table_check_at: '2026-11-25T10:00:00Z' } },
  views: [
    { name: 'sales_metrics',
      fields: [{ dataset: 'Store', column: 'Name', name: 'name' }, { dataset: 'Product', column: 'Name', name: 'product_name' }],
      measures: [{ source_name: 'Total Sales', name: 'total_sales', semantic_status: 'converted' },
        { source_name: 'Sales LY', name: 'sales_ly', semantic_status: 'approximate', comment: 'approximate: ...' }] },
    { name: 'store_metrics',
      fields: [{ dataset: 'Store', column: 'Name', name: 'store_name' }],
      measures: [{ source_name: 'Stores', name: 'stores', semantic_status: 'converted' }] },
  ],
  left_out: [
    { measure: 'Old LY', reason: 'user_excluded', detail: 'unticked by the user' },
    { measure: 'Old LY %', reason: 'depends_on', detail: 'depends on [Old LY]', root_cause: 'Old LY (user_excluded)' },
  ],
  name_requests: [{ source_path: 'datasets.Store.columns.Name', requested: 'shop', status: 'conflict' }],
};

test('planned names come from the plan, per view, matched by source identity', () => {
  const m = plannedNamesBySourcePath(plan);
  assert.deepEqual(m.get('datasets.Store.columns.Name'), [
    { view: 'sales_metrics', name: 'name', kind: 'field' }, { view: 'store_metrics', name: 'store_name', kind: 'field' }]);
  assert.deepEqual(m.get('datasets.Product.columns.Name'), [{ view: 'sales_metrics', name: 'product_name', kind: 'field' }]);
  assert.deepEqual(m.get('metrics.Total Sales'), [{ view: 'sales_metrics', name: 'total_sales', kind: 'measure' }]);
  assert.equal(plannedNamesBySourcePath(null).size, 0);
});

test('approximate choices include unticked measures so they can be ticked back', () => {
  const c = approximateMeasureChoices(plan);
  assert.deepEqual(c.map((x) => [x.source_name, x.excluded]), [['Old LY', true], ['Sales LY', false]]);
  assert.deepEqual(excludedDependents(plan).map((d) => d.measure), ['Old LY %']);
});

test('status, requests and the re-plan body', () => {
  assert.equal(planStatus(plan).revision, 100);
  assert.equal(nameRequestsBySourcePath(plan).get('datasets.Store.columns.Name').status, 'conflict');
  const body = buildReplanBody({
    plan,
    rows: [{ source_path: 'datasets.Store.columns.Name', target_field: 'shop', status: 'manual' },
      { source_path: 'metrics.Total Sales', target_field: 'total_sales', status: 'auto' }],
    planner: { calculate_mode: 'strict', excluded_measures: [] },
    revision: 200,
  });
  assert.deepEqual(body, {
    cache_project_id: 'preview-abc', model_subdir: undefined,
    mappings_overrides: [{ source_path: 'datasets.Store.columns.Name', target_name: 'shop', origin: 'user' }],
    planner: { calculate_mode: 'strict', excluded_measures: [] }, revision: 200,
  });
});

test('an older plan never replaces a newer one', () => {
  const older = { meta: { revision: 50 } };
  const newer = { meta: { revision: 150 } };
  assert.equal(newerPlan(plan, older), plan);
  assert.equal(newerPlan(plan, newer), newer);
  assert.equal(newerPlan(null, older), older);
});

test('plan requests go to the real project, or to the wizard preview', () => {
  assert.equal(planRequestProjectId('proj-1'), 'proj-1');
  assert.equal(planRequestProjectId('preview-abc'), 'preview');
  assert.equal(planRequestProjectId(''), 'preview');
});
