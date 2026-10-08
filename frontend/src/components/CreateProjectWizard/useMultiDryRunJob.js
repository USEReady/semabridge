/**
 * useMultiDryRunJob — owns one multi-model background dry-run job: polls its
 * per-file status, loads each model's result as soon as that model's status
 * flips to success (whether or not its section is expanded), keeps each
 * model's rows (and the user's edits to them) keyed by file_id, and re-runs a
 * single failed model on request.
 *
 * Lives in CreateProjectPage (not inside the step-4 component) so the loaded
 * results and per-model edits survive moving between wizard steps and can be
 * written into the saved project config.
 *
 * Polling stops once every file is terminal (success/failed); a rerun
 * restarts it. Network errors while polling back off and give up after
 * MAX_POLL_ERRORS consecutive failures, surfacing `pollError`.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../utils/api';
import { normalizeRows } from '../../utils/normalizeRows';
import {
  buildMultiModelSections,
  fileAttemptKey,
  isJobTerminal,
  normalizeFileStatus,
} from '../../utils/multiFileDryRunReadiness';

const POLL_INTERVAL_MS = 3000;
const MAX_POLL_ERRORS = 10;
const RESULT_RETRY_MS = 2000;
const MAX_RESULT_RETRIES = 5;

export function useMultiDryRunJob({ projectId, job }) {
  const jobId = job?.job_id || null;
  const [jobStatus, setJobStatus] = useState(() => (job?.job_id ? job : null));
  const [fileStates, setFileStates] = useState({}); // file_id -> { attempt, rows, result, loadError }
  const [pollError, setPollError] = useState('');
  const [pollNonce, setPollNonce] = useState(0);
  const [rerunningIds, setRerunningIds] = useState(() => new Set());
  const [rerunErrors, setRerunErrors] = useState({}); // file_id -> message
  const [trackedJobId, setTrackedJobId] = useState(jobId);
  const requestedRef = useRef(new Set()); // attempt keys whose result fetch was started
  const activeJobRef = useRef(jobId);

  // A new job (or no job) replaces everything from the previous one -- reset
  // during render (React's "reset state when a prop changes" pattern).
  if (trackedJobId !== jobId) {
    setTrackedJobId(jobId);
    setJobStatus(job?.job_id ? job : null);
    setFileStates({});
    setPollError('');
    setRerunningIds(new Set());
    setRerunErrors({});
  }

  useEffect(() => {
    if (activeJobRef.current === jobId) return;
    activeJobRef.current = jobId;
    requestedRef.current = new Set();
  }, [jobId]);

  // ── Status polling ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!projectId || !jobId) return undefined;
    let cancelled = false;
    let timer = null;
    let consecutiveErrors = 0;

    const tick = async () => {
      try {
        const data = await api.getDryRunJobStatus(projectId, jobId);
        if (cancelled) return;
        consecutiveErrors = 0;
        setPollError('');
        setJobStatus(data);
        if (!isJobTerminal(data)) timer = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (err) {
        if (cancelled) return;
        consecutiveErrors += 1;
        if (consecutiveErrors >= MAX_POLL_ERRORS) {
          setPollError(err?.message || 'Lost contact with the dry-run job.');
          return;
        }
        timer = setTimeout(tick, POLL_INTERVAL_MS * Math.min(consecutiveErrors, 3));
      }
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId, jobId, pollNonce]);

  // ── Per-file result loading ───────────────────────────────────────────────
  const loadFileResult = useCallback((file) => {
    const fileId = file.file_id;
    const attempt = fileAttemptKey(file);
    const forJob = activeJobRef.current;
    const attemptLoad = (tries) => {
      api.getDryRunJobFile(projectId, forJob, fileId)
        .then((detail) => {
          if (activeJobRef.current !== forJob) return;
          const result = detail?.result || null;
          if (!result) {
            if (normalizeFileStatus(detail?.status) === 'failed') {
              setFileStates((prev) => ({ ...prev, [fileId]: { attempt, loadError: detail?.error || 'This model failed.' } }));
              return;
            }
            // Status said success but the result row isn't readable yet -- retry briefly.
            if (tries + 1 < MAX_RESULT_RETRIES) {
              setTimeout(() => attemptLoad(tries + 1), RESULT_RETRY_MS);
            } else {
              setFileStates((prev) => ({ ...prev, [fileId]: { attempt, loadError: 'The result for this model is not available yet. Retry to run it again.' } }));
            }
            return;
          }
          setFileStates((prev) => ({
            ...prev,
            [fileId]: { attempt, result, rows: normalizeRows(result), loadError: '' },
          }));
        })
        .catch((err) => {
          if (activeJobRef.current !== forJob) return;
          setFileStates((prev) => ({ ...prev, [fileId]: { attempt, loadError: err?.message || 'Failed to load results.' } }));
        });
    };
    attemptLoad(0);
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !jobId || !jobStatus) return;
    (jobStatus.files || []).forEach((file) => {
      if (normalizeFileStatus(file?.status) !== 'success') return;
      const attempt = fileAttemptKey(file);
      if (requestedRef.current.has(attempt)) return;
      requestedRef.current.add(attempt);
      loadFileResult(file);
    });
  }, [projectId, jobId, jobStatus, loadFileResult]);

  // ── Actions ───────────────────────────────────────────────────────────────
  const updateFileRows = useCallback((fileId, updater) => {
    setFileStates((prev) => {
      const current = prev[fileId];
      if (!current || !Array.isArray(current.rows)) return prev;
      const nextRows = typeof updater === 'function' ? updater(current.rows) : updater;
      if (nextRows === current.rows) return prev;
      return { ...prev, [fileId]: { ...current, rows: nextRows } };
    });
  }, []);

  const rerunFile = useCallback(async (fileId) => {
    if (!projectId || !jobId || !fileId) return;
    setRerunningIds((prev) => new Set(prev).add(fileId));
    setRerunErrors((prev) => {
      if (!(fileId in prev)) return prev;
      const next = { ...prev };
      delete next[fileId];
      return next;
    });
    try {
      await api.rerunDryRunJobFile(projectId, jobId, fileId);
      // Forget the previous attempt's result/edits and let polling report the new run.
      [...requestedRef.current].forEach((key) => {
        if (key.startsWith(`${fileId}@`)) requestedRef.current.delete(key);
      });
      setFileStates((prev) => {
        const next = { ...prev };
        delete next[fileId];
        return next;
      });
      setJobStatus((prev) => (prev ? {
        ...prev,
        status: 'running',
        files: (prev.files || []).map((f) => (
          f.file_id === fileId ? { ...f, status: 'pending', error_message: '', error_summary: '', completed_at: null } : f
        )),
      } : prev));
      setPollNonce((n) => n + 1);
    } catch (err) {
      const message = err?.status === 409 || /\b409\b|already running|in progress/i.test(String(err?.message || ''))
        ? 'This model is still running -- wait for it to finish before retrying.'
        : (err?.message || 'Retry failed.');
      setRerunErrors((prev) => ({ ...prev, [fileId]: message }));
    } finally {
      setRerunningIds((prev) => {
        const next = new Set(prev);
        next.delete(fileId);
        return next;
      });
    }
  }, [projectId, jobId]);

  const retryPolling = useCallback(() => {
    setPollError('');
    setPollNonce((n) => n + 1);
  }, []);

  const sections = useMemo(
    () => (jobId ? buildMultiModelSections(jobStatus, fileStates) : []),
    [jobId, jobStatus, fileStates],
  );

  return {
    jobId,
    jobStatus,
    sections,
    pollError,
    rerunningIds,
    rerunErrors,
    updateFileRows,
    rerunFile,
    retryPolling,
  };
}
