/**
 * Tests for rowConversionOutcome in mappingFilterUtils.js
 * Verifies that measures missing target_expression evaluate to 'conversion_failed'
 * and do not falsely report as 'conversion_success'.
 *
 * Run: node --test src/utils/mappingFilterUtils.conversion.test.js (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rowConversionOutcome, buildFilterOptions } from './mappingFilterUtils.js';

test('rowConversionOutcome returns conversion_success for translated measure with target_expression', () => {
  const row = {
    field_type: 'measure',
    measure_expression: 'SUM(Sales[Amount])',
    target_expression: 'SUM(AMOUNT)',
    sync_enabled: true,
    sync_failure_reason: '',
  };
  assert.equal(rowConversionOutcome(row), 'conversion_success');
});

test('rowConversionOutcome returns conversion_failed when target_expression is empty/missing even if sync_enabled is true', () => {
  const row = {
    field_type: 'measure',
    measure_expression: 'VAR _Root = SELECTEDVALUE(EventEdges[Root]) ...',
    target_expression: '',
    sync_enabled: true,
    sync_failure_reason: '',
  };
  assert.equal(rowConversionOutcome(row), 'conversion_failed');
});

test('rowConversionOutcome returns conversion_failed when target_expression is null', () => {
  const row = {
    field_type: 'measure',
    measure_expression: 'VAR _Root = SELECTEDVALUE(...)',
    target_expression: null,
    sync_enabled: true,
  };
  assert.equal(rowConversionOutcome(row), 'conversion_failed');
});

test('rowConversionOutcome returns conversion_failed when sync_enabled is false', () => {
  const row = {
    field_type: 'measure',
    measure_expression: 'SUM(Sales[Amount])',
    target_expression: 'SUM(AMOUNT)',
    sync_enabled: false,
    sync_failure_reason: 'Excluded by design',
  };
  assert.equal(rowConversionOutcome(row), 'conversion_failed');
});

test('rowConversionOutcome returns null when row has no source or target expression', () => {
  const row = {
    field_type: 'column',
    source_field: 'CUSTOMER_ID',
  };
  assert.equal(rowConversionOutcome(row), null);
});

test('buildFilterOptions tallies missing target_expression under conversion_failed facet', () => {
  const rows = [
    {
      field_type: 'measure',
      measure_expression: 'SUM(Sales[Amount])',
      target_expression: 'SUM(AMOUNT)',
      sync_enabled: true,
    },
    {
      field_type: 'measure',
      measure_expression: 'MS_SINCE_PREVIOUS_EDGE',
      target_expression: '',
      sync_enabled: true,
      sync_failure_reason: '',
    },
  ];
  const options = buildFilterOptions(rows);
  const successOpt = options.find((o) => o.key === 'conversion_success');
  const failedOpt = options.find((o) => o.key === 'conversion_failed');

  assert.equal(successOpt?.count, 1);
  assert.equal(failedOpt?.count, 1);
});
