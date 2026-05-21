import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../utils/api';

const SyncStatusContext = createContext(null);

function shallowEqualObject(a, b) {
  if (a === b) return true;
  const aObj = a && typeof a === 'object' ? a : {};
  const bObj = b && typeof b === 'object' ? b : {};
  const aKeys = Object.keys(aObj);
  const bKeys = Object.keys(bObj);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    if (aObj[key] !== bObj[key]) return false;
  }
  return true;
}

function normalizeStatus(status) {
  const s = String(status || 'draft').toLowerCase();
  if (s === 'running' || s === 'success' || s === 'failed' || s === 'draft') return s;
  if (s === 'warning' || s === 'partial' || s === 'completed') return 'success';
  if (s === 'error' || s === 'cancelled') return 'failed';
  return 'draft';
}

function deriveProgressFromRun(run) {
  if (!run) return 0;
  const explicit = Number(run?.progress_pct);
  if (!Number.isNaN(explicit) && explicit >= 0) {
    return Math.max(0, Math.min(100, Math.round(explicit)));
  }
  const total = Number(run?.total_models || 0);
  const synced = Number(run?.models_synced || 0);
  if (total > 0) {
    return Math.round(Math.max(0, Math.min(1, synced / total)) * 100);
  }
  return 0;
}

function getRunSortTimestamp(run) {
  const started = run?.started_at ? Date.parse(run.started_at) : NaN;
  if (!Number.isNaN(started) && started > 0) return started;
  const updated = run?.updated_at ? Date.parse(run.updated_at) : NaN;
  if (!Number.isNaN(updated) && updated > 0) return updated;
  const created = run?.created_at ? Date.parse(run.created_at) : NaN;
  if (!Number.isNaN(created) && created > 0) return created;
  const numericId = Number(run?.id || run?.run_id || 0);
  if (!Number.isNaN(numericId) && numericId > 0) return numericId;
  return 0;
}

export function buildProjectRunSnapshot(runList, currentTime = Date.now()) {
  const sortedRuns = Array.isArray(runList)
    ? [...runList].sort((a, b) => getRunSortTimestamp(b) - getRunSortTimestamp(a))
    : [];

  const projectStatusById = {};
  const projectProgressById = {};

  for (const run of sortedRuns) {
    const pid = String(run?.project_id || '');
    if (!pid) continue;
    if (!(pid in projectStatusById)) {
      projectStatusById[pid] = normalizeStatus(run?.status);
    }
    if (!(pid in projectProgressById)) {
      projectProgressById[pid] = deriveProgressFromRun(run);
    }
  }

  const completedRun = sortedRuns.find((r) => normalizeStatus(r?.status) === 'success') || null;
  const activeRun = sortedRuns.find((r) => {
    if (normalizeStatus(r?.status) !== 'running') return false;
    const started = r?.started_at ? new Date(r.started_at).getTime() : 0;
    if (!started) {
      const fallbackTs = getRunSortTimestamp(r);
      return fallbackTs > 0 && (currentTime - fallbackTs) < 3600000;
    }
    return (currentTime - started) < 3600000;
  }) || null;

  return {
    sortedRuns,
    projectStatusById,
    projectProgressById,
    completedRun,
    activeRun,
  };
}

export function SyncStatusProvider({ children }) {
  const [runs, setRuns] = useState([]);
  const [projectStatusById, setProjectStatusById] = useState({});
  const [projectProgressById, setProjectProgressById] = useState({});
  const [currentSyncId, setCurrentSyncId] = useState('');
  const [currentSyncStatus, setCurrentSyncStatus] = useState('draft');
  const [currentProgress, setCurrentProgress] = useState(0);
  const [indeterminate, setIndeterminate] = useState(false);
  const [warning, setWarning] = useState('');

  const realProgressRef = useRef(0);
  const lastRealChangeAtRef = useRef(Date.now());
  const previousSyncIdRef = useRef('');
  const completionTimeoutRef = useRef(null);

  useEffect(() => {
    let disposed = false;
    let timeoutId;

    // Listen for fallback event from sync POST
    function handleFallback(e) {
      const { projectId, status, progress } = e.detail || {};
      const normalizedProjectId = String(projectId || '');
      const normalizedStatus = normalizeStatus(status);
      const normalizedProgress = Number(progress);

      if (normalizedProjectId) {
        setCurrentSyncId(normalizedProjectId);
      }

      if (normalizedStatus === 'running') {
        setCurrentSyncStatus('running');
        setCurrentProgress((prev) => (prev > 5 ? prev : 5));
        setIndeterminate(false);
        setWarning('');
        lastRealChangeAtRef.current = Date.now();
        return;
      }

      if (normalizedStatus === 'success' || normalizedProgress === 100) {
        setCurrentProgress(100);
        setCurrentSyncStatus('success');
        setIndeterminate(false);
        setWarning('');
        lastRealChangeAtRef.current = Date.now();
      } else if (normalizedStatus === 'failed') {
        setCurrentSyncStatus('failed');
        setIndeterminate(false);
        setWarning('');
        lastRealChangeAtRef.current = Date.now();
      }
    }
    window.addEventListener('semabridge-sync-fallback', handleFallback);

    const poll = async () => {
      if (disposed) return;
      try {
        const data = await api.listJobRuns();
        if (disposed) return;

        const runList = Array.isArray(data) ? data : [];
        const {
          sortedRuns,
          projectStatusById: nextStatusById,
          projectProgressById: nextProgressById,
          completedRun,
          activeRun,
        } = buildProjectRunSnapshot(runList, Date.now());
        setRuns(runList);

        setProjectStatusById((prev) => (
          shallowEqualObject(prev, nextStatusById) ? prev : nextStatusById
        ));
        setProjectProgressById((prev) => (
          shallowEqualObject(prev, nextProgressById) ? prev : nextProgressById
        ));

        // Fallback: if any run is success/completed, force progress to 100%
        if (completedRun) {
          setCurrentProgress(100);
          setCurrentSyncStatus('success');
        }

        if (!activeRun) {
          setCurrentSyncStatus((prev) => (prev === 'draft' ? prev : 'draft'));
          if (completionTimeoutRef.current) {
            clearTimeout(completionTimeoutRef.current);
            completionTimeoutRef.current = null;
          }
          if (currentSyncId) {
            completionTimeoutRef.current = setTimeout(() => {
              setCurrentSyncId((prev) => (prev === '' ? prev : ''));
              setCurrentProgress((prev) => (prev === 0 ? prev : 0));
              realProgressRef.current = 0;
              previousSyncIdRef.current = '';
              completionTimeoutRef.current = null;
            }, 2000);
          } else {
            setCurrentSyncId((prev) => (prev === '' ? prev : ''));
            setCurrentProgress((prev) => (prev === 0 ? prev : 0));
            realProgressRef.current = 0;
            previousSyncIdRef.current = '';
          }
          lastRealChangeAtRef.current = Date.now();
          setIndeterminate((prev) => (prev ? false : prev));
          setWarning((prev) => (prev ? '' : prev));
          return;
        }

        const pid = String(activeRun?.project_id || '');
        const realProgress = deriveProgressFromRun(activeRun);

        if (completionTimeoutRef.current) {
          clearTimeout(completionTimeoutRef.current);
          completionTimeoutRef.current = null;
        }

        if (pid !== previousSyncIdRef.current) {
          previousSyncIdRef.current = pid;
          setCurrentProgress((prev) => (prev === 0 ? prev : 0));
          realProgressRef.current = 0;
          lastRealChangeAtRef.current = Date.now();
        }

        setCurrentSyncId((prev) => (prev === pid ? prev : pid));
        setCurrentSyncStatus((prev) => (prev === 'running' ? prev : 'running'));

        if (realProgress !== realProgressRef.current) {
          realProgressRef.current = realProgress;
          lastRealChangeAtRef.current = Date.now();
          setCurrentProgress((prev) => {
            const next = Math.max(prev, realProgress);
            return next === prev ? prev : next;
          });
          setIndeterminate((prev) => (prev ? false : prev));
          setWarning((prev) => (prev ? '' : prev));
        }

        const stalledMs = Date.now() - lastRealChangeAtRef.current;
        if (stalledMs > 60000) {
          setIndeterminate((prev) => (prev ? prev : true));
          setWarning((prev) => (prev === 'Sync is taking longer than expected...' ? prev : 'Sync is taking longer than expected...'));
        }

        // Recursive polling: only schedule next poll after this one finishes
        if (!disposed && (activeRun || !completedRun)) {
          timeoutId = setTimeout(poll, 1000);
        }
      } catch {
        // Keep previous values on transient poll errors.
        if (!disposed) {
          timeoutId = setTimeout(poll, 2000);
        }
      }
    };

    poll();
    return () => {
      disposed = true;
      clearTimeout(timeoutId);
      if (completionTimeoutRef.current) {
        clearTimeout(completionTimeoutRef.current);
      }
      window.removeEventListener('semabridge-sync-fallback', handleFallback);
    };
  }, []);

  // Completion watcher: when progress hits 100, force status to 'success' after a short delay
  useEffect(() => {
    if (currentProgress === 100) {
      const timer = setTimeout(() => {
        setCurrentSyncStatus('success');
      }, 1000);
      return () => clearTimeout(timer);
    }
  }, [currentProgress]);

  useEffect(() => {
    if (currentSyncStatus !== 'running') return undefined;

    const crawl = setInterval(() => {
      setCurrentProgress(prev => {
        const floor = realProgressRef.current;
        if (prev < floor) return floor;
        if (prev >= 90) return prev;
        return prev + 1;
      });
    }, 1000);

    return () => clearInterval(crawl);
  }, [currentSyncStatus]);

  const value = useMemo(() => ({
    runs,
    currentSyncId,
    currentSyncStatus,
    currentProgress,
    indeterminate,
    warning,
    projectStatusById,
    projectProgressById,
  }), [runs, currentSyncId, currentSyncStatus, currentProgress, indeterminate, warning, projectStatusById, projectProgressById]);

  return <SyncStatusContext.Provider value={value}>{children}</SyncStatusContext.Provider>;
}

export function useSyncStatusStore() {
  const ctx = useContext(SyncStatusContext);
  if (!ctx) {
    throw new Error('useSyncStatusStore must be used within SyncStatusProvider');
  }
  return ctx;
}
