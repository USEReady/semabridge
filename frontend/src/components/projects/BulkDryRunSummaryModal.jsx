/**
 * BulkDryRunSummaryModal — aggregated dry-run health across several
 * projects run together via ProjectsPage's "Run Dry Run Selected" bulk
 * action, plus a per-project breakdown so a user reviewing many projects at
 * once can see which ones need attention without opening each individually.
 *
 * The actual field-level detail (mapping table, presentation layer,
 * synonyms, etc.) still lives per-project in DryRunMappingTable -- this
 * modal is the "which of these N projects needs my attention" triage step
 * that sits in front of it, not a replacement for it.
 *
 * Props:
 *   open    — boolean
 *   onClose — function
 *   results — Array<{
 *     projectId, projectName,
 *     status: 'success' | 'error',
 *     health?: { success, needs_resolve, at_risk },
 *     compatibilityScore?: number | null,
 *     error?: string,
 *   }>
 *   onOpenProject — (projectId) => void, navigates to that project's mapping view
 */

import { X, CheckCircle2, AlertTriangle, AlertCircle } from 'lucide-react';

function Chip({ count, color, label, title }) {
  if (!count) return null;
  return (
    <span style={{ color, fontWeight: 700, fontSize: 12 }} title={title}>
      {count} {label}
    </span>
  );
}

export default function BulkDryRunSummaryModal({ open, onClose, results = [], onOpenProject }) {
  if (!open) return null;

  const totals = results.reduce((acc, r) => {
    if (r.status === 'error') {
      acc.errored += 1;
      return acc;
    }
    acc.success += r.health?.success || 0;
    acc.needsResolve += r.health?.needs_resolve || 0;
    acc.atRisk += r.health?.at_risk || 0;
    return acc;
  }, { success: 0, needsResolve: 0, atRisk: 0, errored: 0 });

  const projectsNeedingAttention = results.filter(
    r => r.status === 'error' || (r.health?.needs_resolve || 0) > 0 || (r.health?.at_risk || 0) > 0,
  ).length;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 12,
          width: '100%', maxWidth: 760,
          maxHeight: '85vh',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 16px 48px rgba(0,0,0,0.22)',
        }}
      >
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Dry Run Summary</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
              {results.length} project{results.length !== 1 ? 's' : ''} checked
              {projectsNeedingAttention > 0 && ` — ${projectsNeedingAttention} need${projectsNeedingAttention === 1 ? 's' : ''} a closer look`}
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        {/* Grand totals */}
        <div style={{
          display: 'flex', gap: 20, padding: '14px 20px',
          borderBottom: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <CheckCircle2 size={15} style={{ color: 'var(--color-success)' }} />
            <span style={{ fontSize: 13, color: 'var(--text-primary)' }}><b>{totals.success}</b> mapped cleanly</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <AlertCircle size={15} style={{ color: 'var(--color-error)' }} />
            <span style={{ fontSize: 13, color: 'var(--text-primary)' }}><b>{totals.needsResolve}</b> need resolving</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <AlertTriangle size={15} style={{ color: '#f59e0b' }} />
            <span style={{ fontSize: 13, color: 'var(--text-primary)' }}><b>{totals.atRisk}</b> at risk of failing</span>
          </div>
          {totals.errored > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <AlertCircle size={15} style={{ color: 'var(--color-error)' }} />
              <span style={{ fontSize: 13, color: 'var(--text-primary)' }}><b>{totals.errored}</b> failed to run</span>
            </div>
          )}
        </div>

        {/* Per-project breakdown */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          <div style={{
            display: 'grid', gridTemplateColumns: '1.6fr 0.7fr 0.9fr 0.7fr 90px',
            gap: 8, padding: '8px 20px', fontSize: 11, fontWeight: 700,
            color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em',
            borderBottom: '1px solid var(--border-subtle)', position: 'sticky', top: 0, background: 'var(--bg-surface)',
          }}>
            <div>Project</div>
            <div style={{ textAlign: 'right' }}>Clean</div>
            <div style={{ textAlign: 'right' }}>Needs Resolve</div>
            <div style={{ textAlign: 'right' }}>At Risk</div>
            <div />
          </div>
          {results.map(r => (
            <div
              key={r.projectId}
              style={{
                display: 'grid', gridTemplateColumns: '1.6fr 0.7fr 0.9fr 0.7fr 90px',
                gap: 8, padding: '10px 20px', alignItems: 'center', fontSize: 12,
                borderBottom: '1px solid var(--border-subtle)',
              }}
            >
              <div style={{ color: 'var(--text-primary)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {r.projectName}
              </div>
              {r.status === 'error' ? (
                <div style={{ gridColumn: '2 / span 3', color: 'var(--color-error)', fontSize: 11 }} title={r.error}>
                  Dry run failed: {r.error}
                </div>
              ) : (
                <>
                  <div style={{ textAlign: 'right' }}>
                    <Chip count={r.health?.success} color="var(--color-success)" label="" />
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <Chip count={r.health?.needs_resolve} color="var(--color-error)" label="" title="Unmapped or colliding fields" />
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <Chip count={r.health?.at_risk} color="#f59e0b" label="" title="Flagged as likely to fail at real deploy time" />
                  </div>
                </>
              )}
              <div style={{ textAlign: 'right' }}>
                <button
                  onClick={() => onOpenProject?.(r.projectId)}
                  style={{
                    padding: '4px 10px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                    background: 'transparent', border: '1px solid var(--border-main)',
                    color: 'var(--accent-blue)', cursor: 'pointer',
                  }}
                >
                  View
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border-main)', display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              padding: '7px 16px', borderRadius: 7, background: 'var(--accent-blue)', border: 'none',
              color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
            }}
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
