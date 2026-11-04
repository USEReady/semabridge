/**
 * cronTranslator.js — Human-friendly cron expression translator & schedule utilities.
 */

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

function formatTime(hour, minute) {
  const h = parseInt(hour, 10);
  const m = parseInt(minute, 10);
  if (isNaN(h) || isNaN(m)) return `${hour}:${minute}`;
  const period = h >= 12 ? 'PM' : 'AM';
  const displayHour = h % 12 === 0 ? 12 : h % 12;
  const displayMinute = m < 10 ? `0${m}` : `${m}`;
  return `${displayHour}:${displayMinute} ${period}`;
}

function getOrdinal(n) {
  const num = parseInt(n, 10);
  if (isNaN(num)) return `${n}`;
  const s = ['th', 'st', 'nd', 'rd'];
  const v = num % 100;
  return num + (s[(v - 20) % 10] || s[v] || s[0]);
}

/**
 * Translates a 5-part cron expression into human-readable English.
 * Format: minute hour day-of-month month day-of-week
 */
export function translateCron(cronStr) {
  if (!cronStr || typeof cronStr !== 'string') return 'Manual / On-demand';
  const parts = cronStr.trim().split(/\s+/);
  if (parts.length < 5) return cronStr;

  const [min, hour, dom, mon, dow] = parts;

  // Pattern: Every N minutes (*/N * * * *)
  if (min.startsWith('*/') && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    const step = min.replace('*/', '');
    return `Every ${step} minutes`;
  }

  // Pattern: Every minute (* * * * *)
  if (min === '*' && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    return 'Every minute';
  }

  // Pattern: Hourly (0 * * * *)
  if (min === '0' && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    return 'Every hour, on the hour';
  }

  // Pattern: Every N hours (0 */N * * *)
  if (min === '0' && hour.startsWith('*/') && dom === '*' && mon === '*' && dow === '*') {
    const step = hour.replace('*/', '');
    return `Every ${step} hours`;
  }

  // Exact time specified (min, hour are numbers)
  const isFixedTime = /^\d+$/.test(min) && /^\d+$/.test(hour);
  const timeStr = isFixedTime ? formatTime(hour, min) : null;

  if (timeStr) {
    // Pattern: Daily at HH:MM (M H * * *)
    if (dom === '*' && mon === '*' && dow === '*') {
      return `Every day at ${timeStr}`;
    }

    // Pattern: Weekdays (M H * * 1-5)
    if (dom === '*' && mon === '*' && (dow === '1-5' || dow === 'MON-FRI')) {
      return `Every weekday (Mon–Fri) at ${timeStr}`;
    }

    // Pattern: Specific day of week (M H * * D)
    if (dom === '*' && mon === '*' && /^[0-7]$/.test(dow)) {
      const dayIndex = parseInt(dow, 10) % 7;
      return `Every ${DAYS[dayIndex]} at ${timeStr}`;
    }

    // Pattern: Specific day of month (M H D * *)
    if (/^\d+$/.test(dom) && mon === '*' && dow === '*') {
      return `Monthly on the ${getOrdinal(dom)} at ${timeStr}`;
    }
  }

  // Fallback for custom or multi-value schedules
  return `Custom: ${cronStr}`;
}

/**
 * Formats a relative countdown string from an ISO timestamp.
 * e.g., "in 35 minutes", "tomorrow at 2:00 AM", "in 4 hours"
 */
export function formatRelativeCountdown(nextRunIso) {
  if (!nextRunIso) return null;
  const target = new Date(nextRunIso);
  const now = new Date();
  const diffMs = target.getTime() - now.getTime();

  if (isNaN(diffMs)) return null;
  if (diffMs < 0) return 'Overdue / Running soon';

  const diffMinutes = Math.round(diffMs / (1000 * 60));
  const diffHours = Math.round(diffMs / (1000 * 60 * 60));
  const diffDays = Math.round(diffMs / (1000 * 60 * 60 * 24));

  if (diffMinutes < 60) {
    return `in ${diffMinutes} min${diffMinutes === 1 ? '' : 's'}`;
  }
  if (diffHours < 24) {
    return `in ${diffHours} hour${diffHours === 1 ? '' : 's'}`;
  }
  if (diffDays === 1) {
    return 'Tomorrow';
  }
  return `in ${diffDays} days`;
}

/**
 * Builds a 5-part cron string from friendly preset parameters.
 */
export function buildCronFromPreset({ preset = 'daily', hour = 2, minute = 0, dayOfWeek = 1, dayOfMonth = 1 }) {
  const m = parseInt(minute, 10) || 0;
  const h = parseInt(hour, 10) || 0;

  switch (preset) {
    case 'hourly':
      return `${m} * * * *`;
    case 'daily':
      return `${m} ${h} * * *`;
    case 'weekdays':
      return `${m} ${h} * * 1-5`;
    case 'weekly':
      return `${m} ${h} * * ${dayOfWeek}`;
    case 'monthly':
      return `${m} ${h} ${dayOfMonth} * *`;
    default:
      return `${m} ${h} * * *`;
  }
}
