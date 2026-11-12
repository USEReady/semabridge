/**
 * Schedules tab of the Runs page: each schedule in plain language
 * ("Every weekday at 06:00 Asia/Kolkata · next in 14h"), with the raw cron
 * only under "Advanced". Run now / Edit / Delete are kept.
 */
import { useState } from 'react';
import { CalendarClock, Play, Pencil, Trash2, Plus, RefreshCw, ChevronRight, ChevronDown } from 'lucide-react';
import { describeSchedule, countdown, formatDateTime, getUserTimezone } from './runsFormat';

const btn = {
  display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 10px', borderRadius: 8,
  fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--border-main)',
  background: 'var(--bg-surface)', color: 'var(--text-secondary)',
};

function ScheduleRow({ schedule, onRun, onEdit, onDelete, isRunning, isDeleting, userTz }) {
  const [advanced, setAdvanced] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const tz = schedule.timezone || 'UTC';
  const next = countdown(schedule.next_run_at);
  const pid = schedule.project_id || schedule.id;

  return (
    <div style={{ borderTop: '1px solid var(--border-main)', padding: '12px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', overflowWrap: 'anywhere' }}>
            {schedule.project_name || schedule.project_id}
            {schedule.enabled === false && (
              <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)' }}>Paused</span>
            )}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
            {describeSchedule(schedule)} {tz}
            {next && <span style={{ color: 'var(--text-tertiary)' }}> · next {next}</span>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button type="button" onClick={() => onRun(pid)} disabled={isRunning} style={{ ...btn, opacity: isRunning ? 0.6 : 1 }} title="Run this project now">
            {isRunning ? <RefreshCw size={12} className="animate-spin" /> : <Play size={12} />}
            {isRunning ? 'Starting…' : 'Run now'}
          </button>
          <button type="button" onClick={() => onEdit(schedule)} style={btn}>
            <Pencil size={12} /> Edit
          </button>
          {confirmDelete ? (
            <>
              <button
                type="button"
                onClick={() => { setConfirmDelete(false); onDelete(schedule.project_id); }}
                disabled={isDeleting}
                style={{ ...btn, color: 'var(--color-error)', borderColor: 'rgba(239,68,68,0.4)', background: 'var(--color-error-bg)' }}
              >
                Delete schedule
              </button>
              <button type="button" onClick={() => setConfirmDelete(false)} style={btn}>Keep</button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              disabled={isDeleting}
              style={{ ...btn, color: 'var(--color-error)', opacity: isDeleting ? 0.6 : 1 }}
              title="Remove this schedule"
            >
              <Trash2 size={12} /> {isDeleting ? 'Deleting…' : 'Delete'}
            </button>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={() => setAdvanced(v => !v)}
        aria-expanded={advanced}
        style={{ marginTop: 6, padding: 0, border: 'none', background: 'transparent', color: 'var(--text-tertiary)', fontSize: 11, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 3 }}
      >
        {advanced ? <ChevronDown size={11} /> : <ChevronRight size={11} />} Advanced
      </button>
      {advanced && (
        <div style={{ marginTop: 6, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 14px', fontSize: 11, color: 'var(--text-tertiary)' }}>
          {schedule.cron && (<><span>Cron</span><code style={{ fontFamily: 'monospace', color: 'var(--text-secondary)' }}>{schedule.cron}</code></>)}
          <span>Schedule time zone</span><span style={{ color: 'var(--text-secondary)' }}>{tz}</span>
          <span>Next run</span>
          <span style={{ color: 'var(--text-secondary)' }}>{formatDateTime(schedule.next_run_at, userTz)} ({userTz})</span>
          {schedule.last_run_at && (<><span>Last run</span><span style={{ color: 'var(--text-secondary)' }}>{formatDateTime(schedule.last_run_at, userTz)}</span></>)}
        </div>
      )}
    </div>
  );
}

export default function SchedulesTab({ schedules, onAdd, onEdit, onDelete, onRun, runningScheduleId, deletingId }) {
  const userTz = getUserTimezone();

  if (!schedules.length) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 16px', background: 'var(--bg-surface)', borderRadius: 12, border: '1px dashed var(--border-main)' }}>
        <CalendarClock size={28} style={{ color: 'var(--text-tertiary)', margin: '0 auto 10px' }} />
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>No schedules yet</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '4px auto 14px', maxWidth: 420 }}>
          Run a project automatically: every hour, every day, on weekdays, weekly or monthly.
        </div>
        <button type="button" onClick={onAdd} style={{ ...btn, background: 'var(--accent-blue)', color: '#fff', border: 'none', padding: '7px 14px' }}>
          <Plus size={13} /> Add a schedule
        </button>
      </div>
    );
  }

  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px' }}>
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', flex: 1 }}>
          Times use each schedule&apos;s own time zone. Your time zone: {userTz}
        </span>
        <button type="button" onClick={onAdd} style={btn}>
          <Plus size={13} /> Add schedule
        </button>
      </div>
      {schedules.map(s => {
        const pid = String(s.project_id || s.id);
        return (
          <ScheduleRow
            key={s.id || s.project_id}
            schedule={s}
            userTz={userTz}
            onRun={onRun}
            onEdit={onEdit}
            onDelete={onDelete}
            isRunning={runningScheduleId === pid}
            isDeleting={deletingId === String(s.project_id)}
          />
        );
      })}
    </div>
  );
}
