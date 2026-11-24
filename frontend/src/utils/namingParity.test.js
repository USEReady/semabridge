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

test('databricks shares the snowflake naming rule; fabric does not', () => {
  assert.equal(usesSnowflakeNaming('databricks'), true);
  assert.equal(usesSnowflakeNaming('Snowflake'), true);
  assert.equal(usesSnowflakeNaming('fabric'), false);
  assert.equal(sanitizeMappingName('Margin %', 'databricks', 'metric'), sanitizeMappingName('Margin %', 'snowflake', 'metric'));
});

test('databricks has no reserved-keyword block, snowflake does', () => {
  assert.equal(validateTargetName('SELECT', 'snowflake', '', '', 'column').code, 'RESERVED_KEYWORD');
  assert.notEqual(validateTargetName('SELECT', 'databricks', '', '', 'column').code, 'RESERVED_KEYWORD');
  const bad = validateTargetName('Margin %', 'databricks', '', '', 'metric');
  assert.equal(bad.code, 'UNSUPPORTED_CHARACTERS');
  assert.match(bad.message, /Databricks/);
  assert.match(validateTargetName('Margin %', 'snowflake', '', '', 'metric').message, /Snowflake/);
});
