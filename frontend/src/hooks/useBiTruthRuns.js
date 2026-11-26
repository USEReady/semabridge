import { useState, useCallback, useEffect } from 'react';
import { authFetch } from '../utils/api';

export function useBiTruthRuns(projectId) {
  const [runs, setRuns] = useState([]);
  const [gateVerdict, setGateVerdict] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const fetchRuns = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      const [rRes, gRes] = await Promise.all([
        authFetch(`/api/bi-truth/runs${qs}`),
        projectId ? authFetch(`/api/bi-truth/gate/${encodeURIComponent(projectId)}`) : Promise.resolve(null),
      ]);
      if (rRes.ok) {
        const data = await rRes.json();
        setRuns(data.runs || []);
      }
      if (gRes && gRes.ok) {
        const gData = await gRes.json();
        setGateVerdict(gData);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  const runSuite = useCallback(async (view) => {
    setLoading(true);
    setError('');
    try {
      const res = await authFetch('/api/bi-truth/run', {
        method: 'POST',
        timeoutMs: 180000,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          view: view || undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || `Run failed (${res.status})`);
      }
      await fetchRuns();
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId, fetchRuns]);

  return {
    runs,
    latestRun: runs[0] || null,
    gateVerdict,
    loading,
    error,
    setError,
    fetchRuns,
    runSuite,
  };
}
