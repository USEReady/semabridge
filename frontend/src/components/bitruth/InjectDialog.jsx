import React, { useState, useEffect, useCallback } from 'react';
import {
  Send, AlertTriangle, CheckCircle2, XCircle, Info, Database, ShieldAlert, Check, X, RefreshCw
} from 'lucide-react';
import Modal from '../common/Modal';
import { authFetch } from '../../utils/api';

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

const BTN_DANGER = {
  ...BTN,
  borderColor: 'var(--color-warning, #f59e0b)',
  background: 'var(--color-warning, #f59e0b)',
  color: '#000',
};

export default function InjectDialog({
  open,
  onClose,
  projectId,
  selectedView,
}) {
  const [loading, setLoading] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [dryRunData, setDryRunData] = useState(null);
  const [error, setError] = useState('');
  const [injectResult, setInjectResult] = useState(null);

  const fetchDryRun = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError('');
    setInjectResult(null);
    try {
      const res = await authFetch('/api/bi-truth/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          view: selectedView || undefined,
          dry_run: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Dry run inspection failed.');
      } else {
        setDryRunData(data);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [projectId, selectedView]);

  useEffect(() => {
    let ignore = false;
    if (!open || !projectId) return;

    const execute = async () => {
      await Promise.resolve();
      if (ignore) return;
      fetchDryRun();
    };
    execute();

    return () => {
      ignore = true;
    };
  }, [open, projectId, fetchDryRun]);

  const handleClose = () => {
    setDryRunData(null);
    setInjectResult(null);
    setError('');
    onClose();
  };

  const handleExecuteWrite = async () => {
    if (!projectId) return;
    setInjecting(true);
    setError('');
    try {
      const res = await authFetch('/api/bi-truth/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          view: selectedView || undefined,
          dry_run: false,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail || 'Warehouse write failed.');
      } else {
        setInjectResult(data);
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setInjecting(false);
    }
  };

  const qualifyingCount = dryRunData?.injected_count || 0;
  const skippedList = dryRunData?.skipped || [];
  const targetDesc = dryRunData?.target || 'Warehouse downstream semantic model';

  return (
    <Modal open={open} onClose={handleClose} title="Inject Verified Pairs to AI Warehouse (Write)">
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
            color: 'var(--color-warning, #f59e0b)',
          }}
        >
          <ShieldAlert size={18} style={{ flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontWeight: 600, fontSize: 13 }}>Warehouse Write Action</div>
            <div style={{ fontSize: 12, opacity: 0.9, marginTop: 2, color: 'var(--text-secondary)' }}>
              This operation actively writes verified question/SQL pairs into the downstream warehouse
              semantic model (Cortex Analyst staged YAML or Databricks Genie Space). Only approved cases
              with passing run results qualify.
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

        {/* Loading Preview */}
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '20px 0', justifyContent: 'center', color: 'var(--text-tertiary)' }}>
            <RefreshCw size={16} className="spin" /> Inspecting warehouse target and qualification status…
          </div>
        )}

        {/* Injection Execution Result (After Landed) */}
        {injectResult && (
          <div
            style={{
              padding: 14,
              borderRadius: 6,
              background: injectResult.status === 'success' ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
              border: `1px solid ${injectResult.status === 'success' ? 'rgba(34, 197, 94, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, color: injectResult.status === 'success' ? 'var(--color-success)' : 'var(--color-error)' }}>
              {injectResult.status === 'success' ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
              {injectResult.message || 'Operation finished.'}
            </div>

            {injectResult.audit_record && (
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-secondary)' }}>
                <div><strong>Target:</strong> {injectResult.audit_record.target}</div>
                <div><strong>Recorded At:</strong> {injectResult.audit_record.injected_at}</div>
                <div><strong>Executed By:</strong> {injectResult.audit_record.injected_by}</div>
              </div>
            )}

            {injectResult.injected && injectResult.injected.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 600, fontSize: 12 }}>Landed Pairs:</div>
                <ul style={{ margin: '4px 0 0 16px', padding: 0, fontSize: 12 }}>
                  {injectResult.injected.map((p, idx) => (
                    <li key={idx} style={{ marginTop: 2 }}>{p.question}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Pre-flight Dry Run Summary */}
        {!loading && dryRunData && !injectResult && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 16, padding: '10px 14px', borderRadius: 6, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)' }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Target Destination</div>
                <div style={{ fontWeight: 600, marginTop: 2 }}>{targetDesc}</div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Qualifying Pairs</div>
                <div style={{ fontWeight: 600, marginTop: 2, color: qualifyingCount > 0 ? 'var(--color-success)' : 'var(--text-tertiary)' }}>
                  {qualifyingCount} ready
                </div>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Skipped Cases</div>
                <div style={{ fontWeight: 600, marginTop: 2, color: 'var(--text-secondary)' }}>
                  {skippedList.length} cases
                </div>
              </div>
            </div>

            {/* Qualifying List */}
            {qualifyingCount > 0 ? (
              <div>
                <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6, color: 'var(--color-success)' }}>
                  ✓ Qualifying Cases Ready for Injection:
                </div>
                <div style={{ border: '1px solid var(--border-main)', borderRadius: 6, overflow: 'hidden' }}>
                  {dryRunData.injected.map((item, idx) => (
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

            {/* Skipped Details Accordion / List */}
            {skippedList.length > 0 && (
              <div>
                <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6, color: 'var(--text-secondary)' }}>
                  Skipped Cases ({skippedList.length}):
                </div>
                <div style={{ maxHeight: 160, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 6 }}>
                  {skippedList.map((item, idx) => (
                    <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 10px', borderBottom: '1px solid var(--border-main)', fontSize: 11 }}>
                      <span style={{ color: 'var(--text-primary)', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '60%' }}>
                        {item.question}
                      </span>
                      <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>{item.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Dialog Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 10, borderTop: '1px solid var(--border-main)', paddingTop: 12 }}>
          <button type="button" style={BTN} onClick={handleClose} disabled={injecting}>
            {injectResult ? 'Done' : 'Cancel'}
          </button>
          {!injectResult && (
            <button
              type="button"
              style={BTN_DANGER}
              disabled={loading || injecting || qualifyingCount === 0}
              onClick={handleExecuteWrite}
            >
              <Send size={13} /> {injecting ? 'Writing to Warehouse…' : `Execute Injection (${qualifyingCount})`}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
