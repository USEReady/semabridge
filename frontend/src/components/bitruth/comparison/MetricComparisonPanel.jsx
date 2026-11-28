import React, { useState, useEffect } from 'react';
import { Play, Settings, History, Database, Layers, CheckCircle2, XCircle, AlertCircle, RefreshCw } from 'lucide-react';
import { useMetricComparison } from '../../../hooks/useMetricComparison';
import MetricComparisonResults from './MetricComparisonResults';

const INPUT = {
  padding: '6px 10px',
  borderRadius: 6,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
  width: '100%',
};

const LABEL = {
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: 4,
  display: 'block',
};

export default function MetricComparisonPanel({
  projectId,
  availableMetrics = [],
  currentView = '',
  targetPlatform = 'SNOWFLAKE',
  initialMetric = '',
}) {
  const {
    history,
    loading,
    running,
    currentResult,
    setCurrentResult,
    fetchRunDetail,
    error,
    config,
    saveConfig,
    runComparison,
  } = useMetricComparison(projectId);

  const [metricName, setMetricName] = useState(initialMetric || availableMetrics[0] || '');

  useEffect(() => {
    if (initialMetric) {
      setMetricName(initialMetric);
    } else if (!metricName && availableMetrics.length > 0) {
      setMetricName(availableMetrics[0]);
    }
  }, [initialMetric, availableMetrics]);
  const [sourcePlatform, setSourcePlatform] = useState('fabric');
  const [targetPlat, setTargetPlat] = useState(targetPlatform || 'SNOWFLAKE');
  const [targetView, setTargetView] = useState(currentView || '');
  const [tolerancePct, setTolerancePct] = useState(String(config.tolerance_pct || 0.01));
  const [nullPolicy, setNullPolicy] = useState(config.null_policy || 'STRICT_NULL');
  const [mockMode, setMockMode] = useState(false);
  const [activeTab, setActiveTab] = useState('execute'); // 'execute' | 'history'

  const handleRun = async (e) => {
    e.preventDefault();
    if (!metricName) return;
    try {
      await runComparison({
        metric_name: metricName,
        source_platform: sourcePlatform,
        target_platform: targetPlat,
        target_view: targetView || currentView,
        tolerance_pct: parseFloat(tolerancePct),
        null_policy: nullPolicy,
        mock_mode: mockMode,
      });
    } catch (err) {
      // Handled in hook
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Sub-header navigation */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '10px 16px',
          borderBottom: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Layers size={16} style={{ color: 'var(--accent-blue)' }} />
          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
            Real-Time Metric Comparison (Source DAX vs Target SQL)
          </span>
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          <button
            type="button"
            onClick={() => setActiveTab('execute')}
            style={{
              padding: '4px 10px',
              borderRadius: 6,
              fontSize: 11.5,
              fontWeight: 600,
              background: activeTab === 'execute' ? 'rgba(59,130,246,0.15)' : 'transparent',
              color: activeTab === 'execute' ? 'var(--accent-blue)' : 'var(--text-secondary)',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            Run Comparison
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('history')}
            style={{
              padding: '4px 10px',
              borderRadius: 6,
              fontSize: 11.5,
              fontWeight: 600,
              background: activeTab === 'history' ? 'rgba(59,130,246,0.15)' : 'transparent',
              color: activeTab === 'history' ? 'var(--accent-blue)' : 'var(--text-secondary)',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            History ({history.length})
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
        {activeTab === 'execute' ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 380px) 1fr', gap: 16 }}>
            {/* Form Controls */}
            <form
              onSubmit={handleRun}
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
                padding: 14,
                borderRadius: 8,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                height: 'fit-content',
              }}
            >
              {/* Metric Name */}
              <div>
                <label style={LABEL}>Metric / Measure Name</label>
                {availableMetrics.length > 0 ? (
                  <select
                    value={metricName}
                    onChange={(e) => setMetricName(e.target.value)}
                    style={INPUT}
                  >
                    {availableMetrics.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={metricName}
                    onChange={(e) => setMetricName(e.target.value)}
                    placeholder="Enter metric name (e.g. Sales, Revenue)"
                    style={INPUT}
                  />
                )}
              </div>

              {/* Source & Target Platforms */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={LABEL}>Source Platform (DAX)</label>
                  <select
                    value={sourcePlatform}
                    onChange={(e) => setSourcePlatform(e.target.value)}
                    style={INPUT}
                  >
                    <option value="fabric">Microsoft Fabric</option>
                    <option value="power_bi">Power BI (Desktop/REST)</option>
                  </select>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 3 }}>
                    {sourcePlatform === 'fabric'
                      ? 'Fabric REST executeQueries API / XMLA'
                      : 'Desktop port auto-discovery / Service REST'}
                  </div>
                </div>

                <div>
                  <label style={LABEL}>Target Warehouse (SQL)</label>
                  <select
                    value={targetPlat}
                    onChange={(e) => setTargetPlat(e.target.value)}
                    style={INPUT}
                  >
                    <option value="SNOWFLAKE">Snowflake</option>
                    <option value="DATABRICKS">Databricks</option>
                  </select>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 3 }}>
                    {targetPlat === 'SNOWFLAKE'
                      ? 'SELECT * FROM SEMANTIC_VIEW(...) METRICS'
                      : 'SELECT MEASURE(...) FROM <view>'}
                  </div>
                </div>
              </div>

              {/* Target View */}
              <div>
                <label style={LABEL}>Target Semantic View / Metric View</label>
                <input
                  type="text"
                  value={targetView}
                  onChange={(e) => setTargetView(e.target.value)}
                  placeholder="e.g. DB.SCHEMA.SEMANTIC_VIEW or catalog.schema.metric_view"
                  style={INPUT}
                />
              </div>

              {/* Tolerance & NULL Policy */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <label style={LABEL}>Tolerance (%)</label>
                  </div>
                  <input
                    type="number"
                    step="0.001"
                    value={tolerancePct}
                    onChange={(e) => setTolerancePct(e.target.value)}
                    placeholder="0.01"
                    style={INPUT}
                  />
                  {/* Preset tolerance chips */}
                  <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                    {[
                      { val: '0.00', label: '0.00% Exact' },
                      { val: '0.01', label: '0.01% Standard' },
                      { val: '0.10', label: '0.10% Relaxed' },
                    ].map((p) => (
                      <button
                        key={p.val}
                        type="button"
                        onClick={() => setTolerancePct(p.val)}
                        style={{
                          fontSize: 9.5,
                          padding: '2px 5px',
                          borderRadius: 4,
                          border: tolerancePct === p.val ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
                          background: tolerancePct === p.val ? 'rgba(59,130,246,0.15)' : 'transparent',
                          color: tolerancePct === p.val ? 'var(--accent-blue)' : 'var(--text-tertiary)',
                          cursor: 'pointer',
                        }}
                      >
                        {p.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label style={LABEL}>NULL Policy</label>
                  <select
                    value={nullPolicy}
                    onChange={(e) => setNullPolicy(e.target.value)}
                    style={INPUT}
                  >
                    <option value="STRICT_NULL">STRICT_NULL (Strict)</option>
                    <option value="NULL_EQUALS_ZERO">NULL = 0 (Coalesce)</option>
                  </select>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 3 }}>
                    {nullPolicy === 'STRICT_NULL'
                      ? 'Discrepancy if one side is NULL and other is 0'
                      : 'Coalesce NULLs to 0 via COALESCE(val, 0)'}
                  </div>
                </div>
              </div>

              {/* Mock Mode Checkbox */}
              <div style={{
                padding: '8px 10px',
                borderRadius: 6,
                background: mockMode ? 'rgba(99,102,241,0.08)' : 'transparent',
                border: mockMode ? '1px solid rgba(99,102,241,0.3)' : '1px solid transparent',
              }}>
                <label
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    fontSize: 12,
                    fontWeight: mockMode ? 600 : 400,
                    color: mockMode ? 'var(--text-primary)' : 'var(--text-secondary)',
                    cursor: 'pointer',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={mockMode}
                    onChange={(e) => setMockMode(e.target.checked)}
                  />
                  <span>Mock Mode (Persisted Snapshot Fixtures)</span>
                </label>
                {mockMode && (
                  <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 4, paddingLeft: 22 }}>
                    Uses persisted baseline fixtures from <code>baseline_snapshots.json</code> without requiring live cloud credentials. Does not synthesize fake numbers.
                  </div>
                )}
              </div>

              {/* Run Button */}
              <button
                type="submit"
                disabled={running || !metricName}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                  padding: '8px 12px',
                  borderRadius: 6,
                  background: 'var(--accent-blue)',
                  color: '#fff',
                  border: 'none',
                  fontSize: 12,
                  fontWeight: 600,
                  cursor: running ? 'not-allowed' : 'pointer',
                  opacity: running ? 0.7 : 1,
                  marginTop: 8,
                }}
              >
                {running ? <RefreshCw size={13} className="animate-spin" /> : <Play size={13} />}
                {running ? 'Running Comparison...' : 'Run Metric Comparison'}
              </button>

              {error && (
                <div style={{ padding: '8px 10px', borderRadius: 6, background: 'rgba(239,68,68,0.1)', color: '#ef4444', fontSize: 11 }}>
                  {error}
                </div>
              )}
            </form>

            {/* Live Results Panel */}
            <div
              style={{
                borderRadius: 8,
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-main)',
                padding: 16,
              }}
            >
              <MetricComparisonResults result={currentResult} running={running} />
            </div>
          </div>
        ) : (
          /* History Tab */
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {history.length === 0 ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
                No comparison runs recorded yet.
              </div>
            ) : (
              history.map((run) => {
                const isPass = run.verdict === 'PASS';
                const isFail = run.verdict === 'FAIL';
                return (
                  <div
                    key={run.id}
                    onClick={() => {
                      setCurrentResult(run);
                      if (run.id && fetchRunDetail) {
                        fetchRunDetail(run.id).then((d) => d && setCurrentResult(d));
                      }
                      setActiveTab('execute');
                    }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '10px 14px',
                      borderRadius: 6,
                      background: 'var(--bg-surface-raised)',
                      border: '1px solid var(--border-main)',
                      cursor: 'pointer',
                      transition: 'border-color 0.15s ease',
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      {isPass ? (
                        <CheckCircle2 size={16} style={{ color: '#22c55e' }} />
                      ) : isFail ? (
                        <XCircle size={16} style={{ color: '#ef4444' }} />
                      ) : (
                        <AlertCircle size={16} style={{ color: '#eab308' }} />
                      )}
                      <div>
                        <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-primary)' }}>
                          {run.metric_name}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                          Source ({run.source_platform}): {run.source_value ?? 'NULL'} vs Target ({run.target_platform}): {run.target_value ?? 'NULL'}
                          {run.variance_pct !== null && ` • Var: ${Number(run.variance_pct).toFixed(4)}%`}
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <span
                        style={{
                          fontSize: 10.5,
                          fontWeight: 700,
                          padding: '2px 8px',
                          borderRadius: 4,
                          background: isPass ? 'rgba(34,197,94,0.15)' : isFail ? 'rgba(239,68,68,0.15)' : 'rgba(234,179,8,0.15)',
                          color: isPass ? '#22c55e' : isFail ? '#ef4444' : '#eab308',
                        }}
                      >
                        {run.verdict}
                      </span>
                      <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>
                        {run.executed_at ? new Date(run.executed_at).toLocaleTimeString() : ''}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}
