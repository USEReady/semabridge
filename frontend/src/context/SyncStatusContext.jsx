import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../utils/api';
import { normalizeStatus, deriveProgressFromRun } from '../utils/syncUtils';

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

// normalizeStatus and deriveProgressFromRun moved to src/utils/syncUtils.js

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
        const sortedRuns = [...runList].sort((a, b) => getRunSortTimestamp(b) - getRunSortTimestamp(a));
        setRuns(runList);

        const nextStatusById = {};
        const nextProgressById = {};

        // Keep the latest run per project (sortedRuns is newest-first).
        for (const run of sortedRuns) {
          const pid = String(run?.project_id || '');
          if (!pid) continue;
          if (!(pid in nextStatusById)) {
            nextStatusById[pid] = normalizeStatus(run?.status);
          }
          if (!(pid in nextProgressById)) {
            nextProgressById[pid] = deriveProgressFromRun(run);
          }
        }

        setProjectStatusById((prev) => {
          const merged = { ...prev, ...nextStatusById };
          return shallowEqualObject(prev, merged) ? prev : merged;
        });
        setProjectProgressById((prev) => {
          const merged = { ...prev, ...nextProgressById };
          return shallowEqualObject(prev, merged) ? prev : merged;
        });

        // Fallback: if any run is success/completed, force progress to 100%
        const completedRun = sortedRuns.find((r) => normalizeStatus(r?.status) === 'success');
        if (completedRun) {
          setCurrentProgress(100);
          setCurrentSyncStatus('success');
        }

        // Only consider a run as active if it started less than 1 hour ago
        const now = Date.now();
        const activeRun = sortedRuns.find((r) => {
          if (normalizeStatus(r?.status) !== 'running') return false;
          const started = r?.started_at ? new Date(r.started_at).getTime() : 0;
          // If no started_at, treat as active but only if it looks fresh by alternate timestamps.
          if (!started) {
            const fallbackTs = getRunSortTimestamp(r);
            return fallbackTs > 0 && (Date.now() - fallbackTs) < 3600000;
          }
          // 1 hour = 3600000 ms
          return (now - started) < 3600000;
        }) || null;
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

        try {
          if (pid) {
            const project = await api.getProject(pid, { noCache: true });
            if (!disposed && project?.id) {
              setProjectStatusById((prev) => {
                const normalizedProjectStatus = normalizeStatus(project.status);
                if (prev[project.id] === normalizedProjectStatus) return prev;
                return {
                  ...prev,
                  [project.id]: normalizedProjectStatus,
                };
              });
            }
          }
        } catch {}

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

  // Fake crawl logic removed to allow real completion metrics from backend polling.

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
