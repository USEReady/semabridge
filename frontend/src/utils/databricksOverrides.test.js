/**
 * Databricks saves only genuine edits (src/utils/multiFileDryRunReadiness.js):
 * collectMappingOverrides({ ..., targetType }) and databricksEditOrigin.
 *
 * Run: node --test src/utils/databricksOverrides.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { collectMappingOverrides, databricksEditOrigin } from './multiFileDryRunReadiness.js';

const rows = [
  { source_path: 'datasets.S.columns.A', target_field: 'a', status: 'auto' },                 // automatic
  { source_path: 'datasets.S.columns.B', target_field: 'b_new', status: 'manual' },          // edited now
  { source_path: 'datasets.S.columns.C', target_field: 'c_x', status: 'auto_resolved' },     // "Resolve all" suggestion
  { source_path: 'datasets.S.columns.D', target_field: 'old_d', status: 'manual',            // saved earlier, unmarked
    override_origin: 'unmarked', saved_target_name: 'old_d' },
  { source_path: 'datasets.S.columns.E', target_field: 'e_new', status: 'manual',            // unmarked, edited now
    override_origin: 'unmarked', saved_target_name: 'old_e' },
  { source_path: 'datasets.S.columns.F', target_field: 'f', status: 'collision',             // saved earlier, marked
    override_origin: 'user', saved_target_name: 'f' },
];

test('databricks: only edits (marked) and earlier saved names (own provenance) are saved', () => {
  assert.deepEqual(collectMappingOverrides({ rows, targetType: 'databricks' }), [
    { source_path: 'datasets.S.columns.B', target_name: 'b_new', origin: 'user' },
    { source_path: 'datasets.S.columns.D', target_name: 'old_d' },
    { source_path: 'datasets.S.columns.E', target_name: 'e_new', origin: 'user' },
    { source_path: 'datasets.S.columns.F', target_name: 'f', origin: 'user' },
  ]);
});

test('snowflake / fabric / no target: every row, exactly as before, never a marker', () => {
  for (const targetType of ['snowflake', 'fabric', '', undefined]) {
    const out = collectMappingOverrides({ rows, targetType });
    assert.equal(out.length, rows.length);
    assert.ok(out.every((o) => !('origin' in o)));
  }
  assert.deepEqual(collectMappingOverrides({ rows }), collectMappingOverrides({ rows, targetType: 'snowflake' }));
});

test('databricks multi-model sections keep the model key', () => {
  const out = collectMappingOverrides({
    sections: [{ sourceRef: 'a.pbix', rows: [rows[0], rows[1]] }], targetType: 'databricks',
  });
  assert.deepEqual(out, [{ model: 'a.pbix', source_path: 'datasets.S.columns.B', target_name: 'b_new', origin: 'user' }]);
});

test('databricksEditOrigin', () => {
  assert.equal(databricksEditOrigin({ status: 'auto' }, 'x'), null);
  assert.equal(databricksEditOrigin({ status: 'manual' }, 'x'), 'user');
  assert.equal(databricksEditOrigin({ status: 'auto_resolved' }, 'x'), null);
  assert.equal(databricksEditOrigin({ status: 'manual', override_origin: 'unmarked', saved_target_name: 'x' }, 'x'), '');
  assert.equal(databricksEditOrigin({ status: 'manual', override_origin: 'unmarked', saved_target_name: 'x' }, 'y'), 'user');
  assert.equal(databricksEditOrigin({ status: 'auto', override_origin: 'user', saved_target_name: 'x' }, 'x'), 'user');
});
