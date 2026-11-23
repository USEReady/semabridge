import React, { useState } from 'react';
import { AlertTriangle, Check, ChevronDown, ChevronRight, Copy, Loader2, RotateCcw, ShieldCheck } from 'lucide-react';
import AnswerResult from './AnswerResult';
import { answerTitle, ENGINES } from './askApi';
import { BTN, CARD, CHIP, LINK_BTN, pill } from './askStyles';

const engineLabel = (id) => {
  if (!id) return '';
  const raw = String(id).trim();
  const lower = raw.toLowerCase();
  if (lower.startsWith('snowflake_cortex') || lower === 'snowflake' || lower === 'cortex') {
    if (lower.includes(':')) {
      const parts = raw.split(':');
      return `Snowflake Cortex (${parts.slice(1).join(':')})`;
    }
    return 'Snowflake Cortex';
  }
  if (lower.startsWith('databricks_genie') || lower === 'databricks' || lower === 'genie') {
    return 'Databricks Genie';
  }
  if (lower === 'local' || lower.startsWith('local')) {
    return 'Local Engine';
  }
  const found = ENGINES.find((e) => e.id.toLowerCase() === lower);
  return found?.label || raw;
};

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
  const rawPath = result?.query_path || plan?.query_path || (plan?.is_draft ? 'free-form (not governed)' : 'semantic_view');
  const path = rawPath === 'rewritten to governed' ? 'rewritten to governed' : rawPath.includes('free-form') ? 'free-form (not governed)' : (rawPath === 'none' ? 'none' : 'semantic_view');
  const assumptions = plan?.assumptions || [];
  const guardChecks = plan?.guard_checks || [];
  const suggestedMetrics = plan?.suggested_metrics || [];

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
    path,
    metrics.length ? `metric: ${metrics[0]}` : null,
    assumptions.length ? `${assumptions.length} assumption${assumptions.length === 1 ? '' : 's'}` : null,
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
          <Step label="Path">
            <span
              style={{
                ...CHIP,
                color: path.includes('free-form') || path.includes('not governed') ? 'var(--color-warning)' : 'var(--text-primary)',
              }}
            >
              {path}
            </span>
            {plan?.is_certified && (
              <span style={{ ...CHIP, color: 'var(--color-success, #10b981)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <ShieldCheck size={12} /> Certified
              </span>
            )}
          </Step>
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
          {suggestedMetrics.length > 0 && (
            <Step label="Suggested">
              {suggestedMetrics.map((sm, i) => (
                <span key={`sug-${i}`} style={{ ...CHIP, border: '1px dashed var(--border-focus)' }}>
                  {sm}
                </span>
              ))}
            </Step>
          )}
          {assumptions.length > 0 && (
            <Step label="Assumptions">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, width: '100%' }}>
                {assumptions.map((a, i) => (
                  <span key={`asm-${i}`} style={{ color: 'var(--color-warning, #f59e0b)' }}>⚠️ {a}</span>
                ))}
              </div>
            </Step>
          )}
          {guardChecks.length > 0 && (
            <Step label="Checks">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
                {guardChecks.map((chk, i) => (
                  <div key={`chk-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span
                      style={{
                        ...CHIP,
                        color: chk.passed ? 'var(--color-success, #10b981)' : 'var(--color-warning, #f59e0b)',
                        fontWeight: 600,
                      }}
                    >
                      {chk.passed ? '✓ ' : '✗ '}{chk.check}
                    </span>
                    {chk.message && (
                      <span style={{ fontSize: 12, color: chk.passed ? 'var(--text-secondary)' : 'var(--color-warning, #f59e0b)' }}>
                        {chk.message}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </Step>
          )}
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
              · {engineLabel(plan?.engine || result?.dialect || result?.target || target)}
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

const viewTail = (v) => String(v || '').split('.').pop().replace(/"/g, '');

/** Which deployed semantic view or base table path answered. */
function AnsweredBy({ plan, result }) {
  const view = plan?.answered_by_view || plan?.view || plan?.semantic_view;
  const path = result?.query_path || plan?.query_path || (plan?.is_draft ? 'free-form' : '');
  const isFreeForm = path.includes('free-form') || path.includes('not governed') || plan?.is_draft;
  const isRewritten = path === 'rewritten to governed' || path === 'rewritten';
  if (!view && !isFreeForm) return null;
  return (
    <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }} title={view}>
      {isFreeForm ? (
        <>Answered from <b style={{ fontFamily: 'var(--font-mono, monospace)', color: 'var(--text-secondary)' }}>base tables (free-form SQL)</b></>
      ) : isRewritten ? (
        <>Rewritten to semantic view <b style={{ fontFamily: 'var(--font-mono, monospace)', color: 'var(--text-secondary)' }}>{viewTail(view)}</b></>
      ) : (
        <>Answered from semantic view <b style={{ fontFamily: 'var(--font-mono, monospace)', color: 'var(--text-secondary)' }}>{viewTail(view)}</b></>
      )}
      {plan?.answered_by_domain ? <> · domain {plan.answered_by_domain}</> : null}
    </div>
  );
}

export default function AnswerCard({ entry, onCertify, onRetry, onOfflineDraft, onPickView, onAsk }) {
  const { status, plan, result, error, errorStage, cortexUnavailable, target, certifyState, certifyError, question } = entry;
  const candidateViews = entry.candidateViews || [];
  const [confirming, setConfirming] = useState(false);

  if (status === 'loading') {
    return (
      <div style={{ ...CARD, flexDirection: 'row', alignItems: 'center', color: 'var(--text-secondary)', fontSize: 13 }} aria-live="polite">
        <Loader2 size={16} className="animate-spin" style={{ animation: 'spin 1s linear infinite' }} />
        Finding governed metrics and running on {engineLabel(target)}…
      </div>
    );
  }

  const isRefused = status === 'refused' || plan?.refused;
  if (isRefused) {
    const reasonText = plan?.reason_text || plan?.explanation || error || "Can't answer this as asked.";
    const decidingEngine = engineLabel(plan?.engine || 'local');
    const alternatives = plan?.alternatives || [];

    return (
      <div role="alert" style={{ ...CARD, borderColor: 'var(--color-warning, #f59e0b)', background: 'var(--bg-surface)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <AlertTriangle size={16} color="var(--color-warning, #f59e0b)" />
          <b style={{ fontSize: 14, color: 'var(--text-primary)' }}>Can't answer this as asked</b>
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5, wordBreak: 'break-word' }}>
          {reasonText}
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          Decided by {decidingEngine}
        </div>
        {alternatives.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4, paddingTop: 10, borderTop: '1px solid var(--border-main)' }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Suggested alternatives:</span>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {alternatives.map((alt, idx) => {
                const qText = typeof alt === 'string' ? alt : alt.question;
                return (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => onAsk?.(qText)}
                    style={{
                      ...BTN,
                      background: 'var(--bg-surface-raised, rgba(255,255,255,0.05))',
                      borderColor: 'var(--border-focus)',
                      color: 'var(--accent-blue, #38bdf8)',
                      textAlign: 'left',
                      cursor: 'pointer',
                      padding: '6px 12px',
                      borderRadius: 8,
                      fontSize: 12.5,
                    }}
                    title={qText}
                  >
                    {qText}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <AnsweredBy plan={plan} result={result} />
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div role="alert" style={{ ...CARD, borderColor: 'var(--color-error)', background: 'var(--color-error-bg)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <AlertTriangle size={16} color="var(--color-error)" />
          <b style={{ fontSize: 13.5, color: 'var(--text-primary)' }}>
            {errorStage === 'run'
              ? 'The query could not be run'
              : String(error || '').toLowerCase().includes('refus') || String(error || '').toLowerCase().includes('reject')
                ? 'Query refused by safety guard'
                : candidateViews.length > 0
                  ? 'Several models can answer this'
                  : cortexUnavailable ? 'Cortex unavailable' : 'The question could not be interpreted'}
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
        {candidateViews.length > 0 && onPickView && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Ask this view:</span>
            {candidateViews.map((v) => (
              <button key={v} type="button" style={BTN} title={v} onClick={() => onPickView(v)}>
                {viewTail(v)}
              </button>
            ))}
          </div>
        )}
        <AnsweredBy plan={plan} result={result} />
        {plan && !plan.refused && (plan.generated_sql || (plan.guard_checks || []).length > 0) && (
          <HowAnswered plan={plan} result={null} target={target} />
        )}
      </div>
    );
  }

  const usesCertified = (plan?.detected_metrics || []).length > 0;
  const normalizeWarning = (w) => (typeof w === 'string' ? w : (w?.message || String(w || ''))).trim().replace(/\s+/g, ' ');
  const allWarnings = Array.from(
    new Map(
      [...(plan?.warnings || []), ...(result?.warnings || [])]
        .map((w) => [normalizeWarning(w), normalizeWarning(w)])
        .filter(([k]) => Boolean(k))
    ).values()
  );
  const rawPath = result?.query_path || plan?.query_path || (usesCertified ? 'semantic_view' : (plan?.route === 'cortex' ? 'free-form (not governed)' : 'semantic_view'));
  const isGoverned = rawPath === 'semantic_view' || rawPath === 'rewritten to governed' || (!rawPath.includes('free-form') && !rawPath.includes('not governed'));
  const allChecksPassed = (plan?.guard_checks || []).length === 0 || (plan?.guard_checks || []).every((c) => c.passed !== false);
  const isCertifiable = Boolean(
    isGoverned &&
    (plan?.is_certified_eligible ?? true) &&
    (result?.is_certified_eligible ?? true) &&
    allChecksPassed
  );
  const hasCertifiedSource = Boolean(plan?.is_verified_query || plan?.has_certified_metric || plan?.certified_source || result?.certified_source || result?.certified);
  const isCertifiedAnswer = Boolean(
    isCertifiable &&
    hasCertifiedSource &&
    allWarnings.filter((w) => !w.startsWith('Assumption:')).length === 0 &&
    result && result.success !== false
  );
  const displayPath = rawPath === 'rewritten to governed' ? 'rewritten to governed' : rawPath.includes('free-form') ? 'free-form (not governed)' : 'governed';
  const certified = certifyState === 'done' || isCertifiedAnswer;
  // Only answers computed by a live warehouse that are certifiable can be certified, never preview rows or free-form unverified SQL.
  const canCertify = Boolean(isCertifiable && result && result.success !== false && !result.is_simulated);

  return (
    <div style={CARD}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 14, color: 'var(--text-primary)' }}>{answerTitle(plan, question)}</b>
        {isCertifiedAnswer ? (
          <span style={pill('ok')}><ShieldCheck size={12} /> Certified</span>
        ) : (
          <>
            <span style={pill(displayPath.includes('free-form') ? 'warn' : displayPath === 'rewritten to governed' ? 'ok' : 'neutral')}>
              {displayPath}
            </span>
            {allWarnings.map((w, idx) => (
              <span key={idx} style={pill('warn')} title={w}>
                {w}
              </span>
            ))}
          </>
        )}
        <span style={{ flex: 1 }} />
        {(canCertify || certified) && (
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
        )}
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

      <AnsweredBy plan={plan} result={result} />

      <AnswerResult columns={result?.columns} rows={result?.rows} />

      <HowAnswered plan={plan} result={result} target={target} />
    </div>
  );
}
