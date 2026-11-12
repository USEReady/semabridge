/** Inline-style tokens shared by the project page tabs. */

export const card = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 12,
  padding: 16,
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
  minWidth: 0,
  flex: 1,
  boxSizing: 'border-box',
};

export const lbl = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: '0.06em',
  textTransform: 'uppercase',
  color: 'var(--text-tertiary)',
};

export const muted = { color: 'var(--text-secondary)', fontSize: 12 };
export const faint = { color: 'var(--text-tertiary)', fontSize: 12 };

export const th = {
  textAlign: 'left', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
  color: 'var(--text-tertiary)', padding: '8px 14px', borderBottom: '1px solid var(--border-main)', whiteSpace: 'nowrap',
};
export const td = { padding: '9px 14px', borderBottom: '1px solid var(--border-light)', verticalAlign: 'middle', fontSize: 12 };

export const PILL_TONES = {
  ok: { color: 'var(--color-success)', bg: 'var(--color-success-muted)' },
  warn: { color: 'var(--color-warning)', bg: 'var(--color-warning-muted)' },
  err: { color: 'var(--color-error)', bg: 'var(--color-error-muted)' },
  ai: { color: 'var(--accent-purple)', bg: 'rgba(168, 85, 247, 0.14)' },
  info: { color: 'var(--accent-blue)', bg: 'var(--color-accent-faint)' },
  n: { color: 'var(--text-secondary)', bg: 'var(--bg-surface-raised)' },
};

export function statusTone(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'success' || s === 'completed') return 'ok';
  if (s === 'partial' || s === 'warning') return 'warn';
  if (s === 'failed' || s === 'error') return 'err';
  if (s === 'running' || s === 'queued' || s === 'pending') return 'info';
  return 'n';
}

export const btn = {
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '7px 12px', borderRadius: 8,
  border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)',
  fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
};
export const btnPrimary = { ...btn, background: 'var(--accent-blue)', borderColor: 'var(--accent-blue)', color: '#fff' };
export const btnSmall = { ...btn, padding: '3px 9px', fontSize: 11 };
export const btnGhost = { ...btnSmall, border: '1px solid transparent', background: 'transparent', color: 'var(--accent-blue)' };
