import test from 'node:test';
import assert from 'node:assert/strict';
import { targetNameKind, validateTargetName } from './validateTargetName.js';

test('column renames use the real reserved list: DATE is fine, ORDER is not', () => {
  assert.equal(validateTargetName('DATE', 'snowflake', '', '', 'column').isValid, true);
  assert.equal(validateTargetName('VALUE', 'snowflake').isValid, true);
  const order = validateTargetName('ORDER', 'snowflake', '', '', 'column');
  assert.equal(order.code, 'RESERVED_KEYWORD');
  assert.equal(order.suggestion, 'COL_ORDER');
  assert.equal(validateTargetName('LEFT', 'snowflake').code, 'RESERVED_KEYWORD');
});

test('metric renames use the legacy list the deploy applies to metrics', () => {
  const date = validateTargetName('DATE', 'snowflake', '', '', 'measure');
  assert.equal(date.code, 'RESERVED_KEYWORD');
  assert.equal(date.suggestion, 'COL_DATE');
  assert.equal(validateTargetName('VALUE', 'snowflake', '', '', 'metric').code, 'RESERVED_KEYWORD');
  assert.equal(validateTargetName('TOTAL_SALES', 'snowflake', '', '', 'metric').isValid, true);
});

test('Snowflake suggestions are the deployed name', () => {
  assert.equal(validateTargetName('Sales %', 'snowflake').suggestion, 'SALES_PCT');
  assert.equal(validateTargetName('_2024_SALES', 'snowflake').isValid, true);
  assert.equal(validateTargetName('DATE', 'fabric').isValid, true);
});

test('targetNameKind reads entity_kind or field_type', () => {
  assert.equal(targetNameKind({ entity_kind: 'column', field_type: 'measure' }), 'metric');
  assert.equal(targetNameKind({ entity_kind: 'metric' }), 'metric');
  assert.equal(targetNameKind({ entity_kind: 'column' }), 'column');
  assert.equal(targetNameKind(null), 'column');
  assert.equal(targetNameKind('measure'), 'metric');
});
