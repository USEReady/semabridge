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

    const poll = async () => {
      try {
        const data = await api.listJobRuns();
        if (disposed) return;

        const runList = Array.isArray(data) ? data : [];
        setRuns(runList);

        const nextStatusById = {};
        const nextProgressById = {};

        for (const run of runList) {
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

        const activeRun = runList.find((r) => normalizeStatus(r?.status) === 'running') || null;
        if (!activeRun) {
          // Sync has completed or stopped
          setCurrentSyncStatus((prev) => (prev === 'draft' ? prev : 'draft'));
          
          // Clear any pending completion timeout
          if (completionTimeoutRef.current) {
            clearTimeout(completionTimeoutRef.current);
            completionTimeoutRef.current = null;
          }
          
          // Wait 2 seconds before clearing sync ID to let user see 100%
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

        // Clear any pending completion timeout if sync restarts
        if (completionTimeoutRef.current) {
          clearTimeout(completionTimeoutRef.current);
          completionTimeoutRef.current = null;
        }

        // TASK 1: Detect new sync start and reset progress to 0
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
        } catch {
          // Ignore transient project poll failures.
        }

        const stalledMs = Date.now() - lastRealChangeAtRef.current;
        if (stalledMs > 60000) {
          setIndeterminate((prev) => (prev ? prev : true));
          setWarning((prev) => (prev === 'Sync is taking longer than expected...' ? prev : 'Sync is taking longer than expected...'));
        }
      } catch {
        // Keep previous values on transient poll errors.
      }
    };

    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      disposed = true;
      clearInterval(timer);
      if (completionTimeoutRef.current) {
        clearTimeout(completionTimeoutRef.current);
      }
    };
  }, []);

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
