import React, { useState, useEffect } from 'react';
import { X, Calendar, Clock, Globe, Sparkles, Check, AlertCircle } from 'lucide-react';
import { translateCron, buildCronFromPreset } from '../../utils/cronTranslator';
import { api } from '../../utils/api';

const TIMEZONES = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Paris',
  'Asia/Tokyo',
  'Asia/Kolkata',
  'Asia/Singapore',
  'Australia/Sydney',
];

const DAYS_OF_WEEK = [
  { label: 'Sunday', value: 0 },
  { label: 'Monday', value: 1 },
  { label: 'Tuesday', value: 2 },
  { label: 'Wednesday', value: 3 },
  { label: 'Thursday', value: 4 },
  { label: 'Friday', value: 5 },
  { label: 'Saturday', value: 6 },
];

export default function ScheduleModal({
  open,
  onClose,
  onSaved,
  projects = [],
  selectedProject = null,
  initialSchedule = null,
}) {
  const [projectId, setProjectId] = useState(
    selectedProject?.id || selectedProject?.project_id || initialSchedule?.project_id || ''
  );
  const [preset, setPreset] = useState('daily');
  const [time, setTime] = useState('02:00');
  const [dayOfWeek, setDayOfWeek] = useState(1); // Monday
  const [cronExpression, setCronExpression] = useState('0 2 * * *');
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  // Sync state with props when modal opens
  useEffect(() => {
    if (open) {
      setError(null);
      const pid = selectedProject?.id || selectedProject?.project_id || initialSchedule?.project_id || (projects[0]?.id || projects[0]?.project_id || '');
      setProjectId(pid);

      if (initialSchedule?.cron) {
        setCronExpression(initialSchedule.cron);
        setPreset('custom');
      } else {
        setPreset('daily');
        setTime('02:00');
        setCronExpression('0 2 * * *');
      }
      if (initialSchedule?.timezone) {
        setTimezone(initialSchedule.timezone);
      }
    }
  }, [open, selectedProject, initialSchedule, projects]);

  // Recalculate cron expression when preset or inputs change
  useEffect(() => {
    if (preset === 'custom') return;
    const [h, m] = time.split(':').map((v) => parseInt(v, 10) || 0);
    const newCron = buildCronFromPreset({
      preset,
      hour: h,
      minute: m,
      dayOfWeek,
    });
    setCronExpression(newCron);
  }, [preset, time, dayOfWeek]);

  if (!open) return null;

  const humanPreview = translateCron(cronExpression);

  const handleSave = async () => {
    if (!projectId) {
      setError('Please select a project to schedule.');
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const payload = {
        schedule_type: 'cron',
        cron: cronExpression,
        timezone,
        enabled: true,
      };

      const result = await api.saveProjectSchedule(projectId, payload);
      if (onSaved) {
        onSaved({
          ...result,
          project_id: projectId,
          project_name: projects.find((p) => String(p.id || p.project_id) === String(projectId))?.name || projectId,
          schedule_type: 'cron',
          cron: cronExpression,
          timezone,
        });
      }
      onClose();
    } catch (err) {
      console.error('Failed to save schedule:', err);
      setError(err.message || 'Failed to save schedule.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        background: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(8px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 14,
          width: '100%',
          maxWidth: 540,
          boxShadow: '0 20px 40px rgba(0, 0, 0, 0.5)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '18px 24px',
            borderBottom: '1px solid var(--border-main)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-surface-raised)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: 'rgba(59, 130, 246, 0.15)',
                color: '#3b82f6',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Calendar size={18} />
            </div>
            <div>
              <h3 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>
                {initialSchedule ? 'Edit Schedule' : 'Schedule Project Job'}
              </h3>
              <p style={{ fontSize: 12, margin: 0, color: 'var(--text-tertiary)' }}>
                Automate periodic semantic synchronization
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              padding: 6,
              borderRadius: 6,
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 18 }}>
          {error && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 8,
                background: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                color: '#ef4444',
                fontSize: 12,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
              }}
            >
              <AlertCircle size={15} style={{ flexShrink: 0 }} />
              <span>{error}</span>
            </div>
          )}

          {/* Project Selection (if multiple) */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', display: 'block', marginBottom: 6 }}>
              Target Project
            </label>
            {selectedProject ? (
              <div
                style={{
                  padding: '9px 12px',
                  background: 'var(--bg-input)',
                  border: '1px solid var(--border-main)',
                  borderRadius: 8,
                  fontSize: 13,
                  fontWeight: 600,
                  color: 'var(--text-primary)',
                }}
              >
                {selectedProject.name || selectedProject.id}
              </div>
            ) : (
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  background: 'var(--bg-input)',
                  border: '1px solid var(--border-main)',
                  borderRadius: 8,
                  color: 'var(--text-primary)',
                  fontSize: 13,
                  outline: 'none',
                }}
              >
                <option value="">Select a project...</option>
                {projects.map((p) => {
                  const pid = p.id || p.project_id;
                  return (
                    <option key={pid} value={pid}>
                      {p.name || pid}
                    </option>
                  );
                })}
              </select>
            )}
          </div>

          {/* Cadence Presets */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', display: 'block', marginBottom: 6 }}>
              Cadence
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 6 }}>
              {[
                { id: 'hourly', label: 'Hourly' },
                { id: 'daily', label: 'Daily' },
                { id: 'weekdays', label: 'Weekdays' },
                { id: 'weekly', label: 'Weekly' },
                { id: 'custom', label: 'Custom' },
              ].map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setPreset(item.id)}
                  style={{
                    padding: '8px 4px',
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: preset === item.id ? 700 : 500,
                    background: preset === item.id ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                    color: preset === item.id ? '#ffffff' : 'var(--text-secondary)',
                    border: `1px solid ${preset === item.id ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                    cursor: 'pointer',
                    textAlign: 'center',
                    transition: 'all 0.15s ease',
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {/* Dynamic Configuration based on Preset */}
          {preset !== 'hourly' && preset !== 'custom' && (
            <div style={{ display: 'grid', gridTemplateColumns: preset === 'weekly' ? '1fr 1fr' : '1fr', gap: 12 }}>
              {preset === 'weekly' && (
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                    Day of the Week
                  </label>
                  <select
                    value={dayOfWeek}
                    onChange={(e) => setDayOfWeek(parseInt(e.target.value, 10))}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border-main)',
                      borderRadius: 8,
                      color: 'var(--text-primary)',
                      fontSize: 13,
                    }}
                  >
                    {DAYS_OF_WEEK.map((d) => (
                      <option key={d.value} value={d.value}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                  Execution Time
                </label>
                <div style={{ position: 'relative' }}>
                  <input
                    type="time"
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                    style={{
                      width: '100%',
                      padding: '8px 12px',
                      background: 'var(--bg-input)',
                      border: '1px solid var(--border-main)',
                      borderRadius: 8,
                      color: 'var(--text-primary)',
                      fontSize: 13,
                    }}
                  />
                </div>
              </div>
            </div>
          )}

          {preset === 'custom' && (
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                Cron Expression (min hour dom mon dow)
              </label>
              <input
                type="text"
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                placeholder="e.g. 0 2 * * *"
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  background: 'var(--bg-input)',
                  border: '1px solid var(--border-main)',
                  borderRadius: 8,
                  color: 'var(--text-primary)',
                  fontSize: 13,
                  fontFamily: 'monospace',
                }}
              />
            </div>
          )}

          {/* Timezone */}
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
              Timezone
            </label>
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                background: 'var(--bg-input)',
                border: '1px solid var(--border-main)',
                borderRadius: 8,
                color: 'var(--text-primary)',
                fontSize: 13,
              }}
            >
              {TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </div>

          {/* Human-readable Schedule Summary Box */}
          <div
            style={{
              padding: '12px 14px',
              borderRadius: 8,
              background: 'rgba(59, 130, 246, 0.08)',
              border: '1px solid rgba(59, 130, 246, 0.25)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Sparkles size={16} style={{ color: '#3b82f6', flexShrink: 0 }} />
              <div>
                <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: '#3b82f6', letterSpacing: 0.5 }}>
                  Schedule Summary
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginTop: 2 }}>
                  {humanPreview} ({timezone})
                </div>
              </div>
            </div>
            <span
              style={{
                fontFamily: 'monospace',
                fontSize: 11,
                padding: '3px 8px',
                borderRadius: 4,
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-main)',
                color: 'var(--text-tertiary)',
              }}
            >
              {cronExpression}
            </span>
          </div>
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '16px 24px',
            borderTop: '1px solid var(--border-main)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 10,
            background: 'var(--bg-surface-raised)',
          }}
        >
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            style={{
              padding: '8px 16px',
              borderRadius: 8,
              border: '1px solid var(--border-main)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              fontSize: 13,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !projectId}
            style={{
              padding: '8px 20px',
              borderRadius: 8,
              border: 'none',
              background: 'var(--accent-blue)',
              color: '#ffffff',
              fontSize: 13,
              fontWeight: 700,
              cursor: saving || !projectId ? 'not-allowed' : 'pointer',
              opacity: saving || !projectId ? 0.6 : 1,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            {saving ? 'Saving...' : initialSchedule ? 'Update Schedule' : 'Create Schedule'}
          </button>
        </div>
      </div>
    </div>
  );
}
