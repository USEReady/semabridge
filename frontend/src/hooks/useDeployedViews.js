import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { authFetch } from '../utils/api';
import { normalizeViewName, getViewBadgeLabel, canonicalViewName, isViewMatch } from '../utils/viewUtils';

export { normalizeViewName, getViewBadgeLabel, canonicalViewName, isViewMatch };

/**
 * Hook to manage deployed views for a project.
 * Fetches /api/projects/{projectId}/deployed-views, defaults to the latest deploy,
 * and synchronizes selection via the `?view=` URL query parameter.
 */
export function useDeployedViews(projectId) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [currentProjectId, setCurrentProjectId] = useState(projectId);
  const [views, setViews] = useState([]);
  const [loading, setLoading] = useState(Boolean(projectId));
  const [error, setError] = useState(null);

  // Synchronously reset views and set loading to true when projectId changes
  if (projectId !== currentProjectId) {
    setCurrentProjectId(projectId);
    setViews([]);
    setLoading(Boolean(projectId));
    setError(null);
  }

  // Clear stale ?view from URL immediately when projectId changes
  useEffect(() => {
    if (searchParams.has('view')) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('view');
          return next;
        },
        { replace: true }
      );
    }
  }, [projectId]);

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
      const rawViews = Array.isArray(data) ? data : [];
      const deduped = [];
      const seenCanonical = new Set();
      for (const v of rawViews) {
        const rawName = String(v.semantic_view || v.view || '').trim();
        const key = canonicalViewName(rawName);
        if (!key) continue;
        if (seenCanonical.has(key)) {
          const idx = deduped.findIndex(
            (existing) => canonicalViewName(existing.semantic_view || existing.view || '') === key
          );
          if (idx !== -1) {
            if ((!deduped[idx].database || !deduped[idx].schema) && (v.database && v.schema)) {
              deduped[idx] = { ...v, is_latest: deduped[idx].is_latest || v.is_latest };
            }
          }
          continue;
        }
        seenCanonical.add(key);
        deduped.push(v);
      }

      // Sort deployed views latest first (newest deployed_at first, is_latest priority)
      deduped.sort((a, b) => {
        if (a.is_latest && !b.is_latest) return -1;
        if (!a.is_latest && b.is_latest) return 1;
        const timeA = a.deployed_at ? new Date(a.deployed_at).getTime() : 0;
        const timeB = b.deployed_at ? new Date(b.deployed_at).getTime() : 0;
        return timeB - timeA;
      });

      setViews(deduped);
    } catch (err) {
      console.error('Error fetching deployed views:', err);
      setError(String(err?.message || err));
      setViews([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    let ignore = false;
    const timer = setTimeout(() => {
      if (!ignore) {
        fetchViews();
      }
    }, 0);
    return () => {
      ignore = true;
      clearTimeout(timer);
    };
  }, [fetchViews]);

  const latestView = useMemo(() => {
    if (!views || views.length === 0) return null;
    return views.find((v) => v.is_latest) || views[0];
  }, [views]);

  const urlView = searchParams.get('view') || '';

  // Determine selected view: URL query param -> latest view fallback
  const selectedView = useMemo(() => {
    if (loading || !projectId || !views || views.length === 0) {
      return '';
    }
    if (urlView && views.length > 0) {
      // Check if URL view matches any known view in the list
      const matched = views.find(
        (v) =>
          isViewMatch(v.semantic_view, urlView) ||
          isViewMatch(v.view, urlView)
      );
      if (matched) return matched.semantic_view || matched.view;
    }
    return latestView?.semantic_view || latestView?.view || '';
  }, [urlView, views, latestView, loading, projectId]);

  // Persist default (latest) view to URL if ?view is omitted or belongs to another project
  useEffect(() => {
    if (!loading && views.length > 0) {
      const current = searchParams.get('view');
      const matched = current && views.find(
        (v) =>
          isViewMatch(v.semantic_view, current) ||
          isViewMatch(v.view, current)
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
    } else if (!loading && views.length === 0 && searchParams.get('view')) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.delete('view');
          return next;
        },
        { replace: true }
      );
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
