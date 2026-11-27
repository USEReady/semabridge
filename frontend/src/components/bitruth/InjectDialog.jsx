import React, { useState, useCallback } from 'react';
import {
  Send, AlertTriangle, CheckCircle2, XCircle, Info, Database, ShieldAlert, Check, X, RefreshCw, Eye
} from 'lucide-react';
import Modal from '../common/Modal';
import { formatInjectionSkipReason, formatInjectionTargetLabel } from '../../utils/biTruthLabels';

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_WRITE = {
  ...BTN,
  borderColor: 'var(--color-warning, #d97706)',
  background: 'var(--color-warning, #f59e0b)',
  color: '#000',
  fontWeight: 700,
};

export default function InjectDialog({
  open,
  onClose,
  projectId,
  selectedView,
  platform = 'SNOWFLAKE',
}) {
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [hasPreviewed, setHasPreviewed] = useState(false);
  const [error, setError] = useState('');
  const [injectResult, setInjectResult] = useState(null);

  const targetLabel = formatInjectionTargetLabel(platform);

  const resetState = () => {
    setPreviewData(null);
    setHasPreviewed(false);
    setError('');
    setInjectResult(null);
    setInjecting(false);
  };

  const handleRunPreview = useCallback(async () => {
    if (!projectId) return;
    setLoadingPreview(true);
    setError('');
    try {
      const params = new URLSearchParams({
        project_id: projectId,
        dry_run: 'true',
      });
      if (selectedView) params.append('view_name', selectedView);

      const res = await fetch(`/api/bi-truth/inject?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || data?.reason || 'Preview inspection failed.');
      } else {
        setPreviewData(data);
        setHasPreviewed(true);
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setLoadingPreview(false);
    }
  }, [projectId, selectedView]);

  const handleExecutePublish = async () => {
    if (!projectId || !hasPreviewed) return;
    setInjecting(true);
    setError('');
    try {
      const params = new URLSearchParams({
        project_id: projectId,
        dry_run: 'false',
      });
      if (selectedView) params.append('view_name', selectedView);

      const res = await fetch(`/api/bi-truth/inject?${params.toString()}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || data?.reason || 'Warehouse publish failed.');
      } else {
        setInjectResult(data);
      }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setInjecting(false);
    }
  };

  const handleSafeClose = () => {
    if (injecting) return; // Cannot dismiss mid-write
    resetState();
    onClose();
  };

  const qualifyingCount = previewData?.injected_count || 0;
  const qualifyingList = previewData?.injected || [];
  const skippedList = previewData?.skipped || [];
  const targetDesc = previewData?.target || `${platform} downstream semantic model`;

  const isPartialFailure = injectResult && (
    injectResult.status === 'partial' ||
    (injectResult.injected_count > 0 && injectResult.failed_count > 0)
  );

  return (
    <Modal
      open={open}
      onClose={handleSafeClose}
      title={`${targetLabel} (Warehouse Write)`}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, fontSize: 13, maxHeight: '75vh', overflowY: 'auto' }}>
        {/* Prominent Write Warning Banner */}
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            padding: '10px 14px',
            borderRadius: 6,
            background: 'rgba(245, 158, 11, 0.12)',
            border: '1px solid rgba(245, 158, 11, 0.4)',
            color: 'var(--color-warning, #d97706)',
          }}
        >
          <ShieldAlert size={18} style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 13 }}>Warehouse Write Action</div>
            <div style={{ fontSize: 12, opacity: 0.9, marginTop: 2, color: 'var(--text-secondary)' }}>
              This operation actively modifies the destination warehouse model ({targetLabel}).
              To protect warehouse integrity, you must inspect the <strong>Preview</strong> before publishing.
            </div>
          </div>
        </div>

        {error && (
          <div
            style={{
              padding: '8px 12px',
              borderRadius: 6,
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              color: 'var(--color-error, #ef4444)',
              fontSize: 12,
            }}
          >
            {error}
          </div>
        )}

        {/* DURING: Writing in progress */}
        {injecting && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 12,
              padding: '24px 0',
              color: 'var(--text-primary)',
            }}
          >
            <RefreshCw size={24} className="spin" style={{ color: 'var(--color-warning, #f59e0b)' }} />
            <div style={{ fontWeight: 600 }}>Publishing verified pairs to warehouse…</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
              This operation updates stage model specifications. Do not close this window.
            </div>
          </div>
        )}

        {/* AFTER: Injection Execution Result */}
        {!injecting && injectResult && (
          <div
            style={{
              padding: 14,
              borderRadius: 6,
              background: injectResult.status === 'success' && !isPartialFailure
                ? 'rgba(34, 197, 94, 0.1)'
                : 'rgba(239, 68, 68, 0.1)',
              border: `1px solid ${
                injectResult.status === 'success' && !isPartialFailure
                  ? 'rgba(34, 197, 94, 0.3)'
                  : 'rgba(239, 68, 68, 0.3)'
              }`,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontWeight: 700,
                color: injectResult.status === 'success' && !isPartialFailure
                  ? 'var(--color-success, #22c55e)'
                  : 'var(--color-error, #ef4444)',
              }}
            >
              {injectResult.status === 'success' && !isPartialFailure ? (
                <CheckCircle2 size={18} />
              ) : (
                <AlertTriangle size={18} />
              )}
              {isPartialFailure
                ? 'Partial Injection: Some pairs landed, but failures occurred'
                : injectResult.message || 'Injection completed.'}
            </div>

            {injectResult.audit_record && (
              <div style={{ marginTop: 10, padding: 10, borderRadius: 6, background: 'var(--bg-surface-raised)', fontSize: 12 }}>
                <div><strong>Destination Target:</strong> {injectResult.audit_record.target}</div>
                <div><strong>Timestamp:</strong> {injectResult.audit_record.injected_at}</div>
                <div><strong>Actor:</strong> {injectResult.audit_record.injected_by}</div>
              </div>
            )}

            {injectResult.injected && injectResult.injected.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--color-success, #22c55e)' }}>
                  ✓ Landed Pairs ({injectResult.injected.length}):
                </div>
                <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 12 }}>
                  {injectResult.injected.map((p, idx) => (
                    <li key={idx} style={{ marginTop: 2 }}>{p.question || p}</li>
                  ))}
                </ul>
              </div>
            )}

            {injectResult.failed && injectResult.failed.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 12, color: 'var(--color-error, #ef4444)' }}>
                  ✗ Failed Pairs ({injectResult.failed.length}):
                </div>
                <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 12 }}>
                  {injectResult.failed.map((p, idx) => (
                    <li key={idx} style={{ marginTop: 2 }}>
                      {p.question}: <em>{p.reason || 'Warehouse rejected'}</em>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* BEFORE: Destination details & Preview list */}
        {!injecting && !injectResult && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16, padding: '10px 14px', borderRadius: 6, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)' }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Platform & View</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{platform} • {selectedView || 'Active View'}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Target Destination</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{targetDesc}</div>
              </div>
            </div>

            {loadingPreview ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '20px 0', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
                <RefreshCw size={16} className="spin" /> Inspecting qualifying pairs from warehouse…
              </div>
            ) : !hasPreviewed ? (
              <div style={{ padding: '16px 14px', borderRadius: 6, background: 'var(--bg-surface-raised)', color: 'var(--text-secondary)', fontSize: 12 }}>
                Click <strong>Inspect Preview</strong> below to check which approved cases qualify and view the destination stage path before publishing.
              </div>
            ) : (
              <>
                {/* Counts Summary */}
                <div style={{ display: 'flex', gap: 16 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: qualifyingCount > 0 ? 'var(--color-success, #22c55e)' : 'var(--text-tertiary)' }}>
                    Qualifying: {qualifyingCount}
                  </span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>
                    Skipped: {skippedList.length}
                  </span>
                </div>

                {/* Qualifying List */}
                {qualifyingCount > 0 ? (
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6, color: 'var(--color-success, #22c55e)' }}>
                      ✓ Qualifying Cases Ready for Injection:
                    </div>
                    <div style={{ border: '1px solid var(--border-main)', borderRadius: 6, overflow: 'hidden', maxHeight: 180, overflowY: 'auto' }}>
                      {qualifyingList.map((item, idx) => (
                        <div key={idx} style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
                          <div style={{ fontWeight: 500 }}>{item.question}</div>
                          <div style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                            {item.sql}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div style={{ padding: '12px 14px', borderRadius: 6, background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', fontSize: 12 }}>
                    No cases currently qualify for injection. Cases must be approved, carry a benchmark, and pass the latest test suite run.
                  </div>
                )}

                {/* Skipped Details List */}
                {skippedList.length > 0 && (
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6, color: 'var(--text-secondary)' }}>
                      Skipped Cases ({skippedList.length}):
                    </div>
                    <div style={{ maxHeight: 140, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 6 }}>
                      {skippedList.map((item, idx) => (
                        <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', borderBottom: '1px solid var(--border-main)', fontSize: 11 }}>
                          <span style={{ color: 'var(--text-primary)', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '55%' }}>
                            {item.question}
                          </span>
                          <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>
                            {formatInjectionSkipReason(item.reason_code, item.reason)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* Dialog Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 10, borderTop: '1px solid var(--border-main)', paddingTop: 12 }}>
          <button type="button" style={BTN} onClick={handleSafeClose} disabled={injecting}>
            {injectResult ? 'Done' : 'Cancel'}
          </button>

          {!injectResult && (
            <>
              <button
                type="button"
                style={BTN}
                disabled={loadingPreview || injecting}
                onClick={handleRunPreview}
              >
                <Eye size={13} /> {loadingPreview ? 'Inspecting…' : 'Inspect Preview'}
              </button>

              <button
                type="button"
                style={BTN_WRITE}
                disabled={loadingPreview || injecting || !hasPreviewed || qualifyingCount === 0}
                title={!hasPreviewed ? 'Run preview first before publishing' : qualifyingCount === 0 ? 'No qualifying cases' : `Publish ${qualifyingCount} pairs to warehouse`}
                onClick={handleExecutePublish}
              >
                <Send size={13} /> {injecting ? 'Publishing…' : `Publish (${qualifyingCount})`}
              </button>
            </>
          )}
        </div>
      </div>
    </Modal>
  );
}
