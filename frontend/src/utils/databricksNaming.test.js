/**
 * The browser copy of Databricks naming v2 must give exactly the backend's names
 * (fixture written from target_rules/databricks/naming.py).
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { toDatabricksName, isDatabricksName } from './databricksNaming.js';

const fixture = JSON.parse(readFileSync(new URL('./__fixtures__/databricks_naming_parity.json', import.meta.url), 'utf8'));

test('frontend Databricks names equal the backend names', () => {
  // '' = only the backend can name it (identity hash / hashed shortening over 255 characters).
  const bad = fixture.cases.filter((c) => {
    const got = toDatabricksName(c.name, c.kind);
    return got === '' ? !(c.name.length > 255 || /_[0-9a-f]{8}$/.test(c.expected)) : got !== c.expected;
  });
  assert.deepEqual(bad.map((c) => [c.name, c.kind, toDatabricksName(c.name, c.kind), c.expected]), []);
});

test('generated names are valid and idempotent', () => {
  for (const c of fixture.cases) {
    assert.ok(isDatabricksName(c.expected), c.expected);
    assert.equal(toDatabricksName(c.expected, c.kind), c.expected);
  }
});

test('the naming objects dispatch by target and keep Snowflake unchanged', async () => {
  const { namingFor, SnowflakeNaming, DatabricksNaming, GenericNaming } = await import('./targetNaming.js');
  assert.ok(namingFor('databricks') instanceof DatabricksNaming);
  assert.ok(namingFor('Snowflake') instanceof SnowflakeNaming);
  assert.ok(namingFor('fabric') instanceof GenericNaming);
  const dbx = namingFor('databricks');
  const sf = namingFor('snowflake');
  assert.equal(dbx.targetName('Gross Margin %', 'metric'), 'gross_margin_pct');
  assert.equal(sf.targetName('Gross Margin %', 'metric'), 'GROSS_MARGIN_PCT');
  assert.ok(dbx.accepts('district_id') && !dbx.accepts('DISTRICT_ID') && !dbx.accepts('select'));
  assert.ok(sf.accepts('DISTRICT_ID') && sf.accepts('district_id'));   // Snowflake compares upper case, as before
  assert.equal(dbx.normalizeTyped('Sales Amt'), 'sales_amt');
  assert.equal(sf.normalizeTyped('Sales Amt'), 'SALES_AMT');
  assert.ok(dbx.isPlainChange('TotalSales', 'total_sales'));            // the rule itself is not a "change"
  assert.equal(dbx.legacyNote([{ source_field: 'Date', target_field: 'date' }]), '');
});

test('name changes and rename notes follow the target', async () => {
  const { changeReason, renameImpactNote } = await import('./nameChanges.js');
  const row = { source_field: 'Total Sales', target_field: 'total_sales', status: 'auto' };
  assert.equal(changeReason(row, 'databricks'), null);
  const manual = { source_field: 'Total Sales', target_field: 'revenue', status: 'manual' };
  assert.equal(changeReason(manual, 'databricks').kind, 'renamed_by_you');
  const note = renameImpactNote({ source_field: 'Amount', target_field: 'amount', entity_kind: 'column' }, 'net_amount', 'databricks');
  assert.match(note, /metric view/);
  assert.doesNotMatch(note, /Snowflake|COL_/);
});
