import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { VERIFY_TABS, verifyTab } from './verifyTabs.js';

test('verify tabs: known tab from the URL, unknown falls back to questions', () => {
  assert.deepEqual(VERIFY_TABS.map((t) => t.id), ['questions', 'measures', 'targets']);
  assert.equal(verifyTab('?tab=measures'), 'measures');
  assert.equal(verifyTab('?tab=targets&project=p1'), 'targets');
  assert.equal(verifyTab('?tab=nope'), 'questions');
  assert.equal(verifyTab(''), 'questions');
});

test('the old pages redirect into the matching Verify tab', () => {
  const app = readFileSync(new URL('../App.jsx', import.meta.url), 'utf8');
  for (const [from, tab] of [['/bi-truth', 'questions'], ['/metric-comparator', 'measures'], ['/cross-platform', 'targets']]) {
    const line = app.split('\n').find((l) => l.includes(`path="${from}"`)) || '';
    assert.ok(line.includes('to="/verify"') && line.includes(`tab="${tab}"`), `${from} -> /verify?tab=${tab}`);
  }
});
