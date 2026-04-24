import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * useSessionDraft — reusable hook for intent-aware draft persistence.
 *
 * Stores form data in localStorage under `key` so drafts survive across tabs
 * within the same localhost origin.
 * Sets are serialized as arrays and reconstructed on resume.
 *
 * @param {string} key  localStorage key
 * @param {object} opts
 * @param {number} opts.debounceMs  debounce interval for saveDraft (default 300)
 * @returns {{ hasDraft, draft, saveDraft, resumeDraft, discardDraft, clearDraft }}
 */
export default function useSessionDraft(key, { debounceMs = 300 } = {}) {
  // Check once on mount whether a draft exists.
  const [hasDraft, setHasDraft] = useState(() => {
    try {
      return localStorage.getItem(key) !== null;
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
          localStorage.setItem(key, JSON.stringify(data));
          setHasDraft(true);
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
      const raw = localStorage.getItem(key);
      if (raw) {
        setHasDraft(true);
        setDraft(JSON.parse(raw));
      }
    } catch {
      setDraft(null);
    }
  }, [key]);

  // --- Discard / Clear ----------------------------------------------------
  const discardDraft = useCallback(() => {
    try {
      localStorage.removeItem(key);
    } catch { /* noop */ }
    setHasDraft(false);
    setDraft(null);
  }, [key]);

  // Keep hook state in sync when another tab updates or clears this draft key.
  useEffect(() => {
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
  }, [key]);

  // clearDraft is semantically the same but called after successful submit.
  const clearDraft = discardDraft;

  return { hasDraft, draft, saveDraft, resumeDraft, discardDraft, clearDraft };
}
