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