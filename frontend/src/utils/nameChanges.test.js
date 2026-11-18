/**
 * Run: node --test src/utils/nameChanges.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { changeReason, collectNameChanges, legacyNamingNote, renameImpactNote } from './nameChanges.js';

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

test('rename note names the PHYSICAL column (backend sanitize_column), not the dimension name', () => {
  const date = renameImpactNote(row({ source_table_name: 'Sales', source_field: 'Date', target_field: 'DATE' }), 'ORDER_DAY');
  assert.match(date, /SALES\.COL_DATE/);
  const pct = renameImpactNote(row({ source_table_name: 'Sales', source_field: 'Sales %', target_field: 'SALES_PCT' }), 'MARGIN');
  assert.match(pct, /SALES\.SALES_PCT/);
});

test('a column deployed under another name is listed as Renamed in Snowflake', () => {
  const r = changeReason(row({ source_field: 'Date', target_field: 'DATE', deployed_name_change: { from: 'COL_DATE', to: 'DATE' } }));
  assert.equal(r.kind, 'renamed_in_snowflake');
  assert.equal(r.label, 'Renamed in Snowflake');
  assert.match(r.detail, /COL_DATE/);
  assert.match(r.detail, /must be updated/);
  // Without the marker the plain Date -> DATE is not listed.
  assert.equal(changeReason(row({ source_field: 'Date', target_field: 'DATE' })), null);
});

test('project-level note when the last deployed names are unknown', () => {
  const rows = [
    row({ id: 'a', source_field: 'Date', target_field: 'DATE' }),
    row({ id: 'b', source_field: 'Value', target_field: 'VALUE' }),
    row({ id: 'c', source_field: 'Amount', target_field: 'AMOUNT' }),
    row({ id: 'd', source_field: 'Date', target_field: 'COL_DATE', field_type: 'measure' }),
  ];
  const note = legacyNamingNote(rows, false);
  assert.match(note, /^2 columns no longer get the COL_ prefix/);
  assert.match(note, /DATE \(was COL_DATE\)/);
  assert.match(note, /must be updated/);
  assert.equal(legacyNamingNote(rows, true), '');
  assert.equal(legacyNamingNote([row()], false), '');
});
