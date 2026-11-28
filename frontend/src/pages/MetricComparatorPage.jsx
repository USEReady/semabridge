import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Play, RefreshCw, ChevronDown, ChevronRight, Sliders, CheckCircle2,
  XCircle, AlertCircle, Eye, Layers, Database, Search, ArrowRight,
  Shield, Check, Clock
} from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import { useProjects } from '../hooks/useProjects';
import { useDeployedViews } from '../hooks/useDeployedViews';
import { useMetricComparison } from '../hooks/useMetricComparison';
import MetricComparisonResults from '../components/bitruth/comparison/MetricComparisonResults';
import { authFetch } from '../utils/api';

const SELECT_STYLE = {
  padding: '6px 10px',
  borderRadius: 6,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
  width: '100%',
};

const INPUT_STYLE = {
  ...SELECT_STYLE,
};

const LABEL_STYLE = {
  display: 'block',
  fontSize: 11,
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-tertiary)',
  marginBottom: 4,
};

// Helper to determine warehouse type for a project / model
function getProjectWarehouse(p) {
  const t = String(p?.target_type || p?.platform || '').toUpperCase();
  if (t.includes('DATABRICKS')) return 'DATABRICKS';
  if (t.includes('SNOWFLAKE')) return 'SNOWFLAKE';
  return 'OTHER';
}

export default function MetricComparatorPage() {
  const {
    projects = [],
    selectedProjectId,
    setSelectedProjectId,
    loading: projectsLoading,
  } = useProjects();

  // ─── Warehouse-wise Model Filtering State ─────────────────────────────
  const [warehouseFilter, setWarehouseFilter] = useState('ALL'); // 'ALL' | 'SNOWFLAKE' | 'DATABRICKS'
  const [historyWarehouseFilter, setHistoryWarehouseFilter] = useState('ALL');

  // Compute counts per warehouse
  const warehouseCounts = useMemo(() => {
    let sf = 0;
    let db = 0;
    projects.forEach((p) => {
      const w = getProjectWarehouse(p);
      if (w === 'SNOWFLAKE') sf++;
      else if (w === 'DATABRICKS') db++;
    });
    return { ALL: projects.length, SNOWFLAKE: sf, DATABRICKS: db };
  }, [projects]);

  // Filter projects/models based on active warehouse filter
  const filteredProjects = useMemo(() => {
    if (warehouseFilter === 'ALL') return projects;
    return projects.filter((p) => getProjectWarehouse(p) === warehouseFilter);
  }, [projects, warehouseFilter]);

  const activeProjectId = selectedProjectId || (filteredProjects[0]?.id || filteredProjects[0]?.project_id || projects[0]?.id || projects[0]?.project_id || '');

  // Handle warehouse filter change and auto-align selected project and target platform
  const handleWarehouseFilterChange = (w) => {
    setWarehouseFilter(w);
    if (w === 'ALL') return;

    const currentProj = projects.find((p) => (p.id || p.project_id) === activeProjectId);
    const currentW = getProjectWarehouse(currentProj);

    if (currentW !== w) {
      const firstMatching = projects.find((p) => getProjectWarehouse(p) === w);
      if (firstMatching) {
        setSelectedProjectId?.(firstMatching.id || firstMatching.project_id || firstMatching.name);
      }
    }
    setTargetPlatform(w);
  };

  // ─── Project Views & Metrics ─────────────────────────────────────────
  const { views = [], loading: viewsLoading } = useDeployedViews(activeProjectId);
  const [projectMetrics, setProjectMetrics] = useState([]);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricSearch, setMetricSearch] = useState('');

  const fetchProjectMetrics = useCallback(async (projId) => {
    if (!projId) return;
    setMetricsLoading(true);
    try {
      let res = await authFetch(`/api/bi-truth/metric-comparison/metrics?project_id=${encodeURIComponent(projId)}`);
      if (!res.ok) {
        res = await authFetch(`/api/reconciliation/project-metrics?project_id=${encodeURIComponent(projId)}`);
      }
      if (res.ok) {
        const data = await res.json();
        setProjectMetrics(data.metrics || []);
      }
    } catch (e) {
      console.warn('Could not load project metrics', e);
    } finally {
      setMetricsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeProjectId) {
      fetchProjectMetrics(activeProjectId);
    }
  }, [activeProjectId, fetchProjectMetrics]);

  // ─── Comparison Hook ─────────────────────────────────────────────────
  const {
    history,
    loading: historyLoading,
    running,
    currentResult,
    setCurrentResult,
    error,
    config,
    runComparison,
  } = useMetricComparison(activeProjectId);

  // ─── Configuration Form State ────────────────────────────────────────
  const [selectedMetric, setSelectedMetric] = useState('');
  const [sourcePlatform, setSourcePlatform] = useState('fabric'); // 'fabric' | 'power_bi'
  const [targetPlatform, setTargetPlatform] = useState('SNOWFLAKE'); // 'SNOWFLAKE' | 'DATABRICKS'
  const [targetView, setTargetView] = useState('');
  const [tolerancePct, setTolerancePct] = useState('0.01');
  const [nullPolicy, setNullPolicy] = useState('STRICT_NULL'); // 'STRICT_NULL' | 'NULL_EQUALS_ZERO'
  const [activeTab, setActiveTab] = useState('results'); // 'results' | 'history'

  // Advanced settings (collapsible)
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [mockMode, setMockMode] = useState(false);
  const [timeoutSeconds, setTimeoutSeconds] = useState(60);

  // Auto-select first metric when loaded
  useEffect(() => {
    if (projectMetrics.length > 0 && !selectedMetric) {
      const first = projectMetrics[0];
      setSelectedMetric(first.name || first.logical_name || String(first));
    }
  }, [projectMetrics, selectedMetric]);

  // Auto-detect target platform from active project metadata
  useEffect(() => {
    const curProj = projects.find((p) => (p.id || p.project_id) === activeProjectId);
    if (curProj?.target_type) {
      const tt = String(curProj.target_type).toUpperCase();
      if (tt.includes('DATABRICKS')) {
        setTargetPlatform('DATABRICKS');
      } else if (tt.includes('SNOWFLAKE')) {
        setTargetPlatform('SNOWFLAKE');
      }
    }
  }, [activeProjectId, projects]);

  // Sync project comparison configuration defaults (tolerance, null policy)
  useEffect(() => {
    if (config?.tolerance_pct !== undefined) {
      setTolerancePct(String(config.tolerance_pct));
    }
    if (config?.null_policy) {
      setNullPolicy(config.null_policy);
    }
  }, [config]);

  // Auto-select first view when loaded
  useEffect(() => {
    if (views.length > 0 && !targetView) {
      const firstView = views[0].view_name || views[0].name || String(views[0]);
      setTargetView(firstView);
    }
  }, [views, targetView]);

  // Filtered metrics list for selection
  const filteredMetrics = useMemo(() => {
    if (!metricSearch.trim()) return projectMetrics;
    const q = metricSearch.toLowerCase();
    return projectMetrics.filter((m) => {
      const name = String(m.name || m.logical_name || '').toLowerCase();
      const concept = String(m.concept || '').toLowerCase();
      return name.includes(q) || concept.includes(q);
    });
  }, [projectMetrics, metricSearch]);

  const handleRun = async (e) => {
    e?.preventDefault();
    if (!selectedMetric) return;

    try {
      setActiveTab('results');
      await runComparison({
        metric_name: selectedMetric,
        source_platform: sourcePlatform,
        target_platform: targetPlatform,
        target_view: targetView,
        tolerance_pct: parseFloat(tolerancePct) || 0.01,
        null_policy: nullPolicy,
        mock_mode: mockMode,
        timeout_seconds: timeoutSeconds || 60,
      });
    } catch {
      // Handled in useMetricComparison
    }
  };

  const handleSelectHistoryRun = (run) => {
    // Formats a historical run record so it renders in the detailed results view
    const formatted = {
      run_id: run.id,
      metric_name: run.metric_name,
      verdict: run.verdict,
      passed: run.verdict === 'PASS',
      variance_pct: run.variance_pct,
      absolute_difference: run.absolute_difference,
      tolerance_pct: run.tolerance_pct,
      null_policy: run.null_policy || 'STRICT_NULL',
      reason: run.reason,
      mock_mode: run.mock_mode,
      execution_ms: run.execution_ms,
      source: {
        platform: run.source_platform,
        raw_value: run.source_value,
        normalized_value: run.source_value !== null ? run.source_value : null,
        query: run.source_query,
        execution_ms: run.execution_ms ? Math.floor(run.execution_ms / 2) : 0,
        success: run.source_value !== null,
      },
      target: {
        platform: run.target_platform,
        raw_value: run.target_value,
        normalized_value: run.target_value !== null ? run.target_value : null,
        query: run.target_query,
        execution_ms: run.execution_ms ? Math.ceil(run.execution_ms / 2) : 0,
        success: run.target_value !== null,
      },
    };
    if (setCurrentResult) {
      setCurrentResult(formatted);
    }
    setActiveTab('results');
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      minHeight: 0,
      padding: '20px 24px',
      overflowY: 'auto',
      background: 'var(--bg-app)',
    }}>
      {/* ── 1. STANDARDIZED PAGE HEADER ───────────────────────────────── */}
      <PageHeader
        title="Metric Comparator"
        description="Real-time source-to-target semantic metric reconciliation and parity verification."
        action={
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            {/* Warehouse Filter Control */}
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 2,
              background: 'var(--bg-surface)',
              padding: '3px 4px',
              borderRadius: 6,
              border: '1px solid var(--border-main)',
            }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', padding: '0 6px', textTransform: 'uppercase' }}>
                Warehouse:
              </span>
              {[
                { id: 'ALL', label: 'All', count: warehouseCounts.ALL, color: 'var(--accent-blue)' },
                { id: 'SNOWFLAKE', label: 'Snowflake', count: warehouseCounts.SNOWFLAKE, color: '#29B5E8' },
                { id: 'DATABRICKS', label: 'Databricks', count: warehouseCounts.DATABRICKS, color: '#FF3621' },
              ].map((tab) => {
                const isActive = warehouseFilter === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => handleWarehouseFilterChange(tab.id)}
                    style={{
                      padding: '4px 9px',
                      fontSize: 11.5,
                      fontWeight: isActive ? 700 : 500,
                      borderRadius: 4,
                      border: 'none',
                      background: isActive ? tab.color : 'transparent',
                      color: isActive ? '#fff' : 'var(--text-secondary)',
                      cursor: 'pointer',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 5,
                      transition: 'all 0.15s ease',
                    }}
                  >
                    {tab.id !== 'ALL' && (
                      <span style={{
                        width: 6,
                        height: 6,
                        borderRadius: '50%',
                        background: isActive ? '#fff' : tab.color,
                      }} />
                    )}
                    {tab.label}
                    <span style={{
                      fontSize: 10,
                      opacity: isActive ? 0.95 : 0.65,
                      background: isActive ? 'rgba(0,0,0,0.25)' : 'var(--bg-surface-raised)',
                      padding: '1px 5px',
                      borderRadius: 10,
                    }}>
                      {tab.count}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Model / Project Selector Filtered by Warehouse */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontWeight: 600 }}>Model:</span>
              <select
                value={activeProjectId}
                onChange={(e) => setSelectedProjectId?.(e.target.value)}
                style={{
                  ...SELECT_STYLE,
                  width: 'auto',
                  minWidth: 230,
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                {filteredProjects.map((p) => {
                  const pid = p.id || p.project_id || p.name;
                  const wh = getProjectWarehouse(p);
                  const tag = wh === 'SNOWFLAKE' ? '❄️ Snowflake' : wh === 'DATABRICKS' ? '🧱 Databricks' : wh;
                  return (
                    <option key={pid} value={pid}>
                      {p.name || pid} ({tag})
                    </option>
                  );
                })}
              </select>
            </div>
          </div>
        }
      />

      {/* ── 2. WORKBENCH CONTAINER (2-COLUMN ENTERPRISE LAYOUT) ────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(340px, 390px) 1fr',
        gap: 18,
        alignItems: 'start',
      }}>
        {/* ── LEFT: COMPARISON CONFIGURATION PANEL ─────────────────────── */}
        <div style={{
          background: 'var(--bg-surface)',
          borderRadius: 8,
          border: '1px solid var(--border-main)',
          padding: 18,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid var(--border-main)', paddingBottom: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
              Comparison Configuration
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              Auto-resolving connection
            </span>
          </div>

          <form onSubmit={handleRun} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* A. Metric / Measure Selection */}
            <div>
              <label style={LABEL_STYLE}>
                Metric / Measure
              </label>
              {projectMetrics.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <select
                    value={selectedMetric}
                    onChange={(e) => setSelectedMetric(e.target.value)}
                    style={{ ...SELECT_STYLE, fontFamily: 'monospace' }}
                  >
                    <option value="">-- Choose Measure ({projectMetrics.length} available) --</option>
                    {filteredMetrics.map((m, idx) => {
                      const mName = m.name || m.logical_name || String(m);
                      const desc = m.concept ? ` [${m.concept}]` : '';
                      return (
                        <option key={idx} value={mName}>
                          {mName}{desc}
                        </option>
                      );
                    })}
                  </select>
                  {projectMetrics.length > 8 && (
                    <div style={{ position: 'relative' }}>
                      <input
                        type="text"
                        placeholder="Filter measures by name or concept…"
                        value={metricSearch}
                        onChange={(e) => setMetricSearch(e.target.value)}
                        style={{ ...INPUT_STYLE, fontSize: 11, padding: '4px 8px' }}
                      />
                    </div>
                  )}
                </div>
              ) : (
                <input
                  type="text"
                  placeholder="e.g. Total Sales, CLE"
                  value={selectedMetric}
                  onChange={(e) => setSelectedMetric(e.target.value)}
                  style={{ ...INPUT_STYLE, fontFamily: 'monospace' }}
                />
              )}
            </div>

            {/* B & C. Source Platform & Target Platform (Side-by-Side Segmented) */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {/* Source Platform */}
              <div>
                <label style={LABEL_STYLE}>Source Platform</label>
                <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', border: '1px solid var(--border-main)' }}>
                  <button
                    type="button"
                    onClick={() => setSourcePlatform('fabric')}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: 11.5,
                      fontWeight: 600,
                      background: sourcePlatform === 'fabric' ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                      color: sourcePlatform === 'fabric' ? '#fff' : 'var(--text-secondary)',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'center',
                    }}
                  >
                    Fabric
                  </button>
                  <button
                    type="button"
                    onClick={() => setSourcePlatform('power_bi')}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: 11.5,
                      fontWeight: 600,
                      background: sourcePlatform === 'power_bi' ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                      color: sourcePlatform === 'power_bi' ? '#fff' : 'var(--text-secondary)',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'center',
                    }}
                  >
                    Power BI
                  </button>
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 4 }}>
                  {sourcePlatform === 'fabric' ? 'Fabric REST / XMLA' : 'Desktop port / Service REST'}
                </div>
              </div>

              {/* Target Platform */}
              <div>
                <label style={LABEL_STYLE}>Target Warehouse</label>
                <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', border: '1px solid var(--border-main)' }}>
                  <button
                    type="button"
                    onClick={() => setTargetPlatform('SNOWFLAKE')}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: 11.5,
                      fontWeight: 600,
                      background: targetPlatform === 'SNOWFLAKE' ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                      color: targetPlatform === 'SNOWFLAKE' ? '#fff' : 'var(--text-secondary)',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'center',
                    }}
                  >
                    Snowflake
                  </button>
                  <button
                    type="button"
                    onClick={() => setTargetPlatform('DATABRICKS')}
                    style={{
                      flex: 1,
                      padding: '6px 4px',
                      fontSize: 11.5,
                      fontWeight: 600,
                      background: targetPlatform === 'DATABRICKS' ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                      color: targetPlatform === 'DATABRICKS' ? '#fff' : 'var(--text-secondary)',
                      border: 'none',
                      cursor: 'pointer',
                      textAlign: 'center',
                    }}
                  >
                    Databricks
                  </button>
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 4 }}>
                  {targetPlatform === 'SNOWFLAKE' ? 'Semantic View query' : 'Metric View query'}
                </div>
              </div>
            </div>

            {/* D. Target Semantic View / Metric View */}
            <div>
              <label style={LABEL_STYLE}>Target View / Metric View</label>
              {views.length > 0 ? (
                <select
                  value={targetView}
                  onChange={(e) => setTargetView(e.target.value)}
                  style={{ ...SELECT_STYLE, fontFamily: 'monospace' }}
                >
                  <option value="">-- Choose Target View ({views.length} found) --</option>
                  {views.map((v, idx) => {
                    const vName = v.view_name || v.name || String(v);
                    return (
                      <option key={idx} value={vName}>
                        {vName}
                      </option>
                    );
                  })}
                </select>
              ) : (
                <input
                  type="text"
                  placeholder="e.g. DB.SCHEMA.VIEW or catalog.schema.metric_view"
                  value={targetView}
                  onChange={(e) => setTargetView(e.target.value)}
                  style={{ ...INPUT_STYLE, fontFamily: 'monospace' }}
                />
              )}
            </div>

            {/* E. Tolerance Configuration */}
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <label style={LABEL_STYLE}>Acceptable Tolerance (%)</label>
              </div>
              <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                {[
                  { label: '0.00% Exact', val: '0.00' },
                  { label: '0.01% Standard', val: '0.01' },
                  { label: '0.10% Relaxed', val: '0.10' },
                ].map((item) => (
                  <button
                    key={item.val}
                    type="button"
                    onClick={() => setTolerancePct(item.val)}
                    style={{
                      flex: 1,
                      padding: '4px 6px',
                      borderRadius: 4,
                      fontSize: 11,
                      fontWeight: 600,
                      border: tolerancePct === item.val ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
                      background: tolerancePct === item.val ? 'rgba(99, 102, 241, 0.15)' : 'var(--bg-surface-raised)',
                      color: tolerancePct === item.val ? 'var(--accent-blue)' : 'var(--text-secondary)',
                      cursor: 'pointer',
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <input
                type="number"
                step="0.001"
                min="0"
                value={tolerancePct}
                onChange={(e) => setTolerancePct(e.target.value)}
                placeholder="Custom tolerance % (e.g. 0.01)"
                style={INPUT_STYLE}
              />
            </div>

            {/* F. NULL Handling Policy */}
            <div>
              <label style={LABEL_STYLE}>NULL Handling Policy</label>
              <div style={{ display: 'flex', borderRadius: 6, overflow: 'hidden', border: '1px solid var(--border-main)' }}>
                <button
                  type="button"
                  onClick={() => setNullPolicy('STRICT_NULL')}
                  style={{
                    flex: 1,
                    padding: '6px 8px',
                    fontSize: 11.5,
                    fontWeight: 600,
                    background: nullPolicy === 'STRICT_NULL' ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                    color: nullPolicy === 'STRICT_NULL' ? '#fff' : 'var(--text-secondary)',
                    border: 'none',
                    cursor: 'pointer',
                    textAlign: 'center',
                  }}
                >
                  STRICT_NULL
                </button>
                <button
                  type="button"
                  onClick={() => setNullPolicy('NULL_EQUALS_ZERO')}
                  style={{
                    flex: 1,
                    padding: '6px 8px',
                    fontSize: 11.5,
                    fontWeight: 600,
                    background: nullPolicy === 'NULL_EQUALS_ZERO' ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                    color: nullPolicy === 'NULL_EQUALS_ZERO' ? '#fff' : 'var(--text-secondary)',
                    border: 'none',
                    cursor: 'pointer',
                    textAlign: 'center',
                  }}
                >
                  NULL = 0
                </button>
              </div>
              <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 4 }}>
                {nullPolicy === 'STRICT_NULL'
                  ? 'Strict matching: NULL is preserved; failure if one side is numeric and the other NULL.'
                  : 'Coalesce matching: NULLs are converted to 0 via COALESCE(val, 0).'}
              </div>
            </div>

            {/* Collapsible: Advanced Settings */}
            <div style={{ borderTop: '1px solid var(--border-main)', paddingTop: 10 }}>
              <button
                type="button"
                onClick={() => setShowAdvanced(!showAdvanced)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  width: '100%',
                  background: 'transparent',
                  border: 'none',
                  color: 'var(--text-secondary)',
                  fontSize: 11.5,
                  fontWeight: 600,
                  cursor: 'pointer',
                  padding: '4px 0',
                }}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Sliders size={13} /> Advanced Settings
                </span>
                {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>

              {showAdvanced && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 10, padding: 10, borderRadius: 6, background: 'var(--bg-surface-raised)' }}>
                  {/* Mock Mode */}
                  <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={mockMode}
                      onChange={(e) => setMockMode(e.target.checked)}
                      style={{ marginTop: 2 }}
                    />
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                        Mock Mode (Persisted Snapshot Fixtures)
                      </div>
                      <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', marginTop: 2 }}>
                        Replays baseline fixtures from <code>baseline_snapshots.json</code> without live warehouse credentials. Never fabricates values.
                      </div>
                    </div>
                  </label>

                  {/* Execution Timeout */}
                  <div>
                    <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 4 }}>
                      Execution Timeout (seconds)
                    </label>
                    <input
                      type="number"
                      min="5"
                      max="300"
                      value={timeoutSeconds}
                      onChange={(e) => setTimeoutSeconds(parseInt(e.target.value, 10) || 60)}
                      style={{ ...INPUT_STYLE, padding: '4px 8px', fontSize: 11.5 }}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Run Button */}
            <button
              type="submit"
              disabled={running || !selectedMetric}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 8,
                padding: '9px 16px',
                borderRadius: 6,
                background: 'var(--accent-blue)',
                color: '#fff',
                border: 'none',
                fontSize: 12.5,
                fontWeight: 700,
                cursor: running || !selectedMetric ? 'not-allowed' : 'pointer',
                opacity: running || !selectedMetric ? 0.6 : 1,
                marginTop: 4,
              }}
            >
              {running ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />}
              {running ? 'Running Concurrent Comparison…' : 'Run Comparison'}
            </button>
          </form>
        </div>

        {/* ── RIGHT: RESULTS & HISTORY WORKSPACE ───────────────────────── */}
        <div style={{
          background: 'var(--bg-surface)',
          borderRadius: 8,
          border: '1px solid var(--border-main)',
          minHeight: 480,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}>
          {/* Top Tab Bar */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid var(--border-main)',
            padding: '0 16px',
            background: 'var(--bg-surface-raised)',
          }}>
            <div style={{ display: 'flex', gap: 20 }}>
              <button
                type="button"
                onClick={() => setActiveTab('results')}
                style={{
                  padding: '12px 0',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: `2px solid ${activeTab === 'results' ? 'var(--accent-blue)' : 'transparent'}`,
                  color: activeTab === 'results' ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: 12.5,
                  fontWeight: activeTab === 'results' ? 700 : 500,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                Comparison Results
                {currentResult?.verdict && (
                  <span style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: '1px 6px',
                    borderRadius: 4,
                    background: currentResult.passed ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                    color: currentResult.passed ? '#10B981' : '#EF4444',
                  }}>
                    {currentResult.verdict}
                  </span>
                )}
              </button>

              <button
                type="button"
                onClick={() => setActiveTab('history')}
                style={{
                  padding: '12px 0',
                  background: 'transparent',
                  border: 'none',
                  borderBottom: `2px solid ${activeTab === 'history' ? 'var(--accent-blue)' : 'transparent'}`,
                  color: activeTab === 'history' ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: 12.5,
                  fontWeight: activeTab === 'history' ? 700 : 500,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                Comparison History
                <span style={{
                  fontSize: 10.5,
                  padding: '1px 6px',
                  borderRadius: 10,
                  background: 'var(--bg-surface)',
                  color: 'var(--text-tertiary)',
                  fontWeight: 600,
                }}>
                  {history.length}
                </span>
              </button>
            </div>

            <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              {activeTab === 'results' && currentResult?.executed_at && (
                <span>Executed: {new Date(currentResult.executed_at).toLocaleTimeString()}</span>
              )}
            </div>
          </div>

          {/* Tab Content Area */}
          <div style={{ padding: 18, flex: 1 }}>
            {activeTab === 'results' ? (
              <MetricComparisonResults
                result={currentResult}
                running={running}
                error={error}
                projectId={activeProjectId}
              />
            ) : (
              /* History Data Table */
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {/* History Filter Bar */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 6 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontWeight: 700, textTransform: 'uppercase' }}>
                      Warehouse:
                    </span>
                    {['ALL', 'SNOWFLAKE', 'DATABRICKS'].map((wh) => (
                      <button
                        key={wh}
                        type="button"
                        onClick={() => setHistoryWarehouseFilter(wh)}
                        style={{
                          padding: '3px 8px',
                          fontSize: 11,
                          fontWeight: 600,
                          borderRadius: 4,
                          border: historyWarehouseFilter === wh ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
                          background: historyWarehouseFilter === wh ? 'rgba(99, 102, 241, 0.15)' : 'var(--bg-surface-raised)',
                          color: historyWarehouseFilter === wh ? 'var(--accent-blue)' : 'var(--text-secondary)',
                          cursor: 'pointer',
                        }}
                      >
                        {wh === 'ALL' ? 'All' : wh === 'SNOWFLAKE' ? '❄️ Snowflake' : '🧱 Databricks'}
                      </button>
                    ))}
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    Showing {history.filter(h => historyWarehouseFilter === 'ALL' || String(h.target_platform || '').toUpperCase() === historyWarehouseFilter).length} of {history.length} runs
                  </span>
                </div>

                {history.length === 0 ? (
                  <div style={{ padding: '64px 24px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
                    No comparison runs recorded yet for this project.
                  </div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                      <thead>
                        <tr style={{
                          background: 'var(--bg-surface-raised)',
                          borderBottom: '1px solid var(--border-main)',
                          textAlign: 'left',
                          color: 'var(--text-tertiary)',
                          fontSize: 11,
                        }}>
                          <th style={{ padding: '8px 12px' }}>Metric</th>
                          <th style={{ padding: '8px 12px' }}>Source Platform</th>
                          <th style={{ padding: '8px 12px' }}>Target Platform</th>
                          <th style={{ padding: '8px 12px' }}>Source Val</th>
                          <th style={{ padding: '8px 12px' }}>Target Val</th>
                          <th style={{ padding: '8px 12px' }}>Variance</th>
                          <th style={{ padding: '8px 12px' }}>Verdict</th>
                          <th style={{ padding: '8px 12px' }}>Timestamp</th>
                          <th style={{ padding: '8px 12px', textAlign: 'right' }}>Action</th>
                        </tr>
                      </thead>
                      <tbody>
                        {history
                          .filter(h => historyWarehouseFilter === 'ALL' || String(h.target_platform || '').toUpperCase() === historyWarehouseFilter)
                          .map((run) => {
                          const isPass = run.verdict === 'PASS';
                          const isFail = run.verdict === 'FAIL';
                          return (
                            <tr key={run.id} style={{ borderBottom: '1px solid var(--border-main)' }}>
                              <td style={{ padding: '10px 12px', fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                                {run.metric_name}
                              </td>
                              <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>
                                {run.source_platform}
                              </td>
                              <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>
                                {run.target_platform}
                              </td>
                              <td style={{ padding: '10px 12px', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                                {run.source_value !== null && run.source_value !== undefined ? run.source_value : 'NULL'}
                              </td>
                              <td style={{ padding: '10px 12px', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                                {run.target_value !== null && run.target_value !== undefined ? run.target_value : 'NULL'}
                              </td>
                              <td style={{
                                padding: '10px 12px',
                                fontFamily: 'monospace',
                                color: isPass ? '#10B981' : isFail ? '#EF4444' : '#F59E0B',
                              }}>
                                {run.variance_pct !== null && run.variance_pct !== undefined ? `${Number(run.variance_pct).toFixed(4)}%` : 'N/A'}
                              </td>
                              <td style={{ padding: '10px 12px' }}>
                                <span style={{
                                  fontSize: 10.5,
                                  fontWeight: 700,
                                  padding: '2px 7px',
                                  borderRadius: 4,
                                  background: isPass ? 'rgba(16, 185, 129, 0.12)' : isFail ? 'rgba(239, 68, 68, 0.12)' : 'rgba(245, 158, 11, 0.12)',
                                  color: isPass ? '#10B981' : isFail ? '#EF4444' : '#F59E0B',
                                }}>
                                  {run.verdict}
                                </span>
                              </td>
                              <td style={{ padding: '10px 12px', color: 'var(--text-tertiary)', fontSize: 11 }}>
                                {run.executed_at ? new Date(run.executed_at).toLocaleTimeString() : '—'}
                              </td>
                              <td style={{ padding: '10px 12px', textAlign: 'right' }}>
                                <button
                                  type="button"
                                  onClick={() => handleSelectHistoryRun(run)}
                                  style={{
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: 4,
                                    padding: '4px 8px',
                                    borderRadius: 4,
                                    fontSize: 11,
                                    fontWeight: 600,
                                    background: 'var(--bg-surface-raised)',
                                    color: 'var(--text-secondary)',
                                    border: '1px solid var(--border-main)',
                                    cursor: 'pointer',
                                  }}
                                >
                                  <Eye size={12} /> Inspect
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
