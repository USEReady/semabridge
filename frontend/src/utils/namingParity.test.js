/**
 * Browser naming must equal the backend's, for every target that has a
 * naming rule, or a name chosen in the dry run is renamed again at deploy.
 * Expected names come from the backend (Tests/target_rules/
 * write_frontend_naming_fixture.py -> __fixtures__/naming_parity.json).
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { sanitizeMappingName, usesSnowflakeNaming } from './collisionResolution.js';
import { validateTargetName } from './validateTargetName.js';

const EXPECTED = JSON.parse(readFileSync(new URL('./__fixtures__/naming_parity.json', import.meta.url), 'utf-8'));

for (const target of Object.keys(EXPECTED)) {
  for (const kind of Object.keys(EXPECTED[target])) {
    test(`browser names equal backend names: ${target} ${kind}`, () => {
      for (const [name, expected] of Object.entries(EXPECTED[target][kind])) {
        assert.equal(sanitizeMappingName(name, target, kind), expected, `${target}/${kind}: ${JSON.stringify(name)}`);
      }
    });
  }
}

test('databricks has its own naming rule (v2, 25 Nov 2026); snowflake keeps its own; fabric neither', () => {
  assert.equal(usesSnowflakeNaming('databricks'), false);
  assert.equal(usesSnowflakeNaming('Snowflake'), true);
  assert.equal(usesSnowflakeNaming('fabric'), false);
  assert.equal(sanitizeMappingName('Margin %', 'databricks', 'metric'), 'margin_pct');
  assert.equal(sanitizeMappingName('Margin %', 'snowflake', 'metric'), 'MARGIN_PCT');
});

test('databricks has no reserved-keyword block, snowflake does', () => {
  assert.equal(validateTargetName('SELECT', 'snowflake', '', '', 'column').code, 'RESERVED_KEYWORD');
  assert.notEqual(validateTargetName('SELECT', 'databricks', '', '', 'column').code, 'RESERVED_KEYWORD');
  const bad = validateTargetName('Margin %', 'databricks', '', '', 'metric');
  assert.equal(bad.code, 'UNSUPPORTED_CHARACTERS');
  assert.match(bad.message, /Databricks/);
  assert.equal(bad.suggestion, 'margin_pct');
  // A correct Databricks name is valid as it is (it once failed against Snowflake's upper case).
  for (const name of ['district_id', 'total_sales_ly', 'gross_margin_this_year_pct', 'select_field', 'field_bdebd3de']) {
    assert.equal(validateTargetName(name, 'databricks', '', '', 'column').code, 'OK', name);
  }
  assert.equal(validateTargetName('DISTRICT_ID', 'databricks', '', '', 'column').suggestion, 'district_id');
  assert.match(validateTargetName('Margin %', 'snowflake', '', '', 'metric').message, /Snowflake/);
});
