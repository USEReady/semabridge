import test from 'node:test';
import assert from 'node:assert/strict';
import { groupDomainsByProject, viewTail } from './domainGroups.js';

test('multi-model project domains are grouped by project, models first', () => {
  const groups = groupDomainsByProject([
    { domain: 'Glossary' },
    { domain: 'multi · shared', source_project_id: 'proj-multi' },
    { domain: 'multi · B', source_project_id: 'proj-multi', source_model_id: 'b', source_model_name: 'B' },
    { domain: 'multi · A', source_project_id: 'proj-multi', source_model_id: 'a', source_model_name: 'A' },
    { domain: 'Comp', source_project_id: 'proj-comp' },
  ]);
  assert.deepEqual(groups.map((g) => g.label), ['comp', 'multi', 'Other domains']);
  const multi = groups[1];
  assert.equal(multi.multiModel, true);
  assert.deepEqual(multi.items.map((d) => d.domain), ['multi · A', 'multi · B', 'multi · shared']);
  assert.equal(groups[0].multiModel, false);
});

test('viewTail strips database and schema', () => {
  assert.equal(viewTail('DB.S."CONTINENT_SEMANTIC"'), 'CONTINENT_SEMANTIC');
});
