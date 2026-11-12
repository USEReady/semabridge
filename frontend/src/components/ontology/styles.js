// Shared styles and helpers for the Ontology page (Overview, Domains, Health).
// Only app CSS variables, so light and dark themes both work.

// Turn a FastAPI error body into readable text
export function describeApiError(data, fallback) {
  const detail = data && data.detail !== undefined ? data.detail : data;
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  if (Array.isArray(detail.file_errors) && detail.file_errors.length) {
    return detail.file_errors.map((e) => `${e.file}: ${e.error}`).join('\n');
  }
  if (Array.isArray(detail.errors) && detail.errors.length) return detail.errors.join('\n');
  return detail.message || fallback;
}

export const TONES = {
  ok: { color: 'var(--color-success)', bg: 'var(--color-success-bg)' },
  warn: { color: 'var(--color-warning)', bg: 'var(--color-warning-bg)' },
  bad: { color: 'var(--color-error)', bg: 'var(--color-error-bg)' },
  info: { color: 'var(--accent-blue)', bg: 'color-mix(in srgb, var(--accent-blue) 12%, transparent)' },
  n: { color: 'var(--text-secondary)', bg: 'var(--bg-surface-raised)' },
};

export const CARD = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 12,
};

export const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
  padding: '7px 14px',
  borderRadius: 8,
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-main)',
};

export const BTN_PRIMARY = {
  ...BTN,
  background: 'var(--accent-blue)',
  color: '#FFFFFF',
  border: '1px solid var(--accent-blue)',
};

export const BTN_SM = { ...BTN, padding: '4px 10px', fontSize: 11 };

export const SELECT_STYLE = {
  padding: '7px 10px',
  borderRadius: 8,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  outline: 'none',
};

export const INPUT_STYLE = {
  padding: '7px 10px',
  borderRadius: 8,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

export const LABEL = {
  fontSize: 10,
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
  color: 'var(--text-tertiary)',
};

export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

export function timeAgo(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 60) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return `${h} h ago`;
}

export function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || `${one}s`}`;
}
