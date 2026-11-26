import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  DATABRICKS_MAX_IDENTIFIER_LENGTH,
  DATABRICKS_RESERVED_WORDS,
  quoteDatabricksIdentifier,
  toDatabricksIdentifier,
  sanitizeMappingName,
  finalizeTypedName,
} from './collisionResolution.js';

const table = JSON.parse(
  readFileSync(new URL('./databricksNamingTable.json', import.meta.url), 'utf8'),
);

test('JS mirror equals the backend databricks_target_name table (columns and metrics)', () => {
  ['column', 'metric'].forEach((kind) => {
    Object.entries(table[kind]).forEach(([input, expected]) => {
      assert.equal(
        toDatabricksIdentifier(input, kind),
        expected,
        `${kind} ${JSON.stringify(input)}`,
      );
    });
  });
});

test('JS mirror pins Databricks reserved words and 255-char max length', () => {
  assert.deepEqual([...DATABRICKS_RESERVED_WORDS].sort(), table.reserved_words);
  assert.equal(DATABRICKS_MAX_IDENTIFIER_LENGTH, table.max_length);
  assert.equal(DATABRICKS_MAX_IDENTIFIER_LENGTH, 255);
});

test('quoteDatabricksIdentifier wraps with backticks and escapes embedded backticks', () => {
  assert.equal(quoteDatabricksIdentifier('Order'), '`Order`');
  assert.equal(quoteDatabricksIdentifier('a`b'), '`a``b`');
});

test('sanitizeMappingName and finalizeTypedName route target=databricks through toDatabricksIdentifier', () => {
  assert.equal(sanitizeMappingName('Sales %', 'databricks', 'column'), 'Sales');
  assert.equal(sanitizeMappingName('Sales %', 'databricks', 'metric'), 'Sales_Pct');
  assert.equal(sanitizeMappingName('# Orders', 'databricks', 'measure'), 'Num_Orders');
  assert.equal(finalizeTypedName('2024 Sales', 'databricks', 'column'), '2024_Sales');
  assert.equal(sanitizeMappingName('', 'databricks', 'column'), '');
});
