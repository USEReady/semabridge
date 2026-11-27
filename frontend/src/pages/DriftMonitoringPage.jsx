import React, { useCallback, useEffect, useState, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Activity, Play, Check, X, Clock, AlertTriangle, AlertCircle, CheckCircle2,
  Trash2, RefreshCw, Sparkles, Database, Layers, ArrowRight,
  ShieldCheck, ShieldAlert, Search, ChevronRight, FileText, Zap, HelpCircle,
} from 'lucide-react';
import { authFetch, formatApiErrorMessage } from '../utils/api';
import { useProjects, normalizeProjectId, matchesProjectId } from '../hooks/useProjects';
import { useDeployedViews, normalizeViewName } from '../hooks/useDeployedViews';
import Modal from '../components/common/Modal';
import { ImpactReport } from '../components/ontology/GovernancePanels';
import DeployedViewSelector from '../components/common/DeployedViewSelector';
import WarehouseFilter from '../components/common/WarehouseFilter';
import DriftStatusCard from '../components/drift/DriftStatusCard';
import GroupedProjectList from '../components/drift/GroupedProjectList';
import DriftFindingsList from '../components/drift/DriftFindingsList';
import DriftModeToggle from '../components/drift/DriftModeToggle';
import DriftSchedules from '../components/drift/DriftSchedules';
import AIImpactModal from '../components/drift/AIImpactModal';
import EmptyState from '../components/common/EmptyState';
import {
  getCanonicalCategory,
  computeProjectDriftState,
  getDriftStateSortPriority,
  CATEGORY_DETAILS,
} from '../utils/driftLabels';
const CARD = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 10,
  padding: 16,
};

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 7,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_PRIMARY = {
  ...BTN,
  borderColor: 'var(--accent-blue)',
  background: 'var(--accent-blue)',
  color: '#fff',
};

const INPUT = {
  padding: '6px 10px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

function formatOccurred(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    const dateStr = d.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    let ago = '';
    if (diffSec < 60) ago = 'just now';
    else if (diffSec < 3600) ago = `${Math.floor(diffSec / 60)}m ago`;
    else if (diffSec < 86400) ago = `${Math.floor(diffSec / 3600)}h ago`;
    else ago = `${Math.floor(diffSec / 86400)}d ago`;

    return `${dateStr}, ${timeStr} (${ago})`;
  } catch {
    return isoStr;
  }
}

export default function DriftMonitoringPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialMode = (searchParams.get('mode') || 'live').toLowerCase();

  const {
    projects,
    filteredProjects,
    availableWarehouses,
    warehouseCounts,
    selectedWarehouse,
    setSelectedWarehouse,
    selectedProjectId,
    setSelectedProjectId,
    selectedProject,
  } = useProjects();

  const {
    views: deployedViews,
    selectedView: activeDeployedView,
    loading: viewsLoading,
  } = useDeployedViews(selectedProjectId);

  // Sanitized view: only allow view parameter if it genuinely belongs to this project's deployed views
  const selectedView = useMemo(() => {
    if (!selectedProjectId || viewsLoading || !deployedViews || deployedViews.length === 0) {
      return '';
    }
    const raw = searchParams.get('view') || '';
    if (!raw) return activeDeployedView || '';
    const match = deployedViews.find(
      (v) =>
        normalizeViewName(v.semantic_view) === normalizeViewName(raw) ||
        normalizeViewName(v.view) === normalizeViewName(raw)
    );
    return match ? (match.semantic_view || match.view) : (activeDeployedView || '');
  }, [selectedProjectId, viewsLoading, deployedViews, searchParams, activeDeployedView]);

  const [projectSearch, setProjectSearch] = useState('');
  const [allEvents, setAllEvents] = useState([]);
  const [_loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [checkMsg, setCheckMsg] = useState('');
  const [targetInfo, setTargetInfo] = useState({});
  const [refreshing, setRefreshing] = useState(false);
  const [lastCheckResult, setLastCheckResult] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [driftMode, setDriftMode] = useState(initialMode === 'mock' ? 'mock' : 'live');
  const [showSchedules, setShowSchedules] = useState(false);

  // Paged findings list state
  const [findingsPage, setFindingsPage] = useState(1);
  const [findingsPageSize, setFindingsPageSize] = useState(25);
  const [findingsSearch, setFindingsSearch] = useState('');
  const [findingsCategory, setFindingsCategory] = useState('ALL');
  const [findingsKind, setFindingsKind] = useState('ALL');
  const [findingsStatus, setFindingsStatus] = useState('pending');
  const [findingsSort, setFindingsSort] = useState('newest');

  const [pagedEvents, setPagedEvents] = useState([]);
  const [findingsTotalCount, setFindingsTotalCount] = useState(0);
  const [findingsTotalUnfilteredCount, setFindingsTotalUnfilteredCount] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [countsByCategory, setCountsByCategory] = useState({});
  const [countsByKind, setCountsByKind] = useState({});
  const [countsByStatus, setCountsByStatus] = useState({});
  const [findingsTotalPages, setFindingsTotalPages] = useState(1);
  const [aiImpactModalOpen, setAiImpactModalOpen] = useState(false);
  const [selectedAiImpactEvent, setSelectedAiImpactEvent] = useState(null);

  const handleOpenAIImpact = (event) => {
    setSelectedAiImpactEvent(event);
    setAiImpactModalOpen(true);
  };

  useEffect(() => {
    const modeParam = (searchParams.get('mode') || 'live').toLowerCase();
    if (modeParam === 'mock' && driftMode !== 'mock') {
      const timer = setTimeout(() => setDriftMode('mock'), 0);
      return () => clearTimeout(timer);
    } else if (modeParam !== 'mock' && driftMode === 'mock') {
      const timer = setTimeout(() => setDriftMode('live'), 0);
      return () => clearTimeout(timer);
    }
  }, [searchParams, driftMode]);

  // Blast Radius Modal state
  const [blastModalOpen, setBlastModalOpen] = useState(false);
  const [blastFinding, setBlastFinding] = useState(null);
  const [blastTarget, setBlastTarget] = useState('');
  const [blastType, setBlastType] = useState('METRIC');
  const [blastChangeType, setBlastChangeType] = useState('MODIFY_EXPRESSION');
  const [blastTableContext, setBlastTableContext] = useState('');
  const [blastExpression, setBlastExpression] = useState('');
  const [blastLoading, setBlastLoading] = useState(false);
  const [blastReport, setBlastReport] = useState(null);
  const [blastError, setBlastError] = useState('');
  const [projectMetrics, setProjectMetrics] = useState([]);

  // Match project ID flexibly across clean ids and display names
  const cleanPid = normalizeProjectId;
  const matchesProject = (evPid, proj, selId) => {
    if (!selId && !proj) return true;
    if (!evPid) return false;
    if (selId && matchesProjectId(evPid, selId)) return true;
    if (proj && (matchesProjectId(evPid, proj.id) || matchesProjectId(evPid, proj.name))) return true;
    return false;
  };

  const projectEvents = allEvents.filter((e) => {
    if (selectedProjectId && !matchesProject(e.project_id, selectedProject, selectedProjectId)) {
      return false;
    }
    const evMode = (e.mode || 'live').toLowerCase();
    return evMode === driftMode.toLowerCase();
  });

  const quickTargets = Array.from(new Set(projectMetrics.map((m) => m.name || m.logical_name).filter(Boolean)));

  const warehouseAddedTargets = useMemo(() => {
    return Array.from(new Set(
      projectEvents
        .filter((e) => {
          const cat = String(e.category || e.drift_type || '').toUpperCase();
          return cat.includes('ADDITION') || cat.includes('UNMANAGED');
        })
        .map((e) => e.metric_name || e.object_name || e.name)
        .filter(Boolean)
    ));
  }, [projectEvents]);

  const pendingEvents = projectEvents.filter((e) => e.status === 'pending');
  const approvedEvents = projectEvents.filter((e) => e.status === 'approved');
  const acceptedEvents = projectEvents.filter((e) => e.status === 'accepted');
  const ignoredEvents = projectEvents.filter((e) => e.status === 'ignored');

  const categoryCounts = useMemo(() => {
    if (selectedProjectId && countsByCategory && Object.keys(countsByCategory).length > 0) {
      const cats = {
        ADDITION: countsByCategory.ADDITION || 0,
        DELETION: countsByCategory.DELETION || 0,
        EXPRESSION_DRIFT: countsByCategory.EXPRESSION_DRIFT || 0,
        DESCRIPTION_DRIFT: countsByCategory.DESCRIPTION_DRIFT || 0,
      };
      const sum = Object.values(cats).reduce((a, b) => a + b, 0);
      return {
        ALL: sum,
        ...cats,
      };
    }
    const filteredEvents = findingsStatus === 'pending'
      ? projectEvents.filter((e) => e.status === 'pending')
      : projectEvents.filter((e) => e.status !== 'superseded');
    const sum = filteredEvents.length;
    return {
      ALL: sum,
      ADDITION: filteredEvents.filter((e) => getCanonicalCategory(e.category || e.drift_type) === 'ADDITION').length,
      DELETION: filteredEvents.filter((e) => getCanonicalCategory(e.category || e.drift_type) === 'DELETION').length,
      EXPRESSION_DRIFT: filteredEvents.filter((e) => getCanonicalCategory(e.category || e.drift_type) === 'EXPRESSION_DRIFT').length,
      DESCRIPTION_DRIFT: filteredEvents.filter((e) => getCanonicalCategory(e.category || e.drift_type) === 'DESCRIPTION_DRIFT').length,
    };
  }, [selectedProjectId, countsByCategory, findingsStatus, projectEvents]);

  const getProjectTarget = useCallback((proj) => {
    if (!proj) return {};
    const idKey = proj.id ? cleanPid(proj.id) : '';
    const nameKey = proj.name ? cleanPid(proj.name) : '';
    const base = (idKey && targetInfo[idKey])
      || (nameKey && targetInfo[nameKey])
      || (proj.id && targetInfo[proj.id])
      || (proj.name && targetInfo[proj.name])
      || {};
    return {
      ...base,
      platform: base.platform || proj.platform || proj.target_warehouse || 'UNKNOWN',
    };
  }, [targetInfo, cleanPid]);

  const curTarget = useMemo(() => {
    const fromProj = getProjectTarget(selectedProject);
    const fromId = selectedProjectId ? (targetInfo[cleanPid(selectedProjectId)] || targetInfo[selectedProjectId] || {}) : {};
    const fromLast = (lastCheckResult && matchesProject(lastCheckResult.project_id, selectedProject, selectedProjectId)) ? lastCheckResult : {};
    return {
      ...fromProj,
      ...fromId,
      ...fromLast,
      platform: fromLast.platform || fromId.platform || fromProj.platform || selectedProject?.platform || selectedProject?.target_warehouse || 'UNKNOWN',
    };
  }, [selectedProject, selectedProjectId, targetInfo, lastCheckResult, cleanPid, getProjectTarget]);

  const curDriftState = useMemo(() => {
    return computeProjectDriftState(selectedProject, targetInfo, allEvents);
  }, [selectedProject, targetInfo, allEvents]);

  // Filter projects by warehouse and search, sorted by urgency:
  // Needs Attention first, then Drift Detected, then In Sync, then Never Checked
  const visibleProjects = useMemo(() => {
    const term = projectSearch.toLowerCase().trim();
    const filtered = filteredProjects.filter((p) => {
      if (!p) return false;
      if (!term) return true;
      return (p.name || '').toLowerCase().includes(term) || (p.id || '').toLowerCase().includes(term);
    });

    return filtered.sort((a, b) => {
      const stateA = computeProjectDriftState(a, targetInfo, allEvents);
      const stateB = computeProjectDriftState(b, targetInfo, allEvents);
      const prioA = getDriftStateSortPriority(stateA);
      const prioB = getDriftStateSortPriority(stateB);
      if (prioA !== prioB) return prioA - prioB;
      return (a.name || a.id || '').localeCompare(b.name || b.id || '');
    });
  }, [filteredProjects, projectSearch, targetInfo, allEvents]);

  const selectProject = (pid) => {
    setSelectedProjectId(pid);
    setCheckMsg('');
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('view');
      return next;
    }, { replace: true });
  };

  useEffect(() => {
    let ignore = false;
    if (!selectedProjectId || viewsLoading) {
      const timer = setTimeout(() => {
        if (!ignore) setProjectMetrics([]);
      }, 0);
      return () => {
        ignore = true;
        clearTimeout(timer);
      };
    }
    const viewParam = selectedView ? `&view=${encodeURIComponent(selectedView)}` : '';
    authFetch(`/api/reconciliation/project-metrics?project_id=${encodeURIComponent(selectedProjectId)}${viewParam}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!ignore) setProjectMetrics(d?.metrics || []);
      })
      .catch(() => {
        if (!ignore) setProjectMetrics([]);
      });
    return () => {
      ignore = true;
    };
  }, [selectedProjectId, selectedView, viewsLoading]);

  const runBlastSimulation = async (targetOverride, changeTypeOverride, typeOverride, tableOverride, exprOverride, categoryOverride) => {
    const target = (targetOverride ?? blastTarget).trim();
    if (!target) return;
    const cType = changeTypeOverride ?? blastChangeType;
    const cCategory = categoryOverride ?? blastFinding?.category;
    const rawType = typeOverride ?? blastType;
    let normType = (rawType || 'METRIC').toUpperCase();
    if (normType === 'DIMENSION' || normType === 'ATTRIBUTE' || normType === 'COL') {
      normType = 'COLUMN';
    } else if (normType === 'MEASURE') {
      normType = 'METRIC';
    } else if (normType === 'VIEW') {
      normType = 'TABLE';
    }
    const tbl = tableOverride ?? blastTableContext;
    const expr = exprOverride ?? blastExpression;
    setBlastLoading(true);
    setBlastError('');
    try {
      const res = await authFetch('/api/governance/blast-radius', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_name: target,
          target_type: normType,
          change_type: cType,
          category: cCategory || undefined,
          table_context: tbl || undefined,
          expression: expr || undefined,
          project_id: selectedProjectId || undefined,
          platform: (curTarget?.platform && curTarget.platform !== 'UNKNOWN')
            ? curTarget.platform
            : (selectedProject?.target_warehouse?.platform || selectedProject?.platform || undefined),
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setBlastError(formatApiErrorMessage(res, data, 'Blast radius simulation'));
      } else {
        setBlastReport(data);
      }
    } catch (err) {
      setBlastError(`Unable to connect to Blast Radius service: ${err.message || String(err)}`);
    } finally {
      setBlastLoading(false);
    }
  };

  const handleOpenBlast = (initialTarget = '', category = '', kind = '', table = '', expression = '', findingObj = null) => {
    let ev = findingObj;
    if (!ev && initialTarget) {
      ev = projectEvents.find((e) => (e.metric_name || e.object_name || e.name || '') === initialTarget)
        || allEvents.find((e) => (e.metric_name || e.object_name || e.name || '') === initialTarget);
    }
    const candidate = initialTarget || ev?.metric_name || ev?.object_name || quickTargets[0] || '';
    setBlastTarget(candidate);
    let cat = String(category || ev?.category || ev?.drift_type || '').toUpperCase();
    let tbl = table || ev?.table || '';
    let expr = expression || ev?.live_value || ev?.live_expression || '';
    let knd = kind ? String(kind).toUpperCase() : (ev?.kind ? String(ev.kind).toUpperCase() : '');

    if (candidate && !ev) {
      const match = projectEvents.find((e) => (e.metric_name || e.object_name || e.name || '') === candidate)
        || allEvents.find((e) => (e.metric_name || e.object_name || e.name || '') === candidate);
      if (match) {
        if (!cat) cat = String(match.category || match.drift_type || '').toUpperCase();
        if (!tbl) tbl = match.table || '';
        if (!expr) expr = match.live_value || match.live_expression || '';
        if (!knd) knd = String(match.kind || 'METRIC').toUpperCase();
      }
    }

    const effectiveFinding = ev || (candidate ? {
      object_name: candidate,
      category: cat || 'DRIFT',
      kind: knd || 'METRIC',
      table: tbl,
      live_expression: expr,
    } : null);
    setBlastFinding(effectiveFinding);

    let cType = 'MODIFY_EXPRESSION';
    if (cat.includes('ADDITION') || cat.includes('ADD') || cat.includes('UNMANAGED')) {
      cType = 'ADD';
    } else if (cat.includes('DELETION') || cat.includes('MISSING') || cat.includes('DROP')) {
      cType = 'DROP';
    } else if (cat.includes('DESCRIPTION')) {
      cType = 'MODIFY_DESCRIPTION';
    } else if (cat.includes('TYPE')) {
      cType = 'MODIFY_TYPE';
    } else if (cat.includes('RENAME')) {
      cType = 'RENAME';
    }
    const tType = knd || 'METRIC';
    setBlastType(tType);
    setBlastChangeType(cType);
    setBlastTableContext(tbl);
    setBlastExpression(expr);
    setBlastReport(null);
    setBlastError('');
    setBlastModalOpen(true);
    if (candidate) {
      runBlastSimulation(candidate, cType, tType, tbl, expr, cat);
    }
  };

  // Reset finding page when switching project or view
  const [prevScopeKey, setPrevScopeKey] = useState(`${selectedProjectId}|${selectedView}|${driftMode}`);
  const currentScopeKey = `${selectedProjectId}|${selectedView}|${driftMode}`;
  if (prevScopeKey !== currentScopeKey) {
    setPrevScopeKey(currentScopeKey);
    setFindingsPage(1);
  }

  // Load drift events
  const refreshEvents = useCallback(async () => {
    try {
      const modeParam = driftMode ? `?mode=${encodeURIComponent(driftMode)}` : '';
      // Always fetch global drift overview to populate last_checks and all project events
      const globalRes = await authFetch(`/api/drift/events${modeParam}`);
      let globalLastChecks = {};
      let globalEvs = [];
      if (globalRes.ok) {
        const globalData = await globalRes.json();
        globalEvs = globalData.events || [];
        globalLastChecks = (globalData.last_checks && typeof globalData.last_checks === 'object') ? globalData.last_checks : {};
      }

      let selectedProjectInfo = null;

      if (selectedProjectId) {
        if (viewsLoading) {
          // Wait for deployed views to finish resolving before requesting project events
          return;
        }
        const queryParams = new URLSearchParams();
        queryParams.set('project_id', selectedProjectId);
        if (selectedView) queryParams.set('view', selectedView);
        if (driftMode) queryParams.set('mode', driftMode);
        queryParams.set('page', String(findingsPage));
        queryParams.set('page_size', String(findingsPageSize));
        if (findingsSearch && findingsSearch.trim()) queryParams.set('search', findingsSearch.trim());
        if (findingsCategory && findingsCategory !== 'ALL') queryParams.set('category', findingsCategory);
        if (findingsKind && findingsKind !== 'ALL') queryParams.set('kind', findingsKind);
        if (findingsStatus && findingsStatus !== 'all') queryParams.set('status', findingsStatus);
        if (findingsSort) queryParams.set('sort', findingsSort);

        const pRes = await authFetch(`/api/drift/events?${queryParams.toString()}`);
        if (pRes.ok) {
          const pData = await pRes.json();
          if (pData.last_checks && typeof pData.last_checks === 'object') {
            globalLastChecks = { ...globalLastChecks, ...pData.last_checks };
          }
          selectedProjectInfo = {
            views_checked: pData.views_checked || [],
            checked_at: pData.checked_at || null,
            status: pData.status,
            reason: pData.reason || pData.not_checked_reason,
            not_checked_reason: pData.not_checked_reason,
            reason_code: pData.reason_code,
            baseline_source: pData.baseline_source,
            baseline_at: pData.baseline_at,
            metrics_compared: pData.metrics_compared,
          };
          setPagedEvents(pData.events || []);
          setFindingsTotalCount(pData.total_count || 0);
          setFindingsTotalUnfilteredCount(pData.total_unfiltered_count !== undefined ? pData.total_unfiltered_count : (pData.total_count || 0));
          setPendingCount(pData.pending_count !== undefined ? pData.pending_count : (pData.counts_by_status?.pending || 0));
          setCountsByCategory(pData.counts_by_category || {});
          setCountsByKind(pData.counts_by_kind || {});
          setCountsByStatus(pData.counts_by_status || {});
          setFindingsTotalPages(pData.total_pages || 1);
        }
      } else {
        setPagedEvents([]);
        setFindingsTotalCount(0);
        setFindingsTotalUnfilteredCount(0);
        setPendingCount(0);
        setCountsByCategory({});
        setCountsByKind({});
        setCountsByStatus({});
        setFindingsTotalPages(1);
      }

      setAllEvents(globalEvs);
      setTargetInfo((prev) => {
        const next = { ...prev };
        Object.entries(globalLastChecks).forEach(([k, v]) => {
          const normK = cleanPid(k);
          next[normK] = {
            ...(next[normK] || {}),
            ...v,
          };
        });
        if (selectedProjectId && selectedProjectInfo) {
          const normPid = cleanPid(selectedProjectId);
          next[normPid] = {
            ...(next[normPid] || {}),
            ...selectedProjectInfo,
          };
        }
        return next;
      });
    } catch (err) {
      console.error('Failed to load drift events:', err);
    } finally {
      setLoading(false);
    }
  }, [
    selectedProjectId,
    selectedView,
    viewsLoading,
    driftMode,
    findingsPage,
    findingsPageSize,
    findingsSearch,
    findingsCategory,
    findingsKind,
    findingsStatus,
    findingsSort,
    cleanPid,
  ]);

  useEffect(() => {
    let ignore = false;
    const timer = setTimeout(() => {
      if (!ignore) refreshEvents();
    }, 0);
    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return;
      }
      refreshEvents();
    }, 30000);
    return () => {
      ignore = true;
      clearTimeout(timer);
      clearInterval(interval);
    };
  }, [refreshEvents]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'visible') {
        refreshEvents();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [refreshEvents]);

  useEffect(() => {
    authFetch('/api/drift/capabilities')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) setCapabilities(data);
      })
      .catch((err) => console.warn('Failed loading drift capabilities:', err));
  }, []);

  // Event approval / ignore / accept handler
  const handleResolve = async (id, action) => {
    let promptMsg = 'Reason for restoring baseline & syncing:';
    if (action === 'ignore') {
      promptMsg = 'Reason for ignoring this drift (required):';
    } else if (action === 'accept') {
      promptMsg = 'Reason for accepting this warehouse change (required):';
    } else if (action === 'approve') {
      promptMsg = 'Reason for approving sync / restoring baseline (required):';
    }
    const input = window.prompt(promptMsg);
    if (input === null) return;
    const reason = input.trim();
    if (!reason) {
      alert('A non-empty reason is required to submit a reviewer action.');
      return;
    }
    try {
      await authFetch(`/api/drift/events/${id}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });
      await refreshEvents();
    } catch (err) {
      alert(`Action failed: ${err}`);
    }
  };

  const handleBulkResolve = async ({ kind, category, eventIds, action, reason }) => {
    try {
      const res = await authFetch('/api/drift/events/bulk-resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId,
          view: selectedView || undefined,
          kind: kind && kind !== 'ALL' ? kind : undefined,
          category: category && category !== 'ALL' ? category : undefined,
          event_ids: eventIds || undefined,
          action: action || 'ignore',
          reason: reason || '',
        }),
      });
      if (res.ok) {
        setCheckMsg(`Bulk action applied: ${action}`);
        await refreshEvents();
      } else {
        const err = await res.json();
        alert(`Bulk action failed: ${err.detail || 'Error'}`);
      }
    } catch (err) {
      alert(`Bulk action error: ${err}`);
    }
  };

  // Check drift now
  const handleCheckNow = async () => {
    setBusy(true);
    setCheckMsg('');
    try {
      const res = await authFetch('/api/drift/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: selectedProjectId || undefined,
          view: selectedView || undefined,
          mode: driftMode,
          force_refresh: true,
        }),
      });
      const data = await res.json();
      setLastCheckResult(data);
      if (data.status === 'BASELINE_CAPTURED') {
        setCheckMsg(`Baseline captured (${data.baseline_metrics || data.metrics_compared || data.checked_metrics || 0} metrics) — next check compares.`);
      } else if (data.status === 'NOT_CHECKED') {
        setCheckMsg(`Check not run: ${data.not_checked_reason || 'Not checked'}`);
      } else if (data.status === 'IN_SYNC') {
        setCheckMsg(`In sync: Checked ${data.metrics_compared ?? data.checked_metrics} object(s) · 0 drift detected.`);
      } else {
        const objCount = data.metrics_compared ?? data.checked_metrics ?? 0;
        const detCount = data.drift_detected ?? 0;
        const newCount = data.new_events !== undefined ? data.new_events : detCount;
        setCheckMsg(
          newCount > 0 && detCount > newCount
            ? `Drift detected: Checked ${objCount} object(s) · ${detCount} drift event(s) detected (${newCount} pending review).`
            : `Drift detected: Checked ${objCount} object(s) · ${detCount} drift event(s) detected.`
        );
      }
      if (selectedProjectId) {
        const normPid = cleanPid(selectedProjectId);
        setTargetInfo((prev) => ({
          ...prev,
          [normPid]: {
            ...(prev[normPid] || {}),
            views_checked: data.views_checked || [],
            checked_at: data.checked_at || new Date().toISOString(),
            status: data.status,
            reason: data.reason || data.not_checked_reason,
            not_checked_reason: data.not_checked_reason,
            reason_code: data.reason_code,
            baseline_source: data.baseline_source,
            baseline_at: data.baseline_at,
            metrics_compared: data.metrics_compared ?? data.checked_metrics,
          },
        }));
      }
      await refreshEvents();
    } catch (err) {
      setCheckMsg(`Check failed: ${err}`);
    } finally {
      setBusy(false);
    }
  };

  // Refresh drift logs
  const handleRefreshLogs = async () => {
    setRefreshing(true);
    try {
      await refreshEvents();
      setCheckMsg('Drift events refreshed.');
    } catch (err) {
      setCheckMsg(`Refresh failed: ${err}`);
    } finally {
      setRefreshing(false);
    }
  };

  // Clear drift history
  const handleClearHistory = async () => {
    if (!window.confirm(`Clear all drift monitoring history for ${selectedProjectId || 'this project'}?`)) return;
    try {
      const qs = selectedProjectId ? `?project_id=${encodeURIComponent(selectedProjectId)}` : '';
      await authFetch(`/api/drift/events${qs}`, { method: 'DELETE' });
      setCheckMsg('Drift monitoring history cleared.');
      await refreshEvents();
    } catch (err) {
      alert(`Failed to clear: ${err}`);
    }
  };

  return (
    <div style={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--bg-app)' }}>
      {/* ── TOP BREADCRUMB & TITLE BAR ────────────────────────────────────── */}
      <div style={{
        padding: '16px 24px',
        borderBottom: '1px solid var(--border-main)',
        background: 'var(--bg-surface)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36,
            height: 36,
            borderRadius: 8,
            background: 'rgba(59, 130, 246, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-blue)',
          }}>
            <Activity size={20} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: 'var(--text-primary)' }}>
              Drift Monitoring
            </h1>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
              Logs of metric drifts detected during scheduled runs · Review and approve changes before warehouse sync.
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            style={{
              ...BTN,
              background: 'rgba(168, 85, 247, 0.1)',
              borderColor: 'rgba(168, 85, 247, 0.35)',
              color: 'var(--accent-purple, #a855f7)',
            }}
            onClick={() => {
              const ev = projectEvents[0];
              handleOpenBlast(
                ev?.metric_name || quickTargets[0] || '',
                ev?.category || ev?.drift_type || '',
                ev?.kind || '',
                ev?.table || '',
                ev?.live_value || ev?.live_expression || '',
                ev
              );
            }}
            title="Check Blast Radius: see how metric changes affect downstream BI reports & AI agents"
          >
            <Zap size={13} style={{ color: 'var(--accent-purple, #a855f7)' }} /> Check Blast Radius
          </button>
          <button
            type="button"
            style={BTN}
            onClick={handleRefreshLogs}
            disabled={refreshing}
            title="Refresh drift status"
          >
            <RefreshCw size={13} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} /> {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* ── MAIN TWO-PANE LAYOUT ─────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
        {/* ── LEFT PANE: PROJECTS LIST ───────────────────────────────────── */}
        <div style={{
          width: 320,
          borderRight: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
        }}>
          {/* Projects Search Header & Warehouse Filter */}
          <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border-main)', display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                Projects
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontWeight: 500 }}>
                {visibleProjects.length === projects.length
                  ? `${projects.length} projects`
                  : `${visibleProjects.length} of ${projects.length} projects`}
              </div>
            </div>

            <WarehouseFilter
              warehouses={availableWarehouses}
              selectedWarehouse={selectedWarehouse}
              onSelectWarehouse={setSelectedWarehouse}
              counts={warehouseCounts}
              totalCount={projects.length}
            />

            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-tertiary)' }} />
              <input
                type="text"
                placeholder="Filter projects…"
                value={projectSearch}
                onChange={(e) => setProjectSearch(e.target.value)}
                style={{ ...INPUT, width: '100%', boxSizing: 'border-box', paddingLeft: 30, paddingRight: projectSearch ? 26 : 8 }}
              />
              {projectSearch && (
                <button
                  type="button"
                  onClick={() => setProjectSearch('')}
                  style={{
                    position: 'absolute',
                    right: 8,
                    top: 8,
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-tertiary)',
                    padding: 0,
                  }}
                  title="Clear search"
                  aria-label="Clear search"
                >
                  <X size={13} />
                </button>
              )}
            </div>
          </div>

          {/* Projects Scrollable Grouped List */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 8 }}>
            <GroupedProjectList
              projects={projects}
              targetInfo={targetInfo}
              allEvents={allEvents}
              selectedProjectId={selectedProjectId}
              onSelectProject={selectProject}
              selectedWarehouse={selectedWarehouse}
              searchQuery={projectSearch}
              onClearFilters={() => {
                setProjectSearch('');
                setSelectedWarehouse('');
              }}
              loading={_loading && projects.length === 0}
            />
          </div>
        </div>

        {/* ── RIGHT PANE: PROJECT DRIFT MONITORING DETAILS ───────────────── */}
        <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!selectedProjectId ? (
            selectedWarehouse && filteredProjects.length === 0 ? (
              <EmptyState
                icon={Database}
                title={`No projects for ${selectedWarehouse}`}
                description="There are no projects associated with this warehouse platform."
                actionLabel="Clear warehouse filter"
                onAction={() => setSelectedWarehouse('')}
                style={{ marginTop: 40 }}
              />
            ) : (
              <EmptyState
                icon={Activity}
                title="Select a project"
                description="Choose a project from the left sidebar to monitor schema drift, view verified metrics, and check synchronization against warehouse targets."
                style={{ marginTop: 40 }}
              />
            )
          ) : (
            <>
              {/* Project Header Bar */}
              <div style={{
                ...CARD,
                padding: 16,
                background: 'var(--bg-surface)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: 12,
              }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <h2 style={{ margin: 0, fontSize: 17, fontWeight: 800, color: 'var(--text-primary)' }}>
                      {selectedProject?.name || selectedProjectId || 'Select a project'}
                    </h2>
                    {selectedProjectId && (
                      <code style={{ fontSize: 11.5, padding: '2px 6px', borderRadius: 4, background: 'var(--bg-app)', color: 'var(--text-secondary)' }}>
                        {selectedProjectId}
                      </code>
                    )}
                    {curTarget.platform && curTarget.platform !== 'UNKNOWN' && (
                      <span
                        style={{
                          fontSize: 11,
                          padding: '2px 7px',
                          borderRadius: 4,
                          background: 'rgba(59, 130, 246, 0.1)',
                          border: '1px solid rgba(59, 130, 246, 0.25)',
                          color: 'var(--color-primary, #3b82f6)',
                          fontWeight: 600,
                        }}
                      >
                        {curTarget.platform}
                      </span>
                    )}
                    {selectedProjectId && (
                      <DeployedViewSelector
                        projectId={selectedProjectId}
                        projectSchema={selectedProject?.schema}
                      />
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                    <Database size={13} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
                    <span>
                      {(() => {
                        const views = curTarget.views_checked || [];
                        const targetText = views.length > 0
                          ? `Target View${views.length > 1 ? 's' : ''}: ${views.join(', ')}`
                          : 'Target: Semantic views (not checked yet)';
                        const parts = [targetText];
                        if (curTarget.baseline_source) {
                          const srcLabel = curTarget.baseline_source === 'deploy'
                            ? 'Deploy'
                            : curTarget.baseline_source === 'snapshot'
                              ? 'Snapshot'
                              : 'Live capture';
                          const atText = curTarget.baseline_at ? ` (${formatOccurred(curTarget.baseline_at)})` : '';
                          parts.push(`Baseline: ${srcLabel}${atText}`);
                        }
                        if (curTarget.metrics_compared !== undefined && curTarget.metrics_compared !== null && curTarget.metrics_compared > 0) {
                          parts.push(`${curTarget.metrics_compared} object(s) compared`);
                        }
                        if (curTarget.checked_at) {
                          parts.push(`Checked: ${formatOccurred(curTarget.checked_at)}`);
                        }
                        return parts.join(' · ');
                      })()}
                    </span>
                  </div>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <DriftModeToggle
                    mode={driftMode}
                    onChangeMode={(newMode) => {
                      setDriftMode(newMode);
                      setSearchParams((prev) => {
                        const next = new URLSearchParams(prev);
                        next.set('mode', newMode);
                        return next;
                      });
                    }}
                    capabilities={capabilities}
                    disabled={busy}
                  />
                  <button
                    type="button"
                    style={{
                      ...BTN,
                      background: showSchedules ? 'rgba(59, 130, 246, 0.15)' : 'var(--bg-surface-raised)',
                      borderColor: showSchedules ? 'var(--accent-blue)' : 'var(--border-main)',
                      color: showSchedules ? 'var(--accent-blue)' : 'var(--text-primary)',
                    }}
                    onClick={() => setShowSchedules(!showSchedules)}
                    title="Manage automated drift monitoring schedules"
                  >
                    <Clock size={13} /> Schedules
                  </button>
                  <button
                    type="button"
                    style={BTN_PRIMARY}
                    disabled={busy}
                    onClick={handleCheckNow}
                    title="Execute immediate drift inspection"
                  >
                    <Play size={13} /> {busy ? 'Checking…' : 'Check Drift Now'}
                  </button>
                  <button
                    type="button"
                    style={BTN}
                    onClick={handleRefreshLogs}
                    disabled={refreshing}
                    title="Refresh drift logs from warehouse and semantic models"
                  >
                    <RefreshCw size={13} style={{ animation: refreshing ? 'spin 1s linear infinite' : 'none' }} /> {refreshing ? 'Refreshing…' : 'Refresh'}
                  </button>
                  <button
                    type="button"
                    style={{ ...BTN, color: 'var(--color-error)' }}
                    onClick={handleClearHistory}
                    title="Wipe drift events history for this project"
                  >
                    <Trash2 size={13} /> Clear History
                  </button>
                </div>
              </div>

              {/* Automated Drift Schedules Section */}
              {showSchedules && (
                <DriftSchedules
                  projectId={selectedProjectId}
                  onScheduleChange={handleRefreshLogs}
                />
              )}

              {/* Actionable Drift Status Card */}
              <DriftStatusCard
                status={curDriftState.status}
                reasonCode={curDriftState.reasonCode}
                reasonMessage={curDriftState.reason}
                checkedAt={curDriftState.checkedAt}
                baselineAt={curDriftState.baselineAt}
                baselineSource={curDriftState.baselineSource}
                metricsCompared={curTarget.metrics_compared || curTarget.baseline_metrics || 0}
                driftDetected={countsByStatus.pending !== undefined ? countsByStatus.pending : curDriftState.pendingCount}
                totalCount={findingsTotalCount}
                totalUnfilteredCount={findingsTotalUnfilteredCount}
                pendingCount={pendingCount}
                activeStatus={findingsStatus}
                categoryCounts={categoryCounts}
                selectedCategory={findingsCategory}
                onSelectCategory={(cat) => {
                  setFindingsCategory(cat);
                  setFindingsPage(1);
                }}
                showCheckButton={false}
                onCheckNow={handleCheckNow}
                onDeploy={() => navigate(`/projects/${selectedProjectId}?tab=deploy`)}
                onOpenSettings={() => navigate('/settings')}
                busy={busy}
                formatTime={formatOccurred}
              />

              {checkMsg && (
                <div style={{ padding: '8px 12px', borderRadius: 6, background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-blue)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <CheckCircle2 size={14} /> {checkMsg}
                </div>
              )}

              {/* ── DRIFT FINDINGS LIST (PAGED, GROUPED, CLAMPED) ─────────────── */}
              <div style={CARD}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Clock size={16} style={{ color: 'var(--accent-blue)' }} />
                    <strong style={{ fontSize: 13.5 }}>
                      Drift Occurrences &amp; Approvals ({findingsTotalCount})
                    </strong>
                  </div>

                  {/* Quick Status Filter Tabs */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, background: 'var(--bg-surface-raised)', padding: '2px 4px', borderRadius: 8, border: '1px solid var(--border-main)' }}>
                    {[
                      { key: 'all', label: 'All', count: countsByStatus.all ?? (findingsTotalUnfilteredCount || projectEvents.length) },
                      { key: 'pending', label: 'Pending', count: countsByStatus.pending !== undefined ? countsByStatus.pending : pendingEvents.length, color: 'var(--color-warning, #eab308)' },
                      { key: 'approved', label: 'Approved', count: countsByStatus.approved !== undefined ? countsByStatus.approved : approvedEvents.length, color: 'var(--color-success, #22c55e)' },
                      { key: 'accepted', label: 'Accepted', count: countsByStatus.accepted !== undefined ? countsByStatus.accepted : acceptedEvents.length, color: 'var(--accent-blue, #3b82f6)' },
                      { key: 'ignored', label: 'Ignored', count: countsByStatus.ignored !== undefined ? countsByStatus.ignored : ignoredEvents.length, color: 'var(--text-tertiary)' },
                      { key: 'resolved', label: 'Resolved', count: countsByStatus.resolved !== undefined ? countsByStatus.resolved : 0, color: 'var(--color-success, #22c55e)' },
                    ].map((tab) => {
                      const isSel = findingsStatus === tab.key;
                      return (
                        <button
                          key={tab.key}
                          type="button"
                          onClick={() => {
                            setFindingsStatus(tab.key);
                            setFindingsPage(1);
                          }}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 5,
                            padding: '3px 9px',
                            borderRadius: 6,
                            border: 'none',
                            background: isSel ? 'var(--bg-surface)' : 'transparent',
                            color: isSel ? 'var(--text-primary)' : 'var(--text-secondary)',
                            fontWeight: isSel ? 700 : 500,
                            fontSize: 11.5,
                            cursor: 'pointer',
                            boxShadow: isSel ? '0 1px 2px rgba(0,0,0,0.1)' : 'none',
                            transition: 'all 0.15s ease',
                          }}
                        >
                          <span>{tab.label}</span>
                          <span style={{ fontSize: 10.5, color: isSel ? (tab.color || 'var(--text-primary)') : 'var(--text-tertiary)', fontWeight: 600 }}>
                            ({tab.count})
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>

                <DriftFindingsList
                  events={pagedEvents}
                  totalCount={findingsTotalCount}
                  totalUnfilteredCount={findingsTotalUnfilteredCount}
                  countsByCategory={countsByCategory}
                  countsByKind={countsByKind}
                  countsByStatus={countsByStatus}
                  page={findingsPage}
                  pageSize={findingsPageSize}
                  totalPages={findingsTotalPages}
                  onPageChange={setFindingsPage}
                  onPageSizeChange={setFindingsPageSize}
                  search={findingsSearch}
                  onSearchChange={setFindingsSearch}
                  selectedCategory={findingsCategory}
                  onCategoryChange={setFindingsCategory}
                  selectedKind={findingsKind}
                  onKindChange={setFindingsKind}
                  selectedStatus={findingsStatus}
                  onStatusChange={setFindingsStatus}
                  sort={findingsSort}
                  onSortChange={setFindingsSort}
                  onResolve={handleResolve}
                  onBulkResolve={handleBulkResolve}
                  onOpenBlast={handleOpenBlast}
                  onOpenAIImpact={handleOpenAIImpact}
                  formatOccurred={formatOccurred}
                  mode={driftMode}
                  loading={busy}
                />
              </div>
            </>
          )}
        </div>
      </div>

      {/* ── BLAST RADIUS IMPACT MODAL ────────────────────────────────────── */}
      <Modal
        open={blastModalOpen}
        onClose={() => {
          setBlastModalOpen(false);
          setBlastFinding(null);
        }}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Zap size={16} style={{ color: 'var(--accent-purple, #a855f7)' }} />
            <span>Drift Blast Radius &amp; Downstream Impact</span>
          </div>
        }
        size="3xl"
        style={{ maxWidth: 1400, width: '94vw', height: '90vh', maxHeight: '92vh' }}
        bodyStyle={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', padding: '16px 20px', minHeight: 0 }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
            {blastReport?.affected_ai_tools?.length > 0
              ? 'Check how this metric drift propagates downstream across BI dashboards, semantic layers, and Cortex AI agents.'
              : 'Evaluates downstream impact across semantic layers and reporting models.'}
          </div>

          {blastFinding ? (
            /* Finding Context: Runs immediately with finding info, no form, no action dropdown, no quick targets */
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '8px 12px',
              borderRadius: 8,
              background: 'var(--bg-surface-raised)',
              border: '1px solid var(--border-main)',
              fontSize: 12,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 700, color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                  {blastTarget}
                </span>
                <span style={{
                  padding: '2px 8px',
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 700,
                  background: blastChangeType === 'ADD' ? 'rgba(34, 197, 94, 0.15)' : blastChangeType === 'DROP' ? 'rgba(239, 68, 68, 0.15)' : 'rgba(168, 85, 247, 0.15)',
                  color: blastChangeType === 'ADD' ? 'var(--color-success, #22c55e)' : blastChangeType === 'DROP' ? 'var(--color-error, #ef4444)' : 'var(--accent-purple, #a855f7)',
                }}>
                  {blastFinding.category || blastChangeType}
                </span>
                <span style={{ color: 'var(--text-secondary)', fontSize: 11.5 }}>
                  Kind: <strong style={{ color: 'var(--text-primary)' }}>{blastType}</strong>
                </span>
                {blastTableContext && (
                  <span style={{ color: 'var(--text-secondary)', fontSize: 11.5 }}>
                    Table: <code style={{ color: 'var(--text-primary)' }}>{blastTableContext}</code>
                  </span>
                )}
              </div>
              {blastLoading && (
                <span style={{ fontSize: 11.5, color: 'var(--accent-purple)', fontWeight: 600 }}>
                  Analyzing impact…
                </span>
              )}
            </div>
          ) : (
            <>
              {/* Target & Change Type Controls */}
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 2fr) minmax(110px, 1fr) minmax(140px, 1.2fr) auto', gap: 8, alignItems: 'center' }}>
                <input
                  type="text"
                  list="drift-blast-metrics"
                  value={blastTarget}
                  onChange={(e) => {
                    const val = e.target.value;
                    setBlastTarget(val);
                    const term = val.trim().toLowerCase();
                    if (term) {
                      const matchedEv = projectEvents.find((ev) =>
                        (ev.metric_name || ev.object_name || ev.name || '').toLowerCase() === term
                      ) || allEvents.find((ev) =>
                        (ev.metric_name || ev.object_name || ev.name || '').toLowerCase() === term
                      );
                      if (matchedEv) {
                        const cat = String(matchedEv.category || matchedEv.drift_type || '').toUpperCase();
                        if (cat.includes('ADDITION') || cat.includes('UNMANAGED')) {
                          setBlastChangeType('ADD');
                        }
                      }
                    }
                  }}
                  placeholder="Target metric, table or column"
                  style={{
                    padding: '6px 10px',
                    borderRadius: 6,
                    border: '1px solid var(--border-main)',
                    background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                    width: '100%',
                    boxSizing: 'border-box',
                  }}
                />
                <datalist id="drift-blast-metrics">
                  {warehouseAddedTargets.map((m) => (
                    <option key={`wh-${m}`} value={m} label="Added in warehouse" />
                  ))}
                  {quickTargets.map((m) => (
                    <option key={m} value={m} />
                  ))}
                </datalist>

                <select
                  value={blastType}
                  onChange={(e) => {
                    const newType = e.target.value;
                    setBlastType(newType);
                    runBlastSimulation(blastTarget, blastChangeType, newType);
                  }}
                  style={{
                    padding: '6px 10px',
                    borderRadius: 6,
                    border: '1px solid var(--border-main)',
                    background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  <option value="METRIC">Metric</option>
                  <option value="COLUMN">Column</option>
                  <option value="TABLE">Table</option>
                </select>

                <select
                  value={blastChangeType}
                  onChange={(e) => {
                    const newChange = e.target.value;
                    setBlastChangeType(newChange);
                    runBlastSimulation(blastTarget, newChange, blastType);
                  }}
                  style={{
                    padding: '6px 10px',
                    borderRadius: 6,
                    border: '1px solid var(--border-main)',
                    background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                    cursor: 'pointer',
                  }}
                >
                  <option value="MODIFY_EXPRESSION">MODIFY_EXPRESSION</option>
                  <option value="ADD">ADD (New in Warehouse)</option>
                  <option value="DROP">DROP (Delete from Warehouse)</option>
                  <option value="MODIFY_DESCRIPTION">MODIFY_DESCRIPTION</option>
                  <option value="RENAME">RENAME</option>
                  <option value="MODIFY_TYPE">MODIFY_TYPE</option>
                </select>

                <button
                  type="button"
                  disabled={blastLoading || !blastTarget.trim()}
                  onClick={() => runBlastSimulation()}
                  style={{
                    ...BTN_PRIMARY,
                    background: 'var(--accent-purple, #a855f7)',
                    borderColor: 'var(--accent-purple, #a855f7)',
                    color: '#fff',
                    opacity: blastLoading || !blastTarget.trim() ? 0.6 : 1,
                    whiteSpace: 'nowrap',
                  }}
                >
                  <Play size={12} /> {blastLoading ? 'Checking…' : 'Check'}
                </button>
              </div>

              {/* Quick Target Chips */}
              {(warehouseAddedTargets.length > 0 || quickTargets.length > 0) && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', fontSize: 11 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Quick Targets:</span>
                  {warehouseAddedTargets.map((t) => (
                    <button
                      key={`wh-${t}`}
                      type="button"
                      style={{
                        ...BTN,
                        padding: '2px 8px',
                        fontSize: 10.5,
                        fontFamily: 'monospace',
                        background: blastTarget === t ? 'rgba(34, 197, 94, 0.2)' : 'rgba(34, 197, 94, 0.08)',
                        borderColor: 'rgba(34, 197, 94, 0.4)',
                        color: 'var(--color-success, #22c55e)',
                        fontWeight: 600,
                      }}
                      title="Unmanaged warehouse addition"
                      onClick={() => {
                        setBlastTarget(t);
                        setBlastChangeType('ADD');
                        runBlastSimulation(t, 'ADD', blastType);
                      }}
                    >
                      + {t} (Added in warehouse)
                    </button>
                  ))}
                  {quickTargets.slice(0, 12).map((t) => (
                    <button
                      key={t}
                      type="button"
                      style={{
                        ...BTN,
                        padding: '2px 7px',
                        fontSize: 10.5,
                        fontFamily: 'monospace',
                        background: blastTarget === t ? 'rgba(168, 85, 247, 0.15)' : 'var(--bg-surface-raised)',
                        borderColor: blastTarget === t ? 'var(--accent-purple)' : 'var(--border-main)',
                        color: blastTarget === t ? 'var(--accent-purple)' : 'var(--text-secondary)',
                      }}
                      onClick={() => {
                        setBlastTarget(t);
                        runBlastSimulation(t, blastChangeType, blastType);
                      }}
                    >
                      {t}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}

          {/* Error Message */}
          {blastError && (
            <div style={{
              padding: '8px 12px',
              borderRadius: 6,
              background: 'rgba(239, 68, 68, 0.1)',
              color: 'var(--color-error)',
              fontSize: 12,
            }}>
              {blastError}
            </div>
          )}

          {/* Impact Report View */}
          {blastReport && (
            <div style={{ marginTop: 4, flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <ImpactReport
                report={blastReport}
                isFinding={Boolean(blastFinding)}
                style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}
                onPickSuggestion={(sug) => {
                  setBlastTarget(sug);
                  runBlastSimulation(sug);
                }}
                onEvaluateAddition={(target) => {
                  setBlastChangeType('ADD');
                  runBlastSimulation(target, 'ADD', blastType);
                }}
              />
            </div>
          )}
        </div>
      </Modal>

      {/* ── AI IMPACT MODAL ────────────────────────────────────────────── */}
      <AIImpactModal
        isOpen={aiImpactModalOpen}
        onClose={() => setAiImpactModalOpen(false)}
        event={selectedAiImpactEvent}
      />
    </div>
  );
}
