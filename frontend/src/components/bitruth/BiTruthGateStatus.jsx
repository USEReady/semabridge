import React, { useState } from 'react';
import { ShieldCheck, ShieldAlert, AlertTriangle, ChevronDown, ChevronUp, Clock, Info } from 'lucide-react';
import { getGateBadge } from '../../utils/biTruthLabels';

export default function BiTruthGateStatus({ verdict, mode = 'report' }) {
  const [openFindings, setOpenFindings] = useState(false);

  if (!verdict || verdict.status === 'not_gated') return null;

  const badge = getGateBadge(verdict);
  const isBlockMode = verdict.mode === 'block' || mode === 'block';
  const isWarning = verdict.status === 'warning';
  const isBlocked = verdict.status === 'blocked';
  const isPassed = verdict.status === 'passed';
  const isStale = verdict.status === 'stale';

  let borderCol = 'var(--border-main)';
  let bgCol = 'var(--bg-surface)';
  let icon = <ShieldCheck size={16} style={{ color: 'var(--accent-blue)' }} />;

  if (isBlocked) {
    borderCol = 'rgba(239, 68, 68, 0.4)';
    bgCol = 'rgba(239, 68, 68, 0.08)';
    icon = <ShieldAlert size={16} style={{ color: 'var(--color-error, #ef4444)' }} />;
  } else if (isWarning) {
    borderCol = 'rgba(234, 179, 8, 0.4)';
    bgCol = 'rgba(234, 179, 8, 0.08)';
    icon = <AlertTriangle size={16} style={{ color: 'var(--color-warning, #eab308)' }} />;
  } else if (isPassed) {
    borderCol = 'rgba(34, 197, 94, 0.3)';
    bgCol = 'rgba(34, 197, 94, 0.05)';
    icon = <ShieldCheck size={16} style={{ color: 'var(--color-success, #22c55e)' }} />;
  } else if (isStale) {
    borderCol = 'rgba(234, 179, 8, 0.3)';
    bgCol = 'rgba(234, 179, 8, 0.05)';
    icon = <Clock size={16} style={{ color: 'var(--color-warning, #eab308)' }} />;
  }

  const findingsCount = (verdict.findings || []).length;

  return (
    <div
      style={{
        padding: '8px 12px',
        borderRadius: 8,
        background: bgCol,
        border: `1px solid ${borderCol}`,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        fontSize: 12,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {icon}
          <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
            Gate: {badge.label}
          </span>
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              padding: '1px 5px',
              borderRadius: 3,
              background: isBlockMode ? 'rgba(239,68,68,0.15)' : 'rgba(59,130,246,0.15)',
              color: isBlockMode ? 'var(--color-error)' : 'var(--accent-blue)',
            }}
          >
            {isBlockMode ? 'BLOCK' : 'REPORT'}
          </span>
          <span style={{ color: 'var(--text-secondary)' }}>• {badge.text}</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {verdict.critical_total > 0 && (
            <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
              Critical: <strong>{verdict.critical_passed}/{verdict.critical_total}</strong> passed
            </span>
          )}
          {findingsCount > 0 && (
            <button
              type="button"
              onClick={() => setOpenFindings(!openFindings)}
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: isBlocked ? 'var(--color-error)' : 'var(--color-warning)',
                fontSize: 11,
                fontWeight: 600,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 3,
                padding: '2px 4px',
              }}
            >
              {openFindings ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              {findingsCount} {findingsCount === 1 ? 'discrepancy' : 'discrepancies'}
            </button>
          )}
        </div>
      </div>

      {openFindings && findingsCount > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, borderTop: `1px solid ${borderCol}`, paddingTop: 6, marginTop: 2 }}>
          {verdict.findings.map((f, idx) => (
            <div key={idx} style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-primary)' }}>
              • {f}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
