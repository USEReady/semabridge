import test from 'node:test';
import assert from 'node:assert/strict';

import { getRunLogs } from '../src/utils/runLogs.js';

test('running runs append live log stages even when seed logs already exist', () => {
  const originalNow = Date.now;
  const now = new Date('2026-05-15T11:16:00.000Z').getTime();
  Date.now = () => now;

  try {
    const logs = getRunLogs({
      id: 'run-123',
      status: 'running',
      started_at: new Date(now - 17000).toISOString(),
      logs: [
        'LIVE 11:15:35 Run queued. Waiting for execution engine...',
        'INFO 11:15:35 Execution started.',
      ],
    });

    assert.ok(logs.length > 2, 'expected live tail to append additional log lines');
    assert.equal(logs[0], 'LIVE 11:15:35 Run queued. Waiting for execution engine...');
    assert.equal(logs[1], 'INFO 11:15:35 Execution started.');
    assert.ok(logs.some((line) => line.includes('Validating source format...')));
    assert.ok(logs.some((line) => line.includes('Parsing semantic view...')));
    assert.ok(logs.some((line) => line.includes('Generating TMDL payload...')));
  } finally {
    Date.now = originalNow;
  }
});