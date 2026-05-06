import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * usePageCache — auto-restoring session state cache for a single page.
 *
 * Stores UI state in sessionStorage under `key` so it survives SPA navigation
 * within the same browser tab. Cleared automatically when the tab closes.
 *
 * Unlike useSessionDraft, this hook:
 * - Immediately restores the cached state on mount (no explicit resumeDraft call).
 * - Returns a plain [state, setState] tuple — drop-in for useState.
 * - Debounces writes to avoid thrashing sessionStorage on rapid updates.
 *
 * @param {string}  key          Unique sessionStorage key (use kebab-case route slug)
 * @param {object}  defaultState Initial / fallback state object
 * @param {object}  [opts]
 * @param {number}  [opts.debounceMs=250]  Debounce delay for writes (ms)
 * @returns {[object, (partial: object|function) => void, () => void]}
 *          [state, setState, clearCache]
 *
 * @example
 * const [cache, setCache, clearJobsCache] = usePageCache('jobs-page', {
 *   search: '',
 *   statusFilter: 'all',
 *   expandedRunId: null,
 * });
 * // Read:  cache.search
 * // Write: setCache({ search: 'new value' })       // partial merge
 *          setCache(prev => ({ ...prev, x: 1 }))   // functional update
 */
export default function usePageCache(key, defaultState, { debounceMs = 250 } = {}) {
  // Hydrate from sessionStorage on first render.
  const [state, _setState] = useState(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const parsed = JSON.parse(raw);
        // Only merge if BOTH are non-null objects.
        if (
          typeof defaultState === 'object' && defaultState !== null &&
          typeof parsed === 'object' && parsed !== null &&
          !Array.isArray(defaultState)
        ) {
          return { ...defaultState, ...parsed };
        }
        return parsed;
      }
    } catch {
      // Corrupt storage entry — fall back to defaults.
    }
    return defaultState;
  });

  // Debounce timer ref.
  const timerRef = useRef(null);

  // Write to sessionStorage (debounced).
  const persist = useCallback(
    (nextState) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        try {
          sessionStorage.setItem(key, JSON.stringify(nextState));
        } catch (e) {
          console.warn('[usePageCache] Failed to persist state:', e);
        }
      }, debounceMs);
    },
    [key, debounceMs],
  );

  // Cleanup timer on unmount.
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  /**
   * setState — accepts a partial object or a functional updater.
   * If state is an object, it merges. Otherwise, it replaces.
   */
  const setState = useCallback(
    (updater) => {
      _setState((prev) => {
        let next;
        if (typeof updater === 'function') {
          next = updater(prev);
        } else if (
          typeof prev === 'object' && prev !== null &&
          typeof updater === 'object' && updater !== null &&
          !Array.isArray(prev)
        ) {
          next = { ...prev, ...updater };
        } else {
          next = updater;
        }
        persist(next);
        return next;
      });
    },
    [persist],
  );

  /** clearCache — remove the sessionStorage entry and reset to defaults. */
  const clearCache = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    try {
      sessionStorage.removeItem(key);
    } catch { /* noop */ }
    _setState(defaultState);
  }, [key, defaultState]); // eslint-disable-line react-hooks/exhaustive-deps

  return [state, setState, clearCache];
}
