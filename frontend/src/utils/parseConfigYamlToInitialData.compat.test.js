/**
 * Batch 5 fix item 3: projects saved before the separate Fabric target state
 * still open in edit mode with their Fabric target identity/workspace filled.
 * The YAML below is the shape older wizard versions wrote (target.identity_id,
 * target.workspace_id, target.workspace), incl. a Fabric target listed after a
 * Snowflake target.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseConfigYamlToInitialData } from './parseConfigYamlToInitialData.js';
import { hydrateFabricEditState } from './fabricProjectState.js';

const OLD_FABRIC_TO_FABRIC = `
project_id: proj-old-1
source:
  type: fabric
  identity_id: acct-src
  workspace_id: ws-src
  workspace: Source WS
  models: [Sales]
targets:
  - type: fabric
    identity_id: acct-tgt
    workspace_id: ws-tgt
    workspace: Target WS
`;

const OLD_FABRIC_TARGET_SECOND = `
project_id: proj-old-2
source:
  type: pbix
  pbix_path: C:/data/Sales.pbix
targets:
  - type: snowflake
    database: DB
    schema: SCH
  - type: fabric
    identity_id: acct-tgt-2
    workspace_id: ws-tgt-2
    workspace: Reports WS
`;

test('old Fabric -> Fabric project: target fields restored into the new target state', () => {
  const initial = parseConfigYamlToInitialData(OLD_FABRIC_TO_FABRIC, { source: { type: 'fabric' } });
  const state = hydrateFabricEditState(initial);
  assert.deepEqual(state.target, { targetFabricAccountId: 'acct-tgt', targetFabricWorkspaceId: 'ws-tgt' });
  assert.deepEqual(state.source, { fabricAccountId: 'acct-src', selectedConnectionId: 'acct-src', fabricWorkspaceId: 'ws-src' });
});

test('old project with the Fabric target after a Snowflake target still shows it filled', () => {
  const initial = parseConfigYamlToInitialData(OLD_FABRIC_TARGET_SECOND, { source: { type: 'pbix' } });
  assert.equal(initial.target_type, 'snowflake'); // first target unchanged
  assert.equal(initial.target_database, 'DB');
  const state = hydrateFabricEditState(initial);
  assert.deepEqual(state.target, { targetFabricAccountId: 'acct-tgt-2', targetFabricWorkspaceId: 'ws-tgt-2' });
});
