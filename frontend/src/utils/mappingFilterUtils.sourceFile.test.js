/**
 * Tests for the source_file helpers in mappingFilterUtils.js (Part E:
 * multi-PBIX source-file attribution). Uses Node's built-in test runner,
 * matching riskLabels.test.js's convention (no new frontend test framework
 * dependency).
 *
 * These cover the frontend-side half of the two-ended verification this
 * field needed: an API-boundary contract test lives in
 * Tests/test_source_file_attribution.py (backend); this file covers the
 * pure presentation-logic boundary these helpers sit at. Rendering itself
 * (DryRunMappingTable.jsx) and the CreateProjectPage.jsx normalizeRows()
 * allowlist pass-through are not unit-testable with a JSX-free Node runner —
 * see this session's design-doc note recommending a Playwright/visual check
 * as the remaining follow-up, matching this project's prior "Dry-run table
 * Status column layout fix" precedent for the same class of concern.
 *
 * Run: node --test src/utils/mappingFilterUtils.sourceFile.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rowSourceFile, rowSourceFileName, hasSourceFileData } from './mappingFilterUtils.js';

test('rowSourceFile returns null when the field is absent (single-file dry-run today)', () => {
  assert.equal(rowSourceFile({}), null);
  assert.equal(rowSourceFile({ source_file: null }), null);
  assert.equal(rowSourceFile(undefined), null);
});

test('rowSourceFile returns the trimmed path when present', () => {
  assert.equal(rowSourceFile({ source_file: 'C:/Reports/Sales.pbix' }), 'C:/Reports/Sales.pbix');
  assert.equal(rowSourceFile({ source_file: '  C:/Reports/Sales.pbix  ' }), 'C:/Reports/Sales.pbix');
});

test('rowSourceFile treats an empty/whitespace string the same as absent', () => {
  assert.equal(rowSourceFile({ source_file: '' }), null);
  assert.equal(rowSourceFile({ source_file: '   ' }), null);
});

test('rowSourceFileName strips path and extension for display', () => {
  assert.equal(rowSourceFileName({ source_file: 'C:/Reports/Sales Report.pbix' }), 'Sales Report');
  assert.equal(rowSourceFileName({ source_file: 'C:\\Reports\\Sales Report.PBIX' }), 'Sales Report');
});

test('rowSourceFileName returns null when there is no source_file', () => {
  assert.equal(rowSourceFileName({}), null);
});

test('hasSourceFileData is false for an all-single-file row set (today\'s default)', () => {
  assert.equal(hasSourceFileData([{ source_name: 'A' }, { source_name: 'B' }]), false);
});

test('hasSourceFileData is true as soon as one row carries a source_file', () => {
  assert.equal(
    hasSourceFileData([{ source_name: 'A' }, { source_name: 'B', source_file: 'C:/Reports/Sales.pbix' }]),
    true,
  );
});
