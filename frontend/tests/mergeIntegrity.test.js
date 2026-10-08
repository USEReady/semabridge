// Guards against merges that succeed textually but break at runtime:
// duplicate object keys in the api client (the later copy silently wins) and
// duplicated component state / a replaced header action on the Runs page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(path.join(here, '..', rel), 'utf8');

test('api.js defines every method exactly once', () => {
  const src = read('src/utils/api.js');
  const names = [...src.matchAll(/^ {4}async ([A-Za-z0-9_]+)\(/gm)].map((m) => m[1]);
  const seen = new Map();
  for (const name of names) seen.set(name, (seen.get(name) || 0) + 1);
  const duplicates = [...seen].filter(([, count]) => count > 1).map(([name]) => name);
  assert.deepEqual(duplicates, []);
  // The run-report methods the Runs page relies on are all present.
  for (const name of ['getRunReport', 'getRunReportSummary', 'downloadRunReport', 'clearJobRuns',
    'getRunModelReport', 'getRunModelReportSummary', 'downloadRunModelReport']) {
    assert.ok(seen.has(name), `missing api.${name}`);
  }
});

test('Runs page declares its report-modal state once and keeps the Run Now header + server-side Clear Logs', () => {
  const src = read('src/pages/ProjectJobsPage.jsx');
  const decls = src.match(/const \[reportModalRun, setReportModalRun\]/g) || [];
  assert.equal(decls.length, 1);
  assert.match(src, /label: running \? 'Running…' : 'Run Now'/);
  assert.match(src, /onClick: handleRunNow/);
  assert.match(src, /await api\.clearJobRuns\(\)/);
  assert.doesNotMatch(src, /Run Scheduled Jobs/);
});
