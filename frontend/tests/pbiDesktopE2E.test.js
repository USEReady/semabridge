import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPORT_PATH = path.resolve(__dirname, '..', '..', 'screenshots', 'walk_report.json');

test('E2E Walk Verification: zero error boundaries and zero console errors', () => {
  assert.equal(fs.existsSync(REPORT_PATH), true, 'walk_report.json must exist');
  const report = JSON.parse(fs.readFileSync(REPORT_PATH, 'utf8'));

  // 1. Error boundaries
  assert.equal(
    report.errorsFound.length,
    0,
    `Error boundaries detected: ${JSON.stringify(report.errorsFound)}`
  );

  // 2. Console errors
  assert.equal(
    report.consoleErrors.length,
    0,
    `Console errors detected: ${JSON.stringify(report.consoleErrors)}`
  );

  // 3. Page errors
  assert.equal(
    report.pageErrors.length,
    0,
    `Page errors detected: ${JSON.stringify(report.pageErrors)}`
  );

  // 4. Verify all expected screens were walked and passed
  const expectedScreens = [
    '01 Create Project Init',
    '02 Source Card Selected',
    '03 Desktop Step List',
    '04 Desktop Step Inspect Summary',
    '05 Desktop Step Refresh',
    '06 Ontology Review Step',
    '07 Mapping Options Step',
    '08 Dry Run Completed',
    '09 Project Created View',
    '10 Run Report View',
    '11 Edit Project Page',
    '12 Schedule Modal Warning',
    '13 Launcher Open Pre-selected',
    '14 Launcher Closed File Notice',
    '15 Desktop Empty State',
    '16 Desktop Error State',
    '17 Flag OFF Source Card Hidden',
    '18 PBIX Flow Step 1',
    '19 PBIX Flow Step 2',
    '20 Fabric Flow Step 1',
    '21 Fabric Flow Step 2',
  ];

  for (const expected of expectedScreens) {
    const found = report.walkResults.find((r) => r.screen === expected);
    assert.ok(found, `Expected screen '${expected}' in walk results`);
    assert.equal(found.ok, 'OK', `Screen '${expected}' failed: ${found.details}`);
  }
});
