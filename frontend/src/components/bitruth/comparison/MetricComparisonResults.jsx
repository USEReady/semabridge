import React, { useState } from 'react';
import { useEffect } from 'react';
import {
  CheckCircle2, XCircle, AlertCircle, Clock, Database,
  Code, Copy, ChevronDown, ChevronRight, Sparkles, Check,
  Layers, ArrowRight, Shield, AlertTriangle
} from 'lucide-react';
import { authFetch } from '../../../utils/api';

const BADGE_STYLES = {
  PASS: {
    bg: 'rgba(16, 185, 129, 0.10)',
    border: 'rgba(16, 185, 129, 0.30)',
    color: '#10B981',
    icon: CheckCircle2,
    label: 'PASS',
  },
  FAIL: {
    bg: 'rgba(239, 68, 68, 0.10)',
    border: 'rgba(239, 68, 68, 0.30)',
    color: '#EF4444',
    icon: XCircle,
    label: 'FAIL',
  },
  NOT_COMPARABLE: {
    bg: 'rgba(245, 158, 11, 0.10)',
    border: 'rgba(245, 158, 11, 0.30)',
    color: '#F59E0B',
    icon: AlertCircle,
    label: 'NOT COMPARABLE',
  },
};

export default function MetricComparisonResults({ result, running, error, projectId }) {
  const [detailTab, setDetailTab] = useState('queries'); // 'queries' | 'diagnostics' | 'genie'
  const [copiedQuery, setCopiedQuery] = useState(null);
  const [publishing, setPublishing] = useState(false);
  const [publishStatus, setPublishStatus] = useState(null);
  const [evaluating, setEvaluating] = useState(false);
  const [evaluationResult, setEvaluationResult] = useState(null);

  // Dynamic Genie Space Discovery
  const [genieSpaces, setGenieSpaces] = useState([]);
  const [selectedSpaceId, setSelectedSpaceId] = useState('');
  const [customSpaceId, setCustomSpaceId] = useState('');
  const [loadingSpaces, setLoadingSpaces] = useState(false);

  const targetPlatform = String(result?.target?.platform || result?.target_platform || '').toUpperCase();
  const isDatabricks = targetPlatform === 'DATABRICKS';

  // Load actual Genie Spaces from workspace
  useEffect(() => {
    if (isDatabricks && (projectId || result)) {
      setLoadingSpaces(true);
      const q = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
      authFetch(`/api/genie/spaces${q}`)
        .then((r) => r.json())
        .then((data) => {
          const spaces = data.spaces || [];
          setGenieSpaces(spaces);
          if (spaces.length > 0) {
            setSelectedSpaceId(spaces[0].space_id || spaces[0].id || '');
          }
        })
        .catch((err) => console.warn('Could not load Genie spaces', err))
        .finally(() => setLoadingSpaces(false));
    }
  }, [isDatabricks, projectId, result]);

  const handleCopy = (text, key) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedQuery(key);
    setTimeout(() => setCopiedQuery(null), 2000);
  };

  const handlePublishGenie = async () => {
    if (!result) return;
    const finalSpaceId = (selectedSpaceId === '__custom__' ? customSpaceId : selectedSpaceId).trim();
    if (!finalSpaceId) {
      setPublishStatus({ type: 'error', msg: 'Please select or enter a valid Genie Space ID.' });
      return;
    }

    setPublishing(true);
    setPublishStatus(null);
    try {
      const res = await authFetch('/api/genie/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          space_id: finalSpaceId,
          project_id: projectId || undefined,
          pairs: [
            {
              question: `What is the ${result.metric_name}?`,
              expected_sql: result.target?.query || `SELECT MEASURE(${result.metric_name})`,
            },
          ],
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setPublishStatus({
          type: 'success',
          msg: `Successfully published verified benchmark question to Genie Space '${finalSpaceId}'.`,
        });
      } else {
        setPublishStatus({ type: 'error', msg: data.detail || 'Genie benchmark publishing failed.' });
      }
    } catch (e) {
      setPublishStatus({ type: 'error', msg: String(e) });
    } finally {
      setPublishing(false);
    }
  };

  const handleEvaluateGenie = async () => {
    if (!result) return;
    const finalSpaceId = (selectedSpaceId === '__custom__' ? customSpaceId : selectedSpaceId).trim();
    if (!finalSpaceId) {
      setPublishStatus({ type: 'error', msg: 'Please select or enter a valid Genie Space ID.' });
      return;
    }

    setEvaluating(true);
    setPublishStatus(null);
    setEvaluationResult(null);
    try {
      const res = await authFetch('/api/genie/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          space_id: finalSpaceId,
          project_id: projectId || undefined,
          benchmarks: [
            {
              question: `What is the ${result.metric_name}?`,
              expected_sql: result.target?.query || `SELECT MEASURE(${result.metric_name})`,
            },
          ],
          timeout_seconds: 60,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setEvaluationResult(data);
        setPublishStatus({
          type: 'success',
          msg: `Genie evaluation completed. Accuracy score: ${(Number(data.accuracy_score || 0) * 100).toFixed(1)}%.`,
        });
      } else {
        setPublishStatus({ type: 'error', msg: data.detail || 'Genie evaluation failed.' });
      }
    } catch (e) {
      setPublishStatus({ type: 'error', msg: String(e) });
    } finally {
      setEvaluating(false);
    }
  };

  // State: Loading / Running
  if (running) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '64px 24px',
        background: 'var(--bg-surface)',
        borderRadius: 8,
        border: '1px solid var(--border-main)',
        minHeight: 320,
      }}>
        <Clock size={28} className="animate-spin" style={{ color: 'var(--accent-blue)', marginBottom: 14 }} />
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
          Executing Source DAX &amp; Target SQL Concurrently…
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginTop: 4 }}>
          Evaluating Decimal-safe numeric precision and parity against configured tolerance.
        </div>
      </div>
    );
  }

  // State: Top-level error
  if (error && !result) {
    return (
      <div style={{
        padding: 24,
        background: 'rgba(239, 68, 68, 0.08)',
        borderRadius: 8,
        border: '1px solid rgba(239, 68, 68, 0.25)',
        color: '#EF4444',
      }}>
        <div style={{ fontSize: 13, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertCircle size={16} /> Comparison Execution Failed
        </div>
        <div style={{ fontSize: 12, marginTop: 6, color: 'var(--text-secondary)' }}>
          {error}
        </div>
      </div>
    );
  }

  // State A: Before Execution (Minimal, clean empty state)
  if (!result) {
    return (
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '64px 24px',
        background: 'var(--bg-surface)',
        borderRadius: 8,
        border: '1px solid var(--border-main)',
        minHeight: 320,
        textAlign: 'center',
      }}>
        <div style={{
          width: 42,
          height: 42,
          borderRadius: '50%',
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-main)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--text-tertiary)',
          marginBottom: 12,
        }}>
          <Layers size={20} />
        </div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
          No Active Comparison
        </div>
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, maxWidth: 380 }}>
          Configure your source and target metrics, then run a comparison to view reconciliation results.
        </div>
      </div>
    );
  }

  // State B: After Execution
  const verdict = result.verdict || (result.passed ? 'PASS' : 'FAIL');
  const badgeConfig = BADGE_STYLES[verdict] || BADGE_STYLES.NOT_COMPARABLE;
  const BadgeIcon = badgeConfig.icon;

  const sourceObj = result.source || {
    platform: result.source_platform || 'fabric',
    success: result.source_execution_status ? result.source_execution_status !== 'ERROR' : (result.source_value !== null),
    normalized_value: result.source_value,
    raw_value: result.source_value,
    execution_ms: result.source_execution_duration_ms || result.execution_ms,
    query: result.source_query || result.generated_dax,
    error: result.source_execution_status === 'ERROR' ? result.reason : null,
  };

  const targetObj = result.target || {
    platform: result.target_platform || 'snowflake',
    success: result.target_execution_status ? result.target_execution_status !== 'ERROR' : (result.target_value !== null),
    normalized_value: result.target_value,
    raw_value: result.target_value,
    execution_ms: result.target_execution_duration_ms || result.execution_ms,
    query: result.target_query || result.generated_sql,
    error: result.target_execution_status === 'ERROR' ? result.reason : null,
  };

  const sourceVal = sourceObj.normalized_value;
  const targetVal = targetObj.normalized_value;
  const absDiff = result.absolute_difference ?? result.absolute_variance;
  const variancePct = result.variance_pct ?? result.percentage_variance;
  const tolerancePct = result.tolerance_pct ?? result.tolerance_percentage;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* ── 1. OVERALL COMPARISON VERDICT HEADER ─────────────────────── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 12,
          padding: '12px 16px',
          borderRadius: 8,
          background: badgeConfig.bg,
          border: `1px solid ${badgeConfig.border}`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <BadgeIcon size={20} style={{ color: badgeConfig.color, flexShrink: 0 }} />
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 14, fontWeight: 700, color: badgeConfig.color }}>
                {badgeConfig.label}
              </span>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                {result.metric_name}
              </span>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', marginTop: 2 }}>
              {result.reason || (result.passed ? 'Source and target metrics match within configured tolerance.' : 'Discrepancy detected across platforms.')}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {result.mock_mode && (
            <span
              style={{
                fontSize: 10.5,
                fontWeight: 600,
                padding: '2px 8px',
                borderRadius: 4,
                background: 'rgba(99, 102, 241, 0.15)',
                color: '#818CF8',
                border: '1px solid rgba(99, 102, 241, 0.3)',
              }}
            >
              MOCK MODE (Persisted Fixture)
            </span>
          )}
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
            Total Duration: {result.execution_ms || (result.source?.execution_ms || 0) + (result.target?.execution_ms || 0)}ms
          </span>
        </div>
      </div>

      {/* ── 2. PRIMARY SIDE-BY-SIDE METRIC COMPARISON ────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* Source Card */}
        <div style={{
          padding: 14,
          borderRadius: 8,
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-main)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', letterSpacing: '0.04em' }}>
              SOURCE &bull; {sourceObj.platform === 'power_bi' ? 'Power BI' : 'Microsoft Fabric'}
            </span>
            <span style={{
              fontSize: 10.5,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 4,
              background: sourceObj.success ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
              color: sourceObj.success ? '#10B981' : '#EF4444',
            }}>
              {sourceObj.success ? 'SUCCESS' : 'FAILED'}
            </span>
          </div>

          <div style={{
            fontSize: 22,
            fontWeight: 700,
            color: 'var(--text-primary)',
            fontFamily: 'monospace',
            letterSpacing: '-0.02em',
          }}>
            {sourceVal !== null && sourceVal !== undefined ? sourceVal : 'NULL'}
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
            <span>Raw: <code>{String(sourceObj.raw_value ?? 'NULL')}</code></span>
            <span>Duration: {sourceObj.execution_ms || 0}ms</span>
          </div>

          {sourceObj.error && (
            <div style={{ marginTop: 8, padding: '6px 8px', borderRadius: 4, background: 'rgba(239, 68, 68, 0.1)', color: '#EF4444', fontSize: 11 }}>
              {sourceObj.error}
            </div>
          )}
        </div>

        {/* Target Card */}
        <div style={{
          padding: 14,
          borderRadius: 8,
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-main)',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span style={{ fontSize: 10.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)', letterSpacing: '0.04em' }}>
              TARGET &bull; {targetObj.platform || 'Warehouse'}
            </span>
            <span style={{
              fontSize: 10.5,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 4,
              background: targetObj.success ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
              color: targetObj.success ? '#10B981' : '#EF4444',
            }}>
              {targetObj.success ? 'SUCCESS' : 'FAILED'}
            </span>
          </div>

          <div style={{
            fontSize: 22,
            fontWeight: 700,
            color: 'var(--text-primary)',
            fontFamily: 'monospace',
            letterSpacing: '-0.02em',
          }}>
            {targetVal !== null && targetVal !== undefined ? targetVal : 'NULL'}
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8, fontSize: 11, color: 'var(--text-secondary)' }}>
            <span>Raw: <code>{String(targetObj.raw_value ?? 'NULL')}</code></span>
            <span>Duration: {targetObj.execution_ms || 0}ms</span>
          </div>

          {targetObj.error && (
            <div style={{ marginTop: 8, padding: '6px 8px', borderRadius: 4, background: 'rgba(239, 68, 68, 0.1)', color: '#EF4444', fontSize: 11 }}>
              {targetObj.error}
            </div>
          )}
        </div>
      </div>

      {/* ── 3. RECONCILIATION SUMMARY BAR ───────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
        gap: 10,
        padding: '10px 14px',
        borderRadius: 8,
        background: 'var(--bg-surface-raised)',
        border: '1px solid var(--border-main)',
      }}>
        <div>
          <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', fontWeight: 600 }}>ABSOLUTE VARIANCE</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', fontFamily: 'monospace', marginTop: 2 }}>
            {absDiff !== null && absDiff !== undefined
              ? Number(absDiff).toFixed(4)
              : 'N/A'}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', fontWeight: 600 }}>PERCENTAGE VARIANCE</div>
          <div style={{
            fontSize: 13,
            fontWeight: 700,
            color: result.passed ? '#10B981' : '#EF4444',
            fontFamily: 'monospace',
            marginTop: 2,
          }}>
            {variancePct !== null && variancePct !== undefined
              ? `${Number(variancePct).toFixed(4)}%`
              : 'N/A'}
          </div>
        </div>

        <div>
          <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', fontWeight: 600 }}>CONFIGURED TOLERANCE</div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginTop: 2 }}>
            &plusmn;{tolerancePct !== undefined ? tolerancePct : '0.01'}%
          </div>
        </div>

        <div>
          <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', fontWeight: 600 }}>NULL &amp; ROUNDING POLICY</div>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginTop: 2 }}>
            {result.null_policy || 'STRICT_NULL'} &bull; ROUND(4)
          </div>
        </div>
      </div>

      {/* ── 4. DETAILED EXECUTION VIEW (TABS) ────────────────────────── */}
      <div style={{
        borderRadius: 8,
        border: '1px solid var(--border-main)',
        background: 'var(--bg-surface)',
        overflow: 'hidden',
      }}>
        {/* Detail Tabs Bar */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid var(--border-main)',
          padding: '0 12px',
          background: 'var(--bg-surface-raised)',
        }}>
          <div style={{ display: 'flex', gap: 16 }}>
            <button
              type="button"
              onClick={() => setDetailTab('queries')}
              style={{
                padding: '10px 0',
                background: 'transparent',
                border: 'none',
                borderBottom: `2px solid ${detailTab === 'queries' ? 'var(--accent-blue)' : 'transparent'}`,
                color: detailTab === 'queries' ? 'var(--text-primary)' : 'var(--text-secondary)',
                fontSize: 12,
                fontWeight: detailTab === 'queries' ? 700 : 500,
                cursor: 'pointer',
              }}
            >
              Generated Queries
            </button>
            <button
              type="button"
              onClick={() => setDetailTab('diagnostics')}
              style={{
                padding: '10px 0',
                background: 'transparent',
                border: 'none',
                borderBottom: `2px solid ${detailTab === 'diagnostics' ? 'var(--accent-blue)' : 'transparent'}`,
                color: detailTab === 'diagnostics' ? 'var(--text-primary)' : 'var(--text-secondary)',
                fontSize: 12,
                fontWeight: detailTab === 'diagnostics' ? 700 : 500,
                cursor: 'pointer',
              }}
            >
              Reconciliation Diagnostics
            </button>
            {isDatabricks && (
              <button
                type="button"
                onClick={() => setDetailTab('genie')}
                style={{
                  padding: '10px 0',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: `2px solid ${detailTab === 'genie' ? 'var(--accent-blue)' : 'transparent'}`,
                  color: detailTab === 'genie' ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: 12,
                  fontWeight: detailTab === 'genie' ? 700 : 500,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 5,
                }}
              >
                <Sparkles size={12} style={{ color: 'var(--accent-blue)' }} /> Genie Space Integration
              </button>
            )}
          </div>
        </div>

        {/* Tab 1: Generated Queries */}
        {detailTab === 'queries' && (
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Source DAX */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>
                  Source DAX Query ({sourceObj.platform || 'Fabric/Power BI'})
                </span>
                <button
                  type="button"
                  onClick={() => handleCopy(sourceObj.query, 'src')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    fontSize: 11,
                    background: 'transparent',
                    border: 'none',
                    color: copiedQuery === 'src' ? '#10B981' : 'var(--text-tertiary)',
                    cursor: 'pointer',
                  }}
                >
                  {copiedQuery === 'src' ? <Check size={11} /> : <Copy size={11} />}
                  {copiedQuery === 'src' ? 'Copied' : 'Copy Query'}
                </button>
              </div>
              <pre style={{
                margin: 0,
                padding: '10px 12px',
                borderRadius: 6,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                fontSize: 11.5,
                fontFamily: 'monospace',
                overflowX: 'auto',
                color: 'var(--text-primary)',
              }}>
                {sourceObj.query || '-- No DAX query generated'}
              </pre>
            </div>

            {/* Target SQL */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>
                  Target Warehouse Query ({targetObj.platform || 'Warehouse'})
                </span>
                <button
                  type="button"
                  onClick={() => handleCopy(targetObj.query, 'tgt')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    fontSize: 11,
                    background: 'transparent',
                    border: 'none',
                    color: copiedQuery === 'tgt' ? '#10B981' : 'var(--text-tertiary)',
                    cursor: 'pointer',
                  }}
                >
                  {copiedQuery === 'tgt' ? <Check size={11} /> : <Copy size={11} />}
                  {copiedQuery === 'tgt' ? 'Copied' : 'Copy Query'}
                </button>
              </div>
              <pre style={{
                margin: 0,
                padding: '10px 12px',
                borderRadius: 6,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                fontSize: 11.5,
                fontFamily: 'monospace',
                overflowX: 'auto',
                color: 'var(--text-primary)',
              }}>
                {targetObj.query || '-- No SQL query generated'}
              </pre>
            </div>
          </div>
        )}

        {/* Tab 2: Diagnostics */}
        {detailTab === 'diagnostics' && (
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
              Reconciliation Formula &amp; Normalization Rules
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              <div>&bull; <strong>Decimal Normalization:</strong> Numeric values are rounded using Decimal-safe <code>ROUND(val, 4)</code>.</div>
              <div>&bull; <strong>NULL Policy ({result.null_policy || 'STRICT_NULL'}):</strong> {result.null_policy === 'NULL_EQUALS_ZERO' ? 'NULL values are converted to 0 via COALESCE(val, 0).' : 'Strict matching. If either side evaluates to NULL while the other is numeric, comparison fails.'}</div>
              <div>&bull; <strong>Variance Calculation:</strong> <code>Variance % = (|Source - Target| / |Source|) &times; 100</code></div>
              <div>&bull; <strong>Tolerance Condition:</strong> <code>Variance % &le; {result.tolerance_pct}% &rarr; {verdict}</code></div>
            </div>

            {sourceVal !== null && targetVal !== null && (
              <div style={{
                marginTop: 6,
                padding: '8px 12px',
                borderRadius: 6,
                background: 'var(--bg-surface-raised)',
                fontFamily: 'monospace',
                fontSize: 11.5,
                color: 'var(--text-primary)',
              }}>
                |{sourceVal} - {targetVal}| / |{sourceVal}| &times; 100 = {result.variance_pct !== null ? Number(result.variance_pct).toFixed(4) : '0.0000'}%
                {' '}{result.passed ? '<=' : '>'} {result.tolerance_pct}% ({verdict})
              </div>
            )}
          </div>
        )}

        {/* Tab 3: Databricks Genie Space Benchmark */}
        {detailTab === 'genie' && (
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-primary)' }}>
              Publish Verified Benchmark to Databricks Genie Space
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
              Register this verified question-SQL pair as a benchmark in a Databricks Genie Space for automated accuracy testing.
            </div>

            {/* Target Genie Space Selector */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxWidth: 460 }}>
              <label style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>
                Target Genie Space
              </label>
              {loadingSpaces ? (
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Discovering Genie Spaces from Databricks…</div>
              ) : genieSpaces.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <select
                    value={selectedSpaceId}
                    onChange={(e) => setSelectedSpaceId(e.target.value)}
                    style={{
                      padding: '6px 10px',
                      borderRadius: 6,
                      background: 'var(--bg-surface-raised)',
                      border: '1px solid var(--border-main)',
                      color: 'var(--text-primary)',
                      fontSize: 12,
                    }}
                  >
                    {genieSpaces.map((s) => (
                      <option key={s.space_id || s.id} value={s.space_id || s.id}>
                        {s.name || s.title || s.space_id} ({s.space_id || s.id})
                      </option>
                    ))}
                    <option value="__custom__">&plus; Enter custom Space ID…</option>
                  </select>

                  {selectedSpaceId === '__custom__' && (
                    <input
                      type="text"
                      placeholder="e.g. 01ef871b933d1b74a1234567"
                      value={customSpaceId}
                      onChange={(e) => setCustomSpaceId(e.target.value)}
                      style={{
                        padding: '6px 10px',
                        borderRadius: 6,
                        background: 'var(--bg-surface-raised)',
                        border: '1px solid var(--border-main)',
                        color: 'var(--text-primary)',
                        fontSize: 12,
                      }}
                    />
                  )}
                </div>
              ) : (
                <input
                  type="text"
                  placeholder="e.g. 01ef871b933d1b74a1234567 or workspace-space"
                  value={selectedSpaceId}
                  onChange={(e) => setSelectedSpaceId(e.target.value)}
                  style={{
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--border-main)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                  }}
                />
              )}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={handlePublishGenie}
                disabled={publishing || evaluating}
                style={{
                  padding: '7px 16px',
                  borderRadius: 6,
                  background: 'var(--accent-blue)',
                  color: '#fff',
                  border: 'none',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: (publishing || evaluating) ? 'not-allowed' : 'pointer',
                  opacity: (publishing || evaluating) ? 0.7 : 1,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                {publishing ? 'Publishing…' : 'Publish Benchmark Question'}
              </button>

              <button
                type="button"
                onClick={handleEvaluateGenie}
                disabled={publishing || evaluating}
                style={{
                  padding: '7px 16px',
                  borderRadius: 6,
                  background: 'var(--bg-surface-raised)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-main)',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: (publishing || evaluating) ? 'not-allowed' : 'pointer',
                  opacity: (publishing || evaluating) ? 0.7 : 1,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <Sparkles size={13} style={{ color: '#FF3621' }} />
                {evaluating ? 'Evaluating in Genie…' : 'Run Genie Evaluation'}
              </button>
            </div>

            {publishStatus && (
              <div style={{
                marginTop: 6,
                padding: '8px 12px',
                borderRadius: 6,
                fontSize: 11.5,
                background: publishStatus.type === 'success' ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                color: publishStatus.type === 'success' ? '#10B981' : '#EF4444',
                border: `1px solid ${publishStatus.type === 'success' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
              }}>
                {publishStatus.msg}
              </div>
            )}

            {/* Evaluation Results Scoreboard */}
            {evaluationResult && (
              <div style={{
                marginTop: 10,
                padding: 14,
                borderRadius: 8,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Shield size={14} style={{ color: '#10B981' }} /> Genie Benchmark Evaluation Outcome
                  </div>
                  <div style={{
                    fontSize: 11,
                    fontWeight: 700,
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: evaluationResult.accuracy_score === 1.0 ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: evaluationResult.accuracy_score === 1.0 ? '#10B981' : '#EF4444',
                  }}>
                    Accuracy: {(Number(evaluationResult.accuracy_score || 0) * 100).toFixed(1)}%
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8, fontSize: 11 }}>
                  <div>
                    <span style={{ color: 'var(--text-tertiary)' }}>Total Benchmarks:</span>{' '}
                    <strong style={{ color: 'var(--text-primary)' }}>{evaluationResult.total_benchmarks}</strong>
                  </div>
                  <div>
                    <span style={{ color: 'var(--text-tertiary)' }}>Passed:</span>{' '}
                    <strong style={{ color: '#10B981' }}>{evaluationResult.passed_benchmarks}</strong>
                  </div>
                  <div>
                    <span style={{ color: 'var(--text-tertiary)' }}>Failed:</span>{' '}
                    <strong style={{ color: '#EF4444' }}>{evaluationResult.failed_benchmarks}</strong>
                  </div>
                  <div>
                    <span style={{ color: 'var(--text-tertiary)' }}>Duration:</span>{' '}
                    <strong style={{ color: 'var(--text-primary)' }}>{evaluationResult.duration_ms} ms</strong>
                  </div>
                </div>

                {Array.isArray(evaluationResult.results) && evaluationResult.results.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
                    {evaluationResult.results.map((r, i) => (
                      <div key={i} style={{
                        padding: 10,
                        borderRadius: 6,
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-main)',
                        fontSize: 11.5,
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{r.question}</span>
                          <span style={{
                            fontWeight: 700,
                            fontSize: 10.5,
                            color: r.passed ? '#10B981' : '#EF4444',
                          }}>
                            {r.passed ? '✓ PASSED' : '✗ FAILED'}
                          </span>
                        </div>
                        {r.generated_sql && (
                          <div style={{ marginTop: 4 }}>
                            <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Genie Generated SQL:</div>
                            <pre style={{
                              margin: '2px 0 0',
                              padding: '6px 8px',
                              borderRadius: 4,
                              background: 'var(--bg-surface-raised)',
                              fontSize: 11,
                              fontFamily: 'monospace',
                              overflowX: 'auto',
                            }}>
                              {r.generated_sql}
                            </pre>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
