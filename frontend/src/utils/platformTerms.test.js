import test from 'node:test';
import assert from 'node:assert/strict';
import { platformTerms, isDatabricksTarget } from './platformTerms.js';

test('snowflake, unknown and empty targets keep Snowflake words', () => {
  for (const t of [undefined, '', 'snowflake', 'fabric', null]) {
    const terms = platformTerms(t);
    assert.equal(terms.platform, 'Snowflake');
    assert.equal(terms.view, 'semantic view');
    assert.equal(terms.views, 'semantic views');
    assert.equal(terms.assistant, 'Cortex');
    assert.equal(terms.location, 'database.schema');
  }
});

test('databricks gets metric view words', () => {
  const terms = platformTerms('databricks');
  assert.deepEqual({ ...terms }, {
    platform: 'Databricks', view: 'metric view', views: 'metric views',
    assistant: 'Genie', location: 'catalog.schema',
  });
  assert.equal(isDatabricksTarget('Databricks'), true);
  assert.equal(isDatabricksTarget(''), false);
});
