/**
 * Run: node --test src/utils/nameChanges.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { changeReason, collectNameChanges, renameImpactNote } from './nameChanges.js';

const row = (over) => ({ id: 'r', source_field: 'Order Date', target_field: 'ORDER_DATE', status: 'auto', ...over });

test('plain case/space changes are not listed', () => {
  assert.equal(changeReason(row()), null);
});

test('symbol replacements are listed with the rule', () => {
  const r = changeReason(row({ source_field: 'Sales %', target_field: 'SALES_PCT', field_type: 'measure' }));
  assert.equal(r.kind, 'naming_rules');
  assert.match(r.detail, /"%" became PCT/);
});

test('user renames and resolved clashes are listed', () => {
  assert.equal(changeReason(row({ target_field: 'SHIP_DATE', status: 'manual' })).kind, 'renamed_by_you');
  const clash = changeReason(row({ source_field: 'Order-Date_2', renamed_from: 'Order-Date', target_field: 'PRODUCT_ORDER_DATE', status: 'manual' }));
  assert.equal(clash.kind, 'clash_resolved');
});

test('open collisions are left to the collision UI', () => {
  assert.equal(changeReason(row({ target_field: 'X', status: 'collision', collision_detected: true })), null);
  assert.equal(collectNameChanges([row(), row({ id: 'b', source_field: 'A$', target_field: 'A_DOL' })]).length, 1);
});

test('rename note names the physical column for columns and reports for metrics', () => {
  const col = renameImpactNote(row({ source_table_name: 'Product', source_field: 'Order-Date_2', target_field: 'ORDER_DATE_2' }), 'ship_date');
  assert.match(col, /PRODUCT\.ORDER_DATE_2/);
  assert.match(col, /make sure that column exists/);
  const metric = renameImpactNote(row({ field_type: 'measure', target_field: 'TOTAL' }), 'TOTAL_SALES');
  assert.match(metric, /reports, dashboards/);
  assert.equal(renameImpactNote(row(), 'order_date'), '');  // unchanged -> no note
});
