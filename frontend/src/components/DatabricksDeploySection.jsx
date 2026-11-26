/**
 * Databricks deploy (Phase 3.8b): preview -> confirm -> results, plus Reconcile
 * and Re-verify. Everything is decided server-side: the preview is stored as an
 * intent, and Confirm executes exactly that intent (never statements from here).
 */
import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle, Info, Rocket, RefreshCw } from 'lucide-react';
import { api } from '../utils/api';
import {
  ACTION_LABEL, OUTCOME_LABEL, RUN_STATUS_LABEL, attention, blockingReasons, canConfirm, previewCounts, runFinished,
} from '../utils/databricksDeploy';

const ROW = { display: 'flex', gap: 8, alignItems: 'flex-start', padding: '6px 14px', borderTop: '1px solid var(--border-subtle)' };
const BTN = {
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderRadius: 6,
  border: '1px solid var(--border-default)', background: 'var(--bg-elevated)', color: 'var(--text-primary)',
  fontSize: 12, cursor: 'pointer',
};
const PRIMARY = { ...BTN, background: '#2563eb', borderColor: '#2563eb', color: '#fff' };
const PRE = { margin: '4px 0 0', padding: 8, background: 'var(--bg-base)', borderRadius: 6, fontSize: 11,
  whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 240, overflow: 'auto' };
const RED = '#f87171';
const AMBER = '#fbbf24';

export default function DatabricksDeploySection({ projectId, modelSubdir = '', cacheProjectId = '' }) {
  const [createStubs, setCreateStubs] = useState(false);
  const [publishIncomplete, setPublishIncomplete] = useState(false);
  const [result, setResult] = useState(null);       // {preview, intent, fingerprint}
  const [ack, setAck] = useState(false);
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const poll = useRef(null);

  useEffect(() => () => clearTimeout(poll.current), []);

  const scope = {
    ...(modelSubdir ? { model_subdir: modelSubdir } : {}),
    // the dry run's preview id when the plan is stored there (the server checks it belongs to this project)
    ...(cacheProjectId && cacheProjectId !== projectId ? { cache_project_id: cacheProjectId } : {}),
  };

  const doPreview = async () => {
    setBusy('preview'); setError(''); setNote(''); setRun(null); setAck(false);
    try {
      setResult(await api.previewDatabricksDeploy(projectId, {
        create_stubs: createStubs, publish_incomplete: publishIncomplete, ...scope,
      }));
    } catch (err) {
      setResult(null);
      setError(err?.message || 'The deploy preview failed.');
    } finally {
      setBusy('');
    }
  };

  const follow = (runId) => {
    clearTimeout(poll.current);
    const tick = async () => {
      try {
        const r = await api.getDatabricksDeployRun(projectId, runId);
        setRun(r);
        if (!runFinished(r)) poll.current = setTimeout(tick, 2000);
      } catch (err) {
        setError(err?.message || 'Could not read the deploy status.');
      }
    };
    tick();
  };

  const doConfirm = async () => {
    if (!canConfirm(result, ack)) return;
    setBusy('deploy'); setError('');
    try {
      const res = await api.confirmDatabricksDeploy(projectId, {
        intent_id: result.intent.intent_id, acknowledge_destination_access: ack, ...scope,
      });
      setResult((r) => (r ? { ...r, intent: null } : r));      // an intent is used once
      follow(res.run_id);
    } catch (err) {
      setError(err?.message || 'The deploy could not start.');
    } finally {
      setBusy('');
    }
  };

  const doReconcile = async () => {
    setBusy('reconcile'); setError('');
    try {
      const res = await api.reconcileDatabricksDeploy(projectId, scope);
      setNote(res?.status === 'reconciled'
        ? `Reconciled. Staging copies removed: ${(res.dropped || []).length}.`
        : `Reconcile needs attention: ${res?.status}. ${(res?.drop_errors || []).join('; ')}`);
    } catch (err) {
      setError(err?.message || 'Reconcile failed.');
    } finally {
      setBusy('');
    }
  };

  const doReverify = async () => {
    setBusy('reverify'); setError('');
    try {
      const res = await api.reverifyDatabricksDeploy(projectId, scope);
      const r = res?.results || [];
      setNote(r.length === 0 ? 'Nothing to re-verify.'
        : r.map((x) => `${x.fqn}: ${x.outcome === 'verified' ? 'verified ✓' : `still not verified (${(x.problems || []).join('; ')})`}`).join('\n'));
    } catch (err) {
      setError(err?.message || 'Re-verify failed.');
    } finally {
      setBusy('');
    }
  };

  const preview = result?.preview;
  const counts = previewCounts(preview);
  const blocks = blockingReasons(preview);
  const att = attention(run);
  const needsReconcile = att.needsReconcile || (preview?.model_blockers || []).some((b) => b.code === 'reconcile_required');

  return (
    <div style={{ borderTop: '1px solid var(--border-subtle)' }}>
      <div style={{ ...ROW, borderTop: 'none', alignItems: 'center', flexWrap: 'wrap' }}>
        <Rocket size={13} />
        <strong style={{ color: 'var(--text-primary)' }}>Deploy to Databricks</strong>
        <label style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <input type="checkbox" checked={createStubs} onChange={(e) => setCreateStubs(e.target.checked)} />
          Create empty tables for missing sources
        </label>
        <label style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
          <input type="checkbox" checked={publishIncomplete} onChange={(e) => setPublishIncomplete(e.target.checked)} />
          Publish incomplete views
        </label>
        <span style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
          <button type="button" style={BTN} disabled={!!busy} onClick={doPreview}>
            <RefreshCw size={11} /> {busy === 'preview' ? 'Checking…' : 'Preview deploy'}
          </button>
          {needsReconcile && (
            <button type="button" style={BTN} disabled={!!busy} onClick={doReconcile}>
              {busy === 'reconcile' ? 'Reconciling…' : 'Reconcile'}
            </button>
          )}
          {att.unverified.length > 0 && (
            <button type="button" style={BTN} disabled={!!busy} onClick={doReverify}>
              {busy === 'reverify' ? 'Re-verifying…' : 'Re-verify'}
            </button>
          )}
        </span>
      </div>
      <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
        <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
        <span>The preview only reads from Databricks. Nothing is written until you confirm it.</span>
      </div>

      {error && <div style={{ ...ROW, color: RED }}><AlertTriangle size={13} /> <span>{error}</span></div>}
      {note && <div style={{ ...ROW }}><pre style={{ ...PRE, margin: 0, flex: 1 }}>{note}</pre></div>}

      {preview && (
        <>
          <div style={ROW}>
            <span>
              Plan revision {preview.plan_revision} → <code>{preview.destination?.catalog}.{preview.destination?.schema}</code>
              {' · '}{counts.create} to create · {counts.update} to update · {counts.unchanged} unchanged
              {' · '}<span style={{ color: counts.blocked ? AMBER : 'inherit' }}>{counts.blocked} blocked</span>
              {counts.stubs ? ` · ${counts.stubs} empty table(s) to create` : ''}
            </span>
          </div>

          {blocks.model.map((b) => (
            <div key={b.code} style={{ ...ROW, color: RED }}>
              <AlertTriangle size={13} style={{ flexShrink: 0 }} /><span><strong>{b.code}</strong>: {b.message}</span>
            </div>
          ))}
          {(preview.warnings || []).map((w) => (
            <div key={w} style={{ ...ROW, color: 'var(--text-tertiary)' }}><Info size={13} /><span>{w}</span></div>
          ))}

          {(preview.views || []).map((v) => (
            <div key={v.name} style={ROW}>
              <code style={{ flex: '0 0 30%', overflowWrap: 'anywhere' }}>{v.name}</code>
              <span style={{ flex: 1, color: v.action === 'blocked' ? AMBER : 'inherit' }}>
                {ACTION_LABEL[v.action] || v.action}
                {v.readiness === 'incomplete_unverified' && <strong style={{ color: AMBER }}> · INCOMPLETE — not verified for business use</strong>}
                {(v.reasons || []).map((r) => <div key={r.code + r.message} style={{ color: 'var(--text-tertiary)' }}>{r.message}</div>)}
              </span>
            </div>
          ))}

          {blocks.items.filter((b) => !(preview.views || []).some((v) => v.name === b.scope)).map((b) => (
            <div key={`${b.scope}-${b.code}`} style={{ ...ROW, color: AMBER }}>
              <span><strong>{b.scope}</strong>: {b.message}</span>
            </div>
          ))}

          {(preview.breaking_changes || []).length > 0 && (
            <div style={ROW}>
              <span><strong style={{ color: AMBER }}>Breaking changes for consumers:</strong>{' '}
                {preview.breaking_changes.map((c) => (c.from ? `${c.kind}: ${c.from} → ${c.to}` : `${c.kind}: ${c.name || c.view}`)).join('; ')}
              </span>
            </div>
          )}

          {(preview.missing_grants || []).length > 0 && (
            <div style={ROW}>
              <span style={{ flex: 1 }}>Ask an admin to run:
                <pre style={PRE}>{preview.missing_grants.join('\n')}</pre>
              </span>
            </div>
          )}

          {(preview.unused || []).length > 0 && (
            <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
              <span>Not used any more (reported only, never deleted): {preview.unused.map((u) => u.fqn).join(', ')}</span>
            </div>
          )}

          {preview.destination_access && (
            <div style={ROW}>
              <label style={{ display: 'inline-flex', gap: 6, alignItems: 'flex-start' }}>
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                <span>
                  Incomplete views will be readable by:{' '}
                  {preview.destination_access.map((a) => `${a.principal} (${a.via})`).join(', ')}.
                  I understand they are not verified for business use.
                </span>
              </label>
            </div>
          )}

          {(preview.statements || []).length > 0 && (
            <div style={ROW}>
              <details style={{ flex: 1 }}>
                <summary style={{ cursor: 'pointer' }}>Exact SQL, in order ({preview.statements.length} statements)</summary>
                {preview.statements.map((s, i) => (
                  <div key={s.sha256}>
                    <div style={{ marginTop: 6, color: 'var(--text-tertiary)' }}>{i + 1}. {s.kind} — {s.target}</div>
                    <pre style={PRE}>{s.sql}</pre>
                  </div>
                ))}
              </details>
            </div>
          )}

          <div style={{ ...ROW, justifyContent: 'flex-end' }}>
            {!result.intent && <span style={{ marginRight: 'auto', color: 'var(--text-tertiary)' }}>
              {preview.status === 'blocked' ? 'Fix the blocking items, then preview again.' : 'Preview again to deploy.'}
            </span>}
            <button type="button" style={{ ...PRIMARY, opacity: canConfirm(result, ack) && !busy ? 1 : 0.5 }}
              disabled={!canConfirm(result, ack) || !!busy} onClick={doConfirm}>
              <Rocket size={11} /> {busy === 'deploy' ? 'Starting…' : counts.create + counts.update ? 'Confirm and deploy' : 'Confirm (re-verify only)'}
            </button>
          </div>
        </>
      )}

      {run && (
        <>
          <div style={{ ...ROW, alignItems: 'center' }}>
            {runFinished(run) && run.status === 'succeeded' ? <CheckCircle size={13} color="#34d399" /> : <Info size={13} />}
            <strong>{RUN_STATUS_LABEL[run.status] || run.status}</strong>
            <span style={{ color: 'var(--text-tertiary)' }}>run {run.run_id}</span>
          </div>
          {(run.summary?.results || []).map((r) => (
            <div key={`${r.kind}-${r.fqn}`} style={ROW}>
              <code style={{ flex: '0 0 40%', overflowWrap: 'anywhere' }}>{r.fqn}</code>
              <span style={{ flex: 1, color: r.outcome === 'applied_unverified' || r.outcome === 'failed' ? RED : 'inherit' }}>
                {OUTCOME_LABEL[r.outcome] || r.outcome}{r.detail ? ` — ${r.detail}` : ''}
              </span>
            </div>
          ))}
          {run.summary?.stopped && <div style={{ ...ROW, color: RED }}><span>Stopped: {run.summary.stopped}</span></div>}
          {run.summary?.reason && <div style={{ ...ROW, color: AMBER }}><span>{run.summary.reason}</span></div>}
        </>
      )}
    </div>
  );
}
