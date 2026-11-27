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

export function formatInjectionSkipReason(code, defaultMsg = '') {
  if (!code) return defaultMsg || 'Skipped';
  const norm = String(code).toUpperCase().trim();
  const map = {
    NOT_APPROVED: 'Case is not approved',
    NO_BENCHMARK: 'No benchmark value captured',
    DID_NOT_PASS: 'Did not pass in the most recent run',
    STALE_RUN: 'Suite run is older than the configured limit',
    NO_RUN: 'No test suite run exists',
    UNSUPPORTED_PLATFORM: 'Platform does not support verified query injection',
    UNVERIFIED_DATABRICKS: 'Databricks Genie space injection is unverified: workspace contains zero Genie spaces and token lacks space creation scope',
    ALREADY_INJECTED: 'Already injected into target',
  };
  return map[norm] || defaultMsg || code;
}

export function formatInjectionTargetLabel(platform = '') {
  const p = String(platform || '').toUpperCase();
  if (p === 'SNOWFLAKE') return 'Publish verified pairs to Cortex';
  if (p === 'DATABRICKS') return 'Publish sample questions to Genie';
  return 'Publish verified pairs to Warehouse';
}

export function getInjectionDisabledReason({
  qualifyingCount = 0,
  platform = '',
  hasRun = true,
  isStale = false,
  canWrite = true,
} = {}) {
  const p = String(platform || '').toUpperCase();
  if (p === 'DATABRICKS') {
    return 'Databricks Genie space injection is unverified: workspace contains zero Genie spaces and token lacks space creation scope';
  }
  if (!canWrite) {
    return 'You lack permission to publish verified queries to warehouse';
  }
  if (!hasRun) {
    return 'No test suite run exists. Run the suite first to evaluate cases';
  }
  if (isStale) {
    return 'Latest test run is older than allowed window. Re-run suite before publishing';
  }
  if (qualifyingCount === 0) {
    return 'No cases qualify for injection. Approve cases, capture benchmarks, and ensure they pass in the latest run';
  }
  return null;
}


