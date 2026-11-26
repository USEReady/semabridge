import React, { useState } from 'react';
import { ChevronDown, ChevronRight, CheckCircle2, AlertTriangle, AlertCircle, HelpCircle } from 'lucide-react';
import BiTruthContextPanel from './BiTruthContextPanel';
import { formatTruthValue } from '../../utils/biTruthLabels';

export default function BiTruthResultRow({ result, onAcceptBenchmark }) {
  const [open, setOpen] = useState(false);

  if (!result) return null;

  const isPassed = Boolean(result.passed);
  const isComparable = result.comparable !== false;
  const isRowCountMismatch = Boolean(result.row_count_mismatch);
  const variancePct = result.variance_pct;

  let badge = null;
  if (!isComparable) {
    badge = (
      <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: 'rgba(156,163,175,0.15)', color: 'var(--text-tertiary)' }}>
        <HelpCircle size={11} style={{ display: 'inline', verticalAlign: -1, marginRight: 2 }} /> NOT COMPARABLE
      </span>
    );
  } else if (isRowCountMismatch) {
    badge = (
      <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: 'rgba(234,179,8,0.15)', color: 'var(--color-warning, #eab308)' }}>
        <AlertTriangle size={11} style={{ display: 'inline', verticalAlign: -1, marginRight: 2 }} /> ROW COUNT MISMATCH ({result.actual_row_count} vs {result.expected_row_count})
      </span>
    );
  } else if (isPassed) {
    badge = (
      <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: 'rgba(34,197,94,0.15)', color: 'var(--color-success, #22c55e)' }}>
        <CheckCircle2 size={11} style={{ display: 'inline', verticalAlign: -1, marginRight: 2 }} /> PASSED
      </span>
    );
  } else {
    badge = (
      <span style={{ fontSize: 10.5, fontWeight: 700, padding: '2px 6px', borderRadius: 4, background: 'rgba(239,68,68,0.15)', color: 'var(--color-error, #ef4444)' }}>
        <AlertCircle size={11} style={{ display: 'inline', verticalAlign: -1, marginRight: 2 }} /> FAILED {variancePct !== undefined && variancePct !== null ? `(${variancePct > 0 ? '+' : ''}${Number(variancePct).toFixed(2)}%)` : ''}
      </span>
    );
  }

  return (
    <div
      style={{
        borderRadius: 6,
        border: `1px solid ${!isComparable ? 'var(--border-main)' : isPassed ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'
          }`,
        background: isPassed ? 'rgba(34,197,94,0.03)' : !isComparable ? 'transparent' : 'rgba(239,68,68,0.03)',
        overflow: 'hidden',
      }}
    >
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setOpen(!open);
          }
        }}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '8px 12px',
          cursor: 'pointer',
          gap: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {badge}
          {result.window && (
            <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--accent-blue)', background: 'rgba(59,130,246,0.1)', padding: '1px 6px', borderRadius: 4 }}>
              [{result.window.dimension} {result.window.operator} {result.window.value}]
            </span>
          )}
          {result.error && (
            <span style={{ fontSize: 11, color: 'var(--color-error)' }}>
              {result.error}
            </span>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 11, color: 'var(--text-secondary)' }}>
          {result.expected_value !== null && result.expected_value !== undefined && (
            <span>Expected: <strong style={{ color: 'var(--text-primary)' }}>{formatTruthValue(result.expected_value)}</strong></span>
          )}
          {result.actual_value !== null && result.actual_value !== undefined && (
            <span>Actual: <strong style={{ color: 'var(--text-primary)' }}>{formatTruthValue(result.actual_value)}</strong></span>
          )}
        </div>
      </div>

      {open && (
        <div style={{ padding: '0 12px 10px 12px' }}>
          <BiTruthContextPanel result={result} onAcceptBenchmark={onAcceptBenchmark} />
        </div>
      )}
    </div>
  );
}
