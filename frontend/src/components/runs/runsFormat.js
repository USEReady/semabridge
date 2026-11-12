/**
 * Plain-language formatting helpers for the Runs page (history table,
 * run drawer and schedules tab). No React in here.
 */
import { translateCron } from '../../utils/cronTranslator';

export function getUserTimezone() {
  try {
    return localStorage.getItem('semabridge-timezone')
      || Intl.DateTimeFormat().resolvedOptions().timeZone
      || 'UTC';
  } catch {
    return 'UTC';
  }
}

export function formatDuration(ms) {
  const n = Number(ms);
  if (!n || n < 0) return '—';
  if (n < 1000) return `${n}ms`;
  const s = Math.floor(n / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** Backend timestamps come without a zone suffix; they are UTC. */
export function parseIso(iso) {
  if (!iso) return null;
  const s = String(iso);
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(s);
  const d = new Date(hasZone ? s : `${s}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTime(iso, timeZone = getUserTimezone()) {
  const d = parseIso(iso);
  if (!d) return '—';
  try {
    return new Intl.DateTimeFormat('en-GB', {
      day: 'numeric', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false, timeZone,
    }).format(d);
  } catch {
    return d.toLocaleString();
  }
}

/** "now", "5m ago", "2h ago", "Yesterday", "3 days ago", or a date. */
export function formatAgo(iso, now = Date.now()) {
  const d = parseIso(iso);
  if (!d) return '—';
  const diff = Math.max(0, now - d.getTime());
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'now';
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days === 1) return 'Yesterday';
  if (days < 7) return `${days} days ago`;
  return formatDateTime(iso).replace(/,? \d\d:\d\d$/, '');
}

export function normStatus(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'succeeded' || s === 'completed' || s === 'complete') return 'success';
  if (s === 'error') return 'failed';
  if (s === 'queued' || s === 'pending' || s === 'in_progress') return 'running';
  return s || 'unknown';
}

export const STATUS_PILL = {
  success: { label: 'Succeeded', color: 'var(--color-success)', bg: 'var(--color-success-bg)' },
  failed: { label: 'Failed', color: 'var(--color-error)', bg: 'var(--color-error-bg)' },
  running: { label: 'Running', color: 'var(--accent-blue)', bg: 'rgba(99, 102, 241, 0.14)' },
  conflict: { label: 'Conflict', color: 'var(--color-warning)', bg: 'var(--color-warning-bg)' },
  cancelled: { label: 'Cancelled', color: 'var(--text-tertiary)', bg: 'var(--bg-surface-raised)' },
};

export function statusPill(status) {
  const key = normStatus(status);
  return STATUS_PILL[key] || {
    label: key.charAt(0).toUpperCase() + key.slice(1),
    color: 'var(--text-secondary)',
    bg: 'var(--bg-surface-raised)',
  };
}

export function syncModeWords(mode) {
  return String(mode || 'copy').toLowerCase() === 'upsert' ? 'Update changes only' : 'Replace target';
}

/** First sentence-ish chunk of an error, small enough for a table cell. */
export function shortMessage(text, max = 60) {
  if (!text) return '';
  let s = String(text).replace(/\s+/g, ' ').trim();
  s = s.replace(/^(Deployment failed|Execution failed|Run failed|Error):\s*/i, '');
  const cut = s.search(/[.:;](\s|$)/);
  if (cut > 12) s = s.slice(0, cut);
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

/* ── Stage timeline ───────────────────────────────────────────────────── */

const STAGE_RE = /(?:^|\s)(INFO|SUCCESS|LIVE|ERROR|WARNING|WARN|SKIP)?\b.*?Stage (\d+): ([^\n]+?)(?: - (.*))?$/;
const TIME_RE = /\b(\d\d):(\d\d):(\d\d)\b/;

function secondsOf(line) {
  const m = TIME_RE.exec(line);
  return m ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) : null;
}

/**
 * Turns run log lines ("... 13:03:55 INFO ...: Stage 4: Extract from Source - Extracted 9 tables")
 * into an ordered list of stages with how long each took.
 * The last line for a stage number wins (stages are sometimes logged twice).
 */
export function parseStages(lines = [], { failed = false, error = '' } = {}) {
  const byNum = new Map();
  let firstSec = null;
  for (const raw of lines) {
    const line = String(raw || '');
    const sec = secondsOf(line);
    if (firstSec === null && sec !== null) firstSec = sec;
    const m = STAGE_RE.exec(line);
    if (!m) continue;
    const num = Number(m[2]);
    const level = String(m[1] || (/\bERROR\b/.test(line) ? 'ERROR' : 'INFO')).toUpperCase();
    byNum.set(num, {
      num,
      name: m[3].trim(),
      detail: (m[4] || '').trim(),
      level,
      sec,
    });
  }
  const stages = [...byNum.values()].sort((a, b) => a.num - b.num);
  let prev = firstSec;
  for (const st of stages) {
    if (st.sec !== null && prev !== null) {
      let d = st.sec - prev;
      if (d < 0) d += 86400;
      st.tookSec = d;
    } else {
      st.tookSec = null;
    }
    if (st.sec !== null) prev = st.sec;
    st.state = st.level === 'ERROR' ? 'failed' : st.level === 'LIVE' ? 'running' : st.level === 'SKIP' ? 'skipped' : 'done';
  }
  if (failed && !stages.some(s => s.state === 'failed')) {
    stages.push({
      num: (stages[stages.length - 1]?.num || 0) + 1,
      name: 'Stopped with an error',
      detail: shortMessage(error, 140),
      state: 'failed',
      tookSec: null,
    });
  }
  return stages;
}

export function formatStageTook(sec) {
  if (sec === null || sec === undefined) return '';
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

/* ── Schedules in plain language ──────────────────────────────────────── */

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function ordinal(n) {
  const v = n % 100;
  const s = ['th', 'st', 'nd', 'rd'];
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

function hhmm(h, m) {
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** "Every weekday at 06:00", "Every 15 minutes", … Falls back to the shared translator. */
export function describeCron(cron) {
  if (!cron || typeof cron !== 'string') return 'On demand';
  const parts = cron.trim().split(/\s+/);
  if (parts.length < 5) return translateCron(cron);
  const [min, hour, dom, mon, dow] = parts;
  const num = v => /^\d+$/.test(v);
  if (mon !== '*') return translateCron(cron);

  if (min.startsWith('*/') && hour === '*' && dom === '*' && dow === '*') return `Every ${min.slice(2)} minutes`;
  if (min === '*' && hour === '*' && dom === '*' && dow === '*') return 'Every minute';
  if (num(min) && hour === '*' && dom === '*' && dow === '*') {
    return +min === 0 ? 'Every hour, on the hour' : `Every hour at :${String(min).padStart(2, '0')}`;
  }
  if (num(min) && hour.startsWith('*/') && dom === '*' && dow === '*') return `Every ${hour.slice(2)} hours`;
  if (!num(min) || !num(hour)) return translateCron(cron);

  const at = hhmm(+hour, +min);
  if (dom === '*' && dow === '*') return `Every day at ${at}`;
  if (dom === '*' && (dow === '1-5' || dow.toUpperCase() === 'MON-FRI')) return `Every weekday at ${at}`;
  if (dom === '*' && (dow === '0,6' || dow === '6,0')) return `Every weekend day at ${at}`;
  if (dom === '*' && /^[0-7]$/.test(dow)) return `Every ${DAY_NAMES[+dow % 7]} at ${at}`;
  if (dom === '*' && /^[0-7](,[0-7])+$/.test(dow)) {
    return `Every ${dow.split(',').map(d => DAY_SHORT[+d % 7]).join(', ')} at ${at}`;
  }
  if (num(dom) && dow === '*') return `On the ${ordinal(+dom)} of every month at ${at}`;
  return translateCron(cron);
}

/** "in 14h", "in 35 min", "in 3 days", "overdue". */
export function countdown(iso, now = Date.now()) {
  const d = parseIso(iso);
  if (!d) return '';
  const diff = d.getTime() - now;
  if (diff < 0) return 'due now';
  const min = Math.round(diff / 60000);
  if (min < 60) return `in ${Math.max(1, min)} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `in ${h}h`;
  return `in ${Math.round(h / 24)} days`;
}

export function describeSchedule(schedule) {
  if (!schedule) return '';
  if (schedule.schedule_type && schedule.schedule_type !== 'cron') {
    const when = [schedule.date, schedule.time].filter(Boolean).join(' at ');
    return when ? `Once, on ${when}` : 'Once';
  }
  return describeCron(schedule.cron);
}
