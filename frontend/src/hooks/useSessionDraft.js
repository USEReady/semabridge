import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * useSessionDraft — reusable hook for intent-aware draft persistence.
 *
 * Stores form data in sessionStorage under `key`.
 * Does NOT auto-load the draft into state — the consumer must call
 * `resumeDraft()` explicitly (e.g. from a banner button).
 *
 * Sets are serialized as arrays and reconstructed on resume.
 *
 * @param {string} key  sessionStorage key
 * @param {object} opts
 * @param {number} opts.debounceMs  debounce interval for saveDraft (default 300)
 * @returns {{ hasDraft, draft, saveDraft, resumeDraft, discardDraft, clearDraft }}
 */
export default function useSessionDraft(key, { debounceMs = 300 } = {}) {
  // Check once on mount whether a draft exists.
  const [hasDraft, setHasDraft] = useState(() => {
    try {
      return sessionStorage.getItem(key) !== null;
    } catch {
      return false;
    }
  });

  // `draft` is null until the user explicitly calls resumeDraft().
  const [draft, setDraft] = useState(null);

  // --- Save (debounced) ---------------------------------------------------
  const timerRef = useRef(null);

  const saveDraft = useCallback(
    (data) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        try {
          sessionStorage.setItem(key, JSON.stringify(data));
        } catch (e) {
          console.warn('[useSessionDraft] Failed to save draft:', e);
        }
      }, debounceMs);
    },
    [key, debounceMs],
  );

  // Cleanup debounce timer on unmount.
  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  // --- Resume -------------------------------------------------------------
  const resumeDraft = useCallback(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        setDraft(JSON.parse(raw));
      }
    } catch {
      setDraft(null);
    }
  }, [key]);

  // --- Discard / Clear ----------------------------------------------------
  const discardDraft = useCallback(() => {
    try {
      sessionStorage.removeItem(key);
    } catch { /* noop */ }
    setHasDraft(false);
    setDraft(null);
  }, [key]);

  // clearDraft is semantically the same but called after successful submit.
  const clearDraft = discardDraft;

  return { hasDraft, draft, saveDraft, resumeDraft, discardDraft, clearDraft };
}
