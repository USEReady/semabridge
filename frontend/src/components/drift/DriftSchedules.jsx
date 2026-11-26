import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Plus, Trash2, AlertCircle, CheckCircle2, ShieldCheck, Bell } from 'lucide-react';
import { authFetch } from '../../utils/api';

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
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

/**
 * Validates that a webhook URL is HTTPS-only if supplied.
 */
export function isValidHttpsWebhook(url) {
  if (!url || !url.trim()) return true; // optional
  const trimmed = url.trim();
  try {
    const parsed = new URL(trimmed);
    return parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * DriftSchedules Component
 * Displays automated drift monitoring schedules, next/last run times,
 * and allows adding new schedules with strict HTTPS webhook validation.
 */
export default function DriftSchedules({
  projectId = '',
  onScheduleChange,
  compact = false,
}) {
  const [schedules, setSchedules] = useState([]);
  const [intervalMins, setIntervalMins] = useState(60);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  const loadSchedules = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      const res = await authFetch(`/api/drift/schedules${qs}`);
      if (res.ok) {
        const data = await res.json();
        setSchedules(data.schedules || []);
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || 'Failed to load schedules');
      }
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    loadSchedules();
  }, [loadSchedules]);

  const handleAddSchedule = async (e) => {
    if (e) e.preventDefault();
    setError('');
    setSuccessMsg('');

    const mins = Number(intervalMins);
    if (!mins || mins < 5) {
      setError('Interval must be at least 5 minutes.');
      return;
    }

    if (webhookUrl && webhookUrl.trim()) {
      if (!isValidHttpsWebhook(webhookUrl)) {
        setError('Webhook URL must use secure HTTPS (e.g. https://webhook.site/...)');
        return;
      }
    }

    try {
      const res = await authFetch('/api/drift/schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          interval_minutes: mins,
          webhook_url: webhookUrl.trim() || undefined,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        setError(errData.detail || 'Failed to create schedule');
        return;
      }

      setWebhookUrl('');
      setSuccessMsg(`Schedule created: every ${mins} minutes.`);
      setTimeout(() => setSuccessMsg(''), 4000);
      await loadSchedules();
      if (onScheduleChange) onScheduleChange();
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const handleDeleteSchedule = async (scheduleId) => {
    if (!window.confirm('Delete this automated drift check schedule?')) return;
    try {
      const res = await authFetch(`/api/drift/schedules/${scheduleId}`, { method: 'DELETE' });
      if (res.ok) {
        await loadSchedules();
        if (onScheduleChange) onScheduleChange();
      } else {
        const err = await res.json().catch(() => ({}));
        setError(err.detail || 'Failed to delete schedule');
      }
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  return (
    <div style={compact ? undefined : CARD}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Clock size={16} style={{ color: 'var(--accent-blue, #3b82f6)' }} />
          <strong style={{ fontSize: 13.5 }}>
            Automated Drift Schedules {schedules.length > 0 ? `(${schedules.length})` : ''}
          </strong>
        </div>
        {projectId && (
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
            Scope: {projectId}
          </span>
        )}
      </div>

      {/* Schedule Configuration Form */}
      <form onSubmit={handleAddSchedule} style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            style={{ ...INPUT, width: 80 }}
            type="number"
            min={5}
            step={5}
            value={intervalMins}
            onChange={(e) => setIntervalMins(e.target.value)}
            title="Interval in minutes (min 5)"
            placeholder="60"
            aria-label="Interval minutes"
          />
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>min</span>
        </div>

        <div style={{ flex: 1, minWidth: 240 }}>
          <input
            style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
            type="url"
            placeholder="Optional HTTPS Teams / Slack webhook URL"
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            aria-label="Webhook URL"
          />
        </div>

        <button type="submit" style={BTN_PRIMARY}>
          <Plus size={13} /> Add Schedule
        </button>
      </form>

      {error && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, background: 'rgba(239, 68, 68, 0.1)', color: 'var(--color-error, #ef4444)', fontSize: 12, marginBottom: 10 }}>
          <AlertCircle size={14} /> {error}
        </div>
      )}

      {successMsg && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6, background: 'rgba(34, 197, 94, 0.1)', color: 'var(--color-success, #22c55e)', fontSize: 12, marginBottom: 10 }}>
          <CheckCircle2 size={14} /> {successMsg}
        </div>
      )}

      {/* Schedules List */}
      {schedules.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '12px 0' }}>
          No scheduled checks configured. Set an interval above to automatically check for schema and semantic drift.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {schedules.map((s) => (
            <div
              key={s.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '8px 12px',
                borderRadius: 7,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                fontSize: 12,
                flexWrap: 'wrap',
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                  Every {s.interval_minutes} minutes
                </span>
                <span style={{ padding: '2px 8px', borderRadius: 4, background: 'var(--bg-surface)', fontSize: 11, color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                  Project: {s.project_id || 'All'}
                </span>
                {s.webhook_url && (
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--accent-purple, #a855f7)' }}>
                    <Bell size={12} /> Webhook active
                  </span>
                )}
                {s.last_run_at && (
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    Last run: {new Date(s.last_run_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                )}
                {s.next_run_at && (
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    Next run: {new Date(s.next_run_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                )}
              </div>

              <button
                type="button"
                onClick={() => handleDeleteSchedule(s.id)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                  padding: 4,
                  display: 'inline-flex',
                  alignItems: 'center',
                }}
                title="Delete schedule"
                aria-label={`Delete schedule ${s.id}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
