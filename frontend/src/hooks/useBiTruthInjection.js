import { useState, useCallback, useEffect } from 'react';

export function useBiTruthInjection(projectId) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [previewData, setPreviewData] = useState(null);
  const [publishData, setPublishData] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    if (!projectId) return;
    setHistoryLoading(true);
    try {
      const res = await fetch(`/api/bi-truth/inject/history?project_id=${encodeURIComponent(projectId)}`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.history || []);
      }
    } catch (err) {
      // Non-blocking history load
      console.warn('Failed to load injection history:', err);
    } finally {
      setHistoryLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const runPreview = useCallback(async ({ viewName } = {}) => {
    if (!projectId) return null;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        project_id: projectId,
        dry_run: 'true',
      });
      if (viewName) params.append('view_name', viewName);

      const res = await fetch(`/api/bi-truth/inject?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || data.reason || `Preview failed with status ${res.status}`);
      }
      setPreviewData(data);
      return data;
    } catch (err) {
      setError(err.message || 'Preview failed');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const runPublish = useCallback(async ({ viewName } = {}) => {
    if (!projectId) return null;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        project_id: projectId,
        dry_run: 'false',
      });
      if (viewName) params.append('view_name', viewName);

      const res = await fetch(`/api/bi-truth/inject?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || data.reason || `Publish failed with status ${res.status}`);
      }
      setPublishData(data);
      await loadHistory();
      return data;
    } catch (err) {
      setError(err.message || 'Publish failed');
      throw err;
    } finally {
      setLoading(false);
    }
  }, [projectId, loadHistory]);

  const clearDialogState = useCallback(() => {
    setPreviewData(null);
    setPublishData(null);
    setError(null);
    setLoading(false);
  }, []);

  return {
    loading,
    error,
    previewData,
    publishData,
    history,
    historyLoading,
    runPreview,
    runPublish,
    loadHistory,
    clearDialogState,
  };
}
