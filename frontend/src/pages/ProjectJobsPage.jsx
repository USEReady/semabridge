import { useState, useEffect } from 'react';
import { Play, RefreshCw, Clock, CalendarClock, ChevronDown, ChevronRight, BarChart3, Cloud, Snowflake, Database, Link2 } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';
import SearchInput from '../components/common/SearchInput';
import { matchesSmartQuery } from '../components/common/SmartSearchBar';
import { api } from '../utils/api';
import { buildMockRunLogs, getRunLogs, saveRunLogs } from '../utils/runLogs';

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
      setStats({ total_after: 0, removed: 0, preserved: 0, source_mapped: 0 });
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
        let preserved = 0;
        const totalAfter = diff.metadata_diff?.snapshot_b?.model_count || 0;
        const totalBefore = diff.metadata_diff?.snapshot_a?.model_count || 0;

        if (diff.models) {
          for (const mInfo of Object.values(diff.models)) {
             if (mInfo.status === 'removed') removed++;
             else if (mInfo.status === 'added') added++;
             else preserved++; // unchanged or preserved
          }
        }

        const kept = totalBefore - removed;
        setStats({
          total_after: totalAfter,
          removed: removed,
          preserved: kept,
          source_mapped: totalAfter - kept
        });
      })
      .catch(() => {
        if (!isMounted) return;
        setStats({ total_after: 0, removed: 0, preserved: 0, source_mapped: 0 });
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
              <span style={{ color: 'var(--text-secondary)' }}>Target-only models kept:</span>
              <span style={{ fontWeight: 600 }}>{stats.preserved}</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Source models deployed:</span>
              <span style={{ fontWeight: 600 }}>{stats.source_mapped}</span>
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
          ? `UPSERT mode preserved ${stats.preserved} target-only models.`
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
  const [search, setSearch] = useState('');
  const [searchUseRegex, setSearchUseRegex] = useState(false);
  const [statusFilter, setStatusFilter] = useState('all');
  const [expandedRunId, setExpandedRunId] = useState(null);

  useEffect(() => {
    let cancelled = false;

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

    loadPageData();
    const intervalId = window.setInterval(() => {
      loadPageData();
    }, REFRESH_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
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
