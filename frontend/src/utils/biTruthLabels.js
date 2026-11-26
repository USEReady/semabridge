export function formatTimestamp(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    const dateStr = d.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    let ago = '';
    if (diffSec < 60) ago = 'just now';
    else if (diffSec < 3600) ago = `${Math.floor(diffSec / 60)}m ago`;
    else if (diffSec < 86400) ago = `${Math.floor(diffSec / 3600)}h ago`;
    else ago = `${Math.floor(diffSec / 86400)}d ago`;

    return `${dateStr}, ${timeStr} (${ago})`;
  } catch {
    return isoStr;
  }
}

export function formatPercent(val) {
  if (val === null || val === undefined) return '0%';
  const num = Number(val);
  return `${Number.isFinite(num) ? num.toFixed(1) : 0}%`;
}

export function getGateBadge(verdict) {
  if (!verdict) return { label: 'Unknown', tone: 'neutral', icon: 'HelpCircle' };
  if (verdict.status === 'not_gated') {
    return { label: 'Not Gated', tone: 'neutral', text: 'No critical truth cases configured.' };
  }
  if (verdict.status === 'stale') {
    return {
      label: 'Gate Stale',
      tone: 'warn',
      text: verdict.reason || 'Latest BI Truth suite run is older than allowed window.',
    };
  }
  if (verdict.status === 'blocked') {
    return {
      label: 'Deploy Blocked',
      tone: 'error',
      text: verdict.reason || 'Critical measures outside tolerance in block mode.',
    };
  }
  if (verdict.status === 'warning') {
    return {
      label: 'Deploy Warning',
      tone: 'warn',
      text: verdict.reason || 'Critical measures outside tolerance (Report Mode - Deploy allowed).',
    };
  }
  if (verdict.status === 'passed') {
    return {
      label: 'Gate Passed',
      tone: 'success',
      text: 'All critical measures within tolerance.',
    };
  }
  return { label: verdict.status || 'Report', tone: 'neutral', text: verdict.reason || '' };
}

export function formatTruthValue(val) {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'number') {
    return Number.isInteger(val) ? val.toLocaleString() : Number(val.toFixed(4)).toLocaleString();
  }
  if (typeof val === 'string') return val;
  if (typeof val === 'boolean') return val ? 'true' : 'false';

  if (Array.isArray(val)) {
    if (val.length === 0) return '0 rows (empty)';
    if (val.length === 1 && typeof val[0] === 'object' && val[0] !== null) {
      const keys = Object.keys(val[0]);
      if (keys.length === 1) {
        return formatTruthValue(val[0][keys[0]]);
      }
      return keys.map((k) => `${k}: ${formatTruthValue(val[0][k])}`).join(', ');
    }
    if (val.length <= 3 && typeof val[0] === 'object' && val[0] !== null) {
      const summaries = val.map((row) => {
        const entries = Object.entries(row);
        if (entries.length === 1) return formatTruthValue(entries[0][1]);
        return entries.map(([k, v]) => `${k}=${formatTruthValue(v)}`).join(' ');
      });
      return summaries.join(' | ');
    }
    return `${val.length} rows`;
  }

  if (typeof val === 'object') {
    const keys = Object.keys(val);
    if (keys.length === 1) return formatTruthValue(val[keys[0]]);
    return keys.map((k) => `${k}: ${formatTruthValue(val[k])}`).join(', ');
  }

  return String(val);
}

