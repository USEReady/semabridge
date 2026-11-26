/**
 * Databricks target settings on the project Settings page.
 *
 * Run: node --test src/utils/projectTargetSettings.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyDatabricksTarget, databricksFields, effectiveTarget } from './projectTargetSettings.js';

const form = {
  target_catalog: 'workspace', target_schema: 'semabridge_dev', target_source_schemas: 'workspace.sales, workspace.ref',
  target_staging_schema: 'workspace.semabridge_staging', target_deploy_principals: 'admins', target_deployers: '',
};

test('a bare target written by the old form is read through targets[0]', () => {
  const tree = { targets: [{ type: 'databricks', catalog: 'workspace', schema: 'semabridge_dev' }], target: { type: 'databricks' } };
  assert.deepEqual(effectiveTarget(tree), { type: 'databricks', catalog: 'workspace', schema: 'semabridge_dev' });
  assert.equal(databricksFields(tree).target_schema, 'semabridge_dev');
});

test('saving keeps the form the project uses and never adds the other', () => {
  const onlyList = applyDatabricksTarget({ targets: [{ type: 'databricks', catalog: 'x', planner: { calculate_mode: 'strict' } }] }, form);
  assert.equal(onlyList.target, undefined);
  assert.deepEqual(onlyList.targets[0], {
    type: 'databricks', catalog: 'workspace', schema: 'semabridge_dev', staging_schema: 'workspace.semabridge_staging',
    source_schemas: ['workspace.sales', 'workspace.ref'], deploy_principals: ['admins'],
    planner: { calculate_mode: 'strict' },
  });
  const onlyMap = applyDatabricksTarget({ target: { type: 'databricks', stub_form: 'empty_table' } }, form);
  assert.equal(onlyMap.targets, undefined);
  assert.equal(onlyMap.target.stub_form, 'empty_table');
  assert.equal(onlyMap.target.staging_schema, 'workspace.semabridge_staging');
});

test('a broken project (bare target + full targets[0]) becomes consistent', () => {
  const broken = { targets: [{ type: 'databricks', catalog: 'workspace', schema: 'semabridge_dev' }], target: { type: 'databricks' } };
  const fixed = applyDatabricksTarget(broken, form);
  assert.deepEqual(fixed.target, fixed.targets[0]);
  assert.equal(fixed.target.catalog, 'workspace');
  assert.deepEqual(broken.target, { type: 'databricks' }); // input not mutated
});

test('emptied fields are removed, not saved as empty strings', () => {
  const out = applyDatabricksTarget({ target: { type: 'databricks', staging_schema: 'a.b', deployers: ['1'] } },
    { ...form, target_staging_schema: '', target_deployers: '' });
  assert.equal('staging_schema' in out.target, false);
  assert.equal('deployers' in out.target, false);
});
