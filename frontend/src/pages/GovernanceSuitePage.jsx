import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { authFetch } from '../utils/api';
import { useProjects } from '../hooks/useProjects';
import Modal from '../components/common/Modal';
import { ImpactReport } from '../components/ontology/GovernancePanels';
import DeployedViewSelector from '../components/common/DeployedViewSelector';
import WarehouseFilter from '../components/common/WarehouseFilter';
import EmptyState from '../components/common/EmptyState';
import DriftSchedules from '../components/drift/DriftSchedules';
import {
  Play, Check, X, RefreshCw, Clock, Layers, ShieldCheck, ArrowLeftRight,
  Trash2, CheckCircle2, AlertTriangle, Code, Sparkles, HelpCircle,
  Plus, Upload, FileText, Download, Zap, Radio, Target, Database,
} from 'lucide-react';

const CARD = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 12,
  padding: 16,
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
};
const INPUT = {
  padding: '7px 10px',
  borderRadius: 8,
  background: 'var(--bg-input)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12.5,
  outline: 'none',
};
const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 8,
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

export function DriftView({ projectId = '' }) {
  const navigate = useNavigate();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── DEDICATED DRIFT MONITORING SUITE BANNER ── */}
      <div
        style={{
          ...CARD,
          padding: 20,
          background: 'linear-gradient(135deg, var(--bg-surface) 0%, rgba(59, 130, 246, 0.06) 100%)',
          border: '1px solid var(--border-main)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <ShieldCheck size={18} style={{ color: 'var(--accent-blue, #3b82f6)' }} />
              <strong style={{ fontSize: 15, color: 'var(--text-primary)' }}>
                Dedicated Drift Monitoring Suite
              </strong>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 4, maxWidth: 650 }}>
              Inspect live warehouse views, compare semantic formulas, review grouped additions/deletions, and manage sync approvals.
            </div>
          </div>

          <button
            type="button"
            onClick={() => navigate(`/drift${projectId ? `?project=${encodeURIComponent(projectId)}` : ''}`)}
            style={{
              ...BTN_PRIMARY,
              padding: '8px 16px',
              fontSize: 13,
            }}
          >
            Open Full Drift Suite <ArrowLeftRight size={14} />
          </button>
        </div>
      </div>

      {/* ── AUTOMATED DRIFT SCHEDULES ── */}
      <DriftSchedules projectId={projectId} />
    </div>
  );
}

export function ReverseSyncView({ projectId = '' }) {
  const [sfAccounts, setSfAccounts] = useState([]);
  const [biAccounts, setBiAccounts] = useState([]);
  const [sfAccountId, setSfAccountId] = useState('');
  const [biAccountId, setBiAccountId] = useState('');
  const [biWorkspaces, setBiWorkspaces] = useState([]);
  const [biWorkspaceId, setBiWorkspaceId] = useState('');
  const [biModels, setBiModels] = useState([]);
  const [biModelId, setBiModelId] = useState('');
  const [semanticViews, setSemanticViews] = useState([]);
  const [loadingViews, setLoadingViews] = useState(false);
  const [viewsStatus, setViewsStatus] = useState('');
  const [semanticView, setSemanticView] = useState('');
  const [pbipPath, setPbipPath] = useState('');
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState({});
  const [showTmsl, setShowTmsl] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => {
    authFetch('/api/accounts')
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        const rows = Array.isArray(list) ? list : (list?.accounts || []);
        const sf = rows.filter((a) => String(a.connector_type || '').toUpperCase() === 'SNOWFLAKE');
        const bi = rows.filter((a) => ['FABRIC', 'POWERBI', 'POWER_BI'].includes(String(a.connector_type || '').toUpperCase()));
        setSfAccounts(sf);
        setBiAccounts(bi);
        if (sf.length && !sfAccountId) {
          const def = sf.find((a) => a.is_default) || sf[0];
          setSfAccountId(def.id);
        }
        if (bi.length && !biAccountId) {
          const defBi = bi.find((a) => a.is_default) || bi[0];
          setBiAccountId(defBi.id);
        }
      })
      .catch(() => { });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const loadSemanticViews = useCallback(async (acctId) => {
    if (!acctId && !projectId) return;
    setLoadingViews(true);
    setViewsStatus('');
    try {
      const params = new URLSearchParams();
      if (acctId) params.set('account_id', acctId);
      if (projectId) params.set('project_id', projectId);
      const res = await authFetch(`/api/reverse-sync/semantic-views?${params.toString()}`);
      const data = await res.json();
      const views = data?.views || [];
      setSemanticViews(views);
      if (views.length) {
        setSemanticView((prev) => (views.some((v) => v.fqn === prev) ? prev : views[0].fqn));
        setViewsStatus(`${views.length} semantic model(s) loaded`);
      } else {
        setViewsStatus(data?.error || 'No semantic views found in this warehouse account');
      }
    } catch (err) {
      setViewsStatus(String(err));
    } finally {
      setLoadingViews(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (sfAccountId) loadSemanticViews(sfAccountId);
  }, [sfAccountId, loadSemanticViews]);

  useEffect(() => {
    setBiWorkspaces([]);
    setBiWorkspaceId('');
    setBiModels([]);
    setBiModelId('');
    if (!biAccountId) return;
    authFetch(`/api/connections/fabric/workspaces?account_id=${encodeURIComponent(biAccountId)}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((data) => {
        const ws = data?.workspaces || (Array.isArray(data) ? data : []);
        setBiWorkspaces(ws);
        if (ws.length) setBiWorkspaceId(ws[0].id || ws[0].workspace_id || '');
      })
      .catch(() => { });
  }, [biAccountId]);

  useEffect(() => {
    setBiModels([]);
    setBiModelId('');
    if (!biWorkspaceId) return;
    const q = biAccountId ? `?account_id=${encodeURIComponent(biAccountId)}` : '';
    authFetch(`/api/discovery/fabric/workspaces/${encodeURIComponent(biWorkspaceId)}/models${q}`)
      .then((r) => (r.ok ? r.json() : {}))
      .then((data) => {
        const models = data?.models || data?.items || (Array.isArray(data) ? data : []);
        setBiModels(models);
        if (models.length) setBiModelId(models[0].id || models[0].model_id || models[0].name || '');
      })
      .catch(() => { });
  }, [biWorkspaceId, biAccountId]);

  const runPreview = async () => {
    setMsg('');
    const res = await authFetch('/api/reverse-sync/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId || undefined,
        account_id: sfAccountId || undefined,
        semantic_view: semanticView.trim() || undefined,
        pbip_path: pbipPath.trim() || undefined,
      }),
    });
    if (res.ok) {
      const data = await res.json();
      setPreview(data);
      const sel = {};
      (data.upgrades || []).forEach((_, idx) => { sel[idx] = true; });
      setSelected(sel);
    }
  };

  const selectedUpgrades = (preview?.upgrades || []).filter((_, idx) => selected[idx]);

  const applyAll = async () => {
    if (!pbipPath.trim() || !selectedUpgrades.length) return;
    const res = await authFetch('/api/reverse-sync/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pbip_path: pbipPath.trim(), upgrades: selectedUpgrades }),
    });
    const data = await res.json();
    setMsg(res.ok ? `Applied ${data.applied_count} additive upgrade(s) to ${data.target_file}` : (data.detail || 'Apply failed.'));
  };

  return (
    <div style={CARD}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
          Connected Snowflake Account
          <select style={INPUT} value={sfAccountId} onChange={(e) => setSfAccountId(e.target.value)}>
            <option value="">{sfAccounts.length ? 'Select Snowflake account…' : 'No Snowflake accounts connected'}</option>
            {sfAccounts.map((a) => (
              <option key={a.id} value={a.id}>{a.tag || a.identity_email || a.id}</option>
            ))}
          </select>
        </label>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
          Snowflake Semantic Model
          <div style={{ display: 'flex', gap: 6 }}>
            <select style={{ ...INPUT, flex: 1 }} value={semanticView} onChange={(e) => setSemanticView(e.target.value)}>
              <option value="">{loadingViews ? 'Loading semantic views…' : 'All / auto-discover'}</option>
              {semanticViews.map((v) => (
                <option key={v.fqn} value={v.fqn}>{v.fqn}</option>
              ))}
            </select>
            <button type="button" style={BTN} disabled={loadingViews} onClick={() => loadSemanticViews(sfAccountId)} title="Refresh Semantic Views">
              <RefreshCw size={12} />
            </button>
          </div>
        </label>

        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
          Target BI Account (Power BI / Fabric)
          <select style={INPUT} value={biAccountId} onChange={(e) => setBiAccountId(e.target.value)}>
            <option value="">{biAccounts.length ? 'Select Power BI account…' : 'Local PBIP/TMDL or TMSL script'}</option>
            {biAccounts.map((a) => (
              <option key={a.id} value={a.id}>{a.tag || a.identity_email || a.id}</option>
            ))}
          </select>
        </label>

        {biAccountId && (
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Power BI Workspace
            <select style={INPUT} value={biWorkspaceId} onChange={(e) => setBiWorkspaceId(e.target.value)}>
              <option value="">{biWorkspaces.length ? 'Select workspace…' : 'No workspaces found'}</option>
              {biWorkspaces.map((w) => (
                <option key={w.id || w.workspace_id} value={w.id || w.workspace_id}>{w.name || w.displayName || w.id}</option>
              ))}
            </select>
          </label>
        )}

        {biAccountId && (
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Power BI Semantic Model to Update
            <select style={INPUT} value={biModelId} onChange={(e) => setBiModelId(e.target.value)}>
              <option value="">{biModels.length ? 'Select semantic model…' : 'No models in workspace'}</option>
              {biModels.map((m) => (
                <option key={m.id || m.model_id || m.name} value={m.id || m.model_id || m.name}>{m.name || m.displayName || m.id}</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {viewsStatus && <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{viewsStatus}</div>}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <input style={{ ...INPUT, flex: 1, minWidth: 240 }} placeholder="Power BI PBIP/TMDL folder path (optional, for direct file patch)" value={pbipPath} onChange={(e) => setPbipPath(e.target.value)} />
        <button type="button" style={BTN_PRIMARY} onClick={runPreview}><ArrowLeftRight size={13} /> Preview Snowflake → BI Upgrades</button>
        {selectedUpgrades.length > 0 && pbipPath.trim() && (
          <button type="button" style={BTN} onClick={applyAll}><Check size={13} /> Insert Selected ({selectedUpgrades.length})</button>
        )}
        {preview?.tmsl_script && (
          <button type="button" style={BTN} onClick={() => setShowTmsl((v) => !v)}>{showTmsl ? 'Hide TMSL' : 'View TMSL Script'}</button>
        )}
      </div>
      {msg && <div style={{ fontSize: 12, color: 'var(--color-success)' }}>{msg}</div>}
      {preview && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {preview.snowflake_connected ? 'Connected to Snowflake' : `Using governed ontology (${preview.snowflake_error || 'Snowflake offline'})`} · {preview.upgrade_count} additive upgrade(s) found · {preview.existing_bi_measures} existing Power BI measure(s) preserved.
          </span>
          {(preview.upgrades || []).map((u, i) => (
            <label key={i} style={{ padding: 10, borderRadius: 8, background: 'var(--bg-app)', border: '1px solid var(--border-main)', fontSize: 12, display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
              <input type="checkbox" checked={Boolean(selected[i])} onChange={(e) => setSelected((prev) => ({ ...prev, [i]: e.target.checked }))} style={{ marginTop: 2 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <strong style={{ color: 'var(--accent-blue)' }}>{u.action}</strong> · <b>{u.metric_name}</b> <span style={{ color: 'var(--text-tertiary)' }}>({u.source_view})</span>
                {u.dax_expression && <div style={{ fontSize: 11.5, color: 'var(--text-primary)', marginTop: 3 }}>DAX: <code>{u.dax_expression}</code></div>}
                {u.description && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>{u.description}</div>}
              </div>
            </label>
          ))}
          {showTmsl && preview.tmsl_script && (
            <pre style={{ margin: 0, fontSize: 11, maxHeight: 260, overflow: 'auto', background: 'var(--bg-app)', padding: 10, borderRadius: 8 }}>{preview.tmsl_script}</pre>
          )}
        </div>
      )}
    </div>
  );
}

export function BiTruthView({ projectId = '' }) {
  const navigate = useNavigate();

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div
        style={{
          ...CARD,
          padding: 20,
          background: 'linear-gradient(135deg, var(--bg-surface) 0%, rgba(59, 130, 246, 0.06) 100%)',
          border: '1px solid var(--border-main)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 14 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Target size={18} style={{ color: 'var(--accent-blue, #3b82f6)' }} />
              <strong style={{ fontSize: 15, color: 'var(--text-primary)' }}>
                Target-Aware BI Truth Suite
              </strong>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 4, maxWidth: 650 }}>
              Verify warehouse queries, evaluate slice windows, manage critical deploy gates, and benchmark metric accuracy.
            </div>
          </div>

          <button
            type="button"
            onClick={() => navigate(`/bi-truth${projectId ? `?project=${encodeURIComponent(projectId)}` : ''}`)}
            style={{
              ...BTN_PRIMARY,
              padding: '8px 16px',
              fontSize: 13,
            }}
          >
            Open Full BI Truth Suite <ArrowLeftRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

function CrossPlatformView({ projectId }) {
  const [versionTag, setVersionTag] = useState('v1.0.0');
  const [preview, setPreview] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [accounts, setAccounts] = useState([]);
  const [sfAccountId, setSfAccountId] = useState('');
  const [dbAccountId, setDbAccountId] = useState('');
  const [sfSql, setSfSql] = useState('');
  const [dbSql, setDbSql] = useState('');
  const [parity, setParity] = useState(null);
  const [checking, setChecking] = useState(false);
  const [metrics, setMetrics] = useState([]);
  const [selectedMetric, setSelectedMetric] = useState('');
  const [loadingMetrics, setLoadingMetrics] = useState(false);

  useEffect(() => {
    authFetch('/api/accounts')
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        const rows = Array.isArray(list) ? list : (list?.accounts || []);
        setAccounts(rows);
        const sf = rows.find((a) => String(a.connector_type || '').toUpperCase() === 'SNOWFLAKE');
        const db = rows.find((a) => String(a.connector_type || '').toUpperCase() === 'DATABRICKS');
        if (sf) setSfAccountId(sf.id);
        if (db) setDbAccountId(db.id);
      })
      .catch(() => { });
  }, []);

  const handleMetricSelect = useCallback((metricName, metricList = metrics) => {
    setSelectedMetric(metricName);
    const m = (metricList || []).find((x) => x.name === metricName);
    if (m) {
      const expr = m.sql_expression || 'COUNT(*)';
      const tbl = m.table || m.concept;
      const query = tbl ? `SELECT ${expr} AS ${m.name} FROM ${tbl}` : `SELECT ${expr} AS ${m.name}`;
      setSfSql(query);
      setDbSql(query);
    }
  }, [metrics]);

  useEffect(() => {
    if (!projectId) {
      setMetrics([]);
      setSelectedMetric('');
      return;
    }
    setLoadingMetrics(true);
    authFetch(`/api/reconciliation/project-metrics?project_id=${encodeURIComponent(projectId)}`)
      .then((r) => (r.ok ? r.json() : { metrics: [] }))
      .then((data) => {
        const list = data.metrics || [];
        setMetrics(list);
        if (list.length > 0) {
          const first = list[0];
          handleMetricSelect(first.name, list);
        }
      })
      .catch(() => { })
      .finally(() => setLoadingMetrics(false));
  }, [projectId, handleMetricSelect]);

  const loadPreview = async () => {
    setLoadingPreview(true);
    try {
      const res = await authFetch('/api/cross-platform/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          metric: selectedMetric || undefined,
          version_tag: versionTag,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setPreview(data);
        if ((!metrics || metrics.length === 0) && data.metrics?.length > 0) {
          setMetrics(data.metrics);
          if (!selectedMetric) {
            handleMetricSelect(data.metrics[0].name, data.metrics);
          }
        }
      }
    } finally {
      setLoadingPreview(false);
    }
  };

  const runParity = async () => {
    let querySf = sfSql.trim();
    let queryDb = dbSql.trim();

    if (!querySf && selectedMetric) {
      const m = metrics.find((x) => x.name === selectedMetric);
      if (m) {
        const expr = m.sql_expression || 'COUNT(*)';
        const tbl = m.table || m.concept;
        querySf = tbl ? `SELECT ${expr} AS ${m.name} FROM ${tbl}` : `SELECT ${expr} AS ${m.name}`;
        setSfSql(querySf);
      }
    }
    if (!queryDb && querySf) {
      queryDb = querySf;
      setDbSql(queryDb);
    }

    if (!querySf) {
      setParity({
        parity_passed: false,
        error: 'Please select a metric or enter a Snowflake SQL query before running parity.',
      });
      return;
    }

    setChecking(true);
    try {
      const res = await authFetch('/api/cross-platform/parity-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          sql_snowflake: querySf,
          sql_databricks: queryDb || undefined,
          snowflake_account_id: sfAccountId || undefined,
          databricks_account_id: dbAccountId || undefined,
        }),
      });
      if (res.ok) {
        setParity(await res.json());
      } else {
        const err = await res.json().catch(() => ({}));
        setParity({
          parity_passed: false,
          error: err.detail || 'Parity check request failed',
        });
      }
    } catch (exc) {
      setParity({
        parity_passed: false,
        error: String(exc?.message || exc),
      });
    } finally {
      setChecking(false);
    }
  };

  const sfAccounts = accounts.filter((a) => String(a.connector_type || '').toUpperCase() === 'SNOWFLAKE');
  const dbAccounts = accounts.filter((a) => String(a.connector_type || '').toUpperCase() === 'DATABRICKS');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={CARD}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Snowflake Account
            <select style={INPUT} value={sfAccountId} onChange={(e) => setSfAccountId(e.target.value)}>
              <option value="">{sfAccounts.length ? 'Select Snowflake…' : 'No Snowflake connected'}</option>
              {sfAccounts.map((a) => <option key={a.id} value={a.id}>{a.tag || a.identity_email || a.id}</option>)}
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Databricks Account
            <select style={INPUT} value={dbAccountId} onChange={(e) => setDbAccountId(e.target.value)}>
              <option value="">{dbAccounts.length ? 'Select Databricks…' : 'No Databricks connected'}</option>
              {dbAccounts.map((a) => <option key={a.id} value={a.id}>{a.tag || a.identity_email || a.id}</option>)}
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Metric to Verify
            <select
              style={INPUT}
              value={selectedMetric}
              onChange={(e) => handleMetricSelect(e.target.value)}
              aria-label="Metric to verify"
            >
              <option value="">{metrics.length ? 'Select metric to compile…' : (loadingMetrics ? 'Loading metrics…' : 'No metrics in project')}</option>
              {metrics.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.label ? `${m.label} (${m.name})` : m.name}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Version Tag
            <input style={INPUT} value={versionTag} onChange={(e) => setVersionTag(e.target.value)} placeholder="Version tag" />
          </label>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" style={BTN_PRIMARY} disabled={loadingPreview} onClick={loadPreview}>
            <Layers size={13} /> {loadingPreview ? 'Compiling…' : 'Preview Snowflake · Databricks · Fabric'}
          </button>
          <button type="button" style={BTN} disabled={checking} onClick={runParity}>
            <Play size={13} /> {checking ? 'Running Parity…' : 'Run Side-by-Side Parity'}
          </button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Snowflake Query
            <textarea
              style={{ ...INPUT, height: 72, fontFamily: 'monospace', resize: 'vertical' }}
              value={sfSql}
              onChange={(e) => setSfSql(e.target.value)}
              placeholder="Snowflake SQL (e.g. SELECT COUNT(*) AS Case_Count)"
            />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Databricks Query
            <textarea
              style={{ ...INPUT, height: 72, fontFamily: 'monospace', resize: 'vertical' }}
              value={dbSql}
              onChange={(e) => setDbSql(e.target.value)}
              placeholder="Databricks SQL (e.g. SELECT COUNT(*) AS Case_Count)"
            />
          </label>
        </div>
        {parity && (
          <div style={{ fontSize: 12, padding: 12, borderRadius: 8, background: 'var(--bg-app)', border: '1px solid var(--border-main)', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {parity.parity_passed ? <CheckCircle2 size={15} color="var(--color-success)" /> : <AlertTriangle size={15} color="var(--color-warning)" />}
              <strong style={{ color: parity.parity_passed ? 'var(--color-success)' : 'var(--color-warning)' }}>
                {parity.parity_passed ? 'PARITY PASSED — RESULTS MATCH' : 'PARITY MISMATCH / NOT EXECUTED'}
              </strong>
            </div>
            {parity.error && <div style={{ color: 'var(--color-warning)', fontSize: 11.5 }}>{parity.error}</div>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 10 }}>
              <div style={{ padding: 10, borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>Snowflake Result</div>
                <div style={{ fontSize: 11, color: parity.snowflake?.success ? 'var(--color-success)' : 'var(--text-secondary)' }}>
                  {parity.snowflake?.success ? `${parity.snowflake.row_count} row(s) returned` : (parity.snowflake?.error || 'Execution failed')}
                </div>
                {parity.snowflake?.success && parity.snowflake.rows?.length > 0 && (
                  <pre style={{ margin: '6px 0 0', fontSize: 10.5, maxHeight: 110, overflow: 'auto', color: 'var(--text-secondary)' }}>
                    {JSON.stringify(parity.snowflake.rows.slice(0, 5), null, 2)}
                  </pre>
                )}
              </div>
              <div style={{ padding: 10, borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>Databricks Result</div>
                <div style={{ fontSize: 11, color: parity.databricks?.success ? 'var(--color-success)' : 'var(--text-secondary)' }}>
                  {parity.databricks?.success ? `${parity.databricks.row_count} row(s) returned` : (parity.databricks?.error || 'Execution failed')}
                </div>
                {parity.databricks?.success && parity.databricks.rows?.length > 0 && (
                  <pre style={{ margin: '6px 0 0', fontSize: 10.5, maxHeight: 110, overflow: 'auto', color: 'var(--text-secondary)' }}>
                    {JSON.stringify(parity.databricks.rows.slice(0, 5), null, 2)}
                  </pre>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
      {preview && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
          {Object.entries(preview.previews || {}).map(([platform, info]) => (
            <div key={platform} style={CARD}>
              <strong style={{ textTransform: 'uppercase', fontSize: 12 }}>{platform} ({info.metric_count} metric{info.metric_count === 1 ? '' : 's'})</strong>
              {info.gaps?.length > 0 && <div style={{ fontSize: 11, color: 'var(--color-warning)' }}>{info.gaps.join(' · ')}</div>}
              <pre style={{ margin: 0, fontSize: 11, maxHeight: 260, overflow: 'auto', background: 'var(--bg-app)', padding: 10, borderRadius: 8 }}>{info.definition}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const TITLES = {
  drift: ['Drift & Schedule', 'Scheduled parity checks, change detection, and approval workflow'],
  'reverse-sync': ['Reverse Sync', 'Check data-layer semantics and insert upgrade-only additions into BI'],
  'bi-truth': ['BI-Truth Suite', 'Generate and run certified Q&A accuracy tests against AI agents'],
  'cross-platform': ['Cross-Platform', 'Compile and verify one metric definition across Snowflake, Databricks, and Fabric'],
};

export default function GovernanceSuitePage({ mode = 'drift' }) {
  const {
    filteredProjects,
    selectedProjectId: projectId,
    setSelectedProjectId: setProjectId,
    availableWarehouses,
    warehouseCounts,
    selectedWarehouse,
    setSelectedWarehouse,
  } = useProjects();
  const [title, subtitle] = TITLES[mode] || TITLES.drift;
  return (
    <div style={{ height: '100%', overflow: 'auto', padding: '24px 28px', background: 'var(--bg-app)' }}>
      <div style={{ maxWidth: 1200, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text-primary)' }}>{title}</h1>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>{subtitle}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <WarehouseFilter
              warehouses={availableWarehouses}
              selectedWarehouse={selectedWarehouse}
              onSelectWarehouse={setSelectedWarehouse}
              counts={warehouseCounts}
              compact
            />
            <select style={INPUT} value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Project scope">
              <option value="">All projects ({filteredProjects.length})</option>
              {filteredProjects.map((p) => {
                const pid = p.id || p.project_id || p.name;
                return (
                  <option key={pid} value={pid}>
                    {p.name || pid} {p.platform ? `[${p.platform}]` : ''}
                  </option>
                );
              })}
            </select>
          </div>
        </div>
        {selectedWarehouse && filteredProjects.length === 0 ? (
          <EmptyState
            icon={Database}
            title={`No projects for ${selectedWarehouse}`}
            description="There are no projects associated with this warehouse platform."
            actionLabel="Clear warehouse filter"
            onAction={() => setSelectedWarehouse('')}
          />
        ) : (
          <>
            {mode === 'drift' && <DriftView projectId={projectId} />}
            {mode === 'reverse-sync' && <ReverseSyncView projectId={projectId} />}
            {mode === 'bi-truth' && <BiTruthView projectId={projectId} />}
            {mode === 'cross-platform' && <CrossPlatformView projectId={projectId} />}
          </>
        )}
      </div>
    </div>
  );
}

