import { useMemo, useState, useEffect } from 'react';
import { useSyncStatusStore } from '../context/SyncStatusContext';

export default function useProjectSync(projectId, initialStatus = 'draft') {
  const {
    currentSyncId,
    currentSyncStatus,
    currentProgress,
    indeterminate,
    warning,
    projectStatusById,
    projectProgressById,
  } = useSyncStatusStore();

  const [optimisticStatus, setOptimisticStatus] = useState('');

  useEffect(() => {
    setOptimisticStatus('');
  }, [projectId, initialStatus]);

  return useMemo(() => {
    if (!projectId) {
      return {
        status: currentSyncStatus,
        progress: currentProgress,
        indeterminate,
        warning,
        currentSyncId,
        setStatus: () => {},
      };
    }

    const id = String(projectId);
    const backendStatus = projectStatusById[id] || String(initialStatus || 'draft').toLowerCase();
    const status = optimisticStatus || backendStatus;
    const isCurrent = currentSyncId && String(currentSyncId) === id;
    const progress = isCurrent ? currentProgress : Number(projectProgressById[id] || 0);

    return {
      status,
      progress,
      indeterminate: Boolean(isCurrent && indeterminate),
      warning: isCurrent ? warning : '',
      currentSyncId,
      setStatus: (next) => setOptimisticStatus(String(next || '')),
    };
  }, [projectId, initialStatus, currentSyncId, currentSyncStatus, currentProgress, indeterminate, warning, projectStatusById, projectProgressById, optimisticStatus]);
}
