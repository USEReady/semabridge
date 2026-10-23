import test from 'node:test';
import assert from 'node:assert/strict';

import { buildDryRunPayload } from '../src/utils/dryRunPayload.js';

test('buildDryRunPayload includes Fabric identity_id for source and target connectors', () => {
  const payload = buildDryRunPayload({
    sourceConnector: 'fabric',
    targetConnectors: new Set(['fabric']),
    fabricAccountId: 'account-123',
    fabricWorkspaceId: 'workspace-456',
    snowflakeDatabase: '',
    targetDatabase: '',
    selectedModelNames: ['continent'],
  });

  assert.deepEqual(payload.sourceConfig, {
    type: 'fabric',
    identity_id: 'account-123',
    workspace_id: 'workspace-456',
    models: ['continent'],
  });

  assert.deepEqual(payload.targetConfig, {
    type: 'fabric',
    identity_id: 'account-123',
    workspace_id: 'workspace-456',
  });
});

test('buildDryRunPayload keeps snowflake fields unchanged', () => {
  const payload = buildDryRunPayload({
    sourceConnector: 'snowflake',
    targetConnectors: new Set(['snowflake']),
    fabricAccountId: '',
    fabricWorkspaceId: '',
    snowflakeDatabase: 'ANALYTICS',
    targetDatabase: 'REPORTING',
    selectedModelNames: ['orders', 'customers'],
  });

  assert.deepEqual(payload.sourceConfig, {
    type: 'snowflake',
    database: 'ANALYTICS',
    models: ['orders', 'customers'],
  });

  assert.deepEqual(payload.targetConfig, {
    type: 'snowflake',
    database: 'REPORTING',
  });
});

test('buildDryRunPayload includes PBIX pbix_path in sourceConfig', () => {
  const payload = buildDryRunPayload({
    sourceConnector: 'pbix',
    targetConnectors: new Set(['snowflake']),
    fabricAccountId: '',
    fabricWorkspaceId: '',
    snowflakeDatabase: '',
    targetDatabase: 'REPORTING',
    selectedModelNames: [],
    pbixPath: 'C:/Sales.pbix',
  });

  assert.deepEqual(payload.sourceConfig, {
    type: 'pbix',
    pbix_path: 'C:/Sales.pbix',
  });

  assert.deepEqual(payload.targetConfig, {
    type: 'snowflake',
    database: 'REPORTING',
  });
});

test('buildDryRunPayload includes snowflakeSchema and targetSchema', () => {
  const payload = buildDryRunPayload({
    sourceConnector: 'snowflake',
    targetConnectors: new Set(['snowflake']),
    snowflakeDatabase: 'SRC_DB',
    snowflakeSchema: 'SRC_SCHEMA',
    targetDatabase: 'TGT_DB',
    targetSchema: 'TGT_SCHEMA',
    selectedModelNames: [],
  });

  assert.deepEqual(payload.sourceConfig, {
    type: 'snowflake',
    database: 'SRC_DB',
    schema: 'SRC_SCHEMA',
  });

  assert.deepEqual(payload.targetConfig, {
    type: 'snowflake',
    database: 'TGT_DB',
    schema: 'TGT_SCHEMA',
  });
});