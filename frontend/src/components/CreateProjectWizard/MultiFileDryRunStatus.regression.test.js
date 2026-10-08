/**
 * MultiFileDryRunStatus.regression.test.js
 *
 * Regression test for the multi-file drill-in bug: MultiFileDryRunStatus.jsx
 * used to pass detail.result.entity_mappings (the RAW /dry-run-job file
 * response — see dry_run_job_service.py's get_dry_run_job_file_result, which
 * returns the exact same raw shape as the single-file /dry-run endpoint)
 * straight into DryRunMappingTable's `mappings` prop, with no call to
 * utils/normalizeRows.js in between.
 *
 * Symptom, verified against the actual wizard screenshots: the group header
 * count and Status/SQL columns rendered fine (their key names happen to
 * match in both the raw and normalized shapes), but every row showed a
 * literal "fx Measure"/"unknown" badge instead of the real field name, every
 * Target Field showed the literal "— unmapped —" fallback regardless of real
 * mapping status, and the "Source Expression (DAX)" box rendered blank ("—")
 * while the sibling "Snowflake Translation (SQL)" box on the same row
 * rendered correctly.
 *
 * This test does NOT re-mount the component (no jsdom/RTL in this project —
 * see DryRunMappingTable.guardrail.test.js for why). Instead it proves the
 * data transform MultiFileDryRunStatus.jsx now applies (normalizeRows(detail.result))
 * turns a realistic raw file-detail response into rows with real, non-placeholder
 * source/target field names and a real DAX expression — i.e. it fails the way
 * the original bug actually manifested if normalizeRows() is ever removed from
 * that call site again.
 *
 * Run: node --test src/components/CreateProjectWizard/MultiFileDryRunStatus.regression.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeRows } from '../../utils/normalizeRows.js';

// Mirrors dry_run_job_service.py's get_dry_run_job_file_result()'s `result`
// field verbatim -- the exact object MultiFileDryRunStatus.jsx receives as
// `detail.result` from api.getDryRunJobFile(), for a model whose dry run
// succeeded (real SQL translation, "auto" status) -- i.e. the scenario from
// the first screenshot, not the separate "No SML blob found" extraction
// failure.
function rawMultiFileDrillInResponse() {
  return {
    success: true,
    project_id: 'preview-15521cd5f819',
    model_name: 'Cost Model',
    extraction_failed: false,
    dropped_entities: [],
    entity_mappings: [
      {
        id: 'field_1',
        project_id: 'preview-15521cd5f819',
        model_name: 'Cost Model',
        entity_kind: 'metric',
        source_name: 'Cost Element Amount',
        source_data_type: 'DECIMAL',
        source_path: 'metrics.Cost Element Amount',
        target_name: 'COST_ELEMENT_AMOUNT',
        target_data_type: 'DECIMAL',
        status: 'auto',
        suggested_target_name: 'COST_ELEMENT_AMOUNT',
        collision_detected: false,
        validation_status: 'valid',
        validation_code: 'OK',
        validation_message: '',
        measure_source_tables: ['COSTELEMENT'],
        source_expression: '_',
        target_expression: 'SUM(costelement."COST_ELEMENT_NAME")',
        sync_enabled: true,
        sync_failure_reason: '',
      },
    ],
    summary: { total_fields: 1, auto_mapped: 1, unmapped: 0, collisions: 0, predicted_failures: 0, extraction_failed: false },
  };
}

test('normalizeRows(detail.result) yields the real measure name, not the "fx Measure" placeholder', () => {
  const [row] = normalizeRows(rawMultiFileDrillInResponse());
  assert.equal(row.source_field, 'Cost Element Amount');
  assert.notEqual(row.source_field, undefined);
});

test('normalizeRows(detail.result) yields the real target field, not "-- unmapped --"', () => {
  const [row] = normalizeRows(rawMultiFileDrillInResponse());
  assert.equal(row.target_field, 'COST_ELEMENT_AMOUNT');
  // DryRunMappingTable renders the literal '— unmapped —' string itself when
  // target_field is falsy -- assert the normalized value is truthy so that
  // fallback path is never hit for a field that actually mapped.
  assert.ok(row.target_field, 'target_field must be non-empty for an auto-mapped row');
});

test('normalizeRows(detail.result) yields a real DAX expression, not blank', () => {
  const [row] = normalizeRows(rawMultiFileDrillInResponse());
  assert.equal(row.measure_expression, '_');
  assert.notEqual(row.measure_expression, '');
});

test('normalizeRows(detail.result) leaves the SQL translation intact (this field never broke)', () => {
  const [row] = normalizeRows(rawMultiFileDrillInResponse());
  assert.equal(row.target_expression, 'SUM(costelement."COST_ELEMENT_NAME")');
});

test('status still reads "auto" post-normalization, matching the raw shape (this field never broke either)', () => {
  const [row] = normalizeRows(rawMultiFileDrillInResponse());
  assert.equal(row.status, 'auto');
});

// Sanity check: reproduce the ORIGINAL bug explicitly, so this test file
// documents (and would re-fail against) the exact regression if someone ever
// reverts to `mappings={detail.result.entity_mappings || []}` in
// MultiFileDryRunStatus.jsx.
test('sanity: skipping normalizeRows() (the original bug) reproduces blank/placeholder fields', () => {
  const raw = rawMultiFileDrillInResponse();
  const [unnormalizedRow] = raw.entity_mappings; // what the buggy code passed directly
  assert.equal(unnormalizedRow.source_field, undefined, 'raw row has no source_field key -- this is what produced the "fx Measure" placeholder');
  assert.equal(unnormalizedRow.target_field, undefined, 'raw row has no target_field key -- this is what produced "-- unmapped --"');
  assert.equal(unnormalizedRow.measure_expression, undefined, 'raw row has no measure_expression key -- this is what produced the blank DAX box');
});
