/** Shared, non-component helpers for the Projects summary band and table. */

// One colour code everywhere: green clean, purple AI, amber review, red dropped.
export const CONVERSION_PARTS = [
  { key: 'standard', label: 'Converted cleanly', color: 'var(--color-success)' },
  { key: 'ai_assisted', label: 'Converted with AI help', color: 'var(--accent-purple)' },
  { key: 'needs_review', label: 'Needs review', color: 'var(--color-warning)' },
  { key: 'dropped', label: 'Dropped', color: 'var(--color-error)' },
];

export function scoreColor(score) {
  if (score === null || score === undefined) return 'var(--text-quaternary)';
  if (score >= 85) return 'var(--color-success)';
  if (score >= 60) return 'var(--color-warning)';
  return 'var(--color-error)';
}

export function ontologyTooltip(h) {
  if (!h || h.score === null || h.score === undefined) return 'No ontology yet for this project';
  return [
    `Ontology score ${h.score}/100`,
    `Domains: ${(h.domains || []).map(d => d.domain).join(', ') || '—'}`,
    `Metrics: ${h.metric_count} · described ${h.glossary_coverage_pct}%`,
    `Duplicate formulas: ${h.duplicate_groups}`,
    `Conflicts: ${h.conflicts}`,
    h.unresolved ? `Broken references: ${h.unresolved}` : null,
  ].filter(Boolean).join('\n');
}

/** Success-rate points for a sparkline; days without runs carry the last value forward. */
export function successSeries(trend = []) {
  const out = [];
  for (const t of trend) {
    const v = t.runs ? t.succeeded / t.runs : null;
    out.push(v !== null ? v : (out.length ? out[out.length - 1] : null));
  }
  const first = out.find(v => v !== null);
  return out.map(v => (v === null ? first : v)).filter(v => v !== undefined && v !== null);
}
