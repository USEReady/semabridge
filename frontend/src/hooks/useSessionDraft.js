import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * useSessionDraft — reusable hook for intent-aware draft persistence.
 *
 * Stores form data in browser storage under `key`.
 * Defaults to sessionStorage so wizard drafts are tab-scoped and cleared
 * automatically when the tab closes.
 * Sets are serialized as arrays and reconstructed on resume.
 *
 * @param {string} key  localStorage key
 * @param {object} opts
 * @param {number} opts.debounceMs  debounce interval for saveDraft (default 300)
 * @param {'session'|'local'} opts.storage  storage backend (default 'session')
 * @returns {{ hasDraft, draft, saveDraft, resumeDraft, discardDraft, clearDraft }}
 */
export default function useSessionDraft(key, { debounceMs = 300, storage = 'session' } = {}) {
  const getStorage = useCallback(() => {
    if (typeof window === 'undefined') return null;
    return storage === 'local' ? window.localStorage : window.sessionStorage;
  }, [storage]);

  // Check once on mount whether a draft exists.
  const [hasDraft, setHasDraft] = useState(() => {
    try {
      const backend = storage === 'local' ? window.localStorage : window.sessionStorage;
      return backend.getItem(key) !== null;
    } catch {
      return false;
    }
  });

  // Keep the active draft empty until the user explicitly resumes it.
  const [draft, setDraft] = useState(null);

  // --- Save (debounced) ---------------------------------------------------
  const timerRef = useRef(null);

  const saveDraft = useCallback(
    (data) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        try {
          const backend = getStorage();
          if (!backend) return;
          backend.setItem(key, JSON.stringify(data));
          setHasDraft(true);
        } catch (e) {
          console.warn('[useSessionDraft] Failed to save draft:', e);
        }
      }, debounceMs);
    },
    [key, debounceMs, getStorage],
  );

  // Cleanup debounce timer on unmount.
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  // --- Resume -------------------------------------------------------------
  const resumeDraft = useCallback(() => {
    try {
      const backend = getStorage();
      if (!backend) return;
      const raw = backend.getItem(key);
      if (raw) {
        setHasDraft(true);
        setDraft(JSON.parse(raw));
      }
    } catch {
      setDraft(null);
    }
  }, [key, getStorage]);

  // --- Discard / Clear ----------------------------------------------------
  const discardDraft = useCallback(() => {
    try {
      const backend = getStorage();
      if (backend) backend.removeItem(key);
    } catch { /* noop */ }
    setHasDraft(false);
    setDraft(null);
  }, [key, getStorage]);

  // Keep hook state in sync when another tab updates/clears localStorage drafts.
  // sessionStorage is per-tab and does not emit cross-tab updates for this key.
  useEffect(() => {
    if (storage !== 'local') return undefined;
    const onStorage = (event) => {
      if (event.key !== key) return;
      if (event.newValue == null) {
        setHasDraft(false);
        setDraft(null);
        return;
      }
      try {
        setHasDraft(true);
      } catch {
        setDraft(null);
        setHasDraft(false);
      }
    };

    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [key, storage]);

  // clearDraft is semantically the same but called after successful submit.
  const clearDraft = discardDraft;

  return { hasDraft, draft, saveDraft, resumeDraft, discardDraft, clearDraft };
}
