import { useState, useEffect, useCallback } from 'react';
import { authFetch } from '../utils/api';

/**
 * Custom hook to query platform adapter drift capabilities.
 * Determines supported object kinds, categories, and whether mock mode is permitted.
 */
export function useDriftCapabilities(projectId = '') {
  const [capabilities, setCapabilities] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchCapabilities = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      const res = await authFetch(`/api/drift/capabilities${qs}`);
      if (res.ok) {
        const data = await res.json();
        setCapabilities(data);
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || 'Failed to load drift capabilities');
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchCapabilities();
  }, [fetchCapabilities]);

  const allowMock = capabilities ? Boolean(capabilities.allow_mock) : true;
  const supportedCategories = capabilities?.supported_categories || ['ADDITION', 'DELETION', 'EXPRESSION_DRIFT', 'DESCRIPTION_DRIFT'];
  const supportedKinds = capabilities?.supported_kinds || ['METRIC', 'DIMENSION', 'FACT', 'RELATIONSHIP'];

  return {
    capabilities,
    loading,
    error,
    refetch: fetchCapabilities,
    allowMock,
    supportedCategories,
    supportedKinds,
  };
}
