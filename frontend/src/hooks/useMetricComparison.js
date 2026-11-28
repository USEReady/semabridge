import { useState, useCallback, useEffect } from 'react';
import { authFetch } from '../utils/api';

export function useMetricComparison(projectId) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [currentResult, setCurrentResult] = useState(null);
  const [error, setError] = useState(null);
  const [config, setConfig] = useState({
    tolerance_pct: 0.01,
    absolute_floor: 0.01,
    null_policy: 'STRICT_NULL',
  });

  const fetchHistory = useCallback(async (metricName = null) => {
    if (!projectId) return;
    setLoading(true);
    try {
      const q = new URLSearchParams({ project_id: projectId });
      if (metricName) q.append('metric_name', metricName);
      const res = await authFetch(`/api/bi-truth/metric-comparison/history?${q.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setHistory(data.runs || []);
      }
    } catch (err) {
      console.error('Failed to load metric comparison history', err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const fetchConfig = useCallback(async (metricName = null) => {
    if (!projectId) return;
    try {
      const q = new URLSearchParams({ project_id: projectId });
      if (metricName) q.append('metric_name', metricName);
      const res = await authFetch(`/api/bi-truth/metric-comparison/config?${q.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setConfig(data.effective || data.project_default || {
          tolerance_pct: 0.01,
          absolute_floor: 0.01,
          null_policy: 'STRICT_NULL',
        });
      }
    } catch (err) {
      console.error('Failed to load comparison config', err);
    }
  }, [projectId]);

  const saveConfig = useCallback(async (newCfg, metricName = null) => {
    if (!projectId) return;
    try {
      const res = await authFetch('/api/bi-truth/metric-comparison/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          metric_name: metricName,
          tolerance_pct: parseFloat(newCfg.tolerance_pct),
          absolute_floor: parseFloat(newCfg.absolute_floor),
          null_policy: newCfg.null_policy || 'STRICT_NULL',
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setConfig(data);
      }
    } catch (err) {
      console.error('Failed to update config', err);
    }
  }, [projectId]);

  const runComparison = useCallback(async (payload) => {
    setRunning(true);
    setError(null);
    try {
      const res = await authFetch('/api/bi-truth/metric-comparison/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          ...payload,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || 'Comparison execution failed');
      }
      setCurrentResult(data);
      // Prepend to history
      setHistory((prev) => [
        {
          id: data.run_id || String(Date.now()),
          project_id: projectId,
          metric_name: payload.metric_name,
          source_platform: data.source?.platform,
          target_platform: data.target?.platform,
          target_view: data.target?.view,
          source_query: data.source?.query,
          target_query: data.target?.query,
          source_value: data.source?.normalized_value,
          target_value: data.target?.normalized_value,
          variance_pct: data.variance_pct,
          absolute_difference: data.absolute_difference,
          tolerance_pct: data.tolerance_pct,
          verdict: data.verdict,
          reason: data.reason,
          execution_ms: (data.source?.execution_ms || 0) + (data.target?.execution_ms || 0),
          mock_mode: payload.mock_mode || false,
          executed_at: new Date().toISOString(),
        },
        ...prev,
      ]);
      return data;
    } catch (err) {
      setError(err?.message || String(err));
      throw err;
    } finally {
      setRunning(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (projectId) {
      fetchHistory();
      fetchConfig();
    }
  }, [projectId, fetchHistory, fetchConfig]);

  return {
    history,
    loading,
    running,
    currentResult,
    error,
    config,
    fetchHistory,
    fetchConfig,
    saveConfig,
    runComparison,
  };
}
