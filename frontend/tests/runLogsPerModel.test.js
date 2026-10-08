import test from 'node:test';
import assert from 'node:assert/strict';

import { getPerModelRunEntries } from '../src/utils/runLogs.js';

// getPerModelRunEntries() drives the per-model dropdowns on the Runs page
// (ProjectJobsPage.jsx). It must return [] for single-model runs so that
// view renders exactly as before, and otherwise merge run.results with
// run.model_reports by model label.

const HEX = '7ab1bcb7c7d144a6a42c8d58a73c3d46';

function result(model, extra = {}) {
  return { model, status: 'success', summary: {}, dropped_entities: [], ...extra };
}

test('returns [] for missing / empty / single-model runs', () => {
  assert.deepEqual(getPerModelRunEntries(null), []);
  assert.deepEqual(getPerModelRunEntries({}), []);
  assert.deepEqual(getPerModelRunEntries({ results: [] }), []);
  assert.deepEqual(getPerModelRunEntries({ results: [result('a.pbix')] }), []);
  assert.deepEqual(
    getPerModelRunEntries({ results: [result('a.pbix')], model_reports: [{ model: 'a.pbix' }] }),
    [],
  );
});

test('one entry per result, merged with matching model_reports in result order', () => {
  const run = {
    results: [
      result(`C:/up/${HEX}_Sales.pbix`, { status: 'failed', view_name: 'SALES_SV' }),
      result('C:/up/Returns.pbix', { status: 'conflict' }),
    ],
    model_reports: [
      { model: 'C:/up/Returns.pbix', path: 'r2.md' },
      { model: `C:/up/${HEX}_Sales.pbix`, path: 'r1.md' },
    ],
  };
  const entries = getPerModelRunEntries(run);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].label, 'Sales.pbix');
  assert.equal(entries[0].status, 'failed');
  assert.equal(entries[0].viewName, 'SALES_SV');
  assert.equal(entries[0].reportEntry.path, 'r1.md');
  assert.equal(entries[0].reportModel, `C:/up/${HEX}_Sales.pbix`);
  assert.equal(entries[1].label, 'Returns.pbix');
  assert.equal(entries[1].status, 'conflict');
  assert.equal(entries[1].reportEntry.path, 'r2.md');
  assert.equal(entries[1].viewName, null);
  assert.notEqual(entries[0].key, entries[1].key);
});

test('falls back to cleaned-label matching when raw labels differ', () => {
  const run = {
    results: [result(`/tmp/${HEX}_A.pbix`), result('/tmp/B.pbix')],
    model_reports: [{ model: 'A.pbix' }, { model: 'B.pbix' }],
  };
  const [a, b] = getPerModelRunEntries(run);
  assert.equal(a.reportModel, 'A.pbix');
  assert.equal(b.reportModel, 'B.pbix');
});

test('logs prefer console.lines, then console.text, then summary steps', () => {
  const run = {
    results: [
      result('a.pbix', { console: { lines: ['line 1', 'line 2'], text: 'ignored' } }),
      result('b.pbix', { console: { lines: [], text: 'x\r\ny\n' } }),
      result('c.pbix', {
        summary: {
          steps_completed: [{ step_number: 1, step_name: 'Parse', status: 'success' }],
          errors: [{ step_number: 2, step_name: 'Deploy', message: 'boom' }],
        },
      }),
    ],
  };
  const [a, b, c] = getPerModelRunEntries(run);
  assert.deepEqual(a.logs, ['line 1', 'line 2']);
  assert.deepEqual(b.logs, ['x', 'y']);
  assert.equal(c.logs.length, 2);
  assert.match(c.logs[0], /^SUCCESS .* Stage 1: Parse$/);
  assert.match(c.logs[1], /^ERROR .* Stage 2 \(Deploy\) - boom$/);
  // No [model] prefix inside a model's own section.
  assert.ok(!c.logs[0].includes('[c.pbix]'));
});

test('carries dropped entities, reconciliation, artifact path and duration', () => {
  const dropped = [{ entity_kind: 'metric', entity_name: 'M1' }];
  const run = {
    results: [
      result('a.pbix', {
        dropped_entities: dropped,
        reconciliation: { is_clean: false, unaccounted: { M2: 1 } },
        target_artifact_path: 'out/a.yaml',
        summary: { started_at: '2026-01-01T00:00:00Z', completed_at: '2026-01-01T00:00:05Z' },
      }),
      result('b.pbix', { summary: { reconciliation: { is_clean: true }, duration_ms: 1234 } }),
    ],
  };
  const [a, b] = getPerModelRunEntries(run);
  assert.deepEqual(a.droppedEntities, dropped);
  assert.deepEqual(a.reconciliation, { is_clean: false, unaccounted: { M2: 1 } });
  assert.equal(a.targetArtifactPath, 'out/a.yaml');
  assert.equal(a.durationMs, 5000);
  assert.deepEqual(b.reconciliation, { is_clean: true });
  assert.equal(b.durationMs, 1234);
  assert.equal(a.reportEntry, null);
  assert.equal(a.reportModel, null);
});

test('report-only models (no result) are appended; missing labels get stable unique keys', () => {
  const run = {
    results: [result(''), result('')],
    model_reports: [{ model: 'Orphan.pbix' }],
  };
  const entries = getPerModelRunEntries(run);
  assert.equal(entries.length, 3);
  assert.equal(entries[0].label, 'Model 1');
  assert.equal(entries[1].label, 'Model 2');
  assert.equal(entries[2].label, 'Orphan.pbix');
  assert.equal(entries[2].hasResult, false);
  assert.equal(entries[2].status, 'unknown');
  assert.equal(new Set(entries.map((e) => e.key)).size, 3);
});

test('duplicate model labels still produce unique keys and pair reports one-to-one', () => {
  const run = {
    results: [result('dup.pbix'), result('dup.pbix')],
    model_reports: [{ model: 'dup.pbix', path: '1' }, { model: 'dup.pbix', path: '2' }],
  };
  const [a, b] = getPerModelRunEntries(run);
  assert.notEqual(a.key, b.key);
  assert.equal(a.reportEntry.path, '1');
  assert.equal(b.reportEntry.path, '2');
});
