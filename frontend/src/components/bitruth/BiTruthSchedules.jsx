import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Plus, Trash2, Calendar, Power } from 'lucide-react';
import { authFetch } from '../../utils/api';

const CARD = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 10,
  padding: 16,
};

const INPUT = {
  padding: '6px 10px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
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

export default function BiTruthSchedules({ projectId }) {
  const [schedules, setSchedules] = useState([]);
  const [cron, setCron] = useState('0 2 * * *'); // default 2 AM daily
  const [view, setView] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const loadSchedules = useCallback(async () => {
    if (!projectId) return;
    try {
      const res = await authFetch(`/api/bi-truth/schedules?project_id=${encodeURIComponent(projectId)}`);
      if (res.ok) {
        const data = await res.json();
        setSchedules(data.schedules || []);
      }
    } catch {
      // ignore
    }
  }, [projectId]);

  useEffect(() => {
    let ignore = false;
    if (!projectId) return;
    const fetchScheds = async () => {
      try {
        const res = await authFetch(`/api/bi-truth/schedules?project_id=${encodeURIComponent(projectId)}`);
        if (res.ok && !ignore) {
          const data = await res.json();
          setSchedules(data.schedules || []);
        }
      } catch {
        // ignore
      }
    };
    fetchScheds();
    return () => {
      ignore = true;
    };
  }, [projectId]);

  const handleAdd = async (e) => {
    e?.preventDefault();
    if (!cron.trim()) return;
    setBusy(true);
    setError('');
    try {
      const res = await authFetch('/api/bi-truth/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          cron_expression: cron.trim(),
          view: view.trim() || undefined,
        }),
      });
      if (res.ok) {
        setCron('0 2 * * *');
        await loadSchedules();
      } else {
        const d = await res.json().catch(() => ({}));
        setError(d?.detail || 'Failed to create schedule');
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id) => {
    try {
      const res = await authFetch(`/api/bi-truth/schedules/${id}`, { method: 'DELETE' });
      if (res.ok) {
        setSchedules((prev) => prev.filter((s) => s.id !== id));
      }
    } catch {
      // ignore
    }
  };

  return (
    <div style={CARD}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Clock size={16} style={{ color: 'var(--accent-blue)' }} />
          <strong style={{ fontSize: 13.5 }}>Automated BI Truth Verification Schedules</strong>
        </div>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
          Runs suites on APScheduler to refresh expected benchmark freshness
        </span>
      </div>

      {schedules.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '8px 0' }}>
          No automated truth suite schedules configured for this project.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
          {schedules.map((s) => (
            <div
              key={s.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '6px 12px',
                borderRadius: 6,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                fontSize: 12,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <Calendar size={13} style={{ color: 'var(--accent-blue)' }} />
                <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{s.cron_expression}</span>
                {s.view && <span style={{ color: 'var(--text-secondary)' }}>View: {s.view}</span>}
                <span style={{ fontSize: 10.5, color: s.is_active ? 'var(--color-success)' : 'var(--text-tertiary)' }}>
                  {s.is_active ? '● Active' : '○ Disabled'}
                </span>
              </div>
              <button
                type="button"
                style={{ ...BTN, padding: 3, border: 'none', background: 'transparent', color: 'var(--color-error)' }}
                onClick={() => handleDelete(s.id)}
                title="Delete schedule"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add form */}
      <form onSubmit={handleAdd} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="text"
          placeholder="Cron Expression (e.g. 0 2 * * *)"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          style={{ ...INPUT, flex: '1 1 180px' }}
        />
        <input
          type="text"
          placeholder="Optional View Filter"
          value={view}
          onChange={(e) => setView(e.target.value)}
          style={{ ...INPUT, flex: '1 1 140px' }}
        />
        <button
          type="submit"
          disabled={busy || !cron.trim()}
          style={{ ...BTN, background: 'var(--accent-blue)', color: '#fff', borderColor: 'var(--accent-blue)' }}
        >
          <Plus size={13} /> Add Schedule
        </button>
      </form>
      {error && <div style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 4 }}>{error}</div>}
    </div>
  );
}
