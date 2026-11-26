import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyCollision, splitCollisions, metricRows, matchesQuery } from './ontologyDrilldown.js';

const owners = { 'Sales A': 'proj-a', 'Sales B': 'proj-b', 'Finance A': 'proj-a' };

test('different formulas are conflicts; identical ones are shared names', () => {
  const { conflicts, shared } = splitCollisions([
    { item_name: 'Revenue', severity: 'ERROR', conflicting_domains: ['Sales A', 'Sales B'] },
    { item_name: 'Units', severity: 'WARNING', conflicting_domains: ['Sales A', 'Sales B'] },
  ], owners);
  assert.deepEqual(conflicts.map((c) => c.item_name), ['Revenue']);
  assert.deepEqual(shared.map((c) => c.item_name), ['Units']);
});

test('only a conflict inside one project can be resolved there', () => {
  assert.equal(classifyCollision({ severity: 'ERROR', conflicting_domains: ['Sales A', 'Finance A'] }, owners).resolvableIn, 'proj-a');
  assert.equal(classifyCollision({ severity: 'ERROR', conflicting_domains: ['Sales A', 'Sales B'] }, owners).resolvableIn, null);
  assert.equal(classifyCollision({ severity: 'WARNING', conflicting_domains: ['Sales A', 'Finance A'] }, owners).resolvableIn, null);
});

test('metric rows carry domain, formula and BI binding; search matches any text', () => {
  const rows = metricRows([{ domain: 'Sales A' }], { 'Sales A': { parsed: { metrics: [
    { name: 'Revenue', sql_expression: 'SUM(amount)', maps_to: ['x'] },
  ] } } });
  assert.equal(rows[0].boundToBi, true);
  assert.ok(matchesQuery(rows[0], 'sum('));
  assert.ok(!matchesQuery(rows[0], 'margin'));
});
