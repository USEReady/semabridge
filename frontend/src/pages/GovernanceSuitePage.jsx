import React, { useCallback, useEffect, useState } from 'react';
import { authFetch } from '../utils/api';
import {
  Play, Check, X, RefreshCw, Clock, Layers, ShieldCheck, ArrowLeftRight,
  Trash2, CheckCircle2, AlertTriangle, Code, Sparkles, HelpCircle,
  Plus, Upload, FileText, Download,
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

function useProjects() {
  const [projects, setProjects] = useState([]);
  const [projectId, setProjectId] = useState('');
  useEffect(() => {
    authFetch('/api/projects?limit=200&offset=0')
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => setProjects(Array.isArray(list) ? list : (list?.projects || [])))
      .catch(() => setProjects([]));
  }, []);
  return { projects, projectId, setProjectId };
}

export function DriftView({ projectId = '' }) {
  const [events, setEvents] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [intervalMins, setIntervalMins] = useState(60);
  const [webhookUrl, setWebhookUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');

  const refresh = useCallback(async () => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const [eRes, sRes] = await Promise.all([
      authFetch(`/api/drift/events${qs}`),
      authFetch(`/api/drift/schedules${qs}`),
    ]);
    if (eRes.ok) setEvents((await eRes.json()).events || []);
    if (sRes.ok) setSchedules((await sRes.json()).schedules || []);
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  const runNow = async () => {
    setBusy(true);
    setMsg('');
    try {
      const res = await authFetch('/api/drift/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId || undefined, webhook_url: webhookUrl || undefined }),
      });
      const data = await res.json();
      setMsg(`Checked ${data.checked_metrics} metric(s) · ${data.drift_detected} drift event(s) detected.`);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const addSchedule = async () => {
    await authFetch('/api/drift/schedules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId || undefined,
        interval_minutes: Number(intervalMins) || 60,
        webhook_url: webhookUrl || undefined,
      }),
    });
    await refresh();
  };

  const resolve = async (id, action) => {
    const reason = action === 'ignore' ? (window.prompt('Reason for ignoring this drift:') || 'Accepted difference') : 'Approved for sync';
    await authFetch(`/api/drift/events/${id}/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    await refresh();
  };

  const handleClearEvents = async () => {
    if (!window.confirm('Clear drift events history?')) return;
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    await authFetch(`/api/drift/events${qs}`, { method: 'DELETE' });
    await refresh();
  };

  const pendingCount = events.filter((e) => e.status === 'pending').length;
  const approvedCount = events.filter((e) => e.status === 'approved').length;
  const ignoredCount = events.filter((e) => e.status === 'ignored').length;

  const filteredEvents = events.filter((ev) => {
    if (filterStatus === 'all') return true;
    return ev.status === filterStatus;
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── SCHEDULE CONFIGURATION & IMMEDIATE CHECK ─────────────────── */}
      <div style={CARD}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <input style={{ ...INPUT, width: 90 }} type="number" min={5} value={intervalMins} onChange={(e) => setIntervalMins(e.target.value)} title="Interval (minutes)" />
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>min</span>
          <input style={{ ...INPUT, flex: 1, minWidth: 220 }} placeholder="Optional Teams / Slack webhook URL" value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} />
          <button type="button" style={BTN} onClick={addSchedule}><Clock size={13} /> Add Schedule</button>
          <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={runNow}>
            <Play size={13} /> {busy ? 'Checking…' : 'Check Drift Now'}
          </button>
        </div>
        {msg && <div style={{ fontSize: 12, color: 'var(--accent-blue)', marginTop: 8 }}>{msg}</div>}
        {schedules.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10 }}>
            {schedules.map((s) => (
              <span key={s.id} style={{ fontSize: 11.5, padding: '4px 10px', borderRadius: 999, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                Every {s.interval_minutes}m ({s.project_id || 'All'})
                <button type="button" onClick={async () => { await authFetch(`/api/drift/schedules/${s.id}`, { method: 'DELETE' }); refresh(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>×</button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* ── DRIFT APPROVALS & CHANGE REVIEW ───────────────────────────── */}
      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <strong style={{ fontSize: 13 }}>
            Drift Approvals &amp; Change Detection ({events.length})
          </strong>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {[
              { id: 'all', label: `All (${events.length})` },
              { id: 'pending', label: `Pending (${pendingCount})` },
              { id: 'approved', label: `Approved (${approvedCount})` },
              { id: 'ignored', label: `Ignored (${ignoredCount})` },
            ].map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setFilterStatus(f.id)}
                style={{
                  ...BTN,
                  padding: '3px 8px',
                  fontSize: 11,
                  background: filterStatus === f.id ? 'var(--bg-surface-hover)' : 'transparent',
                  borderColor: filterStatus === f.id ? 'var(--accent-blue)' : 'var(--border-main)',
                  color: filterStatus === f.id ? 'var(--accent-blue)' : 'var(--text-secondary)',
                }}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {filteredEvents.length === 0 && (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12.5 }}>
            No drift events in this view. Use the demo bar above to simulate a metric change or run a scheduled drift check.
          </div>
        )}

        {filteredEvents.map((ev) => {
          const isPending = ev.status === 'pending';
          const isApproved = ev.status === 'approved';
          const isIgnored = ev.status === 'ignored';

          return (
            <div
              key={ev.id}
              style={{
                padding: 14,
                borderRadius: 8,
                background: 'var(--bg-app)',
                border: `1px solid ${
                  isPending
                    ? 'rgba(234, 179, 8, 0.4)'
                    : isApproved
                    ? 'rgba(34, 197, 94, 0.3)'
                    : 'var(--border-main)'
                }`,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13.5, fontWeight: 700, color: 'var(--text-primary)' }}>
                  {ev.metric_name}
                </span>
                <span style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  padding: '2px 6px',
                  borderRadius: 4,
                  textTransform: 'uppercase',
                  background: 'rgba(234, 179, 8, 0.15)',
                  color: 'var(--color-warning)',
                }}>
                  {ev.drift_type}
                </span>
                <span style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  padding: '2px 6px',
                  borderRadius: 4,
                  textTransform: 'uppercase',
                  background: isApproved ? 'rgba(34,197,94,0.15)' : isIgnored ? 'rgba(239,68,68,0.15)' : 'rgba(59,130,246,0.15)',
                  color: isApproved ? 'var(--color-success)' : isIgnored ? 'var(--color-error)' : 'var(--accent-blue)',
                }}>
                  {ev.status}
                </span>
                <span style={{ flex: 1 }} />
                {isPending && (
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      type="button"
                      style={{ ...BTN_PRIMARY, background: 'var(--color-success, #22c55e)', color: '#fff', padding: '4px 10px', fontSize: 11.5 }}
                      onClick={() => resolve(ev.id, 'approve')}
                      title="Approve changes and automatically sync to Snowflake warehouse"
                    >
                      <Check size={12} /> Approve &amp; Sync
                    </button>
                    <button
                      type="button"
                      style={{ ...BTN, padding: '4px 10px', fontSize: 11.5 }}
                      onClick={() => resolve(ev.id, 'ignore')}
                      title="Ignore drift and keep current warehouse definition"
                    >
                      <X size={12} /> Ignore
                    </button>
                  </div>
                )}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 11.5 }}>
                <div style={{ padding: '8px 10px', borderRadius: 6, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                  <div style={{ color: 'var(--accent-blue)', fontWeight: 600, marginBottom: 2 }}>BI Semantic Model (Modified):</div>
                  <code style={{ color: 'var(--text-primary)', wordBreak: 'break-all' }}>{ev.bi_definition || '—'}</code>
                </div>
                <div style={{ padding: '8px 10px', borderRadius: 6, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                  <div style={{ color: 'var(--text-secondary)', fontWeight: 600, marginBottom: 2 }}>Snowflake Warehouse (Current):</div>
                  <code style={{ color: 'var(--text-secondary)', wordBreak: 'break-all' }}>{ev.snowflake_definition || '—'}</code>
                </div>
              </div>

              {ev.affected_objects?.length > 0 && (
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  <strong>Impacted Objects:</strong> {ev.affected_objects.join(', ')}
                </div>
              )}
            </div>
          );
        })}
      </div>
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
      .catch(() => {});
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
        setViewsStatus(data?.error || 'No semantic views found in this Snowflake account');
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
      .catch(() => {});
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
      .catch(() => {});
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

function BiTruthView({ projectId }) {
  const [cases, setCases] = useState([]);
  const [runs, setRuns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [infoMsg, setInfoMsg] = useState('');
  const [limit, setLimit] = useState(15);
  const [filter, setFilter] = useState('all'); // 'all' | 'draft' | 'approved' | 'rejected'

  // Forms for adding manual and importing documents
  const [showAddForm, setShowAddForm] = useState(false);
  const [showUploadForm, setShowUploadForm] = useState(false);

  // Manual Question fields
  const [manualQuestion, setManualQuestion] = useState('');
  const [manualMetric, setManualMetric] = useState('');
  const [manualExpectedSql, setManualExpectedSql] = useState('');
  const [manualExpectedDax, setManualExpectedDax] = useState('');

  // Upload fields
  const [uploadText, setUploadText] = useState('');
  const [uploadFileName, setUploadFileName] = useState('');

  const refresh = useCallback(async () => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const [cRes, rRes] = await Promise.all([authFetch(`/api/bi-truth/cases${qs}`), authFetch(`/api/bi-truth/runs${qs}`)]);
    if (cRes.ok) setCases((await cRes.json()).cases || []);
    if (rRes.ok) setRuns((await rRes.json()).runs || []);
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  const handleSaveManual = async (e) => {
    e?.preventDefault();
    if (!manualQuestion.trim()) {
      setError('Question text is required.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const res = await authFetch('/api/bi-truth/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          question: manualQuestion.trim(),
          metric_name: manualMetric.trim() || undefined,
          expected_sql: manualExpectedSql.trim() || undefined,
          expected_dax: manualExpectedDax.trim() || undefined,
          status: 'approved',
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setInfoMsg('Question added and approved successfully!');
        setManualQuestion('');
        setManualMetric('');
        setManualExpectedSql('');
        setManualExpectedDax('');
        setShowAddForm(false);
        setTimeout(() => setInfoMsg(''), 4000);
      } else {
        setError(data?.detail || 'Failed to save question.');
      }
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleImportText = async () => {
    if (!uploadText.trim()) {
      setError('Please provide document content or select a file to import.');
      return;
    }
    setError('');
    setBusy(true);
    try {
      const res = await authFetch('/api/bi-truth/cases/import-text', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          content: uploadText.trim(),
          filename: uploadFileName || 'document.csv',
          status: 'approved',
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setInfoMsg(`Successfully imported and approved ${data.added_count || 0} questions from document!`);
        setUploadText('');
        setUploadFileName('');
        setShowUploadForm(false);
        setTimeout(() => setInfoMsg(''), 4000);
      } else {
        setError(data?.detail || 'Failed to import document.');
      }
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = (file) => {
    if (!file) return;
    setUploadFileName(file.name);
    const reader = new FileReader();
    reader.onload = (e) => {
      setUploadText(String(e.target?.result || ''));
    };
    reader.readAsText(file);
  };

  const generate = async () => {
    setError('');
    setInfoMsg('');
    setBusy(true);
    try {
      const res = await authFetch('/api/bi-truth/generate', {
        method: 'POST',
        timeoutMs: 120000,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          limit: Number(limit) || 15,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data?.detail || 'Failed to generate test cases.');
      } else {
        if (data.added_count > 0) {
          setInfoMsg(`Generated ${data.added_count} new test questions from project model.`);
        } else {
          setInfoMsg('All model questions are already loaded below.');
        }
        setTimeout(() => setInfoMsg(''), 4000);
      }
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const clearCases = async () => {
    if (!window.confirm('Clear all test questions and accuracy results for this project scope?')) return;
    setError('');
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    await authFetch(`/api/bi-truth/cases${qs}`, { method: 'DELETE' });
    setRuns([]);
    await refresh();
  };

  const approveAllDrafts = async () => {
    setError('');
    setBusy(true);
    try {
      const res = await authFetch('/api/bi-truth/cases/batch-status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          status: 'approved',
          project_id: projectId || undefined,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setInfoMsg(`Approved all ${data.updated_count || 'draft'} questions successfully!`);
        setTimeout(() => setInfoMsg(''), 4000);
      } else {
        setError(data?.detail || 'Failed to approve drafts.');
      }
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const runSuite = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await authFetch('/api/bi-truth/run', {
        method: 'POST',
        timeoutMs: 180000,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId || undefined }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setError(data?.detail || `Run failed (${res.status})`);
      }
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const setStatus = async (id, status) => {
    await authFetch(`/api/bi-truth/cases/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    await refresh();
  };

  const latest = runs[0];
  const draftCount = cases.filter((c) => c.status === 'draft').length;
  const approvedCount = cases.filter((c) => c.status === 'approved').length;

  const filteredCases = cases.filter((c) => {
    if (filter === 'all') return true;
    return c.status === filter;
  });

  const resultMap = new Map((latest?.results || []).map((r) => [r.case_id, r]));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ── 1. ACTION TOOLBAR ────────────────────────────────────────────── */}
      <div style={CARD}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <button
                type="button"
                style={{ ...BTN_PRIMARY, background: 'var(--accent-blue)', color: '#fff' }}
                disabled={busy}
                onClick={generate}
              >
                <Sparkles size={13} /> {busy ? 'Generating…' : 'Generate Questions'}
              </button>
              <select
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                style={{ ...INPUT, padding: '5px 8px', fontSize: 12, borderRadius: 8, height: 32 }}
                title="Number of questions to generate"
              >
                <option value={10}>10 questions (Fast)</option>
                <option value={15}>15 questions (Recommended)</option>
                <option value={20}>20 questions</option>
                <option value={50}>50 questions</option>
                <option value={100}>All questions</option>
              </select>
            </div>
            {draftCount > 0 && (
              <button type="button" style={BTN} onClick={approveAllDrafts}>
                <Check size={13} /> Approve All Drafts ({draftCount})
              </button>
            )}
            <button
              type="button"
              style={{
                ...BTN,
                background: showAddForm ? 'var(--bg-surface-hover)' : 'var(--bg-surface-raised)',
                borderColor: showAddForm ? 'var(--accent-blue)' : 'var(--border-main)',
                color: showAddForm ? 'var(--accent-blue)' : 'var(--text-primary)',
              }}
              onClick={() => {
                setShowAddForm(!showAddForm);
                setShowUploadForm(false);
              }}
              title="Add a custom question and expected SQL/DAX manually"
            >
              <Plus size={13} /> Add Question
            </button>
            <button
              type="button"
              style={{
                ...BTN,
                background: showUploadForm ? 'var(--bg-surface-hover)' : 'var(--bg-surface-raised)',
                borderColor: showUploadForm ? 'var(--accent-blue)' : 'var(--border-main)',
                color: showUploadForm ? 'var(--accent-blue)' : 'var(--text-primary)',
              }}
              onClick={() => {
                setShowUploadForm(!showUploadForm);
                setShowAddForm(false);
              }}
              title="Import questions from CSV, TSV, JSON, or text documents"
            >
              <Upload size={13} /> Import Document
            </button>
            <button
              type="button"
              style={{
                ...BTN_PRIMARY,
                background: approvedCount > 0 ? 'var(--color-success, #22c55e)' : 'var(--bg-surface-raised)',
                borderColor: approvedCount > 0 ? 'var(--color-success, #22c55e)' : 'var(--border-main)',
                color: approvedCount > 0 ? '#fff' : 'var(--text-tertiary)',
                cursor: approvedCount > 0 ? 'pointer' : 'not-allowed',
              }}
              disabled={busy || approvedCount === 0}
              onClick={runSuite}
            >
              <ShieldCheck size={14} /> {busy ? 'Testing Engine…' : `Run Approved Suite (${approvedCount})`}
            </button>
            {cases.length > 0 && (
              <button
                type="button"
                style={{ ...BTN, color: 'var(--color-error)' }}
                onClick={clearCases}
                title="Wipe test cases for this project"
              >
                <Trash2 size={13} /> Clear Questions
              </button>
            )}
          </div>

          {/* Filter Pills */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5 }}>
            <span style={{ color: 'var(--text-secondary)' }}>Show:</span>
            {[
              { id: 'all', label: `All (${cases.length})` },
              { id: 'draft', label: `Draft (${draftCount})` },
              { id: 'approved', label: `Approved (${approvedCount})` },
              { id: 'rejected', label: `Rejected (${cases.filter((c) => c.status === 'rejected').length})` },
            ].map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => setFilter(f.id)}
                style={{
                  ...BTN,
                  padding: '3px 8px',
                  fontSize: 11,
                  background: filter === f.id ? 'var(--bg-surface-hover)' : 'transparent',
                  borderColor: filter === f.id ? 'var(--accent-blue)' : 'var(--border-main)',
                  color: filter === f.id ? 'var(--accent-blue)' : 'var(--text-secondary)',
                }}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {error && (
          <div style={{ padding: '8px 12px', borderRadius: 6, background: 'rgba(239,68,68,0.1)', color: 'var(--color-error)', fontSize: 12 }}>
            {error}
          </div>
        )}

        {infoMsg && (
          <div style={{ padding: '8px 12px', borderRadius: 6, background: 'rgba(34,197,94,0.1)', color: 'var(--color-success)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
            <CheckCircle2 size={14} /> {infoMsg}
          </div>
        )}

        {/* Latest Run Accuracy Banner - only show if test cases exist */}
        {latest && cases.length > 0 && (
          <div style={{
            padding: '12px 16px',
            borderRadius: 8,
            background: latest.accuracy_pct >= 90 ? 'rgba(34, 197, 94, 0.08)' : 'rgba(234, 179, 8, 0.08)',
            border: `1px solid ${latest.accuracy_pct >= 90 ? 'rgba(34, 197, 94, 0.3)' : 'rgba(234, 179, 8, 0.3)'}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {latest.accuracy_pct >= 90 ? (
                <CheckCircle2 size={22} style={{ color: 'var(--color-success, #22c55e)' }} />
              ) : (
                <AlertTriangle size={22} style={{ color: 'var(--color-warning, #eab308)' }} />
              )}
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 700 }}>
                  Truth Suite Accuracy: <span style={{ color: latest.accuracy_pct >= 90 ? 'var(--color-success)' : 'var(--color-warning)' }}>{latest.accuracy_pct}%</span>
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
                  {latest.passed} out of {latest.total} approved questions verified accurately against Snowflake &amp; Cortex semantic layer.
                </div>
              </div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              Last executed: {new Date(latest.ran_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </div>
          </div>
        )}
      </div>

      {/* ── MANUAL QUESTION ADD FORM ────────────────────────────────────── */}
      {showAddForm && (
        <form onSubmit={handleSaveManual} style={{ ...CARD, borderColor: 'var(--accent-blue)', background: 'var(--bg-surface-raised)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Plus size={16} style={{ color: 'var(--accent-blue)' }} />
              <strong style={{ fontSize: 13.5 }}>Add Custom Ground-Truth Question</strong>
            </div>
            <button
              type="button"
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              onClick={() => setShowAddForm(false)}
            >
              <X size={16} />
            </button>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            Manually add an executive question and certified SQL / DAX answer to benchmark against the AI agent.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 12 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11.5, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Question *
              </label>
              <input
                type="text"
                placeholder="e.g. What were total sales in the West region last year?"
                value={manualQuestion}
                onChange={(e) => setManualQuestion(e.target.value)}
                style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
                autoFocus
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 11.5, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Metric Tag / Name (Optional)
              </label>
              <input
                type="text"
                placeholder="e.g. Total Sales"
                value={manualMetric}
                onChange={(e) => setManualMetric(e.target.value)}
                style={{ ...INPUT, width: '100%', boxSizing: 'border-box' }}
              />
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11.5, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Expected SQL (Warehouse Query)
              </label>
              <textarea
                placeholder="e.g. SELECT SUM(sales) FROM sales_fact WHERE region = 'West';"
                value={manualExpectedSql}
                onChange={(e) => setManualExpectedSql(e.target.value)}
                rows={3}
                style={{ ...INPUT, width: '100%', boxSizing: 'border-box', fontFamily: 'monospace', fontSize: 11.5, resize: 'vertical' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 11.5, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Expected DAX (Power BI Measure - Optional)
              </label>
              <textarea
                placeholder="e.g. CALCULATE([Total Sales], Customer[Region] = &quot;West&quot;)"
                value={manualExpectedDax}
                onChange={(e) => setManualExpectedDax(e.target.value)}
                rows={3}
                style={{ ...INPUT, width: '100%', boxSizing: 'border-box', fontFamily: 'monospace', fontSize: 11.5, resize: 'vertical' }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            <button
              type="button"
              style={BTN}
              onClick={() => {
                setShowAddForm(false);
                setManualQuestion('');
                setManualMetric('');
                setManualExpectedSql('');
                setManualExpectedDax('');
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy || !manualQuestion.trim()}
              style={{ ...BTN_PRIMARY, background: 'var(--accent-blue)', color: '#fff' }}
            >
              <Check size={14} /> Save &amp; Approve Question
            </button>
          </div>
        </form>
      )}

      {/* ── DOCUMENT IMPORT FORM ────────────────────────────────────────── */}
      {showUploadForm && (
        <div style={{ ...CARD, borderColor: 'var(--accent-blue)', background: 'var(--bg-surface-raised)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Upload size={16} style={{ color: 'var(--accent-blue)' }} />
              <strong style={{ fontSize: 13.5 }}>Import Benchmark Questions from Document</strong>
            </div>
            <button
              type="button"
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}
              onClick={() => setShowUploadForm(false)}
            >
              <X size={16} />
            </button>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            Upload a spreadsheet (CSV, TSV), JSON evaluation suite, or paste question/answer text. All imported items are approved automatically.
          </p>

          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <label
              style={{
                ...BTN,
                padding: '8px 14px',
                cursor: 'pointer',
                borderColor: uploadFileName ? 'var(--color-success)' : 'var(--border-main)',
                background: uploadFileName ? 'rgba(34, 197, 94, 0.08)' : 'var(--bg-surface)',
              }}
            >
              <FileText size={14} style={{ color: uploadFileName ? 'var(--color-success)' : 'var(--accent-blue)' }} />
              {uploadFileName ? `Loaded: ${uploadFileName}` : 'Choose File (.csv, .tsv, .json, .txt, .md)'}
              <input
                type="file"
                accept=".csv,.tsv,.json,.txt,.md,text/csv,application/json,text/plain"
                onChange={(e) => handleFileUpload(e.target.files?.[0])}
                style={{ display: 'none' }}
              />
            </label>

            <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>Or paste content directly below:</span>

            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <button
                type="button"
                style={{ ...BTN, padding: '3px 8px', fontSize: 11 }}
                onClick={() => {
                  setUploadFileName('sample_questions.csv');
                  setUploadText(
`question,expected_sql,metric_name
"What is total sales amount?","SELECT SUM(sales) FROM sales_fact;","Total Sales"
"What is the total order count?","SELECT COUNT(order_id) FROM orders_fact;","Order Count"
"Show sales by region","SELECT region, SUM(sales) FROM sales_fact GROUP BY region;","Regional Sales"`
                  );
                }}
              >
                Insert CSV Template
              </button>
              <button
                type="button"
                style={{ ...BTN, padding: '3px 8px', fontSize: 11 }}
                onClick={() => {
                  setUploadFileName('sample_suite.json');
                  setUploadText(JSON.stringify([
                    {
                      "question": "What is the total gross profit?",
                      "expected_sql": "SELECT SUM(gross_profit) FROM finance_mart;",
                      "metric_name": "Gross Profit"
                    },
                    {
                      "question": "What is total active customer count?",
                      "expected_sql": "SELECT COUNT(DISTINCT customer_id) FROM customers WHERE active = true;",
                      "metric_name": "Active Customers"
                    }
                  ], null, 2));
                }}
              >
                Insert JSON Template
              </button>
              <button
                type="button"
                style={{ ...BTN, padding: '3px 8px', fontSize: 11 }}
                onClick={() => {
                  setUploadFileName('sample_qa.txt');
                  setUploadText(
`Q: What was total units sold this quarter?
A: SELECT SUM(units) FROM sales_fact WHERE quarter = 1;

Q: What is total net margin percentage?
A: SELECT (SUM(margin) / NULLIF(SUM(revenue), 0)) * 100 FROM sales_fact;`
                  );
                }}
              >
                Insert Q&amp;A Template
              </button>
            </div>
          </div>

          <textarea
            value={uploadText}
            onChange={(e) => setUploadText(e.target.value)}
            rows={7}
            placeholder="Paste CSV rows, JSON array of questions, or Q: ... A: ... blocks here..."
            style={{
              ...INPUT,
              width: '100%',
              boxSizing: 'border-box',
              fontFamily: 'monospace',
              fontSize: 12,
              lineHeight: 1.5,
              resize: 'vertical',
            }}
          />

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button
              type="button"
              style={BTN}
              onClick={() => {
                setShowUploadForm(false);
                setUploadText('');
                setUploadFileName('');
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={busy || !uploadText.trim()}
              onClick={handleImportText}
              style={{ ...BTN_PRIMARY, background: 'var(--accent-blue)', color: '#fff' }}
            >
              <Upload size={14} /> Import &amp; Approve Questions
            </button>
          </div>
        </div>
      )}

      {/* ── 2. QUESTIONS REVIEW & APPROVAL LIST ─────────────────────────── */}
      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <strong style={{ fontSize: 13 }}>
            Business Questions &amp; Expected Answers ({filteredCases.length})
          </strong>
          <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
            Approve the questions you want to benchmark against your semantic layer.
          </span>
        </div>

        {filteredCases.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-secondary)', fontSize: 12.5 }}>
            No test questions in this view. Click <strong>Generate from Project Model &amp; Metrics</strong> above to automatically create questions tailored to your project.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {filteredCases.map((c) => {
              const runRes = resultMap.get(c.id);
              const isApproved = c.status === 'approved';
              const isRejected = c.status === 'rejected';

              return (
                <div
                  key={c.id}
                  style={{
                    padding: 12,
                    borderRadius: 8,
                    background: 'var(--bg-app)',
                    border: `1px solid ${
                      runRes
                        ? runRes.passed ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'
                        : isApproved ? 'rgba(34,197,94,0.2)' : 'var(--border-main)'
                    }`,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 8,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                        <span style={{
                          fontSize: 10.5,
                          fontWeight: 700,
                          padding: '2px 6px',
                          borderRadius: 4,
                          textTransform: 'uppercase',
                          background: isApproved ? 'rgba(34,197,94,0.15)' : isRejected ? 'rgba(239,68,68,0.15)' : 'rgba(59,130,246,0.15)',
                          color: isApproved ? 'var(--color-success)' : isRejected ? 'var(--color-error)' : 'var(--accent-blue)',
                        }}>
                          {c.status}
                        </span>
                        {c.metric_name && (
                          <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--accent-blue)' }}>
                            [{c.metric_name}]
                          </span>
                        )}
                        {runRes && (
                          <span style={{
                            fontSize: 10.5,
                            fontWeight: 700,
                            padding: '1px 6px',
                            borderRadius: 4,
                            background: runRes.passed ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)',
                            color: runRes.passed ? 'var(--color-success)' : 'var(--color-error)',
                          }}>
                            {runRes.passed ? '✓ PASSED' : '✕ FAILED'}
                          </span>
                        )}
                      </div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {c.question}
                      </div>
                    </div>

                    {/* Approve / Reject Controls */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                      {c.status !== 'approved' && (
                        <button
                          type="button"
                          style={{ ...BTN_PRIMARY, padding: '4px 10px', fontSize: 11 }}
                          onClick={() => setStatus(c.id, 'approved')}
                          title="Approve this question for accuracy testing"
                        >
                          <Check size={12} /> Approve
                        </button>
                      )}
                      {c.status !== 'rejected' && (
                        <button
                          type="button"
                          style={{ ...BTN, padding: '4px 10px', fontSize: 11 }}
                          onClick={() => setStatus(c.id, 'rejected')}
                          title="Reject / Ignore this question"
                        >
                          <X size={12} /> Reject
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Expected SQL / Formula preview */}
                  {(c.expected_sql || c.dax_expression) && (
                    <div style={{
                      padding: '6px 10px',
                      borderRadius: 6,
                      background: 'var(--bg-surface)',
                      border: '1px solid var(--border-main)',
                      fontSize: 11,
                      fontFamily: 'monospace',
                      color: 'var(--text-secondary)',
                      overflowX: 'auto',
                    }}>
                      <span style={{ color: 'var(--text-tertiary)', marginRight: 6 }}>Expected SQL:</span>
                      <span style={{ color: 'var(--color-success)' }}>{c.expected_sql || c.dax_expression}</span>
                    </div>
                  )}

                  {/* Execution Results comparison */}
                  {runRes && (
                    <div style={{
                      padding: '6px 10px',
                      borderRadius: 6,
                      background: runRes.passed ? 'rgba(34,197,94,0.04)' : 'rgba(239,68,68,0.04)',
                      border: `1px solid ${runRes.passed ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}`,
                      fontSize: 11,
                      fontFamily: 'monospace',
                    }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                        <span style={{ fontWeight: 700, color: runRes.passed ? 'var(--color-success)' : 'var(--color-error)' }}>
                          {runRes.passed ? '✓ AI Engine Generated Matching SQL:' : '✕ AI Engine Output Discrepancy:'}
                        </span>
                        <span style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>({runRes.mode})</span>
                      </div>
                      <div style={{ color: 'var(--text-primary)', whiteSpace: 'pre-wrap', maxHeight: 80, overflowY: 'auto' }}>
                        {runRes.generated_sql || runRes.error || 'No SQL generated'}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function CrossPlatformView({ projectId }) {
  const [versionTag, setVersionTag] = useState('v1.0.0');
  const [preview, setPreview] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [sfAccountId, setSfAccountId] = useState('');
  const [dbAccountId, setDbAccountId] = useState('');
  const [sfSql, setSfSql] = useState('SELECT 1 AS OK');
  const [dbSql, setDbSql] = useState('SELECT 1 AS OK');
  const [parity, setParity] = useState(null);
  const [checking, setChecking] = useState(false);

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
      .catch(() => {});
  }, []);

  const loadPreview = async () => {
    const res = await authFetch('/api/cross-platform/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId || undefined, version_tag: versionTag }),
    });
    if (res.ok) setPreview(await res.json());
  };

  const runParity = async () => {
    setChecking(true);
    try {
      const res = await authFetch('/api/cross-platform/parity-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId || undefined,
          sql_snowflake: sfSql,
          sql_databricks: dbSql,
          snowflake_account_id: sfAccountId || undefined,
          databricks_account_id: dbAccountId || undefined,
        }),
      });
      if (res.ok) setParity(await res.json());
    } finally {
      setChecking(false);
    }
  };

  const sfAccounts = accounts.filter((a) => String(a.connector_type || '').toUpperCase() === 'SNOWFLAKE');
  const dbAccounts = accounts.filter((a) => String(a.connector_type || '').toUpperCase() === 'DATABRICKS');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={CARD}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
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
            Version Tag
            <input style={INPUT} value={versionTag} onChange={(e) => setVersionTag(e.target.value)} placeholder="Version tag" />
          </label>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button type="button" style={BTN_PRIMARY} onClick={loadPreview}><Layers size={13} /> Preview Snowflake · Databricks · Fabric</button>
          <button type="button" style={BTN} disabled={checking} onClick={runParity}><Play size={13} /> {checking ? 'Running…' : 'Run Side-by-Side Parity'}</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 8 }}>
          <input style={INPUT} value={sfSql} onChange={(e) => setSfSql(e.target.value)} placeholder="Snowflake SQL" />
          <input style={INPUT} value={dbSql} onChange={(e) => setDbSql(e.target.value)} placeholder="Databricks SQL" />
        </div>
        {parity && (
          <div style={{ fontSize: 12, padding: 10, borderRadius: 8, background: 'var(--bg-app)', border: '1px solid var(--border-main)' }}>
            <strong style={{ color: parity.parity_passed ? 'var(--color-success)' : 'var(--color-warning)' }}>
              {parity.parity_passed ? 'PARITY PASSED' : 'PARITY MISMATCH / NOT EXECUTED'}
            </strong>
            <div>Snowflake: {parity.snowflake?.success ? `${parity.snowflake.row_count} row(s)` : (parity.snowflake?.error || 'failed')}</div>
            <div>Databricks: {parity.databricks?.success ? `${parity.databricks.row_count} row(s)` : (parity.databricks?.error || 'failed')}</div>
          </div>
        )}
      </div>
      {preview && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 12 }}>
          {Object.entries(preview.previews || {}).map(([platform, info]) => (
            <div key={platform} style={CARD}>
              <strong style={{ textTransform: 'uppercase', fontSize: 12 }}>{platform} ({info.metric_count} metrics)</strong>
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
  const { projects, projectId, setProjectId } = useProjects();
  const [title, subtitle] = TITLES[mode] || TITLES.drift;
  return (
    <div style={{ height: '100%', overflow: 'auto', padding: '24px 28px', background: 'var(--bg-app)' }}>
      <div style={{ maxWidth: 1200, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, color: 'var(--text-primary)' }}>{title}</h1>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 2 }}>{subtitle}</div>
          </div>
          <select style={INPUT} value={projectId} onChange={(e) => setProjectId(e.target.value)} aria-label="Project scope">
            <option value="">All projects</option>
            {projects.map((p) => (
              <option key={p.id || p.name} value={p.id || p.name}>{p.name || p.id}</option>
            ))}
          </select>
        </div>
        {mode === 'drift' && <DriftView projectId={projectId} />}
        {mode === 'reverse-sync' && <ReverseSyncView projectId={projectId} />}
        {mode === 'bi-truth' && <BiTruthView projectId={projectId} />}
        {mode === 'cross-platform' && <CrossPlatformView projectId={projectId} />}
      </div>
    </div>
  );
}

