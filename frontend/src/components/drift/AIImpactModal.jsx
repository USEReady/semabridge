import React, { useState, useEffect } from 'react';
import {
  Sparkles,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  HelpCircle,
  FileText,
  Database,
  ArrowRight,
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import Modal from '../common/Modal';
import { authFetch, formatApiErrorMessage } from '../../utils/api';
import FormattedDiffText from './FormattedDiffText';

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 7,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_PRIMARY = {
  ...BTN,
  borderColor: 'var(--accent-blue)',
  background: 'var(--accent-blue)',
  color: '#fff',
};

export default function AIImpactModal({
  isOpen,
  onClose,
  event,
}) {
  const [loading, setLoading] = useState(false);
  const [report, setReport] = useState(null);
  const [error, setError] = useState('');
  const [questionsExpanded, setQuestionsExpanded] = useState(false);

  const objectName = event?.object_name || event?.name || event?.metric_name || '';
  const kind = event?.kind ? String(event.kind).toUpperCase() : 'METRIC';
  const table = event?.table || '';
  const projectId = event?.project_id || '';
  const viewName = event?.semantic_view || event?.view || '';
  const baselineDesc = event?.baseline_description || event?.details?.baseline_description_normalized || '';
  const liveDesc = event?.live_description || event?.details?.live_description_normalized || '';

  const rawClassification = report?.classification
    || event?.description_classification
    || event?.details?.description_classification
    || event?.classification
    || '';
  const effectiveClassification = rawClassification
    ? rawClassification.toUpperCase().replace(/\s+/g, '_')
    : ((!baselineDesc && liveDesc) ? 'TEXT_ADDED' : ((baselineDesc && !liveDesc) ? 'TEXT_REMOVED' : 'TEXT_REWORDED'));

  const classificationLabel = effectiveClassification === 'TEXT_ADDED'
    ? (!baselineDesc ? 'TEXT ADDED (NEW COMMENT)' : 'TEXT ADDED')
    : effectiveClassification === 'TEXT_REMOVED'
      ? 'TEXT REMOVED'
      : 'TEXT REWORDED';

  const runCheck = async () => {
    if (!objectName) return;
    setLoading(true);
    setError('');
    try {
      const res = await authFetch('/api/drift/ai-impact-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          object_name: objectName,
          metric_name: objectName,
          kind: kind,
          table: table || undefined,
          project_id: projectId || undefined,
          view_name: viewName || undefined,
          baseline_description: baselineDesc,
          live_description: liveDesc,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(formatApiErrorMessage(res, data, 'AI Impact Check'));
      } else {
        setReport(data);
      }
    } catch (err) {
      setError(`Unable to connect to AI Impact Check service: ${err.message || String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen && objectName) {
      setReport(null);
      setError('');
      setQuestionsExpanded(false);
      runCheck();
    }
  }, [isOpen, objectName]);

  if (!isOpen) return null;

  return (
    <Modal
      open={isOpen}
      onClose={onClose}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Sparkles size={16} style={{ color: 'var(--accent-blue)' }} />
          <span>AI Impact Check: <code style={{ color: 'var(--text-primary)' }}>{objectName}</code></span>
        </div>
      }
      width={900}
      style={{ maxWidth: 900, width: '95vw' }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          Evidence-based check: compares query engine routing and SQL generation under the stored baseline description versus the live warehouse comment using sampled real questions.
        </div>

        {/* Side-by-side Descriptions */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
          <div style={{ padding: '8px 12px', borderRadius: 6, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', fontSize: 11.5 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--accent-blue)', fontWeight: 600, marginBottom: 4 }}>
              <FileText size={12} /> Baseline Description
            </div>
            <div style={{ color: 'var(--text-primary)', wordBreak: 'break-word', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
              <FormattedDiffText text={event?.baseline_marked || baselineDesc || '(No description)'} />
            </div>
          </div>

          <div style={{ padding: '8px 12px', borderRadius: 6, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', fontSize: 11.5 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--text-secondary)', fontWeight: 600, marginBottom: 4 }}>
              <Database size={12} /> Warehouse Comment
            </div>
            <div style={{ color: 'var(--text-primary)', wordBreak: 'break-word', whiteSpace: 'pre-wrap', lineHeight: 1.5 }}>
              <FormattedDiffText text={event?.live_marked || liveDesc || '(No description)'} />
            </div>
          </div>
        </div>

        {/* Classification & Metadata Badges */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 11 }}>
          <span style={{
            padding: '2px 8px',
            borderRadius: 4,
            fontWeight: 700,
            background: effectiveClassification === 'TEXT_ADDED'
              ? 'rgba(34, 197, 94, 0.15)'
              : effectiveClassification === 'TEXT_REMOVED'
                ? 'rgba(239, 68, 68, 0.15)'
                : 'rgba(59, 130, 246, 0.15)',
            color: effectiveClassification === 'TEXT_ADDED'
              ? 'var(--color-success, #22c55e)'
              : effectiveClassification === 'TEXT_REMOVED'
                ? 'var(--color-error, #ef4444)'
                : 'var(--accent-blue, #3b82f6)',
          }}>
            Classification: {classificationLabel}
          </span>
          <span style={{ color: 'var(--text-secondary)' }}>
            Kind: <strong style={{ color: 'var(--text-primary)' }}>{kind}</strong>
          </span>
          {table && (
            <span style={{ color: 'var(--text-secondary)' }}>
              Table: <code style={{ color: 'var(--text-primary)' }}>{table}</code>
            </span>
          )}
        </div>

        {/* Error message */}
        {error && (
          <div style={{ padding: '8px 12px', borderRadius: 6, background: 'rgba(239, 68, 68, 0.1)', color: 'var(--color-error)', fontSize: 12 }}>
            {error}
          </div>
        )}

        {/* Loading Progress */}
        {loading && (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 12.5 }}>
            <Sparkles className="animate-spin" size={20} style={{ margin: '0 auto 8px', color: 'var(--accent-blue)' }} />
            Evaluating questions against semantic lexicon and query resolution planner…
          </div>
        )}

        {/* Report Results */}
        {!loading && report && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* If NO covering questions found */}
            {report.status === 'no_covering_questions' || report.questions_tested === 0 ? (
              <div style={{
                padding: '12px 14px',
                borderRadius: 8,
                background: 'rgba(234, 179, 8, 0.08)',
                border: '1px solid rgba(234, 179, 8, 0.25)',
                color: 'var(--text-primary)',
                fontSize: 12,
              }}>
                <div style={{ fontWeight: 700, marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <HelpCircle size={15} style={{ color: 'var(--color-warning)' }} />
                  <span>No Covering Questions Found</span>
                </div>
                <div style={{ lineHeight: 1.5 }}>
                  No certified queries, BI-truth cases, or query history cover <code style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>{objectName}</code>; no synthetic questions were generated.
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 8 }}>
                  To evaluate AI impact for this object, add a verified query in Ask AI or define a BI-truth test case covering it.
                </div>
              </div>
            ) : (
              <>
                {/* 1. Summary First */}
                <div style={{
                  padding: '12px 14px',
                  borderRadius: 8,
                  border: '1px solid var(--border-main)',
                  background: report.overall_impact === 'HIGH_IMPACT'
                    ? 'rgba(239, 68, 68, 0.08)'
                    : (report.overall_impact === 'MEDIUM_IMPACT' ? 'rgba(234, 179, 8, 0.08)' : 'rgba(34, 197, 94, 0.08)'),
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {report.overall_impact === 'HIGH_IMPACT' ? (
                        <ShieldAlert size={18} style={{ color: 'var(--color-error)' }} />
                      ) : (
                        <ShieldCheck size={18} style={{ color: 'var(--color-success)' }} />
                      )}
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                          {report.overall_impact === 'HIGH_IMPACT' && 'High AI Impact: Routing or refusal divergence detected'}
                          {report.overall_impact === 'MEDIUM_IMPACT' && 'Moderate AI Impact: Grouping, filter or SQL shift detected'}
                          {report.overall_impact === 'NO_DETECTED_IMPACT' && 'No AI Impact Detected on Sampled Questions'}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                          Source: <strong>{report.question_source?.replace('_', ' ') || 'test suite'}</strong> &bull; Ran <strong>{report.questions_tested}</strong> question(s) (<strong>{report.questions_compared ?? (report.questions_changed + report.questions_unchanged)}</strong> compared{report.questions_not_comparable > 0 ? `, ${report.questions_not_comparable} not comparable` : ''}) &bull; <strong>{report.questions_changed}</strong> changed, <strong>{report.questions_unchanged}</strong> unchanged
                          {report.cap_applied && <span style={{ marginLeft: 6, color: 'var(--text-tertiary)' }}>(capped at {report.max_questions_cap})</span>}
                        </div>
                        {report.sample_is_weak && (
                          <div style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 4, fontWeight: 600 }}>
                            ⚠️ Weak sample: Majority of questions were not comparable (refused or could not resolve on both sides).
                          </div>
                        )}
                      </div>
                    </div>

                    <span style={{
                      fontSize: 10.5,
                      fontWeight: 700,
                      padding: '3px 8px',
                      borderRadius: 4,
                      textTransform: 'uppercase',
                      background: report.overall_impact === 'HIGH_IMPACT'
                        ? 'var(--color-error)'
                        : (report.overall_impact === 'MEDIUM_IMPACT' ? 'var(--color-warning)' : 'var(--color-success)'),
                      color: '#fff',
                    }}>
                      {report.overall_impact.replace('_', ' ')}
                    </span>
                  </div>

                  {/* Risk Sentence */}
                  {report.risk_summary && (
                    <div style={{ fontSize: 12, color: 'var(--text-primary)', borderTop: '1px solid var(--border-main)', paddingTop: 6 }}>
                      <strong>Risk Assessment:</strong> {report.risk_summary}
                    </div>
                  )}

                  {/* Sampling Disclaimer */}
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    {report.sampling_disclaimer || report.disclaimer}
                  </div>
                </div>

                {/* 2. Per-Question Table Collapsed by Default */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
                      Question Breakdown ({report.questions?.length || 0})
                    </span>
                    <button
                      type="button"
                      onClick={() => setQuestionsExpanded(!questionsExpanded)}
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--accent-blue)',
                        cursor: 'pointer',
                        fontSize: 11.5,
                        fontWeight: 600,
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                        padding: 0,
                      }}
                    >
                      {questionsExpanded ? (
                        <>Hide question details <ChevronUp size={12} /></>
                      ) : (
                        <>Show question details ({report.questions?.length}) <ChevronDown size={12} /></>
                      )}
                    </button>
                  </div>

                  {questionsExpanded && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
                      {report.questions?.map((q, idx) => (
                        <div
                          key={idx}
                          style={{
                            padding: '10px 12px',
                            borderRadius: 6,
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-main)',
                            display: 'flex',
                            flexDirection: 'column',
                            gap: 6,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                            <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                              "{q.question}"
                            </span>
                            <span style={{
                              fontSize: 10,
                              fontWeight: 700,
                              padding: '2px 7px',
                              borderRadius: 4,
                              background: q.status === 'CHANGED'
                                ? 'rgba(239, 68, 68, 0.15)'
                                : (q.status === 'NOT_COMPARABLE'
                                  ? 'rgba(234, 179, 8, 0.15)'
                                  : 'rgba(34, 197, 94, 0.15)'),
                              color: q.status === 'CHANGED'
                                ? 'var(--color-error)'
                                : (q.status === 'NOT_COMPARABLE'
                                  ? 'var(--color-warning)'
                                  : 'var(--color-success)'),
                            }}>
                              {q.status === 'CHANGED' ? 'CHANGED' : (q.status === 'NOT_COMPARABLE' ? 'NOT COMPARABLE' : 'UNCHANGED')}
                            </span>
                          </div>

                          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                            {q.summary}
                          </div>

                          {/* Resolution Details */}
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 11, marginTop: 2 }}>
                            <div style={{ padding: '6px 8px', borderRadius: 4, background: 'var(--bg-app)', border: '1px solid var(--border-main)' }}>
                              <div style={{ fontWeight: 600, color: 'var(--accent-blue)', marginBottom: 2 }}>Baseline Resolution:</div>
                              <div>Object: <code style={{ color: 'var(--text-primary)' }}>{q.baseline_object || '(none)'}</code></div>
                              {q.baseline_refused && <div style={{ color: 'var(--color-error)' }}>Refused: {q.baseline_refusal_reason || 'Yes'}</div>}
                            </div>
                            <div style={{ padding: '6px 8px', borderRadius: 4, background: 'var(--bg-app)', border: '1px solid var(--border-main)' }}>
                              <div style={{ fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 2 }}>Live Warehouse Resolution:</div>
                              <div>Object: <code style={{ color: 'var(--text-primary)' }}>{q.live_object || '(none)'}</code></div>
                              {q.live_refused && <div style={{ color: 'var(--color-error)' }}>Refused: {q.live_refusal_reason || 'Yes'}</div>}
                            </div>
                          </div>

                          {/* SQL Diff if present */}
                          {q.sql_diff && (
                            <details style={{ fontSize: 10.5, color: 'var(--text-secondary)', marginTop: 4 }}>
                              <summary style={{ cursor: 'pointer', color: 'var(--accent-blue)' }}>View SQL Diff</summary>
                              <pre style={{
                                margin: '4px 0 0',
                                padding: '6px 8px',
                                borderRadius: 4,
                                background: 'var(--bg-app)',
                                overflowX: 'auto',
                                fontFamily: 'monospace',
                              }}>
                                {q.sql_diff}
                              </pre>
                            </details>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
          <button type="button" onClick={onClose} style={BTN}>
            Close
          </button>
          <button type="button" disabled={loading} onClick={runCheck} style={BTN_PRIMARY}>
            <Sparkles size={12} /> {loading ? 'Evaluating…' : 'Re-run Evaluation'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
