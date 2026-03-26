import { useState, useEffect } from 'react';
import { Play, RefreshCw, Clock, RotateCcw } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import DataTable from '../components/common/DataTable';
import StatusBadge from '../components/common/StatusBadge';
import SearchInput from '../components/common/SearchInput';
import { api } from '../utils/api';

const TIMEZONES = ['UTC', 'US/Eastern (EST)', 'US/Pacific (PST)', 'Europe/London', 'Asia/Singapore'];
const SCHEDULE_TYPES = ['Manual Trigger Only', 'Cron Expression', 'Time Picker'];

const DEFAULT_CONFIG = { schedule_type: 'Manual Trigger Only', cron: '0 0 * * *', timezone: 'UTC' };

function formatDuration(ms) {
  if (!ms) return '—';
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

export default function ProjectJobsPage() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [configSaving, setConfigSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');

  // Load runs + config
  useEffect(() => {
    (async () => {
      try {
        const [runsData, jobConfig] = await Promise.allSettled([
          api.listJobRuns(),
          api.getJobConfig(),
        ]);
        if (runsData.status === 'fulfilled') setRuns(runsData.value ?? []);
        if (jobConfig.status === 'fulfilled' && jobConfig.value) setConfig(prev => ({ ...prev, ...jobConfig.value }));
      } catch {
        setRuns([]);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const result = await api.triggerJob();
      if (result) {
        setRuns(prev => [{ ...result, status: 'running' }, ...prev]);
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
    } catch (err) {
      console.error('Save config failed:', err);
    } finally {
      setConfigSaving(false);
    }
  };

  const filteredRuns = runs.filter(r => {
    const matchSearch = !search || (r.id || '').includes(search) || (r.project_name || '').toLowerCase().includes(search.toLowerCase());
    const matchStatus = statusFilter === 'all' || r.status === statusFilter;
    return matchSearch && matchStatus;
  });

  const columns = [
    {
      key: 'id',
      label: 'Run ID',
      render: (val) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-secondary)' }}>
          {String(val ?? '—').substring(0, 12)}
        </span>
      ),
    },
    { key: 'project_name', label: 'Project' },
    {
      key: 'schedule',
      label: 'Schedule',
      render: (val) => <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{val || 'Manual'}</span>,
    },
    {
      key: 'status',
      label: 'Status',
      align: 'center',
      render: (val) => <StatusBadge status={val || 'draft'} />,
    },
    {
      key: 'duration_ms',
      label: 'Duration',
      align: 'right',
      render: (val) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-secondary)' }}>
          {formatDuration(val)}
        </span>
      ),
    },
    {
      key: 'started_at',
      label: 'Started',
      render: (val) => <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{formatDate(val)}</span>,
    },
  ];

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

      {/* Execution Schedule Card */}
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
              {SCHEDULE_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
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
                onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
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
              {TIMEZONES.map(t => <option key={t} value={t}>{t}</option>)}
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
                    width: 8, height: 8, borderRadius: '50%',
                    background: config.schedule_type !== 'Manual Trigger Only'
                      ? 'var(--color-success)' : 'var(--text-tertiary)',
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

      {/* Run History */}
      <div>
        <div className="flex items-center gap-3 mb-4">
          <h2 className="text-primary font-semibold" style={{ fontSize: 14, margin: 0 }}>Run History</h2>
          <div className="flex-1" />
          <SearchInput value={search} onChange={setSearch} placeholder="Filter runs…" width={220} />
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

        <DataTable
          columns={columns}
          data={filteredRuns}
          loading={loading}
          emptyText="No job runs yet. Click 'Run Now' to trigger your first execution."
          pageSize={8}
        />
      </div>
    </div>
  );
}
