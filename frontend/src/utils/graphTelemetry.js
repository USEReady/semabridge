/**
 * graphTelemetry — lightweight event emission for Explore graph loading.
 * 
 * Tracks:
 * - snapshot matching (strong vs weak)
 * - graph payload ownership validation
 * - fallback paths taken
 * - mismatch detection
 * 
 * Events are emitted but telemetry backend is pluggable (console, analytics, etc).
 */

const TELEMETRY_EVENTS = {
  GRAPH_LOAD_SUCCESS: 'graph_load_success',
  GRAPH_LOAD_MISMATCH: 'graph_load_mismatch',
  GRAPH_LOAD_EMPTY: 'graph_load_empty',
  SNAPSHOT_MATCH_STRONG: 'snapshot_match_strong',
  SNAPSHOT_MATCH_WEAK: 'snapshot_match_weak',
  SNAPSHOT_NO_MATCH: 'snapshot_no_match',
  FALLBACK_TO_GLOBAL: 'fallback_to_global',
};

let telemetryBackend = null;
let counters = {};

/**
 * Initialize telemetry with a custom backend.
 * Backend should implement: emit(eventName, data)
 */
export function initTelemetry(backend) {
  telemetryBackend = backend;
  counters = Object.values(TELEMETRY_EVENTS).reduce((acc, key) => {
    acc[key] = 0;
    return acc;
  }, {});
  console.debug('[Telemetry] Initialized with backend:', backend?.name || 'custom');
}

/**
 * Emit a telemetry event.
 */
export function emitTelemetryEvent(eventName, data = {}) {
  if (!Object.values(TELEMETRY_EVENTS).includes(eventName)) {
    console.warn(`[Telemetry] Unknown event: ${eventName}`);
    return;
  }

  counters[eventName] = (counters[eventName] || 0) + 1;

  const event = {
    name: eventName,
    timestamp: new Date().toISOString(),
    ...data,
  };

  if (telemetryBackend?.emit) {
    try {
      telemetryBackend.emit(eventName, event);
    } catch (err) {
      console.error('[Telemetry] Backend error:', err);
    }
  }

  // Always log to console for debugging
  console.debug(`[Telemetry] ${eventName}:`, event);
}

/**
 * Get current counter state.
 */
export function getTelemetryCounters() {
  return { ...counters };
}

/**
 * Reset counters (useful for testing).
 */
export function resetTelemetryCounters() {
  Object.keys(counters).forEach((key) => {
    counters[key] = 0;
  });
}

/**
 * Default console backend for development.
 */
export const consoleBackend = {
  name: 'console',
  emit: (eventName, event) => {
    console.log(`📊 [${eventName}]`, event);
  },
};

// Initialize with console backend by default
initTelemetry(consoleBackend);

export { TELEMETRY_EVENTS };
