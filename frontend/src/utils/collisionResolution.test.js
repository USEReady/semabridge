/**
 * Frontend identifier rules must equal the backend's (Snowflake emitter's
 * IdentifierSanitizer.to_alias), or a name the user picks in the dry run is
 * renamed again at deploy time. Expected values below were produced by the
 * backend: `_SNOWFLAKE_SANITIZER.to_alias(name)` in project_mapping_engine.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  toSnowflakeAlias,
  sanitizeMappingName,
  normalizeTypedName,
  finalizeTypedName,
  suggestCollisionResolutions,
  resolveCollisionsForRows,
} from './collisionResolution.js';

const BACKEND_TO_ALIAS = {
  '% Margin': 'PCT_MARGIN',
  'Margin %': 'MARGIN_PCT',
  'Order': 'COL_ORDER',
  'order': 'COL_ORDER',
  '2024 Sales': '_2024_SALES',
  'Café Owner': 'CAF_OWNER',
  'Région': 'R_GION',
  '  Sales  Amount ': 'SALES_AMOUNT',
  'Sales-Amt (USD)': 'SALES_AMT_USD',
  'Net $ Rev': 'NET_DOL_REV',
  'A & B': 'A_AND_B',
  'Q?': 'QQ',
  'x*y+z=1': 'XSTARYPLUSZEQ1',
  '#Count': 'NUMCOUNT',
  'e-mail@domain': 'E_MAILATDOMAIN',
  "O'Brien": 'OBRIEN',
  '"Quoted"': 'QUOTED',
  'date': 'COL_DATE',
  'Date Today': 'DATE_TODAY',
  'Index': 'COL_INDEX',
  'start': 'COL_START',
  'COL_ORDER': 'COL_ORDER',
  '_2024_SALES': '_2024_SALES',
  'PCT_MARGIN': 'PCT_MARGIN',
  '---': 'COLUMN_UNKNOWN',
  '日本語': 'COLUMN_UNKNOWN',
  'Units 😀 Sold': 'UNITS_SOLD',
  'a.b': 'A_B',
  '[Sales].[Amount]': 'SALES_AMOUNT',
  'Tab\tName': 'TAB_NAME',
  'Group': 'COL_GROUP',
  'Value': 'COL_VALUE',
  'min': 'COL_MIN',
  'Sales_': 'SALES',
  '__x__': 'X',
  '9': '_9',
  'Ä': 'COLUMN_UNKNOWN',
};

test('toSnowflakeAlias matches the backend to_alias for tricky names (metrics)', () => {
  Object.entries(BACKEND_TO_ALIAS).forEach(([input, expected]) => {
    assert.equal(toSnowflakeAlias(input, 'metric'), expected, `to_alias(${JSON.stringify(input)})`);
  });
});

test('column names use the semantic rules: only real Snowflake reserved words get COL_', () => {
  // Backend IdentifierSanitizer.sanitize_semantic_alias.
  assert.equal(toSnowflakeAlias('Date'), 'DATE');
  assert.equal(toSnowflakeAlias('Value'), 'VALUE');
  assert.equal(toSnowflakeAlias('Index'), 'INDEX');
  assert.equal(toSnowflakeAlias('Order'), 'COL_ORDER');
  assert.equal(toSnowflakeAlias('Group'), 'COL_GROUP');
  assert.equal(toSnowflakeAlias('Net $ Rev'), 'NET_DOL_REV');
  assert.equal(finalizeTypedName('date'), 'DATE');
  assert.equal(finalizeTypedName('date', 'snowflake', 'measure'), 'COL_DATE');
});

test('sanitizeMappingName defaults to the Snowflake rules and is idempotent', () => {
  Object.values(BACKEND_TO_ALIAS).forEach((alias) => {
    assert.equal(sanitizeMappingName(alias, 'snowflake', 'metric'), alias);
  });
  assert.equal(sanitizeMappingName(''), '');
  assert.equal(sanitizeMappingName('2024 Sales', 'fabric'), 'N_2024_SALES');
});

test('typed names keep a trailing underscore while typing, finalize to to_alias', () => {
  assert.equal(normalizeTypedName('sales_'), 'SALES_');
  assert.equal(normalizeTypedName('order'), 'ORDER');
  assert.equal(finalizeTypedName('ORDER'), 'COL_ORDER');
  assert.equal(finalizeTypedName('SALES_'), 'SALES');
});

test('backend resolution_suggestions come first', () => {
  const row = {
    id: 'r1', source_field: 'Margin %', target_field: 'MARGIN_PCT', status: 'collision',
    mapping_scope: 'column::m::datasets.Product', resolution_suggestions: ['PRODUCT_MARGIN_PCT', 'COL_MARGIN_PCT'],
  };
  const { suggestions } = suggestCollisionResolutions(row, [row]);
  assert.deepEqual(suggestions.map(s => s.label), ['PRODUCT_MARGIN_PCT', 'COL_MARGIN_PCT']);
  assert.equal(suggestions[0].id, 'recommended');
});

test('local fallback suggestions use the Snowflake rules', () => {
  const row = { id: 'r1', source_field: '% Margin', target_field: 'PCT_MARGIN', source_table_name: '2024 Sales', status: 'collision' };
  const { suggestions } = suggestCollisionResolutions(row, [row]);
  assert.equal(suggestions[0].label, '_2024_SALES_PCT_MARGIN');
  suggestions.forEach(s => assert.equal(toSnowflakeAlias(s.label), s.label));
});

test('Resolve All: backend suggestions, unique per namespace', () => {
  const ns = 'column::m::datasets.Product';
  const rows = [
    { id: 'a', source_field: 'Margin %', target_field: 'MARGIN_PCT', status: 'collision', mapping_scope: ns,
      resolution_suggestions: ['PRODUCT_MARGIN_PCT', 'COL_MARGIN_PCT'] },
    { id: 'b', source_field: 'Margin PCT', target_field: 'MARGIN_PCT', status: 'collision', mapping_scope: ns,
      resolution_suggestions: ['COL_MARGIN_PCT', 'MARGIN_PCT_ABCD1234'] },
    // Same name in ANOTHER table is fine (names are per logical table).
    { id: 'c', source_field: 'PRODUCT_MARGIN_PCT', target_field: 'PRODUCT_MARGIN_PCT', status: 'auto',
      mapping_scope: 'column::m::datasets.Other' },
  ];
  const resolved = resolveCollisionsForRows(rows);
  assert.deepEqual(resolved, { a: 'PRODUCT_MARGIN_PCT', b: 'COL_MARGIN_PCT' });
});

test('Resolve All skips a backend suggestion already taken in the namespace', () => {
  const ns = 'metric';
  const rows = [
    { id: 'x', source_field: 'Total', target_field: 'TOTAL', status: 'collision', field_type: 'measure',
      resolution_suggestions: ['MSR_TOTAL'] },
    { id: 'y', source_field: 'MSR_TOTAL', target_field: 'MSR_TOTAL', status: 'auto', mapping_scope: ns, field_type: 'measure' },
  ];
  const resolved = resolveCollisionsForRows(rows);
  assert.notEqual(resolved.x, 'MSR_TOTAL');
  assert.equal(toSnowflakeAlias(resolved.x), resolved.x);
});

test('normalizeRows keeps backend resolution_suggestions and mapping_scope', async () => {
  const { normalizeRows } = await import('./normalizeRows.js');
  const [row] = normalizeRows({
    entity_mappings: [{
      id: 'r1', entity_kind: 'column', source_name: 'Margin %', target_name: 'MARGIN_PCT',
      collision_detected: true, resolution_suggestions: ['PRODUCT_MARGIN_PCT'], mapping_scope: 'column::m::datasets.Product',
    }],
  });
  assert.deepEqual(row.resolution_suggestions, ['PRODUCT_MARGIN_PCT']);
  assert.equal(row.mapping_scope, 'column::m::datasets.Product');
});

// One table, produced by the backend's snowflake_target_name and asserted by
// Tests/test_naming_single_rule.py too: both sides are pinned to it.
test('JS mirror equals the backend snowflake_target_name table (columns and metrics)', async () => {
  const { readFileSync } = await import('node:fs');
  const table = JSON.parse(readFileSync(new URL('./snowflakeNamingTable.json', import.meta.url), 'utf8'));
  const { SNOWFLAKE_RESERVED_WORDS, SNOWFLAKE_LEGACY_RESERVED_WORDS, toPhysicalColumnName } = await import('./collisionResolution.js');
  ['column', 'metric'].forEach((kind) => {
    Object.entries(table[kind]).forEach(([input, expected]) => {
      assert.equal(toSnowflakeAlias(input, kind), expected, `${kind} ${JSON.stringify(input)}`);
    });
  });
  assert.deepEqual([...SNOWFLAKE_RESERVED_WORDS].sort(), table.reserved_words.column);
  assert.deepEqual([...SNOWFLAKE_LEGACY_RESERVED_WORDS].sort(), table.reserved_words.metric);
  // Physical column = backend sanitize_column (= the metric/legacy rule).
  Object.entries(table.metric).forEach(([input, expected]) => {
    assert.equal(toPhysicalColumnName(input), expected, `physical ${JSON.stringify(input)}`);
  });
  assert.equal(toPhysicalColumnName('Date'), 'COL_DATE');
  assert.equal(toPhysicalColumnName('Sales %'), 'SALES_PCT');
});

test('words Snowflake restricts in some contexts keep COL_ for dimensions; type words do not', () => {
  ['Left', 'View', 'Join', 'Case', 'Schema', 'True', 'Try_Cast', 'Using'].forEach((w) => {
    assert.equal(toSnowflakeAlias(w), `COL_${w.toUpperCase()}`);
  });
  ['Date', 'Key', 'Value', 'Count', 'Number', 'Timestamp'].forEach((w) => {
    assert.equal(toSnowflakeAlias(w), w.toUpperCase());
  });
});
