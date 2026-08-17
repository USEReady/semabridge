/**
 * normalizeRows() contract tests.
 *
 * This is the frontend half of the exact "field silently dropped at a
 * boundary" bug class that has now hit this codebase three times:
 * advisory_categories, source_file, and complexity_tier. All three were
 * populated correctly by the backend and dropped by normalizeRows()'s
 * explicit field allowlist before ever reaching DryRunMappingTable. Earlier
 * unit tests on upstream layers (mappingFilterUtils.js's rowComplexityTier,
 * for instance) never caught this, because rowComplexityTier's own logic
 * was always correct -- it just never received a populated value, since
 * normalizeRows() silently stripped it first. That is why these tests build
 * a realistic RAW API RESPONSE SHAPE (matching mappings_controller.py's
 * filtered_mappings.append dict field-for-field) and run it through the
 * real normalizeRows(), rather than constructing an already-normalized row
 * and testing something downstream of the bug.
 *
 * Run: node --test src/utils/normalizeRows.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeRows } from './normalizeRows.js';

// Mirrors mappings_controller.py's filtered_mappings.append({...}) dict
// field-for-field (see the Python source for the canonical list) -- every
// key the real /dry-run (and per-file dry-run-job) API response actually
// sends for one row.
function backendRow(overrides = {}) {
  return {
    id: 'field_1',
    project_id: 'preview-abc123',
    model_name: 'Sales Model',
    entity_kind: 'metric',
    source_name: 'Total Revenue',
    source_data_type: 'DECIMAL',
    source_table: '',
    source_path: 'metrics.Total Revenue',
    source_qualified_path: '',
    target_name: 'TOTAL_REVENUE',
    target_data_type: 'DECIMAL',
    mapping_status: 'auto',
    status: 'auto',
    suggested_target_name: 'TOTAL_REVENUE',
    collision_detected: false,
    validation_status: 'valid',
    validation_code: 'OK',
    validation_message: '',
    measure_source_tables: ['SALES'],
    source_expression: 'SUM([Amount])',
    synonym_overrides: [],
    target_expression: 'SUM(AMOUNT)',
    sync_enabled: true,
    sync_failure_reason: '',
    advisory_notes: ['Uses Time Intelligence; verify against a live schema before deploy.'],
    advisory_categories: ['unreachable_dimension'],
    depends_on_measures: ['OTHER_MEASURE'],
    synonyms: ['Revenue', 'Sales Total'],
    complexity_tier: 3,
    static_risk_tier: 'known_risky_pattern',
    static_risk_label: 'Known risky pattern detected',
    llm_self_reported_confidence: 0.82,
    source_file: 'C:/Reports/Sales Report.pbix',
    ...overrides,
  };
}

function apiResponse(rows) {
  return {
    success: true,
    project_id: 'preview-abc123',
    model_name: 'Sales Model',
    entity_mappings: rows,
    extraction_failed: false,
    dropped_entities: [],
    summary: { total_fields: rows.length, auto_mapped: rows.length, unmapped: 0, collisions: 0, predicted_failures: 0, extraction_failed: false },
  };
}

// ---------------------------------------------------------------------------
// The reported bug: complexity_tier
// ---------------------------------------------------------------------------

test('complexity_tier survives from a real API response shape through normalizeRows() intact', () => {
  const response = apiResponse([backendRow({ complexity_tier: 5 })]);
  const [row] = normalizeRows(response);
  assert.equal(row.complexity_tier, 5, 'complexity_tier must reach the normalized row unchanged');
});

test('complexity_tier of 0 (unknown, not falsy-absent) survives -- must not collapse to null via truthiness', () => {
  const response = apiResponse([backendRow({ complexity_tier: 0 })]);
  const [row] = normalizeRows(response);
  assert.equal(row.complexity_tier, 0);
});

test('complexity_tier is null (not undefined, not dropped as a key) when the backend sends null', () => {
  const response = apiResponse([backendRow({ complexity_tier: null })]);
  const [row] = normalizeRows(response);
  assert.equal(row.complexity_tier, null);
  assert.ok('complexity_tier' in row, 'the key itself must exist on the normalized row');
});

// ---------------------------------------------------------------------------
// Found during the same sweep: advisory_notes (sibling of advisory_categories)
// ---------------------------------------------------------------------------

test('advisory_notes survives from a real API response shape through normalizeRows() intact', () => {
  const notes = ['Uses Time Intelligence; verify against a live schema before deploy.'];
  const response = apiResponse([backendRow({ advisory_notes: notes })]);
  const [row] = normalizeRows(response);
  assert.deepEqual(row.advisory_notes, notes);
});

test('advisory_notes defaults to an empty array, not undefined, when absent', () => {
  const row = backendRow();
  delete row.advisory_notes;
  const [normalized] = normalizeRows(apiResponse([row]));
  assert.deepEqual(normalized.advisory_notes, []);
});

// ---------------------------------------------------------------------------
// Deliberate boundary sweep: every backend field that SHOULD pass through
// normalizeRows() unchanged, checked in one pass -- this is what's meant to
// catch a fourth occurrence of this bug class without waiting for it to be
// found feature-by-feature again.
// ---------------------------------------------------------------------------

// Fields the real API response sends that normalizeRows() is expected to
// forward VERBATIM (no renaming, no derivation) onto the normalized row.
// Deliberately excludes fields that are intentionally renamed
// (source_name -> source_field, target_name -> target_field), recomputed
// (status is re-derived from collision_detected/validation_*, not a
// pass-through of the raw status string), or genuinely inert today
// (source_table, source_qualified_path, mapping_status -- see the sweep
// findings below) -- those have their own dedicated tests/rationale, not a
// blanket pass-through check.
const EXPECTED_PASSTHROUGH_FIELDS = [
  'source_path',
  'validation_status',
  'validation_code',
  'validation_message',
  'suggested_target_name',
  'sync_failure_reason',
  'synonyms',
  'complexity_tier',
  'static_risk_tier',
  'static_risk_label',
  'llm_self_reported_confidence',
  'source_file',
  'advisory_categories',
  'advisory_notes',
];

test('boundary sweep: every field expected to pass through unchanged actually does', () => {
  const row = backendRow();
  const [normalized] = normalizeRows(apiResponse([row]));

  const missing = EXPECTED_PASSTHROUGH_FIELDS.filter((field) => {
    const expected = row[field];
    const actual = normalized[field];
    if (Array.isArray(expected)) return JSON.stringify(actual) !== JSON.stringify(expected);
    return actual !== expected;
  });

  assert.deepEqual(
    missing,
    [],
    `These fields are populated by the backend but do not survive normalizeRows() unchanged: ${missing.join(', ')}. ` +
    'This is the exact silent-drop bug class already hit three times (advisory_categories, source_file, complexity_tier) -- ' +
    'add the missing field to the allowlist in normalizeRows.js.',
  );
});

// ---------------------------------------------------------------------------
// Renamed/derived fields -- confirmed intentional, not drops
// ---------------------------------------------------------------------------

test('source_name is intentionally renamed to source_field (not a drop)', () => {
  const [row] = normalizeRows(apiResponse([backendRow({ source_name: 'Total Revenue' })]));
  assert.equal(row.source_field, 'Total Revenue');
});

test('target_name is intentionally renamed to target_field (not a drop)', () => {
  const [row] = normalizeRows(apiResponse([backendRow({ target_name: 'TOTAL_REVENUE' })]));
  assert.equal(row.target_field, 'TOTAL_REVENUE');
});

test('measure_source_tables is read via the isMeasure branch, not dropped', () => {
  const [row] = normalizeRows(apiResponse([backendRow({ entity_kind: 'metric', measure_source_tables: ['SALES', 'ORDERS'] })]));
  assert.deepEqual(row.measure_source_tables, ['SALES', 'ORDERS']);
});
