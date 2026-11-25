/**
 * Demo / mock data switch. Live data is ALWAYS the default.
 *
 * Demo data is possible only when the server allows it
 * (SEMABRIDGE_DEMO_MODE=true, reported by /api/system/demo-status) AND the
 * user turned the switch on in this browser. Screens call
 * isDemoDataEnabled() when building requests (mock_mode, include_demo).
 */
import { useEffect, useState } from 'react';

const STORAGE_KEY = 'semabridge_demo_data';
let serverAllowsDemo = false;
const listeners = new Set();

function readPreference() {
  try {
    return globalThis.localStorage?.getItem(STORAGE_KEY) === 'on';
  } catch {
    return false;
  }
}

export function isDemoDataEnabled() {
  return serverAllowsDemo && readPreference();
}

export function setDemoDataEnabled(on) {
  try {
    if (on) globalThis.localStorage?.setItem(STORAGE_KEY, 'on');
    else globalThis.localStorage?.removeItem(STORAGE_KEY);
  } catch {
    /* storage blocked: stays off */
  }
  listeners.forEach((fn) => fn());
}

export function applyServerDemoStatus(status) {
  serverAllowsDemo = Boolean(status && status.demo_available);
  listeners.forEach((fn) => fn());
}

/** { available, enabled, setEnabled } for the toggle UI. */
export function useDemoMode(fetchStatus) {
  const [, force] = useState(0);
  useEffect(() => {
    const rerender = () => force((n) => n + 1);
    listeners.add(rerender);
    let alive = true;
    if (fetchStatus) {
      fetchStatus()
        .then((status) => { if (alive) applyServerDemoStatus(status); })
        .catch(() => { if (alive) applyServerDemoStatus(null); });
    }
    return () => { alive = false; listeners.delete(rerender); };
  }, [fetchStatus]);
  return { available: serverAllowsDemo, enabled: isDemoDataEnabled(), setEnabled: setDemoDataEnabled };
}
