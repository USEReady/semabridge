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
  same_file: 'Same PBIX file (identical content)',
  same_view: 'Same view name — this sync would overwrite it',
  same_model: 'Same model name',
  same_content: 'Same metrics and columns',
  same_file_in_progress: 'Same PBIX file — another project is syncing it right now',
  same_file_dry_run: 'Same PBIX file — dry-run by another project, not synced yet',
  same_model_in_progress: 'Same model — another project is syncing it right now',
  same_model_dry_run: 'Same model — dry-run by another project, not synced yet',
};

const TITLES = {
  duplicate: 'Model already synced',
  duplicate_check_failed: 'Could not check for duplicates',
  already_running: 'Sync already running',
  blocked: 'Dry run has unresolved issues',
  removed_fields: 'Fields will be removed',
};

const MAX_REMOVED_SHOWN = 50;

function RemovedFieldsBody({ detail, message }) {
  const models = Array.isArray(detail?.models) ? detail.models : [];
  const unverifiable = Array.isArray(detail?.unverifiable) ? detail.unverifiable : [];
  const promoted = Boolean(detail?.promoted_from_upsert);
  return (
    <>
      <p style={{ margin: '0 0 8px', fontSize: 13, color: 'var(--text-secondary)' }}>
        {message || 'Some fields in the deployed semantic view are no longer in the source.'}
      </p>
      {promoted ? (
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--text-tertiary)' }}>
          This sync runs as a <b>Copy</b> because at least one model has never been deployed by this project
          (a first deployment creates its objects). <b>Remove them and sync</b> runs that Copy.
        </p>
      ) : (
        <p style={{ margin: '0 0 12px', fontSize: 12.5, color: 'var(--text-tertiary)' }}>
          <b>Remove them</b> runs the Copy sync (full rebuild from the source). <b>Keep them</b> runs this sync
          as an Upsert: new and changed fields are deployed and these stay in Snowflake.
        </p>
      )}
      {unverifiable.length > 0 && (
        <div style={{ margin: '0 0 12px', fontSize: 12.5 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Cannot verify</div>
          <div style={{ color: 'var(--text-tertiary)', marginBottom: 4 }}>
            These models are deployed, but there is no successful dry run of the content about to be synced,
            so the fields a Copy would remove are unknown. Re-run the dry run to see them.
          </div>
          <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-secondary)' }}>
            {unverifiable.map((m) => (
              <li key={`${m.model}-${m.view_name}`}>
                <b>{m.model}</b>
                {m.view_name && <span style={{ color: 'var(--text-tertiary)' }}> ({m.view_name})</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div style={{ maxHeight: 320, overflowY: 'auto' }}>
        {models.map((m) => (
          <div key={`${m.model}-${m.view_name}`} style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 4 }}>
              {m.model} <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>({m.count} field{m.count === 1 ? '' : 's'})</span>
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: 'var(--text-secondary)' }}>
              {(m.removed || []).slice(0, MAX_REMOVED_SHOWN).map((f) => (
                <li key={`${f.kind}-${f.name}`}>
                  <code>{f.name}</code> <span style={{ color: 'var(--text-tertiary)' }}>{f.kind}</span>
                </li>
              ))}
              {(m.removed || []).length > MAX_REMOVED_SHOWN && (
                <li style={{ color: 'var(--text-tertiary)' }}>…and {m.removed.length - MAX_REMOVED_SHOWN} more</li>
              )}
            </ul>
          </div>
        ))}
      </div>
    </>
  );
}

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
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>
                    {d.other_view_name ? `${d.target}.${d.other_view_name}` : d.target}
                  </div>
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
  } else if (kind === 'removed_fields') {
    const hasUnverifiable = Array.isArray(detail?.unverifiable) && detail.unverifiable.length > 0;
    const promoted = Boolean(detail?.promoted_from_upsert);
    footer = (
      <>
        <button type="button" style={SECONDARY_BTN} onClick={() => answer('cancel')}>Cancel sync</button>
        {hasUnverifiable && projectId && (
          <button type="button" style={SECONDARY_BTN} onClick={() => answer('open_dry_run')}>Re-run dry run</button>
        )}
        {!promoted && (
          <button type="button" style={SECONDARY_BTN} onClick={() => answer('upsert')}>Keep them (sync as Upsert)</button>
        )}
        <button type="button" style={PRIMARY_BTN} onClick={() => answer('confirm')}>
          {hasUnverifiable && !(detail?.models || []).length ? 'Sync as Copy anyway' : 'Remove them and sync'}
        </button>
      </>
    );
  } else if (kind === 'duplicate_check_failed') {
    footer = (
      <>
        <button type="button" style={SECONDARY_BTN} onClick={() => answer('cancel')}>Cancel sync</button>
        <button type="button" style={PRIMARY_BTN} onClick={() => answer('confirm')}>Sync anyway</button>
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
    const isWizardPrecheck = detail?.stage === 'wizard';
    footer = (
      <>
        <button type="button" style={SECONDARY_BTN} onClick={() => answer('cancel')}>
          {isWizardPrecheck ? 'Change selection' : 'Cancel sync'}
        </button>
        <button type="button" style={PRIMARY_BTN} onClick={() => answer('confirm')}>
          {isWizardPrecheck
            ? (overwrites ? 'Continue (will overwrite)' : 'Continue anyway')
            : (overwrites ? 'Overwrite and sync' : 'Sync anyway')}
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
      {kind === 'duplicate_check_failed' && (
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)' }}>
          {message || 'The check for models already synced by another project could not run.'}
          {' '}Nothing was deployed yet.
        </p>
      )}
      {kind === 'blocked' && <BlockedBody detail={detail} message={message} />}
      {kind === 'removed_fields' && <RemovedFieldsBody detail={detail} message={message} />}
      {kind === 'duplicate' && <DuplicateBody duplicates={duplicates} message={message} overwrites={overwrites} />}
    </Modal>
  );
}
