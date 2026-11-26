import { useState, useEffect, useCallback, useRef } from 'react';
import { authFetch } from '../utils/api';

/**
 * Custom hook for paged drift events, category/kind filtering,
 * visibility-aware polling, and reviewer resolution actions.
 */
export function useDriftEvents({
  projectId = '',
  view = '',
  mode = 'live',
  autoPoll = false,
  pollIntervalMs = 60000,
} = {}) {
  const [events, setEvents] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [totalUnfilteredCount, setTotalUnfilteredCount] = useState(0);
  const [countsByCategory, setCountsByCategory] = useState({});
  const [countsByKind, setCountsByKind] = useState({});
  const [countsByStatus, setCountsByStatus] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filter & Pagination state
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [category, setCategory] = useState('ALL');
  const [kind, setKind] = useState('ALL');
  const [status, setStatus] = useState('active');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState('detected_desc');

  // Metadata from backend
  const [metadata, setMetadata] = useState({
    status: 'IN_SYNC',
    reasonCode: '',
    reasonMessage: '',
    checkedAt: null,
    baselineAt: null,
    baselineSource: null,
    metricsCompared: 0,
    viewsChecked: [],
  });

  const fetchEvents = useCallback(async () => {
    // Skip if browser tab is hidden and this is background execution
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (projectId) params.set('project_id', projectId);
      if (view) params.set('view', view);
      if (mode) params.set('mode', mode);
      if (category && category !== 'ALL') params.set('category', category);
      if (kind && kind !== 'ALL') params.set('kind', kind);
      if (status && status !== 'all') params.set('status', status);
      if (search && search.trim()) params.set('search', search.trim());
      if (sort) params.set('sort', sort);
      params.set('page', String(page));
      params.set('page_size', String(pageSize));

      const res = await authFetch(`/api/drift/events?${params.toString()}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setError(errData.detail || 'Failed to fetch drift events');
        return;
      }

      const data = await res.json();
      setEvents(data.events || []);
      setTotalCount(data.total_count ?? data.count ?? 0);
      setTotalUnfilteredCount(data.total_unfiltered_count ?? data.total_count ?? 0);
      setCountsByCategory(data.counts_by_category || {});
      setCountsByKind(data.counts_by_kind || {});
      setCountsByStatus(data.counts_by_status || {});

      setMetadata({
        status: data.status || 'IN_SYNC',
        reasonCode: data.reason || data.not_checked_reason || '',
        reasonMessage: data.not_checked_reason || data.reason || '',
        checkedAt: data.checked_at || null,
        baselineAt: data.baseline_at || null,
        baselineSource: data.baseline_source || null,
        metricsCompared: data.metrics_compared ?? 0,
        viewsChecked: data.views_checked || [],
      });
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoading(false);
    }
  }, [projectId, view, mode, category, kind, status, search, sort, page, pageSize]);

  // Fetch whenever filters/page change
  useEffect(() => {
    fetchEvents();
  }, [fetchEvents]);

  // Visibility change listener: refetch when user switches back to active tab
  useEffect(() => {
    if (typeof document === 'undefined') return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        fetchEvents();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [fetchEvents]);

  // Reviewer actions
  const resolveEvent = useCallback(async (eventId, action, reason) => {
    try {
      const res = await authFetch(`/api/drift/events/${eventId}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason || 'Reviewed change' }),
      });
      if (res.ok) {
        await fetchEvents();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }, [fetchEvents]);

  const bulkResolveEvents = useCallback(async (eventIds, action, reason) => {
    try {
      const res = await authFetch('/api/drift/events/bulk-resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_ids: eventIds,
          action,
          reason: reason || 'Bulk reviewed change',
        }),
      });
      if (res.ok) {
        await fetchEvents();
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }, [fetchEvents]);

  const clearHistory = useCallback(async () => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const res = await authFetch(`/api/drift/events${qs}`, { method: 'DELETE' });
    if (res.ok) {
      await fetchEvents();
      return true;
    }
    return false;
  }, [projectId, fetchEvents]);

  return {
    events,
    totalCount,
    totalUnfilteredCount,
    countsByCategory,
    countsByKind,
    countsByStatus,
    metadata,
    loading,
    error,
    page,
    setPage,
    pageSize,
    setPageSize,
    category,
    setCategory,
    kind,
    setKind,
    status,
    setStatus,
    search,
    setSearch,
    sort,
    setSort,
    refetch: fetchEvents,
    resolveEvent,
    bulkResolveEvents,
    clearHistory,
  };
}
