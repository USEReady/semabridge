import { useState, useCallback, useEffect } from 'react';
import { authFetch } from '../utils/api';

export function useBiTruthCases(projectId) {
  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const fetchCases = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      const res = await authFetch(`/api/bi-truth/cases${qs}`);
      if (res.ok) {
        const data = await res.json();
        setCases(data.cases || []);
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || 'Failed to load test cases.');
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchCases();
  }, [fetchCases]);

  const setStatus = useCallback(async (caseId, status) => {
    // Optimistic update: immediately update status so UI responds with zero lag
    setCases((prev) =>
      prev.map((c) => (c.id === caseId ? { ...c, status } : c))
    );
    try {
      const res = await authFetch(`/api/bi-truth/cases/${caseId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) {
        // Rollback on non-200 response
        fetchCases();
      }
    } catch (err) {
      setError(String(err));
      // Rollback on exception
      fetchCases();
    }
  }, [fetchCases]);

  const updateCritical = useCallback(async (caseId, isCritical, tolerance) => {
    try {
      const res = await authFetch(`/api/bi-truth/cases/${caseId}/critical`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_critical: isCritical, tolerance }),
      });
      if (res.ok) {
        setCases((prev) =>
          prev.map((c) =>
            c.id === caseId ? { ...c, is_critical: isCritical, tolerance } : c
          )
        );
      }
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const updateWindows = useCallback(async (caseId, windows) => {
    try {
      const res = await authFetch(`/api/bi-truth/cases/${caseId}/windows`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ windows }),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const updated = data?.case;
        setCases((prev) =>
          prev.map((c) =>
            c.id === caseId ? (updated || { ...c, windows }) : c
          )
        );
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || 'Failed to update windows.');
      }
    } catch (err) {
      setError(String(err));
    }
  }, []);

  const acceptBenchmark = useCallback(async (caseId, expectedValue, expectedRowCount) => {
    try {
      const res = await authFetch(`/api/bi-truth/cases/${caseId}/accept-benchmark`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          expected_value: expectedValue,
          new_expected_value: expectedValue,
          expected_row_count: expectedRowCount,
          new_row_count: expectedRowCount,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        let msg = errData.detail || `HTTP ${res.status}`;
        if (Array.isArray(errData.detail)) {
          msg = errData.detail.map((d) => `${(d.loc || []).slice(1).join('.') || 'field'}: ${d.msg}`).join(', ');
        }
        const formattedErr = `Accept benchmark failed: ${msg}`;
        setError(formattedErr);
        throw new Error(formattedErr);
      }
      await fetchCases();
      return { success: true };
    } catch (err) {
      setError(String(err.message || err));
      throw err;
    }
  }, [projectId, fetchCases]);

  const deleteCase = useCallback(async (caseId) => {
    try {
      const res = await authFetch(`/api/bi-truth/cases/${caseId}`, {
        method: 'DELETE',
      });
      if (res.ok) {
        setCases((prev) => prev.filter((c) => c.id !== caseId));
      }
    } catch (err) {
      setError(String(err));
    }
  }, []);

  return {
    cases,
    loading,
    error,
    setError,
    fetchCases,
    setStatus,
    updateCritical,
    updateWindows,
    acceptBenchmark,
    deleteCase,
  };
}
