/**
 * Per-model dry-run grouping + Proceed gating (src/utils/multiFileDryRunReadiness.js).
 *
 * Run: node --test src/utils/multiModelDryRun.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildMultiModelSections,
  resolveMultiModelReadiness,
  collectMappingOverrides,
  applyBulkResolution,
  applyRowRename,
  applySynonymUpdate,
  isJobTerminal,
  fileAttemptKey,
  buildDryRunMigrationFields,
} from './multiFileDryRunReadiness.js';

const col = (id, extra = {}) => ({ id, field_type: 'column', source_path: `datasets.T.${id}`, target_field: id.toUpperCase(), status: 'auto', ...extra });
const met = (id, extra = {}) => ({ id, field_type: 'measure', source_path: `metrics.${id}`, target_field: id.toUpperCase(), status: 'auto', ...extra });

const job = (files) => ({ status: 'running', files });
const fileA = { file_id: 'j-f0', status: 'success', label: 'Sales', source_ref: 'C:/up/aa_Sales.pbix', completed_at: 't1' };
// Legacy status shape (filename/error_summary, no label/source_ref/completed_at).
const fileB = { file_id: 'j-f1', status: 'running', filename: 'Cost', pbix_path: 'C:/up/0123456789abcdef0123456789abcdef_Cost.pbix' };

test('groups rows per model: each section holds exactly its own columns/metrics', () => {
  const states = { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a'), col('b'), met('m')], result: { compatibility_score: 95 } } };
  const [a, b] = buildMultiModelSections(job([fileA, fileB]), states);
  assert.equal(a.label, 'Sales');
  assert.equal(a.loaded, true);
  assert.equal(a.columnCount, 2);
  assert.equal(a.metricCount, 1);
  assert.equal(a.sourceRef, 'C:/up/aa_Sales.pbix');
  assert.equal(a.compatibilityScore, 95);
  assert.equal(b.label, 'Cost');
  assert.equal(b.sourceRef, fileB.pbix_path);
  assert.equal(b.status, 'running');
  assert.equal(b.loaded, false);
  assert.deepEqual(b.rows, []);
});

test('a finished model is "loading" until its result lands, then loaded (even if opened while running)', () => {
  let [a] = buildMultiModelSections(job([fileA]), {});
  assert.equal(a.loading, true);
  assert.equal(a.loaded, false);
  [a] = buildMultiModelSections(job([fileA]), { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a')], result: {} } });
  assert.equal(a.loaded, true);
  assert.equal(a.loading, false);
});

test('a stale result from a previous attempt (re-run) is not reused', () => {
  const rerun = { ...fileA, completed_at: 't2' };
  const [a] = buildMultiModelSections(job([rerun]), { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a')] } });
  assert.equal(a.loaded, false);
});

test('a failed model exposes its real error message (new and legacy field names)', () => {
  const [f, g] = buildMultiModelSections(job([
    { file_id: 'x', status: 'failed', error_message: 'No SML blob found' },
    { file_id: 'y', status: 'failed', error_summary: 'Legacy error' },
  ]), {});
  assert.equal(f.status, 'failed');
  assert.equal(f.error, 'No SML blob found');
  assert.equal(g.error, 'Legacy error');
});

test('readiness: not ready while any model is still running', () => {
  const sections = buildMultiModelSections(job([fileA, fileB]), { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a')] } });
  const r = resolveMultiModelReadiness(sections);
  assert.equal(r.running, true);
  assert.equal(r.ready, false);
});

test('readiness: not ready when any model failed', () => {
  const files = [fileA, { file_id: 'j-f1', status: 'failed', error_message: 'boom' }];
  const sections = buildMultiModelSections(job(files), { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a')] } });
  const r = resolveMultiModelReadiness(sections);
  assert.equal(r.hasFailures, true);
  assert.equal(r.failedCount, 1);
  assert.equal(r.ready, false);
});

test('readiness: blocked when ONE model has an unresolved collision; ready once resolved in that model', () => {
  const f1 = { ...fileB, status: 'success', completed_at: 't3' };
  const states = {
    'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a')] },
    'j-f1': { attempt: fileAttemptKey(f1), rows: [col('x', { status: 'collision', collision_detected: true, validation_code: 'COLLISION' })] },
  };
  let r = resolveMultiModelReadiness(buildMultiModelSections(job([fileA, f1]), states));
  assert.equal(r.allSucceeded, true);
  assert.equal(r.totalBlocking, 1);
  assert.deepEqual(r.blockedModels, ['Cost']);
  assert.equal(r.ready, false);

  states['j-f1'] = { ...states['j-f1'], rows: applyBulkResolution(states['j-f1'].rows, { x: 'X_COST' }) };
  r = resolveMultiModelReadiness(buildMultiModelSections(job([fileA, f1]), states));
  assert.equal(r.totalBlocking, 0);
  assert.equal(r.ready, true);
});

test('readiness: unmapped rows and low compatibility block, like the single-model gate', () => {
  const unmapped = { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a', { status: 'unmapped', target_field: '' })] } };
  assert.equal(resolveMultiModelReadiness(buildMultiModelSections(job([fileA]), unmapped)).ready, false);
  const low = { 'j-f0': { attempt: fileAttemptKey(fileA), rows: [col('a')], result: { compatibility_score: 50 } } };
  const r = resolveMultiModelReadiness(buildMultiModelSections(job([fileA]), low));
  assert.equal(r.ready, false);
  assert.deepEqual(r.lowCompatModels, ['Sales']);
});

test('readiness with no models is never ready', () => {
  assert.equal(resolveMultiModelReadiness([]).ready, false);
});

test('isJobTerminal: polling stops only when every file is success/failed', () => {
  assert.equal(isJobTerminal(job([fileA, fileB])), false);
  assert.equal(isJobTerminal(job([fileA, { ...fileB, status: 'failed' }])), true);
});

test('row edits are scoped: rename/synonyms only touch the given row', () => {
  const rows = [col('a', { status: 'collision', validation_code: 'X' }), col('b')];
  const renamed = applyRowRename(rows, 'a', { target_name: 'NEW_A' });
  assert.equal(renamed[0].target_field, 'NEW_A');
  assert.equal(renamed[0].status, 'manual');
  assert.equal(renamed[0].validation_code, 'OK');
  assert.equal(renamed[1], rows[1]);
  const syn = applySynonymUpdate(rows, 'b', ['bee']);
  assert.deepEqual(syn[1].synonym_overrides, ['bee']);
  assert.equal(syn[0], rows[0]);
});

test('mappings_overrides: single shape unchanged; multi entries keyed by model', () => {
  const single = collectMappingOverrides({ rows: [col('a'), { id: 'z', source_path: '', target_field: 'Z' }] });
  assert.deepEqual(single, [{ source_path: 'datasets.T.a', target_name: 'A' }]);

  const sections = [
    { sourceRef: 'C:/up/Sales.pbix', rows: [col('a', { target_field: 'SALES_A' })] },
    { sourceRef: 'C:/up/Cost.pbix', rows: [col('a', { target_field: 'COST_A' })] },
  ];
  assert.deepEqual(collectMappingOverrides({ sections }), [
    { model: 'C:/up/Sales.pbix', source_path: 'datasets.T.a', target_name: 'SALES_A' },
    { model: 'C:/up/Cost.pbix', source_path: 'datasets.T.a', target_name: 'COST_A' },
  ]);
});

test('dry-run migration fields: single preview id vs multi job id + per-model preview ids', () => {
  assert.deepEqual(buildDryRunMigrationFields({ singlePreviewProjectId: 'preview-abc' }), { preview_project_id: 'preview-abc' });
  assert.deepEqual(buildDryRunMigrationFields({ singlePreviewProjectId: 'preview' }), {});
  assert.deepEqual(
    buildDryRunMigrationFields({ multiJobId: 'dryrun-1', sections: [{ previewProjectId: 'p1' }, { result: { project_id: 'p2' } }, { previewProjectId: 'p1' }] }),
    { dry_run_job_id: 'dryrun-1', preview_project_ids: ['p1', 'p2'] },
  );
});
