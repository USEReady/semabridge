import test from 'node:test';
import assert from 'node:assert/strict';
import { projectLabel, projectName } from './projectLabel.js';

test('project name prefers the name the user gave over the model name', () => {
  assert.equal(projectName({ id: 'proj-nat1', display_name: 'nat1', name: 'Competitive Marketing Analysis' }), 'nat1');
  assert.equal(projectName({ id: 'proj-sales', semantic_name: 'Regional Sales', name: 'x' }), 'Regional Sales');
  assert.equal(projectName({ id: 'proj-comp', name: 'Competitive Marketing Analysis' }), 'Competitive Marketing Analysis');
  assert.equal(projectName({ id: 'proj-x' }), 'proj-x');
});

test('label adds the id only when two projects share a name', () => {
  const all = [
    { id: 'proj-comp', name: 'Competitive Marketing Analysis' },
    { id: 'proj-cma', name: 'Competitive Marketing Analysis' },
    { id: 'proj-nat1', display_name: 'nat1', name: 'Competitive Marketing Analysis' },
  ];
  assert.equal(projectLabel(all[0], all), 'Competitive Marketing Analysis (proj-comp)');
  assert.equal(projectLabel(all[2], all), 'nat1');
});
