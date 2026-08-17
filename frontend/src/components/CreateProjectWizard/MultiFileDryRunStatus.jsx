/**
 * MultiFileDryRunStatus — per-file status list + drill-in + independent
 * re-run for a multi-PBIX background dry-run job.
 *
 * Modeled on ProjectJobsPage.jsx's per-run list pattern (CSS-grid rows +
 * StatusBadge + a self-contained setTimeout polling loop) rather than
 * inventing a new list/poll pattern — the difference here is polling one
 * job's per-file statuses instead of a project's per-run history.
 *
 * On partial failure, every configured file is still shown here with its
 * own real status/error — a failed file never hides a sibling's success,
 * and clicking "Retry" re-runs only that one file (dry_run_jobs_controller's
 * rerun endpoint resets/reschedules just that file's row).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, RotateCcw } from 'lucide-react';
import { api } from '../../utils/api';
import StatusBadge from '../common/StatusBadge';
import DryRunMappingTable from '../DryRunMappingTable';
import { normalizeRows } from '../../utils/normalizeRows';

const POLL_INTERVAL_MS = 3000;
const GRID_COLUMNS = '28px 1fr 140px 120px';

export default function MultiFileDryRunStatus({ projectId, jobId, onEdit }) {
  const [status, setStatus] = useState(null); // { status, files: [...] }
  const [expandedFileId, setExpandedFileId] = useState(null);
  const [fileDetails, setFileDetails] = useState({}); // file_id -> { loading, result, error }
  const [rerunningFileIds, setRerunningFileIds] = useState(new Set());
  const timeoutRef = useRef(null);
  const cancelledRef = useRef(false);

  const poll = useCallback(async () => {
    if (!projectId || !jobId || cancelledRef.current) return;
    try {
      const data = await api.getDryRunJobStatus(projectId, jobId);
      if (cancelledRef.current) return;
      setStatus(data);
      if (data.status === 'running' || data.status === 'pending') {
        timeoutRef.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    } catch (err) {
      if (!cancelledRef.current) {
        timeoutRef.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }
  }, [projectId, jobId]);

  useEffect(() => {
    cancelledRef.current = false;
    setStatus(null);
    setExpandedFileId(null);
    setFileDetails({});
    poll();
    return () => {
      cancelledRef.current = true;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, [poll]);

  const toggleExpand = useCallback(async (fileId) => {
    if (expandedFileId === fileId) {
      setExpandedFileId(null);
      return;
    }
    setExpandedFileId(fileId);
    if (fileDetails[fileId]?.result || fileDetails[fileId]?.loading) return;
    setFileDetails((prev) => ({ ...prev, [fileId]: { loading: true } }));
    try {
      const detail = await api.getDryRunJobFile(projectId, jobId, fileId);
      setFileDetails((prev) => ({ ...prev, [fileId]: { loading: false, result: detail.result, error: detail.error } }));
    } catch (err) {
      setFileDetails((prev) => ({ ...prev, [fileId]: { loading: false, error: err?.message || 'Failed to load results.' } }));
    }
  }, [expandedFileId, fileDetails, projectId, jobId]);

  const handleRerun = useCallback(async (fileId, evt) => {
    evt.stopPropagation();
    setRerunningFileIds((prev) => new Set(prev).add(fileId));
    setFileDetails((prev) => { const next = { ...prev }; delete next[fileId]; return next; });
    try {
      await api.rerunDryRunJobFile(projectId, jobId, fileId);
    } finally {
      setRerunningFileIds((prev) => { const next = new Set(prev); next.delete(fileId); return next; });
    }
    // Resume polling immediately to reflect the reset 'pending' state and
    // then the new outcome, without touching any sibling file's row.
    poll();
  }, [projectId, jobId, poll]);

  if (!status) {
    return (
      <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 16, fontSize: 12, color: 'var(--text-tertiary)' }}>
        Starting dry-run job…
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
          Multi-file dry run — {status.files.length} file{status.files.length !== 1 ? 's' : ''}
        </div>
        <StatusBadge status={status.status} size="sm" />
      </div>

      {status.files.map((file) => {
        const expanded = expandedFileId === file.file_id;
        const detail = fileDetails[file.file_id];
        const isRerunning = rerunningFileIds.has(file.file_id);
        const canRerun = file.status === 'failed' && !isRerunning;
        return (
          <div key={file.file_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <div
              onClick={() => toggleExpand(file.file_id)}
              style={{
                display: 'grid',
                gridTemplateColumns: GRID_COLUMNS,
                gap: 10,
                alignItems: 'center',
                padding: '10px 14px',
                cursor: 'pointer',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
            >
              {expanded
                ? <ChevronDown size={13} style={{ color: 'var(--text-tertiary)' }} />
                : <ChevronRight size={13} style={{ color: 'var(--text-tertiary)' }} />}
              <div style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{file.filename}</span>
                {file.status === 'failed' && file.error_summary && (
                  <div style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {file.error_summary}
                  </div>
                )}
              </div>
              <StatusBadge status={isRerunning ? 'running' : file.status} size="sm" />
              <div>
                {canRerun && (
                  <button
                    onClick={(evt) => handleRerun(file.file_id, evt)}
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
            </div>

            {expanded && (
              <div style={{ padding: '0 14px 14px' }}>
                {detail?.loading && (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '8px 0' }}>Loading results…</div>
                )}
                {detail?.error && !detail?.result && (
                  <div style={{ fontSize: 12, color: 'var(--color-error)', padding: '8px 0' }}>{detail.error}</div>
                )}
                {detail?.result && (
                  <DryRunMappingTable
                    // MUST go through normalizeRows() here, same as the single-file
                    // flow (CreateProjectPage.jsx's runDryRun) -- detail.result is
                    // the RAW /dry-run-job file response (source_name/target_name/
                    // source_expression/...), not the NormalizedRow[] shape
                    // DryRunMappingTable's `mappings` prop requires. Passing
                    // detail.result.entity_mappings directly here previously
                    // produced blank DAX expressions and "fx Measure"/"unknown"/
                    // "-- unmapped --" placeholders on every row despite the
                    // underlying dry run having succeeded. See the guardrail test
                    // in DryRunMappingTable.guardrail.test.js, which fails the
                    // build if any future caller reintroduces this.
                    mappings={normalizeRows(detail.result)}
                    summary={detail.result.summary}
                    onEdit={onEdit}
                    projectId={projectId}
                    droppedEntities={detail.result.dropped_entities || []}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
