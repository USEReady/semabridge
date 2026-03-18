/**
 * ProjectDetailModal — Runs history + config preview for a project.
 *
 * Props:
 *   project  — project object { project_id, name, adapter, target_type, ... }
 *   open     — boolean
 *   onClose  — function
 *   onDuplicate — called to start wizard with this project's config pre-filled
 */

import { useState, useEffect } from 'react';
import { X, Play, Copy, Clock, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';
import { api } from '../../utils/api';

const TABS = ['Runs', 'Config'];

function RunRow({ run }) {
  const duration = run.duration_ms
    ? run.duration_ms < 1000
      ? `${run.duration_ms}ms`
      : run.duration_ms < 60000
        ? `${(run.duration_ms / 1000).toFixed(1)}s`
        : `${Math.floor(run.duration_ms / 60000)}m ${Math.floor((run.duration_ms % 60000) / 1000)}s`
    : '—';

  const started = run.started_at ? new Date(run.started_at).toLocaleString() : '—';

  return (
    <tr style={{ borderBottom: '1px solid var(--border-subtle)' }}>
      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
        {run.run_id?.slice(0, 8)}…
      </td>
      <td style={{ padding: '8px 12px' }}>
        <StatusBadge status={run.status} />
      </td>
      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text-secondary)' }}>{started}</td>
      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text-tertiary)' }}>{duration}</td>
      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text-secondary)' }}>
        {run.source_type ? `${run.source_type} → ${run.target_type || '?'}` : '—'}
      </td>
      {run.error_message && (
        <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--color-error)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {run.error_message}
        </td>
      )}
    </tr>
  );
}

export default function ProjectDetailModal({ project, open, onClose, onDuplicate }) {
  const [tab, setTab] = useState('Runs');
  const [runs, setRuns] = useState([]);
  const [config, setConfig] = useState('');
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!open || !project) return;
    setTab('Runs');
    setRuns([]);
    setConfig('');
    setLoading(true);

    const pid = project.project_id;
    Promise.allSettled([
      api.getProjectRuns(pid),
      api.getProjectConfig(pid),
    ]).then(([runsRes, cfgRes]) => {
      if (runsRes.status === 'fulfilled') setRuns(runsRes.value ?? []);
      if (cfgRes.status === 'fulfilled') setConfig(cfgRes.value?.config_yaml ?? '');
    }).finally(() => setLoading(false));
  }, [open, project]);

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const result = await api.runProjectNow(project.project_id);
      setRuns(prev => [{ run_id: result.run_id, status: 'running', started_at: new Date().toISOString() }, ...prev]);
    } catch (err) {
      console.error('Run failed:', err);
    } finally {
      setRunning(false);
    }
  };

  if (!open || !project) return null;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 12,
          width: '100%', maxWidth: 820,
          maxHeight: '85vh',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 16px 48px rgba(0,0,0,0.22)',
        }}
      >
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>{project.name}</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
              {project.adapter || '—'} → {project.target_type || '—'}
            </div>
          </div>

          <button
            onClick={handleRunNow}
            disabled={running}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 14px', borderRadius: 7,
              background: 'var(--accent-blue)', border: 'none',
              color: '#fff', fontSize: 12, fontWeight: 600, cursor: running ? 'wait' : 'pointer',
              opacity: running ? 0.7 : 1,
            }}
          >
            {running ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={13} />}
            Run Now
          </button>

          {onDuplicate && (
            <button
              onClick={() => onDuplicate(project)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '6px 14px', borderRadius: 7,
                background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
                color: 'var(--text-primary)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              }}
            >
              <Copy size={13} />
              Duplicate
            </button>
          )}

          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 4 }}>
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border-main)', padding: '0 20px' }}>
          {TABS.map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                padding: '10px 16px',
                border: 'none', background: 'none',
                cursor: 'pointer',
                fontSize: 13, fontWeight: tab === t ? 600 : 400,
                color: tab === t ? 'var(--accent-blue)' : 'var(--text-secondary)',
                borderBottom: tab === t ? '2px solid var(--accent-blue)' : '2px solid transparent',
                marginBottom: -1,
              }}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Content */}
        <div style={{ flex: 1, overflow: 'auto', padding: 20 }}>
          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
              <Loader2 size={20} style={{ color: 'var(--text-tertiary)', animation: 'spin 1s linear infinite' }} />
            </div>
          ) : tab === 'Runs' ? (
            runs.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 40, color: 'var(--text-tertiary)', fontSize: 13 }}>
                No runs yet. Click "Run Now" to start the first run.
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border-main)' }}>
                      {['Run ID', 'Status', 'Started', 'Duration', 'Route'].map(h => (
                        <th key={h} style={{ padding: '6px 12px', textAlign: 'left', fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map(r => <RunRow key={r.run_id} run={r} />)}
                  </tbody>
                </table>
              </div>
            )
          ) : (
            <pre style={{
              background: 'var(--bg-input)',
              border: '1px solid var(--border-main)',
              borderRadius: 8,
              padding: 16,
              fontSize: 12,
              color: 'var(--text-primary)',
              overflowX: 'auto',
              fontFamily: 'monospace',
              lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
            }}>
              {config || '# No configuration stored yet.'}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
