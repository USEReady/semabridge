/**
 * MultiFileDryRunStatus — one collapsible section per model for a
 * multi-model (multiple PBIX files OR multiple Fabric models) dry run.
 *
 * Purely presentational: polling, per-file result loading and the per-model
 * rows/edits live in useMultiDryRunJob (owned by CreateProjectPage) and are
 * passed in as `sections` (see utils/multiFileDryRunReadiness.js
 * buildMultiModelSections). Each section's body is a regular
 * DryRunMappingTable instance fed only that model's rows, so every feature of
 * the single-model page (filter bar, edit/rename, collision Fix + Resolve
 * All, synonyms, dropped fields, risk labels) works per model with its own
 * independent filter state. Nothing here is model-specific.
 *
 * A section expanded while its model is still running shows the table as
 * soon as that model's result lands. A failed model shows its real error and
 * a per-model Retry; other models are never hidden by a sibling's failure.
 */
import { useCallback, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, RotateCcw } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';
import DryRunMappingTable from '../DryRunMappingTable';
import UpsertChangesView from '../UpsertChangesView';
import PanelBoundary from '../common/PanelBoundary';
import TableBindingPlan from '../TableBindingPlan';

function pluralize(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

function SectionCounts({ section }) {
  if (!section.loaded) return null;
  const unresolvedCollisions = section.unresolvedCollisions ?? (
    Array.isArray(section.rows)
      ? section.rows.filter((r) => String(r.status || '').toLowerCase() === 'collision').length
      : 0
  );
  return (
    <span style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
      {pluralize(section.columnCount, 'column')} · {pluralize(section.metricCount, 'metric')}
      {unresolvedCollisions > 0 && (
        <>
          {' · '}
          <span style={{ color: 'var(--color-error)', fontWeight: 700 }}>
            {pluralize(unresolvedCollisions, 'unresolved collision')}
          </span>
        </>
      )}
      {' · '}
      <span style={{ color: section.blockingCount > 0 ? 'var(--color-error)' : 'var(--color-success)', fontWeight: 700 }}>
        {section.blockingCount > 0 ? `${section.blockingCount} blocking` : 'no blocking issues'}
      </span>
    </span>
  );
}

export default function MultiFileDryRunStatus({
  sections = [],
  jobStatus = null,
  projectId,
  relationships = [],
  rerunningIds = new Set(),
  rerunErrors = {},
  pollError = '',
  onRetryPolling,
  onEditRow,          // (fileId, rowId, updates?) => void
  onBulkResolved,     // (fileId, resolvedMap) => void
  onSynonymUpdate,    // (fileId, rowId, synonyms) => void
  onRetryFile,        // (fileId) => void
}) {
  const [expanded, setExpanded] = useState(() => new Set());

  const toggle = useCallback((fileId) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) next.delete(fileId); else next.add(fileId);
      return next;
    });
  }, []);

  const allExpanded = sections.length > 0 && sections.every((s) => expanded.has(s.fileId));
  const kind = sections.length === 1 ? 'model' : 'models';

  if (!jobStatus && sections.length === 0) {
    return (
      <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 16, fontSize: 12, color: 'var(--text-tertiary)' }}>
        Starting dry-run job…
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
          Multi-model dry run — {sections.length} {kind}
        </div>
        {jobStatus?.status && <StatusBadge status={jobStatus.status} size="sm" />}
        <button
          type="button"
          onClick={() => setExpanded(allExpanded ? new Set() : new Set(sections.map((s) => s.fileId)))}
          style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--accent-blue)', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}
        >
          {allExpanded ? 'Collapse all' : 'Expand all'}
        </button>
      </div>

      {pollError && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, border: '1px solid rgba(239, 68, 68, 0.45)', background: 'rgba(239, 68, 68, 0.10)', color: 'var(--color-error)', borderRadius: 8, padding: '8px 12px', fontSize: 12 }}>
          <span style={{ flex: 1 }}>Lost contact with the dry-run job: {pollError}</span>
          {onRetryPolling && (
            <button type="button" onClick={onRetryPolling} style={{ background: 'none', border: '1px solid var(--border-main)', borderRadius: 6, padding: '3px 8px', fontSize: 11, color: 'var(--text-secondary)', cursor: 'pointer' }}>
              Reconnect
            </button>
          )}
        </div>
      )}

      {sections.map((section) => {
        const isOpen = expanded.has(section.fileId);
        const isRerunning = rerunningIds.has(section.fileId);
        const failed = section.status === 'failed' || Boolean(section.loadError);
        const canRetry = failed && !isRerunning;
        const badgeStatus = isRerunning ? 'running' : section.status;
        const errorText = section.status === 'failed'
          ? (section.error || 'This model failed during the dry run.')
          : section.loadError;
        return (
          <div
            key={section.fileId}
            style={{
              border: `1px solid ${failed ? 'rgba(239, 68, 68, 0.35)' : section.blockingCount > 0 ? 'rgba(251, 191, 36, 0.35)' : 'var(--border-main)'}`,
              borderRadius: 10,
              background: 'var(--bg-surface)',
              overflow: 'hidden',
            }}
          >
            <div
              role="button"
              tabIndex={0}
              aria-expanded={isOpen}
              onClick={() => toggle(section.fileId)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(section.fileId); } }}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', cursor: 'pointer', userSelect: 'none' }}
            >
              {isOpen
                ? <ChevronDown size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                : <ChevronRight size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div title={section.sourceRef || section.label} style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {section.label}
                </div>
                {failed && errorText && !isOpen && (
                  <div title={errorText} style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {errorText}
                  </div>
                )}
              </div>
              <SectionCounts section={section} />
              <StatusBadge status={badgeStatus} size="sm" />
              {canRetry && onRetryFile && (
                <button
                  type="button"
                  onClick={(evt) => { evt.stopPropagation(); onRetryFile(section.fileId); }}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 5,
                    background: 'none', border: '1px solid var(--border-main)', borderRadius: 6,
                    padding: '4px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', cursor: 'pointer',
                  }}
                >
                  <RotateCcw size={11} /> Retry
                </button>
              )}
            </div>

            {rerunErrors[section.fileId] && (
              <div style={{ padding: '0 14px 8px', fontSize: 11, color: 'var(--color-error)' }}>
                Retry failed: {rerunErrors[section.fileId]}
              </div>
            )}

            {isOpen && (
              <div style={{ padding: '0 14px 14px', borderTop: '1px solid var(--border-subtle)' }}>
                {(section.status === 'pending' || section.status === 'running' || isRerunning) && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-tertiary)', padding: '12px 0' }}>
                    <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />
                    Dry run in progress for this model — results will appear here as soon as it finishes.
                  </div>
                )}
                {section.loading && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-tertiary)', padding: '12px 0' }}>
                    <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />
                    Loading results…
                  </div>
                )}
                {failed && errorText && (
                  <div style={{ fontSize: 12, color: 'var(--color-error)', padding: '12px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {errorText}
                  </div>
                )}
                {failed && (section.result?.blockers?.length > 0 || section.result?.table_binding_plan?.blockers?.length > 0) && (
                  <div style={{ paddingBottom: 12 }}>
                    <TableBindingPlan
                      plan={section.result?.table_binding_plan}
                      blockers={section.result?.blockers || section.result?.table_binding_plan?.blockers}
                      projectId={section.previewProjectId || section.result?.project_id || projectId}
                      modelName={section.result?.model_name || section.label}
                      onReSync={onRetryFile ? () => onRetryFile(section.fileId) : undefined}
                      style={{ marginTop: 0, marginBottom: 12 }}
                    />
                  </div>
                )}
                {section.loaded && (
                  <div style={{ paddingTop: 12 }}>
                    <PanelBoundary name="Target tables">
                      <TableBindingPlan
                        plan={section.result?.table_binding_plan}
                        blockers={section.result?.blockers || section.result?.table_binding_plan?.blockers}
                        projectId={section.previewProjectId || section.result?.project_id || projectId}
                        modelName={section.result?.model_name || section.label}
                        onReSync={onRetryFile ? () => onRetryFile(section.fileId) : undefined}
                        style={{ marginTop: 0, marginBottom: 12 }}
                      />
                    </PanelBoundary>
                    <UpsertChangesView delta={section.result?.upsert_delta} modelLabel={section.result?.model_name || section.label} isDryRun>
                      <DryRunMappingTable
                        // key: a re-run (new attempt) starts with fresh filter state.
                        key={section.attempt}
                        // Rows are already NormalizedRow[] -- useMultiDryRunJob runs
                        // every per-file result through normalizeRows() on load.
                        mappings={section.rows}
                        summary={section.result?.summary}
                        relationships={relationships}
                        onEdit={(rowId, updates) => onEditRow?.(section.fileId, rowId, updates)}
                        onBulkResolved={(resolvedMap) => onBulkResolved?.(section.fileId, resolvedMap)}
                        onSynonymUpdate={(rowId, synonyms) => onSynonymUpdate?.(section.fileId, rowId, synonyms)}
                        projectId={section.previewProjectId || section.result?.project_id || projectId}
                        modelName={section.result?.model_name || section.label}
                        schemaConflicts={section.result?.schema_conflicts || []}
                        compatibilityScore={section.compatibilityScore}
                        onReSync={onRetryFile ? () => onRetryFile(section.fileId) : undefined}
                        droppedEntities={section.result?.dropped_entities || []}
                        unresolvedReportFieldReferences={section.result?.unresolved_report_field_references || []}
                      />
                    </UpsertChangesView>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
