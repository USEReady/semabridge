/** Small presentational pieces shared by the project page tabs. */
import React from 'react';
import { PILL_TONES } from './projectStyles';

export function Pill({ tone = 'n', children, title }) {
  const t = PILL_TONES[tone] || PILL_TONES.n;
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap',
        padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600,
        color: t.color, background: t.bg, fontVariantNumeric: 'tabular-nums',
      }}
    >
      {children}
    </span>
  );
}

export function Chip({ children, mono = false }) {
  return (
    <span
      style={{
        display: 'inline-flex', alignItems: 'center', padding: '2px 8px', borderRadius: 6,
        border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)',
        fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)',
        fontFamily: mono ? 'ui-monospace, SFMono-Regular, Menlo, monospace' : undefined,
      }}
    >
      {children}
    </span>
  );
}
