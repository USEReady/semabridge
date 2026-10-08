/**
 * resolveEffectiveSources / buildMappingSignature (src/utils/dryRunPayload.js)
 * — the single derived source list used by BOTH the dry run and the project
 * save, and the signature that invalidates a stale dry run.
 *
 * Run: node --test tests/effectiveSources.test.js   (from frontend/)
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveEffectiveSources, buildMappingSignature } from '../src/utils/dryRunPayload.js';
import { planPbixFileAdditions, donePbixPaths, markInterruptedUploads, entriesFromSavedPaths } from '../src/utils/pbixFileEntries.js';

const A = 'C:/up/aaa_Sales.pbix';
const B = 'C:/up/bbb_Cost.pbix';

test('PBIX single upload mode -> single run of the uploaded path', () => {
  const r = resolveEffectiveSources({ sourceConnector: 'pbix', pbixSourceMode: 'MANUAL', pbixUploadPath: A });
  assert.equal(r.mode, 'single');
  assert.equal(r.singlePbixPath, A);
  assert.equal(r.isMulti, false);
});

test('PBIX "Multiple files" with >1 file -> multi run of every file', () => {
  const r = resolveEffectiveSources({ sourceConnector: 'pbix', pbixSourceMode: 'MANUAL', pbixMultiFileMode: true, pbixFilePaths: [A, B], pbixUploadPath: 'C:/stale.pbix' });
  assert.equal(r.mode, 'multi');
  assert.deepEqual(r.sources, [A, B]);
  assert.equal(r.isMulti, true);
  assert.equal(r.singlePbixPath, '');
});

test('PBIX "Multiple files" with exactly 1 file -> single run of THAT file (not the stale single-upload path)', () => {
  const r = resolveEffectiveSources({ sourceConnector: 'pbix', pbixSourceMode: 'MANUAL', pbixMultiFileMode: true, pbixFilePaths: [A], pbixUploadPath: 'C:/stale.pbix' });
  assert.equal(r.mode, 'single');
  assert.equal(r.singlePbixPath, A);
  assert.equal(r.isMulti, false);
  assert.equal(r.useMultiFileList, true);
});

test('PBIX folder tag mode ignores a leftover multi-file list', () => {
  const r = resolveEffectiveSources({ sourceConnector: 'pbix', pbixSourceMode: 'TAG', pbixMultiFileMode: true, pbixFilePaths: [A, B], selectedPbixFilePath: 'D:/tag/x.pbix' });
  assert.equal(r.mode, 'single');
  assert.equal(r.singlePbixPath, 'D:/tag/x.pbix');
  assert.equal(r.isMulti, false);
});

test('PBIX with nothing selected -> none', () => {
  assert.equal(resolveEffectiveSources({ sourceConnector: 'pbix', pbixSourceMode: 'MANUAL', pbixMultiFileMode: true, pbixFilePaths: [] }).mode, 'none');
  assert.equal(resolveEffectiveSources({ sourceConnector: 'pbix', pbixSourceMode: 'TAG' }).mode, 'none');
});

test('Fabric: 1 model -> single; >1 (deduped) -> multi', () => {
  assert.equal(resolveEffectiveSources({ sourceConnector: 'fabric', selectedModelNames: ['Sales'] }).mode, 'single');
  assert.equal(resolveEffectiveSources({ sourceConnector: 'fabric', selectedModelNames: ['Sales', 'Sales'] }).mode, 'single');
  const multi = resolveEffectiveSources({ sourceConnector: 'fabric', selectedModelNames: ['Sales', 'Cost'] });
  assert.equal(multi.isMulti, true);
  assert.deepEqual(multi.sources, ['Sales', 'Cost']);
});

test('signature changes when the PBIX file list, multi toggle or source mode changes', () => {
  const base = { sourceConnector: 'pbix', pbixSourceMode: 'MANUAL', pbixMultiFileMode: true, pbixFilePaths: [A, B], effectiveSources: [A, B] };
  const sig = buildMappingSignature(base);
  assert.notEqual(sig, buildMappingSignature({ ...base, pbixFilePaths: [A], effectiveSources: [A] }), 'removing a file must invalidate');
  assert.notEqual(sig, buildMappingSignature({ ...base, pbixFilePaths: [A, B, 'C:/c.pbix'] }), 'adding a file must invalidate');
  assert.notEqual(sig, buildMappingSignature({ ...base, pbixMultiFileMode: false }), 'toggling Single/Multiple must invalidate');
  assert.notEqual(sig, buildMappingSignature({ ...base, pbixSourceMode: 'TAG' }), 'switching source mode must invalidate');
  assert.equal(sig, buildMappingSignature({ ...base }), 'identical input -> identical signature');
});

test('signature ignores selection order of model names/sets', () => {
  const a = buildMappingSignature({ sourceConnector: 'fabric', selectedModelNames: ['A', 'B'], selectedModels: new Set(['1', '2']), targetConnectors: new Set(['snowflake']) });
  const b = buildMappingSignature({ sourceConnector: 'fabric', selectedModelNames: ['B', 'A'], selectedModels: new Set(['2', '1']), targetConnectors: new Set(['snowflake']) });
  assert.equal(a, b);
});

test('planPbixFileAdditions reports duplicates / non-pbix / over-cap files instead of silently dropping them', () => {
  const existing = [{ id: 'e1', name: 'Sales.pbix', status: 'done', path: A }];
  const plan = planPbixFileAdditions(existing, [{ name: 'sales.PBIX' }, { name: 'notes.txt' }, { name: 'Cost.pbix' }], 2);
  assert.deepEqual(plan.accepted.map((f) => f.name), ['Cost.pbix']);
  assert.deepEqual(plan.duplicates, ['sales.PBIX']);
  assert.deepEqual(plan.invalid, ['notes.txt']);
  assert.match(plan.message, /already in the list/);
  assert.equal(plan.newEntries[0].status, 'uploading');

  const capped = planPbixFileAdditions(existing, [{ name: 'X.pbix' }, { name: 'Y.pbix' }], 2);
  assert.deepEqual(capped.overCap, ['Y.pbix']);
  assert.match(capped.message, /Up to 2 PBIX files/);
});

test('donePbixPaths / markInterruptedUploads / entriesFromSavedPaths', () => {
  const entries = [
    { id: '1', name: 'a', status: 'done', path: A },
    { id: '2', name: 'b', status: 'uploading', path: '' },
    { id: '3', name: 'c', status: 'error', path: '' },
  ];
  assert.deepEqual(donePbixPaths(entries), [A]);
  assert.equal(markInterruptedUploads(entries)[1].status, 'error');
  const H = '0123456789abcdef0123456789abcdef';
  const saved = entriesFromSavedPaths([`C:/up/${H}_Sales.pbix`, B]);
  assert.deepEqual(saved.map((e) => e.name), ['Sales.pbix', 'bbb_Cost.pbix']);
  assert.deepEqual(donePbixPaths(saved), [`C:/up/${H}_Sales.pbix`, B]);
});
