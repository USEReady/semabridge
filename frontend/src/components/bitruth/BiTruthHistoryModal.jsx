import React from 'react';
import { History, Eye, Send, CheckCircle2, AlertTriangle, XCircle, Clock } from 'lucide-react';
import Modal from '../common/Modal';
import { formatTimestamp } from '../../utils/biTruthLabels';

export default function BiTruthHistoryModal({
  open,
  onClose,
  runs = [],
  injectionHistory = [],
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="BI Truth Activity History (Reads vs Writes)"
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13, maxHeight: '75vh' }}>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
          Test suite runs read warehouse data to verify benchmark accuracy. Injections actively write verified pairs to warehouse semantic models. They are recorded side-by-side to maintain clean governance boundaries.
        </p>

        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 16,
            minHeight: 280,
            maxHeight: '60vh',
            overflowY: 'auto',
          }}
        >
          {/* Left Column: Test Runs (Reads) */}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
              padding: 12,
              borderRadius: 8,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 13, color: 'var(--accent-blue)' }}>
              <Eye size={16} /> Suite Runs (Read Operations)
            </div>

            {runs.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '20px 0', textAlign: 'center' }}>
                No suite execution runs recorded yet.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
                {runs.map((r, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: 10,
                      borderRadius: 6,
                      background: 'var(--bg-surface-raised)',
                      border: '1px solid var(--border-main)',
                      fontSize: 12,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontWeight: 600 }}>Accuracy: {r.accuracy_pct !== undefined ? `${r.accuracy_pct}%` : '—'}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{formatTimestamp(r.ran_at)}</span>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
                      {r.passed || 0} passed / {r.total || 0} evaluated
                      {r.view ? ` • ${r.view}` : ''}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right Column: Injections (Writes) */}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
              padding: 12,
              borderRadius: 8,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 13, color: 'var(--color-warning, #d97706)' }}>
              <Send size={16} /> Warehouse Injections (Write Operations)
            </div>

            {injectionHistory.length === 0 ? (
              <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '20px 0', textAlign: 'center' }}>
                No verified query injections recorded yet.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
                {injectionHistory.map((inj, idx) => {
                  const isFail = inj.status === 'failed' || inj.outcome === 'failed';
                  const isPartial = inj.status === 'partial' || inj.outcome === 'partial';
                  const isSuccess = !isFail && !isPartial;

                  return (
                    <div
                      key={idx}
                      style={{
                        padding: 10,
                        borderRadius: 6,
                        background: 'var(--bg-surface-raised)',
                        border: `1px solid ${
                          isSuccess
                            ? 'rgba(34, 197, 94, 0.3)'
                            : isPartial
                            ? 'rgba(245, 158, 11, 0.4)'
                            : 'rgba(239, 68, 68, 0.3)'
                        }`,
                        fontSize: 12,
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <span
                          style={{
                            fontWeight: 700,
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 4,
                            color: isSuccess
                              ? 'var(--color-success, #22c55e)'
                              : isPartial
                              ? 'var(--color-warning, #d97706)'
                              : 'var(--color-error, #ef4444)',
                          }}
                        >
                          {isSuccess && <CheckCircle2 size={13} />}
                          {isPartial && <AlertTriangle size={13} />}
                          {isFail && <XCircle size={13} />}
                          {inj.status ? inj.status.toUpperCase() : 'PUBLISHED'}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                          {formatTimestamp(inj.injected_at || inj.timestamp)}
                        </span>
                      </div>

                      <div style={{ fontSize: 11, color: 'var(--text-primary)', marginTop: 4, fontWeight: 500 }}>
                        {inj.injected_count || 0} pair{(inj.injected_count || 0) === 1 ? '' : 's'} written
                        {inj.platform ? ` to ${inj.platform}` : ''}
                      </div>

                      <div style={{ fontSize: 10.5, color: 'var(--text-secondary)', marginTop: 2, fontFamily: 'monospace' }}>
                        Target: {inj.target || 'Stage model YAML'}
                      </div>

                      <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 2 }}>
                        Actor: {inj.injected_by || inj.actor || 'System'}
                      </div>

                      {inj.reason && (
                        <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 4, fontStyle: 'italic' }}>
                          {inj.reason}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--bg-surface-raised)',
              border: '1px solid var(--border-main)',
              color: 'var(--text-primary)',
              fontSize: 12,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Close
          </button>
        </div>
      </div>
    </Modal>
  );
}
