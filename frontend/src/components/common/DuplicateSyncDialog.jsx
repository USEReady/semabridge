import Modal from './Modal';
import { useDuplicateSyncPrompt } from '../../utils/duplicateSyncPrompt';

const BTN = { padding: '7px 14px', borderRadius: 8, fontSize: 12.5, fontWeight: 600, cursor: 'pointer' };
const SECONDARY_BTN = { ...BTN, background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)' };
const PRIMARY_BTN = { ...BTN, background: 'var(--accent-blue)', border: '1px solid var(--accent-blue)', color: '#fff' };

const REASON_LABELS = {
  same_view: 'Same view name — this sync would overwrite it',
  same_model: 'Same model name',
  same_content: 'Same metrics and columns',
};

function overlapText(part) {
  if (!part || !part.total) return '—';
  return `${part.shared} of ${part.total}`;
}

/** Mounted once in the app layout; opened by api.runProjectNow. */
export default function DuplicateSyncDialog() {
  const { open, duplicates, message, resolve, close } = useDuplicateSyncPrompt();

  const answer = (confirmed) => {
    resolve?.(confirmed);
    close();
  };

  const overwrites = duplicates.some((d) => d.overwrites);

  return (
    <Modal
      open={open}
      onClose={() => answer(false)}
      title="Model already synced"
      size="lg"
      footer={(
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" style={SECONDARY_BTN} onClick={() => answer(false)}>
            Cancel sync
          </button>
          <button type="button" style={PRIMARY_BTN} onClick={() => answer(true)}>
            {overwrites ? 'Overwrite and sync' : 'Sync anyway'}
          </button>
        </div>
      )}
    >
      <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-secondary)' }}>
        {message || 'Some models in this project are already synced by another project.'}
        {overwrites && ' At least one would replace the other project\'s semantic view.'}
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--text-tertiary)' }}>
              <th style={{ padding: '6px 8px' }}>Model</th>
              <th style={{ padding: '6px 8px' }}>Already synced by</th>
              <th style={{ padding: '6px 8px' }}>Why</th>
              <th style={{ padding: '6px 8px' }}>Metrics</th>
              <th style={{ padding: '6px 8px' }}>Columns</th>
            </tr>
          </thead>
          <tbody>
            {duplicates.map((d) => (
              <tr key={`${d.model}-${d.other_project_id}-${d.other_view_name}`} style={{ borderTop: '1px solid var(--border-main)' }}>
                <td style={{ padding: '8px' }}>
                  <b>{d.model}</b>
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>{d.view_name}</div>
                </td>
                <td style={{ padding: '8px' }}>
                  {d.other_project_name}
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 11.5 }}>{d.target}.{d.other_view_name}</div>
                </td>
                <td style={{ padding: '8px' }}>
                  {(d.reasons || []).map((r) => <div key={r}>{REASON_LABELS[r] || r}</div>)}
                </td>
                <td style={{ padding: '8px', whiteSpace: 'nowrap' }}>{overlapText(d.metrics)}</td>
                <td style={{ padding: '8px', whiteSpace: 'nowrap' }}>{overlapText(d.columns)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Modal>
  );
}
