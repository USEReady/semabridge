/**
 * Small presentational helpers shared by the Settings sections.
 * Inline styles + app CSS variables only.
 */

export function SectionHeader({ title, description, action }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16, marginBottom: 18, flexWrap: 'wrap' }}>
      <div style={{ minWidth: 0 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>{title}</h2>
        {description && (
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>{description}</p>
        )}
      </div>
      {action}
    </div>
  );
}

export function Card({ children, style }) {
  return (
    <div style={{
      padding: 16, borderRadius: 12,
      border: '1px solid var(--border-main)', background: 'var(--bg-surface)',
      display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
      minWidth: 0,
      ...style,
    }}>
      {children}
    </div>
  );
}

export function CardGrid({ children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 12 }}>
      {children}
    </div>
  );
}

const PILL_TONES = {
  success: { color: 'var(--color-success)', bg: 'var(--color-success-muted, rgba(34,197,94,.12))' },
  warning: { color: 'var(--color-warning)', bg: 'var(--color-warning-muted, rgba(245,158,11,.12))' },
  error: { color: 'var(--color-error)', bg: 'var(--color-error-muted, rgba(239,68,68,.12))' },
  neutral: { color: 'var(--text-tertiary)', bg: 'var(--bg-surface-raised)' },
};

export function Pill({ tone = 'neutral', children }) {
  const t = PILL_TONES[tone] || PILL_TONES.neutral;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '2px 9px', borderRadius: 999,
      fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
      color: t.color, background: t.bg,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: t.color }} />
      {children}
    </span>
  );
}

export function ActionButton({ primary = false, children, style, ...rest }) {
  return (
    <button
      type="button"
      {...rest}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '6px 12px', borderRadius: 8,
        fontSize: 12, fontWeight: 600, whiteSpace: 'nowrap',
        cursor: rest.disabled ? 'not-allowed' : 'pointer',
        opacity: rest.disabled ? 0.6 : 1,
        border: primary ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
        background: primary ? 'var(--accent-blue)' : 'var(--bg-surface)',
        color: primary ? '#fff' : 'var(--text-primary)',
        ...style,
      }}
    >
      {children}
    </button>
  );
}
