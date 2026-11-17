import { useNavigate } from 'react-router-dom';
import Modal from './Modal';
import { useDuplicateSyncPrompt } from '../../utils/duplicateSyncPrompt';

const BTN = { padding: '7px 14px', borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: 'pointer' };
const SECONDARY_BTN = { ...BTN, background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)' };
const PRIMARY_BTN = { ...BTN, background: 'var(--accent-blue)', border: '1px solid var(--accent-blue)', color: '#fff' };
const CELL = { padding: '8px' };
const HEAD = { padding: '6px 8px' };
const MAX_BLOCKERS_SHOWN = 25;

const REASON_LABELS = {
  same_view: 'Same view name — this sync would overwrite it',
  same_model: 'Same model name',
  same_content: 'Same metrics and columns',
};

const TITLES = {
  duplicate: 'Model already synced',
  already_running: 'Sync already running',
  blocked: 'Dry run has unresolved issues',
};

function overlapText(part) {
  if (!part || !part.total) return '—';
  return `${part.shared} of ${part.total}`;
}

function DuplicateBody({ duplicates, message, overwrites }) {
  return (
    <>
      <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-secondary)' }}>
        {message || 'Some models in this project are already synced by another project.'}
        {overwrites && ' At least one would replace the other project\'s semantic view.'}
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-tertiary)' }}>
              <th style={HEAD}>Model</th>
              <th style={HEAD}>Already synced by</th>
              <th style={HEAD}>Why</th>
              <th style={HEAD}>Metrics</th>
              <th style={HEAD}>Columns</th>
            </tr>
          </thead>
          <tbody>
            {duplicates.map((d) => (
              <tr key={`${d.model}-${d.other_project_id}-${d.other_view_name}`} style={{ borderTop: '1px solid var(--border-main)' }}>
                <td style={CELL}>
                  <b>{d.model}</b>
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>{d.view_name}</div>
                </td>
                <td style={CELL}>
                  {d.other_project_name}
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>{d.target}.{d.other_view_name}</div>
                </td>
                <td style={CELL}>
                  {(d.reasons || []).map((r) => <div key={r}>{REASON_LABELS[r] || r}</div>)}
                </td>
                <td style={{ ...CELL, whiteSpace: 'nowrap' }}>{overlapText(d.metrics)}</td>
                <td style={{ ...CELL, whiteSpace: 'nowrap' }}>{overlapText(d.columns)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function BlockedBody({ detail, message }) {
  const blockers = Array.isArray(detail?.blockers) ? detail.blockers : [];
  const shown = blockers.slice(0, MAX_BLOCKERS_SHOWN);
  return (
    <>
      <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-secondary)' }}>
        {message || 'The dry run for this project has unresolved issues, so the sync was not started.'}
        {' '}Nothing was deployed. Open the dry run, resolve the issues, then sync again.
      </p>
      {shown.length > 0 && (
        <div style={{ overflowX: 'auto', maxHeight: 320 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--text-tertiary)' }}>
                <th style={HEAD}>Field</th>
                <th style={HEAD}>Target name</th>
                <th style={HEAD}>Issue</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((b, i) => (
                <tr key={b.id || `${b.source_path}-${i}`} style={{ borderTop: '1px solid var(--border-main)' }}>
                  <td style={CELL}>
                    <b>{b.source_name || b.source_path}</b>
                    {b.source_path && b.source_name && (
                      <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>{b.source_path}</div>
                    )}
                  </td>
                  <td style={CELL}><code>{b.target_name || '—'}</code></td>
                  <td style={CELL}>
                    {b.validation_code && <div style={{ fontWeight: 600 }}>{b.validation_code}</div>}
                    <div style={{ color: 'var(--text-secondary)' }}>{b.validation_message || b.validation_status || b.status}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {blockers.length > shown.length && (
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '6px 8px' }}>
              …and {blockers.length - shown.length} more in the dry run.
            </div>
          )}
        </div>
      )}
    </>
  );
}

/** Mounted once in the app layout; opened by api.runProjectNow / retryFailedModels. */
export default function DuplicateSyncDialog() {
  const navigate = useNavigate();
  const { open, kind, projectId, detail, duplicates, message, resolve, close } = useDuplicateSyncPrompt();

  const answer = (choice) => {
    resolve?.(choice);
    close();
    if (choice === 'view_run') {
      const runId = detail?.run_id;
      navigate(runId ? `/runs?run=${encodeURIComponent(runId)}` : '/runs', {
        state: runId ? { runId, autoExpand: true } : undefined,
      });
    } else if (choice === 'open_dry_run' && projectId) {
      // The edit wizard re-runs the dry run and shows the resolver.
      navigate(`/projects/${encodeURIComponent(projectId)}/edit`);
    }
  };

  const overwrites = duplicates.some((d) => d.overwrites);

  let footer;
  if (kind === 'already_running') {
    footer = (
      <>
        <button type="button" style={SECONDARY_BTN} onClick={() => answer('cancel')}>Close</button>
        <button type="button" style={PRIMARY_BTN} onClick={() => answer('view_run')}>View running sync</button>
      </>
    );
  } else if (kind === 'blocked') {
    footer = (
      <>
        <button type="button" style={SECONDARY_BTN} onClick={() => answer('cancel')}>Close</button>
        {projectId && (
          <button type="button" style={PRIMARY_BTN} onClick={() => answer('open_dry_run')}>Open dry run to resolve</button>
        )}
      </>
    );
  } else {
    footer = (
      <>
        <button type="button" style={SECONDARY_BTN} onClick={() => answer('cancel')}>Cancel sync</button>
        <button type="button" style={PRIMARY_BTN} onClick={() => answer('confirm')}>
          {overwrites ? 'Overwrite and sync' : 'Sync anyway'}
        </button>
      </>
    );
  }

  return (
    <Modal
      open={open}
      onClose={() => answer('cancel')}
      title={TITLES[kind] || TITLES.duplicate}
      size="lg"
      footer={<div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>{footer}</div>}
    >
      {kind === 'already_running' && (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)' }}>
          {message || 'A sync of this project is already running.'}
          {' '}Only one sync per project runs at a time, so this one was not started. Nothing was changed.
        </p>
      )}
      {kind === 'blocked' && <BlockedBody detail={detail} message={message} />}
      {kind === 'duplicate' && <DuplicateBody duplicates={duplicates} message={message} overwrites={overwrites} />}
    </Modal>
  );
}
