import React from 'react';
import { CheckCircle2, AlertTriangle, Clock } from 'lucide-react';
import { formatTimestamp } from '../../utils/biTruthLabels';

export default function BiTruthRunReport({ run }) {
  if (!run) return null;

  const passed = run.passed || 0;
  const total = run.total || 0;
  const accuracyPct = run.accuracy_pct !== undefined ? run.accuracy_pct : 0;
  const isHighAccuracy = accuracyPct >= 90;

  return (
    <div
      style={{
        padding: '12px 16px',
        borderRadius: 8,
        background: isHighAccuracy ? 'rgba(34, 197, 94, 0.08)' : 'rgba(234, 179, 8, 0.08)',
        border: `1px solid ${isHighAccuracy ? 'rgba(34, 197, 94, 0.3)' : 'rgba(234, 179, 8, 0.3)'}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        {isHighAccuracy ? (
          <CheckCircle2 size={24} style={{ color: 'var(--color-success, #22c55e)' }} />
        ) : (
          <AlertTriangle size={24} style={{ color: 'var(--color-warning, #eab308)' }} />
        )}
        <div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>
            Truth Suite Accuracy:{' '}
            <span style={{ color: isHighAccuracy ? 'var(--color-success)' : 'var(--color-warning)' }}>
              {accuracyPct}%
            </span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {passed} out of {total} approved questions and window slices verified accurately against Snowflake &amp; Cortex semantic layer.
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-tertiary)' }}>
        <Clock size={12} /> Executed: {formatTimestamp(run.ran_at)}
      </div>
    </div>
  );
}
