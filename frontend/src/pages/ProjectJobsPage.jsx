import { useState, useEffect } from 'react';
import { Play, RefreshCw, Clock, CalendarClock, RotateCcw, ChevronDown, ChevronRight, BarChart3, Cloud, Snowflake, Database, Link2 } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';
import SearchInput from '../components/common/SearchInput';
import { matchesSmartQuery } from '../components/common/SmartSearchBar';
import { api } from '../utils/api';
import { buildMockRunLogs, getRunLogs, saveRunLogs } from '../utils/runLogs';

const TIMEZONES = ['UTC', 'US/Eastern (EST)', 'US/Pacific (PST)', 'Europe/London', 'Asia/Singapore'];
const SCHEDULE_TYPES = ['Manual Trigger Only', 'Cron Expression', 'Time Picker'];

const DEFAULT_CONFIG = { schedule_type: 'Manual Trigger Only', cron: '0 0 * * *', timezone: 'UTC' };
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

export default function ProjectJobsPage() {
  const [runs, setRuns] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [configSaving, setConfigSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [scheduleDeletingId, setScheduleDeletingId] = useState('');
  const [search, setSearch] = useState('');
  const [searchUseRegex, setSearchUseRegex] = useState(false);
  const [statusFilter, setStatusFilter] = useState('all');
  const [expandedRunId, setExpandedRunId] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const loadPageData = async ({ includeConfig = false } = {}) => {
      try {
        const [runsData, schedulesData, jobConfig] = await Promise.all([
          api.listJobRuns().catch(() => []),
          api.listJobSchedules().catch(() => []),
          includeConfig ? api.getJobConfig().catch(() => null) : Promise.resolve(null),
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

        if (includeConfig && jobConfig) {
          setConfig(prev => ({ ...prev, ...jobConfig }));
        }
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

    loadPageData({ includeConfig: true });
    const intervalId = window.setInterval(() => {
      loadPageData({ includeConfig: false });
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

  const handleSaveConfig = async () => {
    setConfigSaving(true);
    try {
      await api.updateJobConfig(config);
      const refreshedSchedules = await api.listJobSchedules().catch(() => []);
      setSchedules(Array.isArray(refreshedSchedules) ? refreshedSchedules : []);
    } catch (err) {
      console.error('Save config failed:', err);
    } finally {
      setConfigSaving(false);
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
    <div style={{ padding: '28px 32px', minHeight: '100%' }}>
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
          <Clock size={16} style={{ color: 'var(--accent-blue)' }} />
          <h2 className="text-primary font-semibold" style={{ fontSize: 14, margin: 0 }}>Execution Schedule</h2>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
              Schedule Type
            </label>
            <select
              value={config.schedule_type}
              onChange={e => setConfig(c => ({ ...c, schedule_type: e.target.value }))}
              style={{ ...inputStyle, width: '100%', cursor: 'pointer' }}
            >
              {SCHEDULE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          {config.schedule_type === 'Cron Expression' && (
            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Cron Expression
              </label>
              <input
                type="text"
                value={config.cron}
                onChange={e => setConfig(c => ({ ...c, cron: e.target.value }))}
                placeholder="0 0 * * *"
                style={{ ...inputStyle, width: '100%', fontFamily: 'monospace' }}
              />
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>e.g. every day at midnight</p>
            </div>
          )}

          <div>
            <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
              Timezone
            </label>
            <select
              value={config.timezone}
              onChange={e => setConfig(c => ({ ...c, timezone: e.target.value }))}
              style={{ ...inputStyle, width: '100%', cursor: 'pointer' }}
            >
              {TIMEZONES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div className="flex items-end">
            <div className="w-full">
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Schedule Status
              </label>
              <div className="flex items-center gap-2">
                <span
                  className={config.schedule_type !== 'Manual Trigger Only' ? 'animate-pulse' : ''}
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: config.schedule_type !== 'Manual Trigger Only'
                      ? 'var(--color-success)'
                      : 'var(--text-tertiary)',
                    flexShrink: 0,
                  }}
                />
                <span className="text-secondary text-sm">
                  {config.schedule_type !== 'Manual Trigger Only' ? 'Active' : 'Manual only'}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end mt-4">
          <button
            onClick={handleSaveConfig}
            disabled={configSaving}
            className="flex items-center gap-2 rounded-lg text-sm font-semibold px-4 py-2"
            style={{
              background: configSaving ? 'var(--bg-surface-raised)' : 'var(--accent-blue)',
              color: configSaving ? 'var(--text-tertiary)' : '#fff',
              border: 'none',
              cursor: configSaving ? 'not-allowed' : 'pointer',
            }}
          >
            {configSaving ? <RefreshCw size={13} className="animate-spin" /> : <RotateCcw size={13} />}
            {configSaving ? 'Saving…' : 'Update Configuration'}
          </button>
        </div>
      </div>

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
                style={{
                  border: '1px solid var(--border-main)',
                  borderRadius: 10,
                  padding: 14,
                  display: 'grid',
                  gridTemplateColumns: 'minmax(0, 1.2fr) minmax(120px, 0.9fr) minmax(180px, 1fr) auto',
                  gap: 12,
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

      <div>
        <div className="flex items-center gap-3 mb-4">
          <h2 className="text-primary font-semibold" style={{ fontSize: 14, margin: 0 }}>Run History</h2>
          <div className="flex-1" />
          <SearchInput
            value={search}
            onChange={setSearch}
            useRegex={searchUseRegex}
            onToggleRegex={setSearchUseRegex}
            allowRegex
            helperText={searchUseRegex ? 'Regex examples: ^run_\\d+$ or failed|running' : 'Tip: enable regex to use patterns like ^run_\\d+$'}
            placeholder="Filter runs…"
            width={220}
          />
          <select
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
            style={{
              ...inputStyle,
              padding: '7px 10px',
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            <option value="all">All Status</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="running">Running</option>
          </select>
        </div>

        <div
          className="rounded-xl overflow-hidden"
          style={{ border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}
        >
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
                      gridTemplateColumns: 'auto minmax(120px, 1fr) minmax(120px, 1.1fr) minmax(100px, 0.9fr) minmax(120px, 1fr) auto',
                      gap: 12,
                      alignItems: 'center',
                      textAlign: 'left',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      {isExpanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-secondary)' }}>
                        {String(run.id ?? '-').substring(0, 12)}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600 }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                        {sourceIcon(run.source_type)}
                      </span>
                      <span>{run.project_name || 'Project run'}</span>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{run.schedule || 'Manual'}</div>
                    <div style={{ display: 'flex', justifyContent: 'center' }}>
                      <StatusBadge status={run.status || 'draft'} />
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                      {formatDate(run.started_at)}
                    </div>
                    <div style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-secondary)' }}>
                      {formatDuration(run.duration_ms)}
                    </div>
                  </button>

                  {isExpanded && (
                    <div style={{ padding: '0 16px 16px 16px', background: 'var(--bg-input)', borderTop: '1px solid var(--border-main)' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12, padding: '14px 0' }}>
                        <InfoCard label="Run ID" value={String(run.id ?? '-')} mono />
                        <InfoCard label="Started" value={formatDate(run.started_at)} />
                        <InfoCard label="Duration" value={formatDuration(run.duration_ms)} mono />
                        <InfoCard label="Status" value={String(run.status || 'draft')} />
                        <InfoCard label="Message" value={run.message || run.error || '—'} />
                      </div>

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
