import { useEffect, useRef, useState } from 'react';
import { api } from '../utils/api';

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

  const total = Number(run?.total_items ?? run?.total_models ?? 0);
  const synced = Number(run?.completed_items ?? run?.models_synced ?? 0);
  if (total > 0) {
    const ratio = Math.max(0, Math.min(1, synced / total));
    return Math.round(ratio * 100);
  }
  return 0;
}

export default function useSyncStatus(projectId, initialStatus = 'draft') {
  const [status, setStatus] = useState(normalizeStatus(initialStatus));
  const [progress, setProgress] = useState(0);
  const [indeterminate, setIndeterminate] = useState(false);
  const [warning, setWarning] = useState('');

  const statusRef = useRef(normalizeStatus(initialStatus));
  const progressRef = useRef(0);
  const indeterminateRef = useRef(false);
  const warningRef = useRef('');
  const lastProgressRef = useRef(0);
  const lastProgressChangeAtRef = useRef(Date.now());

  const updateStatus = (nextStatus) => {
    if (statusRef.current === nextStatus) return;
    statusRef.current = nextStatus;
    setStatus(nextStatus);
  };

  const updateProgress = (nextProgress) => {
    if (progressRef.current === nextProgress) return;
    progressRef.current = nextProgress;
    setProgress(nextProgress);
  };

  const updateIndeterminate = (nextValue) => {
    if (indeterminateRef.current === nextValue) return;
    indeterminateRef.current = nextValue;
    setIndeterminate(nextValue);
  };

  const updateWarning = (nextWarning) => {
    if (warningRef.current === nextWarning) return;
    warningRef.current = nextWarning;
    setWarning(nextWarning);
  };

  useEffect(() => {
    const normalized = normalizeStatus(initialStatus);
    updateStatus(normalized);
    if (normalized !== 'running') {
      updateIndeterminate(false);
      updateWarning('');
    }
  }, [projectId, initialStatus]);

  useEffect(() => {
    if (!projectId || normalizeStatus(status) !== 'running') return undefined;

    let disposed = false;

    const poll = async () => {
      try {
        const [project, runs] = await Promise.all([
          api.getProject(projectId, { noCache: true }),
          api.getProjectRuns(projectId),
        ]);
        if (disposed) return;

        const nextStatus = normalizeStatus(project?.status);
        updateStatus(nextStatus);

        const runList = Array.isArray(runs) ? runs : [];
        const activeRun = runList.find((r) => String(r?.status || '').toLowerCase() === 'running') || runList[0] || null;
        const nextProgress = deriveProgressFromRun(activeRun);

        updateProgress(nextProgress);

        if (nextProgress !== lastProgressRef.current) {
          lastProgressRef.current = nextProgress;
          lastProgressChangeAtRef.current = Date.now();
          updateIndeterminate(false);
          updateWarning('');
        } else if (nextStatus === 'running') {
          const stalledMs = Date.now() - lastProgressChangeAtRef.current;
          if (stalledMs > 60000) {
            updateIndeterminate(true);
            updateWarning('Sync is taking longer than expected...');
          }
        }

        if (nextStatus !== 'running') {
          if (nextStatus === 'success') {
            updateProgress(100);
          }
          updateIndeterminate(false);
          updateWarning('');
        }
      } catch {
        // Keep showing current state during transient failures.
      }
    };

    poll();
    const timer = setInterval(poll, 3000);

    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [projectId, status]);

  return { status, setStatus, progress, indeterminate, warning };
}
