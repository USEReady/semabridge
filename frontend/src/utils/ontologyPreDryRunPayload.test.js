/**
 * Source-agnostic pre-dry-run payload + saved ontology domain ID propagation tests.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildDryRunPayload,
  buildMappingSignature,
  extractSavedOntologyDomainIds,
} from './dryRunPayload.js';

test('buildDryRunPayload populates sourceConfig for all 5 source connectors', () => {
  const fab = buildDryRunPayload({
    sourceConnector: 'fabric',
    targetConnectors: new Set(['databricks']),
    fabricAccountId: 'acc-fab',
    fabricWorkspaceId: 'ws-123',
    databricksAccountId: 'acc-dbx',
    targetDatabase: 'workspace',
    targetSchema: 'default',
    targetWarehouse: 'wh-1',
    selectedModelNames: ['Sales Exec', 'Regional Ops'],
  });
  assert.deepEqual(fab.sourceConfig, {
    type: 'fabric',
    identity_id: 'acc-fab',
    workspace_id: 'ws-123',
    models: ['Sales Exec', 'Regional Ops'],
  });
  assert.deepEqual(fab.targetConfig, {
    type: 'databricks',
    identity_id: 'acc-dbx',
    catalog: 'workspace',
    schema: 'default',
    warehouse_id: 'wh-1',
  });

  const sf = buildDryRunPayload({
    sourceConnector: 'snowflake',
    targetConnectors: new Set(['databricks']),
    snowflakeAccountId: 'acc-sf',
    snowflakeDatabase: 'ANALYTICS',
    snowflakeSchema: 'GOLD',
    selectedModelNames: ['SV_SALES'],
  });
  assert.equal(sf.sourceConfig.identity_id, 'acc-sf');
  assert.equal(sf.sourceConfig.database, 'ANALYTICS');
  assert.equal(sf.sourceConfig.schema, 'GOLD');

  const tab = buildDryRunPayload({
    sourceConnector: 'tableau',
    targetConnectors: new Set(['databricks']),
    tableauPath: '/tmp/orders.tdsx',
  });
  assert.equal(tab.sourceConfig.path, '/tmp/orders.tdsx');
  assert.equal(tab.sourceConfig.file_path, '/tmp/orders.tdsx');

  const dbx = buildDryRunPayload({
    sourceConnector: 'databricks',
    targetConnectors: new Set(['snowflake']),
    databricksAccountId: 'acc-dbx',
    selectedDatabricksTables: new Set(['main.gold.mv_orders', 'main.gold.fact_orders']),
  });
  assert.equal(dbx.sourceConfig.identity_id, 'acc-dbx');
  assert.equal(dbx.sourceConfig.catalog, 'main');
  assert.equal(dbx.sourceConfig.schema, 'gold');
  assert.deepEqual(dbx.sourceConfig.tables, ['main.gold.mv_orders', 'main.gold.fact_orders']);
});

test('extractSavedOntologyDomainIds handles single-model and multi-model Step 4 saves', () => {
  assert.deepEqual(extractSavedOntologyDomainIds(null), []);
  assert.deepEqual(extractSavedOntologyDomainIds({ id: 'sales_domain' }), ['sales_domain']);
  assert.deepEqual(
    extractSavedOntologyDomainIds({
      multi_model: true,
      domains: [
        { id: 'sales__exec', domain: 'Sales · Exec' },
        { id: 'sales__ops', domain: 'Sales · Ops' },
      ],
    }),
    ['sales__exec', 'sales__ops'],
  );
});

test('buildMappingSignature invalidates stale Dry Run when Step 4 ontology domains are saved', () => {
  const before = buildMappingSignature({
    sourceConnector: 'fabric',
    targetConnectors: ['databricks'],
    selectedModelNames: ['Sales Exec'],
    ontologyDomainIds: [],
  });
  const after = buildMappingSignature({
    sourceConnector: 'fabric',
    targetConnectors: ['databricks'],
    selectedModelNames: ['Sales Exec'],
    ontologyDomainIds: ['sales_exec_domain'],
  });
  assert.notEqual(before, after);
});

