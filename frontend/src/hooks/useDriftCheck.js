import { useState, useCallback } from 'react';
import { authFetch } from '../utils/api';

/**
 * Custom hook to execute drift checks with force_refresh and manage progress states.
 */
export function useDriftCheck() {
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState(null);
  const [checkError, setCheckError] = useState(null);

  const runCheck = useCallback(async ({
    projectId,
    view,
    forceRefresh = true,
    webhookUrl,
  } = {}) => {
    setChecking(true);
    setCheckError(null);
    try {
      const payload = {
        project_id: projectId || undefined,
        view: view || undefined,
        force_refresh: forceRefresh,
        webhook_url: webhookUrl || undefined,
      };

      const res = await authFetch('/api/drift/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) {
        setCheckError(data.detail || 'Drift check failed');
        return { success: false, data };
      }

      setCheckResult(data);
      return { success: true, data };
    } catch (err) {
      const msg = String(err.message || err);
      setCheckError(msg);
      return { success: false, error: msg };
    } finally {
      setChecking(false);
    }
  }, []);

  return {
    checking,
    checkResult,
    checkError,
    runCheck,
  };
}
