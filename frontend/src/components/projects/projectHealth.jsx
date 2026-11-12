/**
 * Shared bits for the Projects page summary band and table view:
 * the conversion bar (clean / AI / review / dropped) and the ontology score ring.
 * One colour code everywhere: green clean, purple AI, amber review, red dropped.
 */
import React from 'react';
import { CONVERSION_PARTS, scoreColor, ontologyTooltip } from './projectHealthUtils';

export function ConversionBar({ counts, height = 8, title }) {
  const total = CONVERSION_PARTS.reduce((sum, p) => sum + (Number(counts?.[p.key]) || 0), 0);
  if (!total) {
    return <div style={{ height, borderRadius: 4, background: 'var(--bg-surface-raised)' }} />;
  }
  const tip = title || CONVERSION_PARTS
    .map(p => `${p.label}: ${Number(counts?.[p.key]) || 0}`)
    .join('\n');
  return (
    <div
      title={tip}
      role="img"
      aria-label={tip.replace(/\n/g, ', ')}
      style={{ display: 'flex', height, borderRadius: 4, overflow: 'hidden', background: 'var(--bg-surface-raised)' }}
    >
      {CONVERSION_PARTS.map(p => {
        const n = Number(counts?.[p.key]) || 0;
        return n ? <i key={p.key} style={{ display: 'block', width: `${(100 * n) / total}%`, background: p.color }} /> : null;
      })}
    </div>
  );
}

export function ConversionLegend({ counts }) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 11, color: 'var(--text-tertiary)' }}>
      {CONVERSION_PARTS.map(p => (
        <span key={p.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <i style={{ width: 8, height: 8, borderRadius: 2, background: p.color, display: 'inline-block' }} />
          {counts ? `${(Number(counts[p.key]) || 0).toLocaleString()} ` : ''}{p.label.toLowerCase()}
        </span>
      ))}
    </div>
  );
}

export function OntologyScore({ health, size = 26 }) {
  const score = health?.score;
  const none = score === null || score === undefined;
  const color = scoreColor(score);
  return (
    <span
      title={ontologyTooltip(health)}
      style={{
        display: 'inline-grid', placeItems: 'center', flex: 'none',
        width: size, height: size, borderRadius: '50%',
        border: `2.5px ${none ? 'dashed' : 'solid'} ${color}`,
        color, fontSize: Math.round(size * 0.38), fontWeight: 700,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {none ? '–' : score}
    </span>
  );
}
