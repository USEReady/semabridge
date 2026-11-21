import test from 'node:test';
import assert from 'node:assert/strict';
import { scopeCollisions, buildHealthResults } from './healthChecks.js';

test('scopeCollisions in project scope keeps intra-project collisions and groups cross-project ones', () => {
  const collisions = [
    {
      item_name: 'ACTIVES',
      conflicting_domains: ['hr__human_resources_sample_pbix', 'ai_databricks__metric_view'],
      identical: false,
    },
    {
      item_name: 'SEPS',
      conflicting_domains: ['hr__human_resources_sample_pbix', 'ai_sample_test__model'],
      identical: true,
    },
    {
      item_name: 'INTERNAL_COLLISION',
      conflicting_domains: ['hr__human_resources_sample_pbix', 'hr__other_model'],
      identical: false,
    },
    {
      item_name: 'UNRELATED_COLLISION',
      conflicting_domains: ['finance__model_a', 'finance__model_b'],
      identical: false,
    },
  ];

  const projectDomains = ['hr__human_resources_sample_pbix', 'hr__other_model'];
  const scoped = scopeCollisions(collisions, projectDomains, true);

  // Exactly 1 intra-project collision and 1 summary row for cross-project
  assert.equal(scoped.length, 2);
  const internal = scoped.find((c) => c.item_name === 'INTERNAL_COLLISION');
  assert.ok(internal, 'Intra-project collision must be preserved');

  const summary = scoped.find((c) => c._isCrossProjectSummary);
  assert.ok(summary, 'Cross-project occurrences must be in one collapsed summary');
  assert.equal(summary.occurrences.length, 2);
});

test('buildHealthResults formats identical formulas as info and different as warning', () => {
  const collisions = [
    {
      item_name: 'IdenticalMetric',
      conflicting_domains: ['DomA', 'DomB'],
      identical: true,
      expressions: { DomA: 'SUM(a)', DomB: 'SUM(a)' },
    },
    {
      item_name: 'DifferentMetric',
      conflicting_domains: ['DomA', 'DomB'],
      identical: false,
      expressions: { DomA: 'SUM(a)', DomB: 'AVG(a)' },
    },
  ];

  const rows = buildHealthResults({
    collisions,
    qualityRan: true,
    lintReport: { metrics_checked: 10, concepts_checked: 5, issues: [] },
  });

  const idRow = rows.find((r) => r.id === 'collision-0');
  assert.equal(idRow.severity, 'info');
  assert.match(idRow.detail, /Same definition in 2 places/);

  const diffRow = rows.find((r) => r.id === 'collision-1');
  assert.equal(diffRow.severity, 'warning');
  assert.match(diffRow.detail, /Different definitions/);

  // Counts by severity match rows shown
  const counts = rows.reduce((acc, r) => ({ ...acc, [r.severity]: (acc[r.severity] || 0) + 1 }), {});
  assert.equal(counts.info || 0, rows.filter((r) => r.severity === 'info').length);
  assert.equal(counts.warning || 0, rows.filter((r) => r.severity === 'warning').length);
  assert.equal(counts.passing || 0, rows.filter((r) => r.severity === 'passing').length);
  assert.equal(counts.pending || 0, rows.filter((r) => r.severity === 'pending').length);
  assert.equal(counts.breaking || 0, rows.filter((r) => r.severity === 'breaking').length);

  // Not checked row must not exist when metrics_checked > 0
  assert.ok(!rows.some((r) => r.title?.includes('no metrics in this project')));
});

test('buildHealthResults with zero metrics shows not checked', () => {
  const rows = buildHealthResults({
    collisions: [],
    qualityRan: true,
    lintReport: { metrics_checked: 0, concepts_checked: 0, issues: [] },
  });

  const emptyRow = rows.find((r) => r.id === 'lint-empty');
  assert.ok(emptyRow);
  assert.equal(emptyRow.severity, 'pending');
  assert.match(emptyRow.title, /Not checked: no metrics in this project/);
});
