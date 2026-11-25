import test from 'node:test';
import assert from 'node:assert/strict';
import { applyServerDemoStatus, isDemoDataEnabled, setDemoDataEnabled } from './demoMode.js';

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

test('live is the default: off until the server allows it AND the user turns it on', () => {
  applyServerDemoStatus({ demo_available: false });
  setDemoDataEnabled(true);
  assert.equal(isDemoDataEnabled(), false); // server does not allow demo data

  applyServerDemoStatus({ demo_available: true });
  setDemoDataEnabled(false);
  assert.equal(isDemoDataEnabled(), false); // allowed, but user has not opted in

  setDemoDataEnabled(true);
  assert.equal(isDemoDataEnabled(), true);
});

test('an unreachable status endpoint means live only', () => {
  setDemoDataEnabled(true);
  applyServerDemoStatus(null);
  assert.equal(isDemoDataEnabled(), false);
});
