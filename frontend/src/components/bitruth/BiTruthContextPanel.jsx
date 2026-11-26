import React from 'react';
import { Check } from 'lucide-react';

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '4px 8px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 11.5,
  cursor: 'pointer',
};

export default function BiTruthContextPanel({ result, onAcceptBenchmark }) {
  if (!result) return null;

  const canAccept = onAcceptBenchmark && result.actual_value !== null && result.actual_value !== undefined && !result.passed;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 11.5, borderTop: '1px solid var(--border-main)', paddingTop: 8 }}>
      {result.generated_sql && (
        <div>
          <div style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>Generated SQL:</div>
          <pre
            style={{
              margin: 0,
              padding: 8,
              borderRadius: 6,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              fontFamily: 'monospace',
              fontSize: 11,
              whiteSpace: 'pre-wrap',
              maxHeight: 120,
              overflowY: 'auto',
            }}
          >
            {result.generated_sql}
          </pre>
        </div>
      )}

      {result.message && (
        <div style={{ color: 'var(--text-secondary)' }}>
          <strong>Detail:</strong> {result.message}
        </div>
      )}

      {canAccept && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 4 }}>
          <button
            type="button"
            style={{ ...BTN, background: 'rgba(59,130,246,0.1)', borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' }}
            onClick={() => onAcceptBenchmark(result.case_id, result.actual_value, result.actual_row_count)}
            title="Accept this warehouse result as the new benchmark expected value"
          >
            <Check size={12} /> Accept Actual as New Benchmark
          </button>
        </div>
      )}
    </div>
  );
}
