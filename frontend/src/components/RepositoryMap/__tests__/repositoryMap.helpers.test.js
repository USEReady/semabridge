import test from 'node:test';
import assert from 'node:assert/strict';

import {
  filterSnapshotsByProjectCandidates,
  graphMatchesProjectCandidates,
  resolveProjectCandidates,
} from '../graphScopeHelpers.js';
import { resetTelemetryCounters, getTelemetryCounters, TELEMETRY_EVENTS } from '../../../utils/graphTelemetry.js';

test.beforeEach(() => {
  resetTelemetryCounters();
});

test('filterSnapshotsByProjectCandidates prefers strong matches', () => {
  const rows = [
    { project_id: 'proj-123', model_id: 'proj-123', snapshot_id: 's1' },
    { model_name: 'client', snapshot_id: 's2' },
  ];
  const result = filterSnapshotsByProjectCandidates(rows, ['proj-123']);
  assert.equal(Array.isArray(result), true);
  assert.equal(result.length, 1);
  assert.equal(result[0].snapshot_id, 's1');

  const counters = getTelemetryCounters();
  assert.ok(counters[TELEMETRY_EVENTS.SNAPSHOT_MATCH_STRONG] >= 1);
});

test('resolveProjectCandidates keeps the exact id ahead of weak aliases', () => {
  const result = resolveProjectCandidates('sale', ['client', 'sale', 'proj-sale']);
  assert.deepEqual(result.slice(0, 2), ['sale', 'proj-sale']);
  assert.ok(!result.includes('client'));
});

test('graphMatchesProjectCandidates rejects weak fallback payloads', () => {
  const weakPayload = {
    nodes: [
      { id: 'model-client', data: { nodeType: 'model', model_name: 'client' } },
    ],
  };
  const strongPayload = {
    nodes: [
      { id: 'model-proj-sale', data: { nodeType: 'model', model_id: 'proj-sale' } },
    ],
  };

  assert.equal(graphMatchesProjectCandidates(weakPayload, ['sale'], { requireStrong: true }), false);
  assert.equal(graphMatchesProjectCandidates(strongPayload, ['sale'], { requireStrong: true }), true);

  const counters = getTelemetryCounters();
  assert.ok(counters[TELEMETRY_EVENTS.GRAPH_LOAD_MISMATCH] >= 1);
});
