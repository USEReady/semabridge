import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { authFetch } from '../utils/api';
import { normalizeViewName, getViewBadgeLabel } from '../utils/viewUtils';

export { normalizeViewName, getViewBadgeLabel };

/**
 * Hook to manage deployed views for a project.
 * Fetches /api/projects/{projectId}/deployed-views, defaults to the latest deploy,
 * and synchronizes selection via the `?view=` URL query parameter.
 */
export function useDeployedViews(projectId) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [views, setViews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchViews = useCallback(async () => {
    if (!projectId) {
      setViews([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch(`/api/projects/${encodeURIComponent(projectId)}/deployed-views`);
      if (!res.ok) {
        throw new Error(`Failed to load deployed views (${res.status})`);
      }
      const data = await res.json();
      setViews(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Error fetching deployed views:', err);
      setError(String(err?.message || err));
      setViews([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchViews();
  }, [fetchViews]);

  const latestView = useMemo(() => {
    if (!views || views.length === 0) return null;
    return views.find((v) => v.is_latest) || views[0];
  }, [views]);

  const urlView = searchParams.get('view') || '';

  // Determine selected view: URL query param -> latest view fallback
  const selectedView = useMemo(() => {
    if (urlView && views.length > 0) {
      // Check if URL view matches any known view in the list
      const matched = views.find(
        (v) =>
          normalizeViewName(v.semantic_view) === normalizeViewName(urlView) ||
          normalizeViewName(v.view) === normalizeViewName(urlView)
      );
      if (matched) return matched.semantic_view || matched.view;
      return latestView?.semantic_view || latestView?.view || '';
    }
    if (urlView && views.length === 0 && loading) {
      return urlView;
    }
    return latestView?.semantic_view || latestView?.view || '';
  }, [urlView, views, latestView, loading]);

  // Persist default (latest) view to URL if ?view is omitted or belongs to another project
  useEffect(() => {
    if (!loading && views.length > 0) {
      const current = searchParams.get('view');
      const matched = current && views.find(
        (v) =>
          normalizeViewName(v.semantic_view) === normalizeViewName(current) ||
          normalizeViewName(v.view) === normalizeViewName(current)
      );
      const def = latestView?.semantic_view || latestView?.view || '';
      if ((!current || !matched) && def) {
        setSearchParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            next.set('view', def);
            return next;
          },
          { replace: true }
        );
      }
    }
  }, [loading, views, latestView, searchParams, setSearchParams]);

  const selectView = useCallback(
    (viewStr) => {
      const val = String(viewStr || '').trim();
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (val) {
          next.set('view', val);
        } else {
          next.delete('view');
        }
        return next;
      });
    },
    [setSearchParams]
  );

  return {
    views,
    loading,
    error,
    selectedView,
    latestView,
    selectView,
    refetch: fetchViews,
  };
}
