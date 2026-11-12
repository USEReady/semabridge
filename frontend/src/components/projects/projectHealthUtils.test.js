/**
 * Run: node --test src/components/projects/projectHealthUtils.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { scoreColor, ontologyTooltip, successSeries } from './projectHealthUtils.js';

test('score colour bands match the Projects legend (85+ green, 60-84 amber, <60 red)', () => {
  assert.equal(scoreColor(92), 'var(--color-success)');
  assert.equal(scoreColor(85), 'var(--color-success)');
  assert.equal(scoreColor(72), 'var(--color-warning)');
  assert.equal(scoreColor(48), 'var(--color-error)');
  assert.equal(scoreColor(null), 'var(--text-quaternary)');
});

test('tooltip explains a missing ontology and summarises a scored one', () => {
  assert.equal(ontologyTooltip(null), 'No ontology yet for this project');
  const tip = ontologyTooltip({
    score: 72, domains: [{ domain: 'Sales' }, { domain: 'Finance' }], metric_count: 134,
    glossary_coverage_pct: 74, duplicate_groups: 3, conflicts: 1, unresolved: 0,
  });
  assert.match(tip, /Ontology score 72\/100/);
  assert.match(tip, /Domains: Sales, Finance/);
  assert.doesNotMatch(tip, /Broken references/);
});

test('success series carries the last value over days without runs', () => {
  const series = successSeries([
    { runs: 0, succeeded: 0 },
    { runs: 2, succeeded: 1 },
    { runs: 0, succeeded: 0 },
    { runs: 4, succeeded: 4 },
  ]);
  assert.deepEqual(series, [0.5, 0.5, 0.5, 1]);
  assert.deepEqual(successSeries([]), []);
});
