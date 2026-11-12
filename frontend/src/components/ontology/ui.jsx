import React from 'react';
import { TONES } from './styles';

export function Pill({ tone = 'n', children, style }) {
  const t = TONES[tone] || TONES.n;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 11,
        fontWeight: 600,
        padding: '2px 8px',
        borderRadius: 999,
        whiteSpace: 'nowrap',
        background: t.bg,
        color: t.color,
        ...style,
      }}
    >
      {children}
    </span>
  );
}

export function Segmented({ options, value, onChange, size = 'md' }) {
  return (
    <div
      role="tablist"
      style={{
        display: 'inline-flex',
        padding: 2,
        gap: 2,
        borderRadius: 8,
        background: 'var(--bg-surface-raised)',
        border: '1px solid var(--border-main)',
      }}
    >
      {options.map((o) => {
        const on = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(o.id)}
            style={{
              padding: size === 'sm' ? '3px 9px' : '5px 12px',
              borderRadius: 6,
              border: 'none',
              fontSize: size === 'sm' ? 11 : 12,
              fontWeight: 600,
              cursor: 'pointer',
              background: on ? 'var(--bg-surface)' : 'transparent',
              color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
              boxShadow: on ? 'var(--shadow-sm)' : 'none',
            }}
          >
            {o.label}
            {o.count !== undefined && (
              <span style={{ marginLeft: 5, color: 'var(--text-tertiary)', fontWeight: 500 }}>{o.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
