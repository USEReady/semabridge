import { useState, useCallback, useRef, useEffect } from 'react';

/**
 * useSessionDraft — reusable hook for intent-aware draft persistence.
 *
 * Stores form data in localStorage under `key` so drafts survive across tabs
 * within the same localhost origin.
 * Auto-loads any existing draft on mount.
 *
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

  // Auto-hydrate draft on mount so new tabs can recover in-progress forms.
  const [draft, setDraft] = useState(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  });

  // --- Save (debounced) ---------------------------------------------------
  const timerRef = useRef(null);

  const saveDraft = useCallback(
    (data) => {
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        try {
          localStorage.setItem(key, JSON.stringify(data));
          setHasDraft(true);
          setDraft(data);
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
        setDraft(JSON.parse(event.newValue));
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
