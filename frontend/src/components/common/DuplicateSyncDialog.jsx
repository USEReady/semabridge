import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Modal from './Modal';
import { useDuplicateSyncPrompt } from '../../utils/duplicateSyncPrompt';
import { AlertTriangle, Clock, Layers, Database, FileText } from 'lucide-react';

const BTN = { padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' };
const SECONDARY_BTN = { ...BTN, background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)' };
const PRIMARY_BTN = { ...BTN, background: 'var(--accent-blue)', border: '1px solid var(--accent-blue)', color: '#fff' };
const DISABLED_BTN = { ...BTN, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)', color: 'var(--text-tertiary)', cursor: 'not-allowed', opacity: 0.5 };

const REASON_LABELS = {
  same_view: 'Same view name — this sync would overwrite it',
  same_model: 'Same model name',
  same_content: 'Same metrics and columns',
};

function overlapText(part) {
  if (!part || !part.total) return '—';
  return `${part.shared} of ${part.total}`;
}

export default function DuplicateSyncDialog({ currentProjectId = null }) {
  const navigate = useNavigate();
  const { open, detail, duplicates, message, resolve, close } = useDuplicateSyncPrompt();
  const [selectedMode, setSelectedMode] = useState(null);
  const [confirmedRemovals, setConfirmedRemovals] = useState(false);

  const answer = (result) => {
    resolve?.(result);
    setSelectedMode(null);
    setConfirmedRemovals(false);
    close();
  };

  const matchType = String(detail?.match_type || '').toUpperCase();

  // Match A: Identical file already synced
  if (matchType === 'A') {
    const fileName = detail?.file_name || detail?.source_path || 'Uploaded PBIX file';
    const matchedProjectName = detail?.project_name || detail?.matched_project_id || 'Existing Project';
    const lastSyncTime = detail?.last_sync_time ? new Date(detail.last_sync_time).toLocaleString() : 'Never';
    const targetSchema = detail?.target_db_schema || '—';
    const viewCount = detail?.number_of_views ?? 1;

    return (
      <Modal
        open={open}
        onClose={() => answer(false)}
        title="This file has already been synced"
        size="md"
        footer={(
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap' }}>
            <button type="button" style={SECONDARY_BTN} onClick={() => answer(false)}>
              Cancel
            </button>
            <button
              type="button"
              style={SECONDARY_BTN}
              onClick={() => {
                if (detail.matched_project_id) {
                  navigate(`/projects/${detail.matched_project_id}`);
                }
                answer(false);
              }}
            >
              Open existing project
            </button>
            <button
              type="button"
              style={PRIMARY_BTN}
              onClick={() => answer({ mode: 'new_project', resync_mode: 'resync_anyway', sync_mode: 'copy' })}
            >
              Create a separate project anyway
            </button>
          </div>
        )}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <p style={{ margin: 0, fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            An identical file has already been synced in this workspace. Review the existing project or create a separate project anyway.
          </p>

          <div style={{
            padding: '12px 14px',
            borderRadius: 8,
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--border-main)',
            display: 'grid',
            gap: 10,
            fontSize: 12.5,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <FileText size={15} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>File:</span>
              <span style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{fileName}</span>
            </div>
            <div>
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Matched Project: </span>
              <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{matchedProjectName}</span>
            </div>
            <div>
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Last Sync Time: </span>
              <span>{lastSyncTime}</span>
            </div>
            <div>
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Target DB / Schema: </span>
              <code>{targetSchema}</code>
            </div>
            <div>
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Semantic Views Deployed: </span>
              <span>{viewCount}</span>
            </div>
          </div>
        </div>
      </Modal>
    );
  }

  // Match B: Same source, changed file -> offer Upsert / Copy for the existing project's view
  if (matchType === 'B') {
    const matchedProjectName = detail?.project_name || detail?.matched_project_id || 'Existing Project';
    const lastSyncTime = detail?.last_sync_time ? new Date(detail.last_sync_time).toLocaleString() : 'Never';
    const targetSchema = detail?.target_db_schema || '—';
    const targetViewName = detail?.view_name || (detail?.target_db_schema ? `${targetSchema}.${matchedProjectName}` : matchedProjectName);
    const upsertAvailable = detail?.upsert_available !== false;
    const removedFields = Array.isArray(detail?.removed_fields) ? detail.removed_fields : [];
    const needsRemovalConfirmation = selectedMode === 'copy' && removedFields.length > 0;
    const canContinueB = Boolean(selectedMode) && (!needsRemovalConfirmation || confirmedRemovals);

    return (
      <Modal
        open={open}
        onClose={() => answer(false)}
        title="Semantic view already exists"
        size="lg"
        footer={(
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
            <button type="button" style={SECONDARY_BTN} onClick={() => answer(false)}>
              Cancel
            </button>
            <button
              type="button"
              disabled={!canContinueB}
              style={canContinueB ? PRIMARY_BTN : DISABLED_BTN}
              onClick={() => {
                if (!canContinueB) return;
                answer({
                  mode: selectedMode,
                  resync_mode: selectedMode,
                  sync_mode: selectedMode,
                  confirmed_target_view: detail?.view_name || detail?.raw_view_name || targetViewName,
                  confirm_removals: selectedMode === 'copy' ? removedFields : undefined,
                });
              }}
            >
              Continue
            </button>
          </div>
        )}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{
            padding: '12px 14px',
            borderRadius: 8,
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--border-main)',
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-primary)' }}>
              <Database size={15} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
              <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 13.5, wordBreak: 'break-all' }}>
                {targetViewName}
              </span>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <Clock size={13} style={{ color: 'var(--text-tertiary)' }} />
                Last deployed: {lastSyncTime}
              </span>
              <div>
                <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Project: </span>
                <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{matchedProjectName}</span>
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Select synchronization mode:
            </div>

            {/* Upsert Option */}
            <label style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 12,
              padding: '12px 14px',
              borderRadius: 8,
              border: `1px solid ${selectedMode === 'upsert' ? 'var(--accent-blue)' : 'var(--border-main)'}`,
              background: selectedMode === 'upsert' ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface)',
              cursor: upsertAvailable ? 'pointer' : 'not-allowed',
              opacity: upsertAvailable ? 1 : 0.6,
              transition: 'all 0.15s ease',
            }}>
              <input
                type="radio"
                name="match_b_mode"
                value="upsert"
                disabled={!upsertAvailable}
                checked={selectedMode === 'upsert'}
                onChange={() => setSelectedMode('upsert')}
                style={{ marginTop: 3, accentColor: 'var(--accent-blue)', cursor: upsertAvailable ? 'pointer' : 'not-allowed' }}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-primary)' }}>
                  Upsert
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                  Update changed fields, add new ones, keep fields removed in Power BI.
                </div>
                {!upsertAvailable && (
                  <div style={{ fontSize: 11.5, color: 'var(--color-warning)', marginTop: 4 }}>
                    Upsert is unavailable because there is no baseline snapshot for this view (it was created outside SemaBridge).
                  </div>
                )}
              </div>
            </label>

            {/* Copy Option */}
            <label style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 12,
              padding: '12px 14px',
              borderRadius: 8,
              border: `1px solid ${selectedMode === 'copy' ? 'var(--accent-blue)' : 'var(--border-main)'}`,
              background: selectedMode === 'copy' ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface)',
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}>
              <input
                type="radio"
                name="match_b_mode"
                value="copy"
                checked={selectedMode === 'copy'}
                onChange={() => setSelectedMode('copy')}
                style={{ marginTop: 3, accentColor: 'var(--accent-blue)', cursor: 'pointer' }}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-primary)' }}>
                  Copy
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                  Replace the view exactly; fields removed in Power BI are dropped.
                </div>
              </div>
            </label>
          </div>

          {/* Removed Fields Warning & Confirmation for Copy Mode */}
          {selectedMode === 'copy' && removedFields.length > 0 && (
            <div style={{
              padding: '12px 14px',
              borderRadius: 8,
              background: 'rgba(251, 191, 36, 0.08)',
              border: '1px solid rgba(251, 191, 36, 0.35)',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
              fontSize: 12.5,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--color-warning)', fontWeight: 600 }}>
                <AlertTriangle size={15} />
                <span>{removedFields.length} field(s) will be dropped from the target view:</span>
              </div>
              <div style={{
                maxHeight: 120,
                overflowY: 'auto',
                fontFamily: 'monospace',
                fontSize: 12,
                color: 'var(--text-secondary)',
                paddingLeft: 8,
              }}>
                {removedFields.map((f, i) => (
                  <div key={i}>• {f}</div>
                ))}
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: 'var(--text-primary)', marginTop: 4 }}>
                <input
                  type="checkbox"
                  checked={confirmedRemovals}
                  onChange={(e) => setConfirmedRemovals(e.target.checked)}
                  style={{ accentColor: 'var(--accent-blue)', cursor: 'pointer' }}
                />
                <span style={{ fontWeight: 600 }}>I understand and confirm dropping these fields</span>
              </label>
            </div>
          )}
        </div>
      </Modal>
    );
  }

  const isViewExists = Boolean(detail?.view_name || detail?.status === 'target_view_exists');
  const isOtherProjectOwner = Boolean(
    detail?.owner_project_id && (!currentProjectId || detail.owner_project_id !== currentProjectId)
  );
  const upsertAvailable = detail?.upsert_available !== false;
  const removedFields = Array.isArray(detail?.removed_fields) ? detail.removed_fields : [];
  const needsRemovalConfirmation = selectedMode === 'copy' && removedFields.length > 0;
  const canContinue = selectedMode && (!needsRemovalConfirmation || confirmedRemovals);

  // Fallback to legacy cross-model duplicates view if no target view exists info
  if (!isViewExists && duplicates.length > 0) {
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

  return (
    <Modal
      open={open}
      onClose={() => answer(false)}
      title="Semantic view already exists"
      size="lg"
      footer={(
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button type="button" style={SECONDARY_BTN} onClick={() => answer(false)}>
            Cancel
          </button>
          <button
            type="button"
            disabled={!canContinue}
            style={canContinue ? PRIMARY_BTN : DISABLED_BTN}
            onClick={() => {
              if (!canContinue) return;
              answer({
                sync_mode: selectedMode,
                mode: selectedMode,
                confirmed_target_view: detail?.view_name || detail?.raw_view_name,
                confirm_removals: selectedMode === 'copy' ? removedFields : undefined,
              });
            }}
          >
            Continue
          </button>
        </div>
      )}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* View Name & Metadata */}
        <div style={{
          padding: '12px 14px',
          borderRadius: 8,
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-main)',
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-primary)' }}>
            <Database size={15} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
            <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 13.5, wordBreak: 'break-all' }}>
              {detail?.view_name || 'Target Semantic View'}
            </span>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
            {detail?.last_deployed_at && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <Clock size={13} style={{ color: 'var(--text-tertiary)' }} />
                Last deployed: {new Date(detail.last_deployed_at).toLocaleString()}
              </span>
            )}
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <Layers size={13} style={{ color: 'var(--text-tertiary)' }} />
              {detail?.metric_count ?? 0} metrics · {detail?.dimension_count ?? 0} dimensions
            </span>
          </div>
        </div>

        {/* Warning if owner is another project */}
        {isOtherProjectOwner && (
          <div style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            padding: '10px 12px',
            borderRadius: 8,
            background: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            color: 'var(--color-error)',
            fontSize: 12.5,
            lineHeight: 1.4,
          }}>
            <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
            <div>
              <strong>Warning:</strong> This view is owned by another project:{' '}
              <b>{detail.owner_project_name || detail.owner_project_id}</b>.
              Overwriting or updating it will affect that project.
            </div>
          </div>
        )}

        {/* Radio Options */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Select synchronization mode:
          </div>

          {/* Upsert Option */}
          <label style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 12,
            padding: '12px 14px',
            borderRadius: 8,
            border: `1px solid ${selectedMode === 'upsert' ? 'var(--accent-blue)' : 'var(--border-main)'}`,
            background: selectedMode === 'upsert' ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface)',
            cursor: upsertAvailable ? 'pointer' : 'not-allowed',
            opacity: upsertAvailable ? 1 : 0.6,
            transition: 'all 0.15s ease',
          }}>
            <input
              type="radio"
              name="sync_mode_choice"
              value="upsert"
              disabled={!upsertAvailable}
              checked={selectedMode === 'upsert'}
              onChange={() => setSelectedMode('upsert')}
              style={{ marginTop: 3, accentColor: 'var(--accent-blue)', cursor: upsertAvailable ? 'pointer' : 'not-allowed' }}
            />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-primary)' }}>
                Upsert
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                Update changed fields, add new ones, keep fields removed in Power BI.
              </div>
              {!upsertAvailable && (
                <div style={{ fontSize: 11.5, color: 'var(--color-warning)', marginTop: 4 }}>
                  Upsert is unavailable because there is no baseline snapshot for this view (it was created outside SemaBridge).
                </div>
              )}
            </div>
          </label>

          {/* Copy Option */}
          <label style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 12,
            padding: '12px 14px',
            borderRadius: 8,
            border: `1px solid ${selectedMode === 'copy' ? 'var(--accent-blue)' : 'var(--border-main)'}`,
            background: selectedMode === 'copy' ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface)',
            cursor: 'pointer',
            transition: 'all 0.15s ease',
          }}>
            <input
              type="radio"
              name="sync_mode_choice"
              value="copy"
              checked={selectedMode === 'copy'}
              onChange={() => setSelectedMode('copy')}
              style={{ marginTop: 3, accentColor: 'var(--accent-blue)', cursor: 'pointer' }}
            />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-primary)' }}>
                Copy
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                Replace the view exactly; fields removed in Power BI are dropped.
              </div>
            </div>
          </label>
        </div>

        {/* Removed Fields Warning & Confirmation for Copy Mode */}
        {selectedMode === 'copy' && removedFields.length > 0 && (
          <div style={{
            padding: '12px 14px',
            borderRadius: 8,
            background: 'rgba(251, 191, 36, 0.08)',
            border: '1px solid rgba(251, 191, 36, 0.35)',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-primary)' }}>
              Fields to be dropped ({removedFields.length}):
            </div>
            <div style={{
              maxHeight: 120,
              overflowY: 'auto',
              fontSize: 12,
              fontFamily: 'monospace',
              color: 'var(--color-warning)',
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
              padding: '6px 8px',
              background: 'var(--bg-surface)',
              borderRadius: 6,
            }}>
              {removedFields.map((f) => (
                <div key={f}>- {f}</div>
              ))}
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5, color: 'var(--text-primary)', cursor: 'pointer', marginTop: 4 }}>
              <input
                type="checkbox"
                checked={confirmedRemovals}
                onChange={(e) => setConfirmedRemovals(e.target.checked)}
                style={{ accentColor: 'var(--accent-blue)' }}
              />
              <span>I confirm dropping these {removedFields.length} field(s) from the target view.</span>
            </label>
          </div>
        )}
      </div>
    </Modal>
  );
}
