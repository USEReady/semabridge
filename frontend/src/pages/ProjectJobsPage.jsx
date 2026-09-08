import { useState, useEffect, useMemo } from 'react';
import usePageCache from '../hooks/usePageCache';
import { Play, RefreshCw, Clock, CalendarClock, ChevronDown, ChevronRight, BarChart3, Cloud, Snowflake, Database, Link2, Download, FileText, AlertTriangle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';
import SearchInput from '../components/common/SearchInput';
import DroppedFieldsPanel from '../components/common/DroppedFieldsPanel';
import Modal from '../components/common/Modal';
import RunReportSummary from '../components/RunReportSummary';
import { matchesSmartQuery } from '../components/common/smartSearchQuery.js';
import { api } from '../utils/api';
import { buildMockRunLogs, getRunLogs, saveRunLogs } from '../utils/runLogs';
import { cleanPbixLabel } from '../utils/pbixLabel';

const REFRESH_INTERVAL_MS = 4000;

function formatDuration(ms) {
  if (!ms) return '—';
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  }).format(date);
}

function normalizeSourceKey(value) {
  if (!value) return '';

  let key = '';
  if (typeof value === 'string') {
    key = value.toLowerCase().trim();
  } else if (typeof value === 'object') {
    key = String(
      value.type || value.adapter || value.source || value.source_type || value.connector || ''
    ).toLowerCase().trim();
  }

  if (!key) return '';
  if (key.includes('pbix') || key.includes('powerbi') || key.includes('power bi') || key === 'pbi') return 'pbix';
  if (key.includes('fabric')) return 'fabric';
  if (key.includes('snowflake')) return 'snowflake';
  if (key.includes('databricks')) return 'databricks';
  return key;
}

function sourceIcon(sourceType) {
  const normalized = normalizeSourceKey(sourceType);

  if (normalized.includes('pbix')) return <BarChart3 size={14} color="#F2C811" />;
  if (normalized.includes('fabric')) return <Cloud size={14} color="#3b82f6" />;
  if (normalized.includes('snowflake')) return <Snowflake size={14} color="#38bdf8" />;
  if (normalized.includes('databricks')) return <Database size={14} color="#f97316" />;
  return <Link2 size={14} color="var(--text-tertiary)" />;
}

function RunDiffViewer({ run, projectId }) {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const beforeId = run.before_target_snapshot_ids?.[0];
    const afterId = run.after_target_snapshot_ids?.[0] || run.after_tgt_snapshots?.[0];

    if (!beforeId || !afterId || !projectId) {
      setStats({ total_after: 0, removed: 0, retained_from_target: 0, source_added: 0 });
      setLoading(false);
      return;
    }

    let isMounted = true;
    setLoading(true);
    api.compareProjectSnapshots(projectId, beforeId, afterId)
      .then(diff => {
        if (!isMounted) return;
        let removed = 0;
        let added = 0;
        const totalAfter = diff.metadata_diff?.snapshot_b?.model_count || 0;
        const totalBefore = diff.metadata_diff?.snapshot_a?.model_count || 0;

        if (diff.models) {
          for (const mInfo of Object.values(diff.models)) {
             if (mInfo.status === 'removed') removed++;
             else if (mInfo.status === 'added') added++;
          }
        }

        const retainedFromTarget = Math.max(0, totalBefore - removed);
        setStats({
          total_after: totalAfter,
          removed: removed,
          retained_from_target: retainedFromTarget,
          source_added: added
        });
      })
      .catch(() => {
        if (!isMounted) return;
        setStats({ total_after: 0, removed: 0, retained_from_target: 0, source_added: 0 });
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });

    return () => { isMounted = false; };
  }, [run, projectId]);

  if (loading || !stats) {
    return (
      <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, padding: 16, background: 'var(--bg-surface)', marginBottom: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>
        Loading backend stats...
      </div>
    );
  }

  const isUpsert = (run.sync_mode || 'copy') === 'upsert';

  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, padding: 16, background: 'var(--bg-surface)', marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>Run Summary & Diffs</div>
      
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', flexDirection: 'column' }}>
        {isUpsert ? (
          <div style={{ display: 'grid', gap: 6, fontSize: 12 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Sync Mode:</span>
              <span style={{ fontWeight: 700, color: '#10b981' }}>UPSERT</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Models retained from target snapshot:</span>
              <span style={{ fontWeight: 600 }}>{stats.retained_from_target}</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Net new source models added:</span>
              <span style={{ fontWeight: 600 }}>{stats.source_added}</span>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>Total after snapshot:</span>
              <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{stats.total_after}</span>
            </div>
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 6, fontSize: 12 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Sync Mode:</span>
              <span style={{ fontWeight: 700, color: '#64748b' }}>COPY</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Target replaced with source</span>
              <span style={{ fontWeight: 600, color: 'var(--color-error)' }}>({stats.removed} target-only models removed)</span>
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
              <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>Total after snapshot:</span>
              <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{stats.total_after} (exact source copy)</span>
            </div>
          </div>
        )}
      </div>
      
      <div style={{ marginTop: 16, padding: 10, background: 'var(--bg-input)', borderRadius: 6, fontSize: 12, borderLeft: `3px solid ${isUpsert ? '#10b981' : '#64748b'}` }}>
        {isUpsert 
          ? `UPSERT retained ${stats.retained_from_target} models from the previous target snapshot and added ${stats.source_added} net new source models.`
          : `COPY mode removed ${stats.removed} models not present in source.`}
      </div>
    </div>
  );
}

export default function ProjectJobsPage() {
  const [runs, setRuns] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [scheduleDeletingId, setScheduleDeletingId] = useState('');

  // Cached UI state — survives SPA navigation within the same tab.
  const [jobsCache, setJobsCache] = usePageCache('jobs-page', {
    search: '',
    searchUseRegex: false,
    statusFilter: 'all',
    expandedRunId: null,
  });
  const { search, searchUseRegex, statusFilter, expandedRunId } = jobsCache;
  const setSearch = (v) => setJobsCache({ search: typeof v === 'function' ? v(search) : v });
  const setSearchUseRegex = (v) => setJobsCache({ searchUseRegex: typeof v === 'function' ? v(searchUseRegex) : v });
  const setStatusFilter = (v) => setJobsCache({ statusFilter: typeof v === 'function' ? v(statusFilter) : v });
  const setExpandedRunId = (v) => setJobsCache({ expandedRunId: typeof v === 'function' ? v(expandedRunId) : v });

  useEffect(() => {
    let cancelled = false;
    let timerId;

    const loadPageData = async () => {
      try {
        const [runsData, schedulesData] = await Promise.all([
          api.listJobRuns().catch(() => []),
          api.listJobSchedules().catch(() => []),
        ]);

        if (cancelled) return;

        const nextRuns = (Array.isArray(runsData) ? runsData : []).map((run) => {
          if (run?.id) {
            saveRunLogs(run.id, getRunLogs(run));
          }
          return run;
        });
        setRuns(nextRuns);
        setSchedules(Array.isArray(schedulesData) ? schedulesData : []);
      } catch {
        if (cancelled) return;
        setRuns([]);
        setSchedules([]);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    const poll = async () => {
      if (cancelled) return;
      await loadPageData();
      if (cancelled) return;
      timerId = window.setTimeout(poll, REFRESH_INTERVAL_MS);
    };

    poll();

    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, []);

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const result = await api.triggerJob();
      if (result) {
        const nextRun = { ...result, status: 'running' };
        if (nextRun?.id) {
          saveRunLogs(nextRun.id, buildMockRunLogs(nextRun));
        }
        setRuns(prev => [nextRun, ...prev]);
        setExpandedRunId(nextRun.id ?? null);
      }
    } catch (err) {
      console.error('Trigger job failed:', err);
    } finally {
      setRunning(false);
    }
  };

  // "View Report Preview" modal -- always exactly ONE button per run, even
  // for a multi-PBIX batch (run.model_reports has 2+ entries in that case,
  // never exactly 1 -- see generate_per_model_reports()). reportModalModel
  // is null for a single-file run (which selects the non-model-scoped
  // endpoints below) and defaults to the first file's model for a
  // multi-file run -- a file-selector dropdown inside the modal (rendered
  // only when model_reports.length > 1) then lets the user switch which
  // file's Summary/Raw Report are shown, without closing and reopening the
  // modal. Summary (JSON) and Raw Report (Markdown) are both fetched up
  // front, not lazily on tab switch, so toggling between them is instant
  // once the modal (or a newly-selected file) has finished loading --
  // matches demo_version_ref's exact approach, since this modal is ported
  // from there.
  const [reportModalRun, setReportModalRun] = useState(null);
  const [reportModalModel, setReportModalModel] = useState(null);
  const [reportModalMaximized, setReportModalMaximized] = useState(false);
  const [reportContent, setReportContent] = useState('');
  const [reportLoading, setReportLoading] = useState(false);
  const [reportError, setReportError] = useState(null);
  const [reportViewMode, setReportViewMode] = useState('summary');
  const [reportSummary, setReportSummary] = useState(null);
  const [reportSummaryLoading, setReportSummaryLoading] = useState(false);
  const [reportSummaryError, setReportSummaryError] = useState(null);

  const loadReportForModel = async (run, modelLabel) => {
    const pid = run.project_id;
    const rid = run.id || run.run_id;
    setReportLoading(true);
    setReportError(null);
    setReportContent('');
    setReportSummaryLoading(true);
    setReportSummaryError(null);
    setReportSummary(null);
    await Promise.all([
      (async () => {
        try {
          const text = modelLabel
            ? await api.getRunModelReport(pid, rid, modelLabel)
            : await api.getRunReport(pid, rid);
          setReportContent(text);
        } catch (err) {
          console.error('Failed to load report preview:', err);
          setReportError(err.message || 'Failed to load report content.');
        } finally {
          setReportLoading(false);
        }
      })(),
      (async () => {
        try {
          const summary = modelLabel
            ? await api.getRunModelReportSummary(pid, rid, modelLabel)
            : await api.getRunReportSummary(pid, rid);
          setReportSummary(summary);
        } catch (err) {
          console.error('Failed to load report summary:', err);
          setReportSummaryError(err.message || 'Failed to load report summary.');
        } finally {
          setReportSummaryLoading(false);
        }
      })(),
    ]);
  };

  const openReportModal = async (run) => {
    const modelEntries = Array.isArray(run.model_reports) ? run.model_reports : [];
    const initialModel = modelEntries.length > 1 ? modelEntries[0].model : null;
    setReportModalRun(run);
    setReportModalModel(initialModel);
    setReportModalMaximized(false);
    setReportViewMode('summary');
    await loadReportForModel(run, initialModel);
  };

  const selectReportModel = async (modelLabel) => {
    if (!reportModalRun) return;
    setReportModalModel(modelLabel);
    await loadReportForModel(reportModalRun, modelLabel);
  };

  const closeReportModal = () => {
    setReportModalRun(null);
    setReportModalModel(null);
  };

  // "Dropped Fields" modal -- a separate, standalone button/modal from
  // "View Report Preview" (previously a third tab inside that modal; moved
  // back out to its own display). Mirrors the report modal's file-selector
  // pattern exactly (same run.model_reports dropdown, hidden for a
  // single-file run) but needs no fetch at all: the data is derived
  // straight from run.results, already loaded client-side -- the same
  // source the original page-level DroppedFieldsPanel used before it was
  // ever moved into the report modal.
  const [droppedFieldsModalRun, setDroppedFieldsModalRun] = useState(null);
  const [droppedFieldsModalModel, setDroppedFieldsModalModel] = useState(null);

  const openDroppedFieldsModal = (run) => {
    const modelEntries = Array.isArray(run.model_reports) ? run.model_reports : [];
    setDroppedFieldsModalRun(run);
    setDroppedFieldsModalModel(modelEntries.length > 1 ? modelEntries[0].model : null);
  };

  const selectDroppedFieldsModel = (modelLabel) => {
    setDroppedFieldsModalModel(modelLabel);
  };

  const closeDroppedFieldsModal = () => {
    setDroppedFieldsModalRun(null);
    setDroppedFieldsModalModel(null);
  };

  // Scoped to whichever file is currently selected (or the whole run, for
  // a single-file run). No `model` field is added when scoped to one file,
  // so DroppedFieldsPanel's own per-model grouping never kicks in here --
  // it only ever sees one file's entries at a time.
  const droppedFieldsModalEntries = useMemo(() => {
    const results = Array.isArray(droppedFieldsModalRun?.results) ? droppedFieldsModalRun.results : [];
    const scoped = droppedFieldsModalModel
      ? results.filter((r) => r?.model === droppedFieldsModalModel)
      : results;
    return scoped.flatMap(
      (r) => (Array.isArray(r?.dropped_entities) ? r.dropped_entities : []).map(
        (entry) => ({ ...entry, model: r?.model })
      )
    );
  }, [droppedFieldsModalRun, droppedFieldsModalModel]);

  const handleDeleteSchedule = async (projectId) => {
    if (!projectId) return;
    setScheduleDeletingId(String(projectId));
    try {
      await api.deleteProjectSchedule(projectId);
      setSchedules(prev => prev.filter(item => String(item.project_id) !== String(projectId)));
    } catch (err) {
      console.error('Delete schedule failed:', err);
    } finally {
      setScheduleDeletingId('');
    }
  };

  const filteredRuns = runs.filter((run) => {
    const matchSearch = !search || matchesSmartQuery(
      `${String(run.id || '')} ${String(run.project_name || '')}`,
      search,
      searchUseRegex,
    );
    const matchStatus = statusFilter === 'all' || String(run.status || '').toLowerCase() === statusFilter;
    return matchSearch && matchStatus;
  });

  const inputStyle = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border-main)',
    borderRadius: 8,
    color: 'var(--text-primary)',
    padding: '8px 12px',
    fontSize: 13,
    outline: 'none',
    fontFamily: 'inherit',
  };

  return (
    <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1400, margin: '0 auto' }} className="md:px-10">
      <PageHeader
        title="Runs"
        description="Configure execution schedules and monitor run history."
        action={{
          label: running ? 'Running…' : 'Run Now',
          icon: running ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />,
          onClick: handleRunNow,
        }}
      />

      <div
        className="rounded-xl mb-6"
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          padding: 24,
        }}
      >
        <div className="flex items-center gap-2 mb-4">
          <CalendarClock size={16} style={{ color: 'var(--accent-blue)' }} />
          <h2 className="text-primary font-semibold" style={{ fontSize: 14, margin: 0 }}>Scheduled Jobs</h2>
        </div>

        {schedules.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            No saved schedules yet. Use the Schedule action from Edit Config to create one.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {schedules.map((schedule) => (
              <div
                key={schedule.id || schedule.project_id}
                className="flex flex-col md:grid md:grid-cols-[1.5fr_0.8fr_1.2fr_auto] gap-4"
                style={{
                  border: '1px solid var(--border-main)',
                  borderRadius: 10,
                  padding: 14,
                  alignItems: 'center',
                  background: 'var(--bg-surface-raised)',
                }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                    {schedule.project_name || schedule.project_id}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                    {schedule.schedule_type === 'cron'
                      ? `Cron: ${schedule.cron || 'N/A'}`
                      : `One-time: ${schedule.date || 'N/A'} ${schedule.time || ''}`.trim()}
                  </div>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {String(schedule.schedule_type || 'manual').toUpperCase()}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  Next run: {formatDate(schedule.next_run_at)}
                </div>
                <button
                  onClick={() => handleDeleteSchedule(schedule.project_id)}
                  disabled={scheduleDeletingId === String(schedule.project_id)}
                  style={{
                    border: '1px solid var(--border-main)',
                    background: 'transparent',
                    color: 'var(--text-secondary)',
                    borderRadius: 8,
                    padding: '8px 12px',
                    cursor: scheduleDeletingId === String(schedule.project_id) ? 'not-allowed' : 'pointer',
                    opacity: scheduleDeletingId === String(schedule.project_id) ? 0.6 : 1,
                  }}
                >
                  {scheduleDeletingId === String(schedule.project_id) ? 'Cancelling…' : 'Cancel'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mt-10">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <h2 className="text-primary font-bold" style={{ fontSize: 18, margin: 0 }}>Run History</h2>
            <span className="px-2 py-0.5 rounded-full bg-surface-raised border border-main text-[11px] text-tertiary">
              {runs.length} runs
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setRuns([])}
              className="text-[11px] text-tertiary hover:text-primary transition-colors bg-transparent border-none cursor-pointer"
            >
              Clear Logs
            </button>
          </div>
        </div>

        <div className="flex flex-row items-center gap-3 mb-6">
          <div className="flex-1 relative">
            <SearchInput
              value={search}
              onChange={setSearch}
              useRegex={searchUseRegex}
              onToggleRegex={setSearchUseRegex}
              allowRegex
              helperText={searchUseRegex ? 'Regex enabled' : ''}
              placeholder="Search by prefix or regex..."
              width="100%"
            />
          </div>

          <div className="shrink-0">
            <div className="relative group">
              <select
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)}
                style={{
                  ...inputStyle,
                  padding: '7px 12px',
                  fontSize: 12,
                  cursor: 'pointer',
                  minWidth: 140,
                  appearance: 'none',
                  backgroundImage: 'none',
                  textAlign: 'center',
                }}
                className="hover:border-accent group-hover:bg-surface-hover"
              >
                <option value="all">🔍 All Status</option>
                <option value="success">✅ Success</option>
                <option value="failed">❌ Failed</option>
                <option value="running">🔄 Running</option>
              </select>
            </div>
          </div>
        </div>

        <div
          className="rounded-xl overflow-x-auto custom-scrollbar"
          style={{ border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}
        >
          <div style={{ minWidth: 850 }}>
            {loading ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                Loading runs...
              </div>
            ) : filteredRuns.length === 0 ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                No job runs yet. Click 'Run Now' to trigger your first execution.
              </div>
            ) : (
              filteredRuns.map((run, index) => {
                const isExpanded = expandedRunId === run.id;
                const logs = getRunLogs(run);
                return (
                  <div
                    key={run.id ?? index}
                    style={{
                      borderTop: index === 0 ? 'none' : '1px solid var(--border-main)',
                      background: index % 2 === 0 ? 'transparent' : 'var(--bg-surface-raised)',
                    }}
                  >
                    <button
                      onClick={() => setExpandedRunId(isExpanded ? null : run.id)}
                      style={{
                        width: '100%',
                        border: 'none',
                        background: 'transparent',
                        cursor: 'pointer',
                        padding: '14px 16px',
                        display: 'grid',
                        gridTemplateColumns: 'minmax(120px, 0.8fr) minmax(200px, 2.5fr) minmax(80px, 0.5fr) 80px 110px 160px 80px',
                        gap: 16,
                        alignItems: 'center',
                        textAlign: 'left',
                        color: 'var(--text-primary)',
                        minHeight: 56,
                      }}
                    >
                      <div className="hidden lg:flex items-center gap-10 min-w-0">
                        <div className="flex items-center gap-2">
                          {isExpanded ? <ChevronDown size={14} className="text-tertiary" /> : <ChevronRight size={14} className="text-tertiary" />}
                          <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                            {String(run.id ?? '-').substring(0, 12)}
                          </span>
                        </div>
                      </div>

                      <div className="lg:hidden flex items-center justify-center flex-shrink-0" style={{ width: 24 }}>
                        {isExpanded ? <ChevronDown size={14} className="text-tertiary" /> : <ChevronRight size={14} className="text-tertiary" />}
                      </div>
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600,
                        minWidth: 0,
                      }}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                          {sourceIcon(run.source_type)}
                        </span>
                        <span style={{
                          whiteSpace: isExpanded ? 'normal' : 'nowrap',
                          overflow: isExpanded ? 'visible' : 'hidden',
                          textOverflow: isExpanded ? 'clip' : 'ellipsis',
                          flex: 1,
                          minWidth: 0,
                          overflowWrap: 'break-word',
                          wordBreak: 'break-word',
                        }}>
                          {run.project_name || 'Project run'}
                        </span>
                      </div>
                      <div className="hidden sm:block" style={{ fontSize: 12, color: 'var(--text-secondary)', fontWeight: 500 }}>{run.schedule || 'Manual'}</div>
                      <div style={{ display: 'flex', justifyContent: 'center' }}>
                        <StatusBadge status={run.status || 'draft'} size="sm" />
                      </div>
                      {/* Sync Mode Badge */}
                      <div style={{ display: 'flex', justifyContent: 'center' }}>
                        <span style={{
                          fontSize: 10,
                          fontWeight: 700,
                          letterSpacing: '0.08em',
                          padding: '2px 8px',
                          borderRadius: 4,
                          textTransform: 'uppercase',
                          background: (run.sync_mode || 'copy') === 'upsert'
                            ? 'rgba(16, 185, 129, 0.15)' /* Green for UPSERT */
                            : 'rgba(100, 116, 139, 0.15)', /* Gray/Blue for COPY */
                          color: (run.sync_mode || 'copy') === 'upsert'
                            ? '#10b981'
                            : '#64748b',
                          border: `1px solid ${(run.sync_mode || 'copy') === 'upsert' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(100, 116, 139, 0.25)'}`,
                        }}>
                          {(run.sync_mode || 'copy').toUpperCase()}
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--text-tertiary)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {formatDate(run.started_at)}
                      </div>
                      <div className="hidden md:block" style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-secondary)', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        {formatDuration(run.duration_ms)}
                      </div>
                    </button>

                    {isExpanded && (
                      <div style={{ padding: '0 16px 16px 16px', background: 'var(--bg-input)', borderTop: '1px solid var(--border-main)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, padding: '14px 0' }}>
                          <InfoCard label="Run ID" value={String(run.id ?? '-')} mono />
                          <InfoCard label="Sync Mode" value={(run.sync_mode || 'copy').toUpperCase()} />
                          <InfoCard label="Started" value={formatDate(run.started_at)} />
                          <InfoCard label="Duration" value={formatDuration(run.duration_ms)} mono />
                          <InfoCard label="Status" value={String(run.status || 'draft')} />
                          <InfoCard label="Message" value={run.message || run.error || '—'} />
                        </div>

                        {/* "View Report Preview" -- always exactly ONE button per run,
                            whether it's a single-file run or a multi-PBIX batch. The
                            modal itself (below) handles picking which file's report to
                            show via its own file-selector dropdown. "Dropped Fields" is
                            a separate button/modal next to it -- not a tab inside the
                            report modal -- with its own independent file selector. */}
                        {(Boolean(run.report_path) || (Array.isArray(run.model_reports) && run.model_reports.length > 0) || (Array.isArray(run.results) && run.results.length > 0)) && (
                          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 8, marginBottom: 8 }}>
                            {(Boolean(run.report_path) || (Array.isArray(run.model_reports) && run.model_reports.length > 0)) && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openReportModal(run);
                                }}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: 6,
                                  padding: '6px 12px',
                                  borderRadius: 6,
                                  fontSize: 12,
                                  fontWeight: 600,
                                  background: 'var(--bg-surface-raised)',
                                  color: 'var(--text-primary)',
                                  border: '1px solid var(--border-main)',
                                  cursor: 'pointer',
                                }}
                              >
                                <FileText size={14} /> View Report Preview
                              </button>
                            )}
                            {Array.isArray(run.results) && run.results.length > 0 && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  openDroppedFieldsModal(run);
                                }}
                                style={{
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: 6,
                                  padding: '6px 12px',
                                  borderRadius: 6,
                                  fontSize: 12,
                                  fontWeight: 600,
                                  background: 'var(--bg-surface-raised)',
                                  color: 'var(--text-primary)',
                                  border: '1px solid var(--border-main)',
                                  cursor: 'pointer',
                                }}
                              >
                                <AlertTriangle size={14} /> Dropped Fields
                              </button>
                            )}
                          </div>
                        )}

                        {run.status === 'success' && (
                          <RunDiffViewer run={run} projectId={run.project_id} />
                        )}

                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 10 }}>
                          Execution Stages For Selected Run
                        </div>
                        <div
                          style={{
                            borderRadius: 8,
                            border: '1px solid var(--border-main)',
                            background: 'var(--bg-surface)',
                            padding: 12,
                            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                            fontSize: 12,
                            lineHeight: 1.65,
                            color: 'var(--text-primary)',
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          {logs.join('\n')}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Run Report Preview Modal -- shared by the whole-run button and every
          per-model button; reportModalModel selects which endpoints/label to use. */}
      <Modal
        open={Boolean(reportModalRun)}
        onClose={closeReportModal}
        title={
          `Run Report Preview — ${reportModalRun?.id || reportModalRun?.run_id || ''}` +
          (reportModalModel ? ` — ${cleanPbixLabel(reportModalModel)}` : '')
        }
        size="lg"
        allowMaximize
        isMaximized={reportModalMaximized}
        onToggleMaximize={() => setReportModalMaximized((v) => !v)}
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
            <button
              type="button"
              onClick={async () => {
                if (!reportModalRun) return;
                const rid = reportModalRun.id || reportModalRun.run_id;
                try {
                  if (reportModalModel) {
                    await api.downloadRunModelReport(reportModalRun.project_id, rid, reportModalModel);
                  } else {
                    await api.downloadRunReport(reportModalRun.project_id, rid);
                  }
                } catch (err) {
                  console.error('Failed to download report:', err);
                }
              }}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '6px 12px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 600,
                background: 'var(--accent-primary, #3b82f6)',
                color: '#ffffff',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              <Download size={14} /> Download Raw .md
            </button>
            <button
              type="button"
              onClick={closeReportModal}
              style={{
                padding: '6px 14px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 500,
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
              }}
            >
              Close
            </button>
          </div>
        }
      >
        {Array.isArray(reportModalRun?.model_reports) && reportModalRun.model_reports.length > 1 && (
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 4 }}>
              File
            </label>
            <select
              value={reportModalModel || ''}
              onChange={(e) => selectReportModel(e.target.value)}
              style={{ ...inputStyle, width: '100%', maxWidth: 360, cursor: 'pointer' }}
            >
              {reportModalRun.model_reports.map((entry) => (
                <option key={entry.model} value={entry.model}>
                  {cleanPbixLabel(entry.model)}
                </option>
              ))}
            </select>
          </div>
        )}
        <div style={{ display: 'flex', gap: 4, padding: '0 4px 12px 4px', borderBottom: '1px solid var(--border-subtle)', marginBottom: 8 }}>
          {[
            { key: 'summary', label: 'Summary' },
            { key: 'markdown', label: 'Raw Report' },
          ].map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setReportViewMode(tab.key)}
              style={{
                padding: '6px 14px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
                background: reportViewMode === tab.key ? 'var(--accent-primary, #3b82f6)' : 'transparent',
                color: reportViewMode === tab.key ? '#ffffff' : 'var(--text-secondary)',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {reportViewMode === 'summary' ? (
          reportSummaryLoading ? (
            <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-secondary)' }}>
              Loading summary...
            </div>
          ) : reportSummaryError ? (
            <div style={{ padding: 24, color: 'var(--color-error)' }}>
              {reportSummaryError}
            </div>
          ) : (
            <RunReportSummary data={reportSummary} />
          )
        ) : reportLoading ? (
          <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-secondary)' }}>
            Loading report...
          </div>
        ) : reportError ? (
          <div style={{ padding: 24, color: 'var(--color-error)' }}>
            {reportError}
          </div>
        ) : (
          <div style={{ padding: '8px 4px', fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                table: ({ ...props }) => (
                  <table style={{ width: '100%', borderCollapse: 'collapse', margin: '16px 0', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }} {...props} />
                ),
                th: ({ style, align, ...props }) => (
                  <th style={{ background: 'var(--bg-surface-raised)', padding: '10px 20px', border: '1px solid var(--border-main)', textAlign: style?.textAlign || align || 'left', fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap', ...style }} {...props} />
                ),
                td: ({ style, align, ...props }) => (
                  <td style={{ padding: '10px 20px', border: '1px solid var(--border-main)', textAlign: style?.textAlign || align || 'left', color: 'var(--text-primary)', ...style }} {...props} />
                ),
                h1: ({ ...props }) => (
                  <h1 style={{ fontSize: '1.4em', fontWeight: 700, margin: '16px 0 8px 0', color: 'var(--text-primary)' }} {...props} />
                ),
                h2: ({ ...props }) => (
                  <h2 style={{ fontSize: '1.2em', fontWeight: 600, margin: '14px 0 6px 0', color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)', paddingBottom: 4 }} {...props} />
                ),
                h3: ({ ...props }) => (
                  <h3 style={{ fontSize: '1.05em', fontWeight: 600, margin: '12px 0 4px 0', color: 'var(--text-primary)' }} {...props} />
                ),
                ul: ({ ...props }) => (
                  <ul style={{ paddingLeft: 20, margin: '8px 0' }} {...props} />
                ),
                ol: ({ ...props }) => (
                  <ol style={{ paddingLeft: 20, margin: '8px 0' }} {...props} />
                ),
                li: ({ ...props }) => (
                  <li style={{ marginBottom: 4 }} {...props} />
                ),
                code: ({ inline, ...props }) => (
                  inline ? (
                    <code style={{ background: 'var(--bg-surface-raised)', padding: '2px 5px', borderRadius: 4, fontFamily: 'monospace', fontSize: '0.9em' }} {...props} />
                  ) : (
                    <code style={{ display: 'block', background: 'var(--bg-surface-raised)', padding: 10, borderRadius: 6, fontFamily: 'monospace', fontSize: '0.9em', overflowX: 'auto' }} {...props} />
                  )
                ),
                blockquote: ({ ...props }) => (
                  <blockquote style={{ borderLeft: '3px solid var(--accent-primary, #3b82f6)', paddingLeft: 12, margin: '8px 0', color: 'var(--text-secondary)', fontStyle: 'italic' }} {...props} />
                ),
              }}
            >
              {reportContent}
            </ReactMarkdown>
          </div>
        )}
      </Modal>

      {/* Dropped Fields Modal -- separate from the report modal, with its
          own independent file-selector state (droppedFieldsModalModel),
          so switching files here never affects whichever file is selected
          in the report modal, and vice versa. */}
      <Modal
        open={Boolean(droppedFieldsModalRun)}
        onClose={closeDroppedFieldsModal}
        title={
          `Dropped Fields — ${droppedFieldsModalRun?.id || droppedFieldsModalRun?.run_id || ''}` +
          (droppedFieldsModalModel ? ` — ${cleanPbixLabel(droppedFieldsModalModel)}` : '')
        }
        size="lg"
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', width: '100%' }}>
            <button
              type="button"
              onClick={closeDroppedFieldsModal}
              style={{
                padding: '6px 14px',
                borderRadius: 6,
                fontSize: 12,
                fontWeight: 500,
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border-main)',
                cursor: 'pointer',
              }}
            >
              Close
            </button>
          </div>
        }
      >
        {Array.isArray(droppedFieldsModalRun?.model_reports) && droppedFieldsModalRun.model_reports.length > 1 && (
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 4 }}>
              File
            </label>
            <select
              value={droppedFieldsModalModel || ''}
              onChange={(e) => selectDroppedFieldsModel(e.target.value)}
              style={{ ...inputStyle, width: '100%', maxWidth: 360, cursor: 'pointer' }}
            >
              {droppedFieldsModalRun.model_reports.map((entry) => (
                <option key={entry.model} value={entry.model}>
                  {cleanPbixLabel(entry.model)}
                </option>
              ))}
            </select>
          </div>
        )}
        {droppedFieldsModalEntries.length === 0 ? (
          <div style={{ padding: 24, color: 'var(--text-secondary)', fontSize: 13 }}>
            Nothing was dropped for this file.
          </div>
        ) : (
          <div style={{ padding: '8px 4px' }}>
            <DroppedFieldsPanel entries={droppedFieldsModalEntries} defaultExpanded />
          </div>
        )}
      </Modal>
    </div>
  );
}

function InfoCard({ label, value, mono = false }) {
  return (
    <div
      style={{
        border: '1px solid var(--border-main)',
        borderRadius: 8,
        padding: 12,
        background: 'var(--bg-surface)',
      }}
    >
      <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 12, color: 'var(--text-primary)', fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' : 'inherit' }}>
        {value || '—'}
      </div>
    </div>
  );
}
