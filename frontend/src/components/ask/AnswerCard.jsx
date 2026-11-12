import React, { useState } from 'react';
import { AlertTriangle, Check, ChevronDown, ChevronRight, Copy, Loader2, RotateCcw, ShieldCheck } from 'lucide-react';
import AnswerResult from './AnswerResult';
import { answerTitle, ENGINES } from './askApi';
import { BTN, CARD, CHIP, LINK_BTN, pill } from './askStyles';

const engineLabel = (id) => ENGINES.find((e) => e.id === id)?.label || id;

function Step({ label, children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '84px minmax(0,1fr)', gap: 10, fontSize: 12.5, alignItems: 'baseline' }}>
      <span style={{ color: 'var(--text-tertiary)' }}>{label}</span>
      <div style={{ color: 'var(--text-secondary)', display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'baseline', minWidth: 0 }}>
        {children}
      </div>
    </div>
  );
}

function HowAnswered({ plan, result, target }) {
  const [open, setOpen] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [copied, setCopied] = useState(false);
  const sql = plan?.generated_sql || '';
  const lineCount = sql ? sql.split('\n').length : 0;
  const metrics = plan?.detected_metrics || [];
  const dims = plan?.detected_dimensions || [];
  const derived = plan?.derived_from_metrics || [];
  const policies = plan?.applied_policies || [];
  const filters = (plan?.filters || []).filter((f) => !policies.includes(f));
  const warnings = plan?.warnings || [];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard unavailable */
    }
  };

  const summary = [
    metrics.length ? `matched ${metrics.length + dims.length} term${metrics.length + dims.length === 1 ? '' : 's'}` : 'no governed metric matched',
    policies.length ? `${policies.length} polic${policies.length === 1 ? 'y' : 'ies'} applied` : null,
    lineCount ? `${lineCount} lines of SQL` : null,
    plan?.is_draft ? 'offline draft (not governed)' : null,
  ].filter(Boolean).join(' · ');

  return (
    <div style={{ borderTop: '1px solid var(--border-main)', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{ ...LINK_BTN, textDecoration: 'none', display: 'flex', alignItems: 'center', gap: 6, textAlign: 'left' }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <b style={{ color: 'var(--text-primary)', fontSize: 12.5 }}>How this was answered</b>
        {!open && <span style={{ color: 'var(--text-tertiary)' }}>· {summary}</span>}
      </button>

      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingLeft: 20 }}>
          <Step label="Matched">
            {metrics.length + dims.length === 0 && <span>No metrics or concepts matched.</span>}
            {metrics.map((m) => (
              <span key={`m-${m}`} style={CHIP} title="Governed metric">{m}</span>
            ))}
            {dims.map((d) => (
              <span key={`d-${d}`} style={{ ...CHIP, color: 'var(--text-secondary)' }} title="Business concept">{d}</span>
            ))}
            {derived.length > 0 && (
              <span>
                from{' '}
                {derived.map((d, i) => (
                  <React.Fragment key={d}>
                    {i > 0 && ', '}
                    <span style={CHIP}>{d}</span>
                  </React.Fragment>
                ))}
              </span>
            )}
          </Step>
          <Step label="Policies">
            {policies.length === 0 ? (
              <span>None applied</span>
            ) : (
              policies.map((p) => <span key={p} style={CHIP}>{p}</span>)
            )}
          </Step>
          {filters.length > 0 && (
            <Step label="Filters">
              {filters.map((f) => <span key={f} style={CHIP}>{f}</span>)}
            </Step>
          )}
          {warnings.length > 0 && (
            <Step label="Notes">
              <span style={{ color: 'var(--color-warning)' }}>{warnings.join(' ')}</span>
            </Step>
          )}
          <Step label="SQL">
            {plan?.is_draft && (
              <span
                style={{ ...CHIP, color: 'var(--color-warning)' }}
                title="Reads physical tables directly; no deployed semantic view governs this query. Review before use."
              >
                Offline draft
              </span>
            )}
            <span>{lineCount} lines</span>
            <span>·</span>
            <button type="button" style={LINK_BTN} onClick={() => setShowSql((v) => !v)}>
              {showSql ? 'hide SQL' : 'show SQL'}
            </button>
            <span>·</span>
            <button type="button" style={{ ...LINK_BTN, display: 'inline-flex', alignItems: 'center', gap: 4 }} onClick={copy}>
              {copied ? <Check size={12} /> : <Copy size={12} />}
              {copied ? 'copied' : 'copy'}
            </button>
            <span style={{ color: 'var(--text-tertiary)' }}>
              · {engineLabel(target)}
              {result?.execution_time_ms ? ` · ${result.execution_time_ms} ms` : ''}
            </span>
          </Step>
          {showSql && (
            <pre
              style={{
                margin: 0,
                padding: 12,
                borderRadius: 8,
                background: 'var(--bg-app)',
                border: '1px solid var(--border-main)',
                color: 'var(--text-primary)',
                fontSize: 11.5,
                lineHeight: 1.55,
                overflowX: 'auto',
                whiteSpace: 'pre',
              }}
            >
              {sql}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

export default function AnswerCard({ entry, onCertify, onRetry, onOfflineDraft }) {
  const { status, plan, result, error, errorStage, cortexUnavailable, target, certifyState, certifyError, question } = entry;
  const [confirming, setConfirming] = useState(false);

  if (status === 'loading') {
    return (
      <div style={{ ...CARD, flexDirection: 'row', alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }} aria-live="polite">
        <Loader2 size={16} className="animate-spin" style={{ animation: 'spin 1s linear infinite' }} />
        Finding governed metrics and running on {engineLabel(target)}…
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div role="alert" style={{ ...CARD, borderColor: 'var(--color-error)', background: 'var(--color-error-bg)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <AlertTriangle size={16} color="var(--color-error)" />
          <b style={{ fontSize: 13.5, color: 'var(--text-primary)' }}>
            {errorStage === 'run' ? 'The query could not be run' : cortexUnavailable ? 'Cortex unavailable' : 'The question could not be interpreted'}
          </b>
          <span style={{ flex: 1 }} />
          {cortexUnavailable && onOfflineDraft && (
            <button
              type="button"
              style={{ ...BTN, borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' }}
              onClick={onOfflineDraft}
            >
              Generate offline draft
            </button>
          )}
          <button type="button" style={BTN} onClick={onRetry}>
            <RotateCcw size={13} /> Try again
          </button>
        </div>
        <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>{error}</div>
        {plan?.generated_sql && <HowAnswered plan={plan} result={null} target={target} />}
      </div>
    );
  }

  const usesCertified = (plan?.detected_metrics || []).length > 0;
  const certified = certifyState === 'done';
  // Only answers computed by a live warehouse can be certified, never preview rows.
  // (0 rows is a valid answer; the backend also requires a recorded real run.)
  const canCertify = Boolean(result && result.success !== false && !result.is_simulated);

  return (
    <div style={CARD}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 14, color: 'var(--text-primary)' }}>{answerTitle(plan, question)}</b>
        {usesCertified ? (
          <span style={pill('ok')}><ShieldCheck size={12} /> Uses certified metric</span>
        ) : (
          <span style={pill('warn')} title="No governed metric matched this question; check the SQL before relying on it.">
            No governed metric matched
          </span>
        )}
        <span style={{ flex: 1 }} />
        <button
          type="button"
          style={{ ...BTN, ...(certified ? { color: 'var(--color-success)', cursor: 'default' } : {}) }}
          disabled={certified || certifyState === 'saving' || !canCertify || confirming}
          onClick={() => setConfirming(true)}
          title={canCertify
            ? 'Save this question and its SQL as a verified query so AI engines reuse it'
            : 'Run the question against a live warehouse (not a preview) before certifying it'}
        >
          {certified ? <Check size={13} /> : <ShieldCheck size={13} />}
          {certified ? 'Certified' : certifyState === 'saving' ? 'Certifying…' : 'Certify answer'}
        </button>
      </div>
      {confirming && !certified && (
        <div role="alertdialog" aria-label="Confirm certification" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', fontSize: 12.5 }}>
          <span style={{ flex: 1, minWidth: 200 }}>Is this answer correct? Certifying saves the question and its SQL so AI engines reuse it.</span>
          <button type="button" style={BTN} onClick={() => setConfirming(false)}>Cancel</button>
          <button type="button" style={{ ...BTN, background: 'var(--accent-blue)', color: '#fff', borderColor: 'var(--accent-blue)' }} onClick={() => { setConfirming(false); onCertify(); }}>Yes, certify</button>
        </div>
      )}

      {result?.is_simulated && (
        <div style={{ fontSize: 12, color: 'var(--color-warning)' }}>
          Preview data · {result.message || 'the warehouse is not connected, so these rows are a sample.'}
        </div>
      )}
      {certifyError && <div style={{ fontSize: 12, color: 'var(--color-error)' }}>{certifyError}</div>}

      <AnswerResult columns={result?.columns} rows={result?.rows} />

      <HowAnswered plan={plan} result={result} target={target} />
    </div>
  );
}
