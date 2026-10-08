import { useState } from 'react';
import { ChevronDown, ChevronRight, FileText, AlertTriangle } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';

/**
 * ModelRunSection -- one collapsible block per model inside an expanded
 * multi-model run on the Runs page. Purely presentational: the entry comes
 * from getPerModelRunEntries(run) (utils/runLogs.js) and the two buttons
 * delegate to the page's existing report / dropped-fields modals, opened
 * preselected to this model.
 *
 * Props:
 *   entry               : one getPerModelRunEntries() item
 *   onViewReport        : (entry) => void   -- omitted/undefined hides the button
 *   onViewDroppedFields : (entry) => void   -- omitted/undefined hides the button
 *   formatDuration      : (ms) => string
 *   defaultOpen         : boolean (default false)
 */

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

const STATUS_BADGE = {
  success: { status: 'success' },
  failed: { status: 'failed' },
  error: { status: 'failed' },
  conflict: { status: 'warning', label: 'Conflict' },
  partial: { status: 'partial' },
  warning: { status: 'warning' },
  running: { status: 'running' },
  unknown: { status: 'pending', label: 'Unknown' },
};

const actionButtonStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 600,
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-main)',
  cursor: 'pointer',
};

function ReconciliationSummary({ reconciliation }) {
  if (!reconciliation) return null;
  const unaccounted = reconciliation.unaccounted && typeof reconciliation.unaccounted === 'object'
    ? Object.entries(reconciliation.unaccounted)
    : [];
  const clean = reconciliation.is_clean === true;
  const accent = reconciliation.ddl_error
    ? 'var(--color-error)'
    : clean ? 'var(--color-success)' : 'var(--color-warning)';

  return (
    <div
      style={{
        marginTop: 10,
        padding: 10,
        borderRadius: 6,
        background: 'var(--bg-input)',
        borderLeft: `3px solid ${accent}`,
        fontSize: 12,
        color: 'var(--text-secondary)',
        overflowWrap: 'anywhere',
      }}
    >
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Reconciliation{' '}
        <span style={{ color: accent, fontWeight: 600 }}>
          {reconciliation.ddl_error ? 'could not run' : clean ? 'clean' : 'needs attention'}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
        {reconciliation.deployed_live_count != null && (
          <span>Live metrics: <strong style={{ color: 'var(--text-primary)' }}>{reconciliation.deployed_live_count}</strong></span>
        )}
        {reconciliation.deployed_dead_count != null && (
          <span>Dead metrics: <strong style={{ color: 'var(--text-primary)' }}>{reconciliation.deployed_dead_count}</strong></span>
        )}
        <span>Unaccounted: <strong style={{ color: unaccounted.length ? 'var(--color-warning)' : 'var(--text-primary)' }}>{unaccounted.length}</strong></span>
      </div>
      {unaccounted.length > 0 && (
        <div style={{ marginTop: 6, fontFamily: MONO, fontSize: 11 }}>
          {unaccounted.map(([name, count]) => `${name} (${count})`).join(', ')}
        </div>
      )}
      {reconciliation.ddl_error && (
        <div style={{ marginTop: 6, color: 'var(--color-error)' }}>{String(reconciliation.ddl_error)}</div>
      )}
    </div>
  );
}

export default function ModelRunSection({
  entry,
  onViewReport,
  onViewDroppedFields,
  formatDuration,
  defaultOpen = false,
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!entry) return null;

  const badge = STATUS_BADGE[entry.status] || { status: entry.status || 'pending' };
  const durationLabel = entry.durationMs && formatDuration ? formatDuration(entry.durationMs) : null;
  const droppedCount = entry.droppedEntities?.length || 0;

  return (
    <div
      style={{
        border: '1px solid var(--border-main)',
        borderRadius: 8,
        background: 'var(--bg-surface)',
        marginBottom: 10,
        minWidth: 0,
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{
          width: '100%',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 10,
          padding: '10px 12px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          textAlign: 'left',
          color: 'var(--text-primary)',
        }}
      >
        {open
          ? <ChevronDown size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
          : <ChevronRight size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
        <span
          title={entry.model || entry.label}
          style={{ fontSize: 13, fontWeight: 600, flex: '1 1 180px', minWidth: 0, overflowWrap: 'anywhere' }}
        >
          {entry.label}
        </span>
        <StatusBadge status={badge.status} label={badge.label} size="sm" />
        {entry.viewName && (
          <span
            title="Semantic view"
            style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-secondary)', minWidth: 0, overflowWrap: 'anywhere' }}
          >
            {entry.viewName}
          </span>
        )}
        {durationLabel && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
            {durationLabel}
          </span>
        )}
      </button>

      {open && (
        <div style={{ padding: '0 12px 12px 12px', borderTop: '1px solid var(--border-subtle, var(--border-main))' }}>
          {(onViewReport || onViewDroppedFields) && (
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8, margin: '10px 0' }}>
              {onViewReport && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onViewReport(entry); }}
                  style={actionButtonStyle}
                >
                  <FileText size={14} /> View Report
                </button>
              )}
              {onViewDroppedFields && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onViewDroppedFields(entry); }}
                  style={actionButtonStyle}
                >
                  <AlertTriangle size={14} /> Dropped Fields{droppedCount ? ` (${droppedCount})` : ''}
                </button>
              )}
            </div>
          )}

          {entry.targetArtifactPath && (
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 8, overflowWrap: 'anywhere' }}>
              Artifact: <span style={{ fontFamily: MONO }}>{entry.targetArtifactPath}</span>
            </div>
          )}

          <ReconciliationSummary reconciliation={entry.reconciliation} />

          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '10px 0 8px 0' }}>
            Execution Stages
          </div>
          <div
            style={{
              borderRadius: 8,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-input)',
              padding: 12,
              fontFamily: MONO,
              fontSize: 12,
              lineHeight: 1.65,
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              maxHeight: 420,
              overflowY: 'auto',
            }}
          >
            {entry.logs?.length ? entry.logs.join('\n') : 'No logs recorded for this model.'}
          </div>
        </div>
      )}
    </div>
  );
}
