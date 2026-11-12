import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import usePageCache from '../hooks/usePageCache';
import { Play, RefreshCw, ChevronDown } from 'lucide-react';
import SearchInput from '../components/common/SearchInput';
import DroppedFieldsPanel from '../components/common/DroppedFieldsPanel';
import Modal from '../components/common/Modal';
import ScheduleModal from '../components/projects/ScheduleModal';
import { ConversionBar } from '../components/projects/projectHealth';
import { matchesSmartQuery } from '../components/common/smartSearchQuery.js';
import { api } from '../utils/api';
import { buildMockRunLogs, getRunLogs, saveRunLogs } from '../utils/runLogs';
import { cleanPbixLabel } from '../utils/pbixLabel';
import RunDrawer, { StatusPill } from '../components/runs/RunDrawer';
import RunReportModal from '../components/runs/RunReportModal';
import { defaultReportModel, downloadRunMarkdown } from '../components/runs/runReportActions';
import SchedulesTab from '../components/runs/SchedulesTab';
import { DriftView } from './GovernanceSuitePage';
import useRunReportCounts, { clearRunCounts } from '../components/runs/useRunReportCounts';
import { formatAgo, formatDateTime, formatDuration, normStatus, parseIso, shortMessage } from '../components/runs/runsFormat';

const REFRESH_INTERVAL_MS = 4000;
const PAGE_SIZE = 50;
const DAY_MS = 24 * 60 * 60 * 1000;

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'Running' },
  { key: 'failed', label: 'Failed' },
  { key: 'success', label: 'Succeeded' },
];

const th = {
  textAlign: 'left', fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', padding: '10px 14px',
  borderBottom: '1px solid var(--border-main)', whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '0.04em',
};
const td = { padding: '11px 14px', fontSize: 13, color: 'var(--text-secondary)', verticalAlign: 'middle' };

export default function ProjectJobsPage() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [runs, setRuns] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadedAt, setLoadedAt] = useState(0);
  const [running, setRunning] = useState(false);
  const [runMenuOpen, setRunMenuOpen] = useState(false);
  const [scheduleDeletingId, setScheduleDeletingId] = useState('');
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState(null);
  const [runningScheduleId, setRunningScheduleId] = useState('');
  const [liveManifests, setLiveManifests] = useState({});
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [confirmClear, setConfirmClear] = useState(false);
  const [clearingLogs, setClearingLogs] = useState(false);
  const runMenuRef = useRef(null);
  const wide = useIsWide(1180);

  // Cached UI state — survives SPA navigation within the same tab.
  const [jobsCache, setJobsCache] = usePageCache('jobs-page', {
    search: '',
    searchUseRegex: false,
    statusFilter: 'all',
    expandedRunId: null,
    tab: 'history',
  });
  const { search, searchUseRegex, statusFilter, expandedRunId: openRunId, tab } = jobsCache;
  const setSearch = (v) => setJobsCache({ search: v });
  const setSearchUseRegex = (v) => setJobsCache({ searchUseRegex: v });
  const setStatusFilter = (v) => setJobsCache({ statusFilter: v });
  const setTab = (v) => setJobsCache({ tab: v });

  const openRun = useCallback((id) => {
    setJobsCache({ expandedRunId: id || null, ...(id ? { tab: 'history' } : {}) });
    setSearchParams(prev => {
      const next = new URLSearchParams(prev);
      if (id) next.set('run', id); else next.delete('run');
      return next;
    }, { replace: true });
  }, [setJobsCache, setSearchParams]);
  const closeRun = useCallback(() => openRun(null), [openRun]);

  // Deep links: ?run=<id> on first load.
  const initialRunParam = useRef(searchParams.get('run'));
  useEffect(() => {
    if (initialRunParam.current) setJobsCache({ expandedRunId: initialRunParam.current, tab: 'history' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Other pages navigate here with { runId } or { autoExpand }: open that run's drawer once.
  const handledNavKey = useRef(null);
  useEffect(() => {
    const st = location.state;
    if (!st || handledNavKey.current === location.key) return;
    if (st.runId) {
      handledNavKey.current = location.key;
      openRun(st.runId);
    } else if (st.autoExpand && runs.length > 0) {
      handledNavKey.current = location.key;
      openRun(runs[0].id);
    }
  }, [location.key, location.state, runs, openRun]);

  // While the open run is running, poll its per-model manifest.
  const openRunObj = runs.find(r => r.id === openRunId);
  const openRunIsRunning = normStatus(openRunObj?.status) === 'running';
  const openRunProject = openRunObj?.project_id;
  useEffect(() => {
    if (!openRunId || !openRunIsRunning || !openRunProject) return undefined;
    let cancelled = false;
    const fetchManifest = async () => {
      try {
        const manifest = await api.getRunModels(openRunProject, openRunId);
        if (!cancelled && Array.isArray(manifest) && manifest.length > 0) {
          setLiveManifests(prev => ({ ...prev, [openRunId]: manifest }));
        }
      } catch {
        // best effort polling while running
      }
    };
    fetchManifest();
    const interval = setInterval(fetchManifest, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [openRunId, openRunIsRunning, openRunProject]);

  useEffect(() => {
    let cancelled = false;
    let timerId;

    const loadPageData = async () => {
      try {
        const [runsData, schedulesData, projectsData] = await Promise.all([
          api.listJobRuns().catch(() => []),
          api.listJobSchedules().catch(() => []),
          api.listProjects().catch(() => []),
        ]);
        if (cancelled) return;
        const nextRuns = (Array.isArray(runsData) ? runsData : []).map((run) => {
          if (run?.id) saveRunLogs(run.id, getRunLogs(run));
          return run;
        });
        setRuns(nextRuns);
        setLoadedAt(Date.now());
        setSchedules(Array.isArray(schedulesData) ? schedulesData : []);
        setProjects(Array.isArray(projectsData) ? projectsData : []);
      } catch {
        if (cancelled) return;
        setRuns([]);
        setSchedules([]);
        setProjects([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    const poll = async () => {
      if (cancelled) return;
      await loadPageData();
      if (cancelled) return;
      timerId = window.setTimeout(poll, REFRESH_INTERVAL_MS);
    };
    poll();
    return () => { cancelled = true; window.clearTimeout(timerId); };
  }, []);

  useEffect(() => {
    if (!runMenuOpen) return undefined;
    const onDown = (e) => { if (runMenuRef.current && !runMenuRef.current.contains(e.target)) setRunMenuOpen(false); };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [runMenuOpen]);

  const addStartedRun = (result) => {
    if (!result) return;
    const nextRun = { ...result, id: result.id ?? result.run_id, status: 'running' };
    if (nextRun.id) saveRunLogs(nextRun.id, buildMockRunLogs(nextRun));
    setRuns(prev => [nextRun, ...prev.filter(r => r.id !== nextRun.id)]);
    if (nextRun.id) openRun(nextRun.id);
  };

  // "Run a project": all projects (the original Run now) or one picked from the menu.
  const handleRunNow = async (projectId = null) => {
    setRunMenuOpen(false);
    setRunning(true);
    try {
      addStartedRun(await api.triggerJob(projectId || undefined));
    } catch (err) {
      console.error('Trigger job failed:', err);
      alert(`Could not start the run: ${err.message || 'Unknown error'}`);
    } finally {
      setRunning(false);
    }
  };

  const handleRunScheduledProject = async (projectId) => {
    if (!projectId || runningScheduleId) return;
    setRunningScheduleId(String(projectId));
    try {
      addStartedRun(await api.runProjectNow(projectId));
    } catch (err) {
      console.error('Trigger project run failed:', err);
      alert(`Trigger run failed: ${err.message || 'Unknown error'}`);
    } finally {
      setRunningScheduleId('');
    }
  };

  const handleOpenScheduleModal = (schedule = null) => {
    setEditingSchedule(schedule);
    setScheduleModalOpen(true);
  };

  const handleScheduleSaved = (saved) => {
    setSchedules((prev) => {
      const filtered = prev.filter(s => String(s.project_id || s.id) !== String(saved.project_id || saved.id));
      return [saved, ...filtered];
    });
  };

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

  const handleClearLogs = async () => {
    if (clearingLogs) return;
    setClearingLogs(true);
    try {
      await api.clearJobRuns();
      try { localStorage.removeItem('semabridge_run_logs_v1'); } catch { /* ignore */ }
      clearRunCounts();
      setRuns([]);
      setLiveManifests({});
      closeRun();
    } catch (err) {
      console.error('Clear runs failed:', err);
    } finally {
      setClearingLogs(false);
      setConfirmClear(false);
    }
  };

  // Report viewer (Summary / Raw Report, per-file picker, download).
  const [report, setReport] = useState(null); // { run, model, view, key }
  const openReport = (run, model = null, view = 'summary') => {
    setReport({ run, model: model ? defaultReportModel(run, model) : defaultReportModel(run), view, key: Date.now() });
  };
  const downloadReport = (run) => {
    downloadRunMarkdown(run, null).catch(err => console.error('Failed to download report:', err));
  };

  // Dropped fields viewer, derived from run.results (no fetch).
  const [droppedFieldsModalRun, setDroppedFieldsModalRun] = useState(null);
  const [droppedFieldsModalModel, setDroppedFieldsModalModel] = useState(null);
  const openDroppedFieldsModal = (run, preselectModel = null) => {
    const modelEntries = Array.isArray(run.model_reports) ? run.model_reports : [];
    setDroppedFieldsModalRun(run);
    setDroppedFieldsModalModel(preselectModel || (modelEntries.length > 1 ? modelEntries[0].model : null));
  };
  const closeDroppedFieldsModal = () => {
    setDroppedFieldsModalRun(null);
    setDroppedFieldsModalModel(null);
  };
  const droppedFieldsModalEntries = useMemo(() => {
    const results = Array.isArray(droppedFieldsModalRun?.results) ? droppedFieldsModalRun.results : [];
    const scoped = droppedFieldsModalModel ? results.filter(r => r?.model === droppedFieldsModalModel) : results;
    let list = scoped.flatMap(r => (Array.isArray(r?.dropped_entities) ? r.dropped_entities : []).map(entry => ({ ...entry, model: r?.model })));
    if (!list.length && droppedFieldsModalRun) {
      const fallback = (
        (Array.isArray(droppedFieldsModalRun.dropped_entities) && droppedFieldsModalRun.dropped_entities) ||
        (Array.isArray(droppedFieldsModalRun.summary?.dropped_entities) && droppedFieldsModalRun.summary.dropped_entities) ||
        (Array.isArray(droppedFieldsModalRun.summary?.drop_ledger) && droppedFieldsModalRun.summary.drop_ledger) ||
        []
      );
      list = fallback.map(entry => ({ ...entry, model: droppedFieldsModalRun.source_model_name || droppedFieldsModalRun.model || 'Model' }));
    }
    return list;
  }, [droppedFieldsModalRun, droppedFieldsModalModel]);

  // Header numbers: last 30 days.
  const stats = useMemo(() => {
    const since = loadedAt - 30 * DAY_MS;
    const recent = runs.filter(r => (parseIso(r.started_at)?.getTime() || 0) >= since);
    const finished = recent.filter(r => ['success', 'failed'].includes(normStatus(r.status)));
    const ok = finished.filter(r => normStatus(r.status) === 'success').length;
    return { recent: recent.length, pct: finished.length ? Math.round((100 * ok) / finished.length) : null };
  }, [runs, loadedAt]);

  // "#N" labels: position in time order across the loaded history.
  const runNumbers = useMemo(() => {
    const sorted = [...runs].sort((a, b) => (parseIso(a.started_at)?.getTime() || 0) - (parseIso(b.started_at)?.getTime() || 0));
    return new Map(sorted.map((r, i) => [r.id, i + 1]));
  }, [runs]);

  const projectName = (run) => {
    if (run.project_name && run.project_name !== 'Project run') return run.project_name;
    const p = projects.find(x => String(x.id) === String(run.project_id));
    return p?.name || p?.display_name || run.project_id || 'Project run';
  };

  const filteredRuns = runs.filter((run) => {
    const matchSearch = !search || matchesSmartQuery(
      `${String(run.id || '')} #${runNumbers.get(run.id) || ''} ${projectName(run)}`,
      search,
      searchUseRegex,
    );
    const matchStatus = statusFilter === 'all' || normStatus(run.status) === statusFilter;
    return matchSearch && matchStatus;
  });
  const visibleRuns = filteredRuns.slice(0, visibleCount);
  const drawerRun = openRunObj ? { ...openRunObj, project_name: projectName(openRunObj) } : null;
  const countsByRun = useRunReportCounts(drawerRun ? [...visibleRuns, drawerRun] : visibleRuns);

  const tabBtn = (key, label, extra) => (
    <button
      type="button"
      onClick={() => setTab(key)}
      style={{
        padding: '8px 2px', marginRight: 20, border: 'none', background: 'transparent', cursor: 'pointer',
        fontSize: 13, fontWeight: 600,
        color: tab === key ? 'var(--text-primary)' : 'var(--text-tertiary)',
        borderBottom: `2px solid ${tab === key ? 'var(--accent-blue)' : 'transparent'}`,
        marginBottom: -1,
      }}
    >
      {label}{extra !== undefined && <span style={{ marginLeft: 6, color: 'var(--text-tertiary)', fontWeight: 500 }}>{extra}</span>}
    </button>
  );

  const renderResult = (run) => {
    const st = normStatus(run.status);
    if (st === 'running') return <span style={{ color: 'var(--text-tertiary)' }}>running…</span>;
    const c = countsByRun[run.id];
    if (c?.counts) return <div style={{ width: 150 }}><ConversionBar counts={c.counts} /></div>;
    if (st === 'failed') {
      const msg = run.error || run.message;
      return <span title={msg} style={{ color: 'var(--text-secondary)' }}>{shortMessage(msg, 48) || 'Failed'}</span>;
    }
    return <span style={{ color: 'var(--text-tertiary)' }}>{c ? '—' : ''}</span>;
  };

  return (
    <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1400, margin: openRunId && wide ? '0 520px 0 auto' : '0 auto' }} className="md:px-10">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14, flexWrap: 'wrap', marginBottom: 14 }}>
        <div style={{ flex: 1, minWidth: 220 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0, color: 'var(--text-primary)', lineHeight: 1.2 }}>Runs</h1>
          <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginTop: 6 }}>
            {stats.recent
              ? `${stats.recent} in the last 30 days${stats.pct !== null ? ` · ${stats.pct}% succeeded` : ''}`
              : 'No runs in the last 30 days'}
          </div>
        </div>
        <div ref={runMenuRef} style={{ position: 'relative', display: 'inline-flex' }}>
          <button
            type="button"
            onClick={() => setRunMenuOpen(v => !v)}
            disabled={running}
            aria-haspopup="menu"
            aria-expanded={runMenuOpen}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 8,
              background: 'var(--accent-blue)', color: '#fff', border: 'none', fontSize: 13, fontWeight: 600,
              cursor: running ? 'wait' : 'pointer', boxShadow: '0 2px 8px rgba(99,102,241,0.25)',
            }}
          >
            {running ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
            {running ? 'Starting…' : 'Run a project'}
            <ChevronDown size={14} />
          </button>
          {runMenuOpen && (
            <div
              role="menu"
              className="custom-scrollbar"
              style={{
                position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 30, minWidth: 240, maxHeight: 320, overflowY: 'auto',
                background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 10,
                boxShadow: 'var(--shadow-md)', padding: 4,
              }}
            >
              <MenuItem onClick={() => handleRunNow(null)} label="Run all scheduled projects now" hint="Same as before: every project that has a schedule" />
              {projects.length > 0 && <div style={{ height: 1, background: 'var(--border-main)', margin: '4px 0' }} />}
              {projects.map(p => (
                <MenuItem key={p.id} onClick={() => handleRunNow(p.id)} label={p.name || p.display_name || p.id} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border-main)', marginBottom: 16 }}>
        {tabBtn('history', 'History')}
        {tabBtn('schedules', 'Schedules', schedules.length)}
        {tabBtn('drift', 'Drift Approvals')}
      </div>

      {tab === 'drift' ? (
        <DriftView />
      ) : tab === 'schedules' ? (
        <SchedulesTab
          schedules={schedules}
          onAdd={() => handleOpenScheduleModal(null)}
          onEdit={handleOpenScheduleModal}
          onDelete={handleDeleteSchedule}
          onRun={handleRunScheduledProject}
          runningScheduleId={runningScheduleId}
          deletingId={scheduleDeletingId}
        />
      ) : (
        <>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            <SearchInput
              value={search}
              onChange={setSearch}
              useRegex={searchUseRegex}
              onToggleRegex={setSearchUseRegex}
              allowRegex
              helperText={searchUseRegex ? 'Regex enabled' : ''}
              placeholder="Filter by project or run…"
              width={280}
            />
            <div role="tablist" aria-label="Status" style={{ display: 'inline-flex', padding: 2, borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
              {FILTERS.map(f => (
                <button
                  key={f.key}
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === f.key}
                  onClick={() => setStatusFilter(f.key)}
                  style={{
                    padding: '5px 12px', borderRadius: 6, border: 'none', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    background: statusFilter === f.key ? 'var(--bg-surface-raised)' : 'transparent',
                    color: statusFilter === f.key ? 'var(--text-primary)' : 'var(--text-tertiary)',
                    boxShadow: statusFilter === f.key ? 'var(--shadow-sm)' : 'none',
                  }}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <span style={{ flex: 1 }} />
            {confirmClear ? (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-secondary)', paddingTop: 6 }}>
                Clear all run history?
                <button type="button" onClick={handleClearLogs} disabled={clearingLogs} style={{ ...linkBtn, color: 'var(--color-error)' }}>
                  {clearingLogs ? 'Clearing…' : 'Clear'}
                </button>
                <button type="button" onClick={() => setConfirmClear(false)} style={linkBtn}>Cancel</button>
              </span>
            ) : (
              <button type="button" onClick={() => setConfirmClear(true)} disabled={runs.length === 0} style={{ ...linkBtn, paddingTop: 6, opacity: runs.length ? 1 : 0.5 }}>
                Clear runs
              </button>
            )}
          </div>

          <div className="custom-scrollbar" style={{ border: '1px solid var(--border-main)', borderRadius: 12, background: 'var(--bg-surface)', overflowX: 'auto' }}>
            {loading ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>Loading runs…</div>
            ) : filteredRuns.length === 0 ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                {runs.length ? 'No runs match this filter.' : 'No runs yet. Use Run a project to start the first one.'}
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
                <thead>
                  <tr>
                    <th style={th}>Run</th>
                    <th style={th}>Project</th>
                    <th style={th}>Started</th>
                    <th style={th}>Took</th>
                    <th style={{ ...th, width: 180 }}>Result</th>
                    <th style={th}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRuns.map((run, i) => {
                    const selected = run.id === openRunId;
                    return (
                      <tr
                        key={run.id ?? i}
                        onClick={() => (selected ? closeRun() : openRun(run.id))}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openRun(run.id); } }}
                        tabIndex={0}
                        aria-selected={selected}
                        style={{
                          cursor: 'pointer',
                          borderTop: i ? '1px solid var(--border-main)' : 'none',
                          background: selected ? 'rgba(99, 102, 241, 0.08)' : 'transparent',
                          boxShadow: selected ? 'inset 3px 0 0 var(--accent-blue)' : 'none',
                        }}
                        className="hover:bg-surface-hover"
                      >
                        <td style={{ ...td, color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums' }} title={String(run.id || '')}>
                          #{runNumbers.get(run.id) || '?'}
                        </td>
                        <td style={{ ...td, color: 'var(--text-primary)', fontWeight: 600, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {projectName(run)}
                        </td>
                        <td style={{ ...td, whiteSpace: 'nowrap' }} title={formatDateTime(run.started_at)}>{formatAgo(run.started_at)}</td>
                        <td style={{ ...td, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>{formatDuration(run.duration_ms)}</td>
                        <td style={{ ...td, fontSize: 12, maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{renderResult(run)}</td>
                        <td style={td}><StatusPill status={run.status} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            {filteredRuns.length > visibleCount && (
              <div style={{ borderTop: '1px solid var(--border-main)', padding: 10, textAlign: 'center' }}>
                <button type="button" onClick={() => setVisibleCount(n => n + PAGE_SIZE)} style={linkBtn}>
                  Show more ({filteredRuns.length - visibleCount} older)
                </button>
              </div>
            )}
          </div>
        </>
      )}

      {openRunId && (
        <RunDrawer
          key={openRunId}
          runId={openRunId}
          run={drawerRun}
          runNumber={runNumbers.get(openRunId)}
          liveManifest={liveManifests[openRunId]}
          counts={countsByRun[openRunId]}
          onClose={closeRun}
          onOpenReport={openReport}
          onDownload={downloadReport}
          onOpenDropped={openDroppedFieldsModal}
          modalOpen={Boolean(report || droppedFieldsModalRun || scheduleModalOpen)}
        />
      )}

      {report && (
        <RunReportModal
          key={report.key}
          run={report.run}
          initialModel={report.model}
          initialView={report.view}
          onClose={() => setReport(null)}
        />
      )}

      <Modal
        open={Boolean(droppedFieldsModalRun)}
        onClose={closeDroppedFieldsModal}
        title={`Dropped fields · ${droppedFieldsModalRun?.id || droppedFieldsModalRun?.run_id || ''}${droppedFieldsModalModel ? ` · ${cleanPbixLabel(droppedFieldsModalModel)}` : ''}`}
        size="lg"
        footer={
          <div style={{ display: 'flex', justifyContent: 'flex-end', width: '100%' }}>
            <button
              type="button"
              onClick={closeDroppedFieldsModal}
              style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, background: 'var(--bg-surface-raised)', color: 'var(--text-secondary)', border: '1px solid var(--border-main)', cursor: 'pointer' }}
            >
              Close
            </button>
          </div>
        }
      >
        {Array.isArray(droppedFieldsModalRun?.model_reports) && droppedFieldsModalRun.model_reports.length > 1 && (
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 4 }}>File</label>
            <select
              value={droppedFieldsModalModel || ''}
              onChange={(e) => setDroppedFieldsModalModel(e.target.value)}
              style={{ background: 'var(--bg-input)', border: '1px solid var(--border-main)', borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px', fontSize: 13, width: '100%', maxWidth: 360, cursor: 'pointer' }}
            >
              {droppedFieldsModalRun.model_reports.map(entry => (
                <option key={entry.model} value={entry.model}>{cleanPbixLabel(entry.model)}</option>
              ))}
            </select>
          </div>
        )}
        {droppedFieldsModalEntries.length === 0 ? (
          <div style={{ padding: 24, color: 'var(--text-secondary)', fontSize: 13 }}>Nothing was dropped for this file.</div>
        ) : (
          <div style={{ padding: '8px 4px' }}>
            <DroppedFieldsPanel entries={droppedFieldsModalEntries} defaultExpanded />
          </div>
        )}
      </Modal>

      <ScheduleModal
        open={scheduleModalOpen}
        onClose={() => setScheduleModalOpen(false)}
        onSaved={handleScheduleSaved}
        projects={projects}
        initialSchedule={editingSchedule}
      />
    </div>
  );
}

const linkBtn = {
  border: 'none', background: 'transparent', padding: 0, cursor: 'pointer',
  fontSize: 12, fontWeight: 600, color: 'var(--text-tertiary)',
};

function MenuItem({ label, hint, onClick }) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="hover:bg-surface-hover"
      style={{
        display: 'block', width: '100%', textAlign: 'left', padding: '7px 10px', borderRadius: 6, border: 'none',
        background: 'transparent', cursor: 'pointer', color: 'var(--text-primary)', fontSize: 13,
      }}
    >
      {label}
      {hint && <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 1 }}>{hint}</div>}
    </button>
  );
}

// Leave room for the drawer beside the table when the window is wide enough.
function useIsWide(minWidth) {
  const get = () => (typeof window !== 'undefined' ? window.innerWidth >= minWidth : true);
  const [wide, setWide] = useState(get);
  useEffect(() => {
    const onResize = () => setWide(window.innerWidth >= minWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [minWidth]);
  return wide;
}
