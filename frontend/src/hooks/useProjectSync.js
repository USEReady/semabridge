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
    let status = optimisticStatus || backendStatus;
    const isCurrent = currentSyncId && String(currentSyncId) === id;
    const progress = isCurrent ? currentProgress : Number(projectProgressById[id] || 0);

    // Fallback: if progress is 100 and backend status is success, force status to 'success'
    if (progress === 100 && backendStatus === 'success') {
      status = 'success';
    }

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
