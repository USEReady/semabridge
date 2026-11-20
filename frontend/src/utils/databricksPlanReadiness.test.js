import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildMultiModelSections,
  resolveMultiModelReadiness,
} from './multiFileDryRunReadiness.js';

function makeJobStatus() {
  return {
    status: 'success',
    files: [
      {
        file_id: 'f-1',
        status: 'success',
        label: 'Sales',
        source_ref: 'Sales.pbix',
        completed_at: '2026-11-19T10:00:00Z',
      },
    ],
  };
}

function makeFileStates(databricksPlan) {
  return {
    'f-1': {
      attempt: 'f-1@2026-11-19T10:00:00Z',
      rows: [
        {
          id: 'r1',
          entity_kind: 'column',
          source_name: 'Amount',
          target_name: 'Amount',
          status: 'auto',
          validation_status: 'valid',
          validation_code: 'OK',
        },
      ],
      result: {
        databricks_plan: databricksPlan,
        blocking_conflicts: [],
      },
    },
  };
}

test('resolveMultiModelReadiness allows a clean Databricks plan (even with placeholder measures)', () => {
  const sections = buildMultiModelSections(
    makeJobStatus(),
    makeFileStates({
      target: 'main.gold',
      views: [
        {
          name: '`main`.`gold`.`mv_Sales`',
          skipped: false,
          measures: [{ name: 'Margin_Pct', status: 'placeholder', reason: 'Complex DAX' }],
        },
      ],
      missing_tables: [],
      blocked: [],
      warnings: ['Placeholder measure'],
      error: null,
    }),
  );
  const readiness = resolveMultiModelReadiness(sections);
  assert.equal(readiness.ready, true);
  assert.equal(readiness.totalBlocking, 0);
});

test('resolveMultiModelReadiness blocks when Databricks plan has blocked items, missing tables, skipped views, or error', () => {
  const cases = [
    { blocked: [{ table: 'fact_sales', message: 'Conflict' }], missing_tables: [], views: [] },
    { blocked: [], missing_tables: ['`main`.`gold`.`fact_sales`'], views: [] },
    { blocked: [], missing_tables: [], views: [{ name: 'mv_x', skipped: true, reason: 'No source' }] },
    { blocked: [], missing_tables: [], views: [], error: 'Warehouse unavailable' },
  ];

  cases.forEach((plan, idx) => {
    const sections = buildMultiModelSections(makeJobStatus(), makeFileStates(plan));
    const readiness = resolveMultiModelReadiness(sections);
    assert.equal(readiness.ready, false, `case ${idx} should block readiness`);
    assert.ok(readiness.totalBlocking >= 1, `case ${idx} should count at least 1 blocker`);
  });
});
