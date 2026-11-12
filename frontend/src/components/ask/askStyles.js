/** Shared inline styles for the Ask AI page. */
export const CARD = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 12,
  padding: 16,
  display: 'flex',
  flexDirection: 'column',
  gap: 14,
};

export const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 8,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

export const BTN_PRIMARY = {
  ...BTN,
  background: 'var(--accent-blue)',
  borderColor: 'var(--accent-blue)',
  color: '#fff',
};

export const LINK_BTN = {
  background: 'none',
  border: 'none',
  padding: 0,
  color: 'var(--text-secondary)',
  fontSize: 12,
  textDecoration: 'underline',
  textUnderlineOffset: 2,
  cursor: 'pointer',
};

export const CHIP = {
  display: 'inline-block',
  padding: '1px 7px',
  borderRadius: 5,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 11.5,
};

export const pill = (tone) => {
  const map = {
    ok: ['var(--color-success-bg)', 'var(--color-success)'],
    warn: ['var(--color-warning-bg)', 'var(--color-warning)'],
    bad: ['var(--color-error-bg)', 'var(--color-error)'],
  };
  const [bg, fg] = map[tone] || map.ok;
  return {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '2px 8px',
    borderRadius: 999,
    background: bg,
    color: fg,
    fontSize: 11,
    fontWeight: 600,
    whiteSpace: 'nowrap',
  };
};

export const LABEL = {
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: '0.04em',
  textTransform: 'uppercase',
  color: 'var(--text-tertiary)',
};
