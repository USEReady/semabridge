import React, { useCallback, useEffect, useState } from 'react';
import { authFetch } from '../utils/api';
import { Play, Check, X, RefreshCw, Clock, Layers, ShieldCheck, ArrowLeftRight } from 'lucide-react';

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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
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
        {msg && <div style={{ fontSize: 12, color: 'var(--accent-blue)' }}>{msg}</div>}
        {schedules.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {schedules.map((s) => (
              <span key={s.id} style={{ fontSize: 11.5, padding: '4px 10px', borderRadius: 999, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                Every {s.interval_minutes}m ({s.project_id || 'All'})
                <button type="button" onClick={async () => { await authFetch(`/api/drift/schedules/${s.id}`, { method: 'DELETE' }); refresh(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>×</button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div style={CARD}>
        <strong style={{ fontSize: 13 }}>Drift Events ({events.length})</strong>
        {events.length === 0 && <span style={{ fontSize: 12.5, color: 'var(--text-tertiary)' }}>No drift detected yet.</span>}
        {events.map((ev) => (
          <div key={ev.id} style={{ padding: 12, borderRadius: 8, background: 'var(--bg-app)', border: '1px solid var(--border-main)', display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <strong>{ev.metric_name}</strong>
              <span style={{ fontSize: 11, color: 'var(--color-warning)', fontWeight: 700 }}>{ev.drift_type}</span>
              <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>[{ev.status}]</span>
              <span style={{ flex: 1 }} />
              {ev.status === 'pending' && (
                <>
                  <button type="button" style={BTN_PRIMARY} onClick={() => resolve(ev.id, 'approve')}><Check size={12} /> Approve &amp; Sync</button>
                  <button type="button" style={BTN} onClick={() => resolve(ev.id, 'ignore')}><X size={12} /> Ignore</button>
                </>
              )}
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>BI: <code>{ev.bi_definition || '—'}</code> | Snowflake: <code>{ev.snowflake_definition || '—'}</code></div>
            {ev.affected_objects?.length > 0 && <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Affected: {ev.affected_objects.join(', ')}</div>}
          </div>
        ))}
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

  const refresh = useCallback(async () => {
    const qs = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    const [cRes, rRes] = await Promise.all([authFetch(`/api/bi-truth/cases${qs}`), authFetch(`/api/bi-truth/runs${qs}`)]);
    if (cRes.ok) setCases((await cRes.json()).cases || []);
    if (rRes.ok) setRuns((await rRes.json()).runs || []);
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  const generate = async () => {
    setError('');
    await authFetch('/api/bi-truth/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId || undefined,
      }),
    });
    await refresh();
  };

  const approveAllDrafts = async () => {
    setError('');
    const drafts = cases.filter((c) => c.status !== 'approved');
    await Promise.all(
      drafts.map((c) =>
        authFetch(`/api/bi-truth/cases/${c.id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status: 'approved' }),
        })
      )
    );
    await refresh();
  };

  const runSuite = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await authFetch('/api/bi-truth/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: projectId || undefined }),
        timeoutMs: 300000,
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
    await authFetch(`/api/bi-truth/cases/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    await refresh();
  };

  const latest = runs[0];
  const draftCount = cases.filter((c) => c.status !== 'approved').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={CARD}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button type="button" style={BTN} onClick={generate}><RefreshCw size={13} /> Generate from Certified Metrics</button>
          {draftCount > 0 && (
            <button type="button" style={BTN} onClick={approveAllDrafts}><Check size={13} /> Approve All ({draftCount})</button>
          )}
          <button type="button" style={BTN_PRIMARY} disabled={busy} onClick={runSuite}><ShieldCheck size={13} /> {busy ? 'Running…' : 'Run Approved Suite'}</button>
          {latest && <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--accent-blue)' }}>Latest Accuracy: {latest.accuracy_pct}% ({latest.passed}/{latest.total})</span>}
        </div>
        {error && <div style={{ fontSize: 12, color: 'var(--color-error)' }}>{error}</div>}
      </div>
      <div style={CARD}>
        <strong style={{ fontSize: 13 }}>Test Questions ({cases.length})</strong>
        {cases.map((c) => (
          <div key={c.id} style={{ padding: 10, borderRadius: 8, background: 'var(--bg-app)', border: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600 }}>{c.question}</div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{c.metric_name} · status: <b>{c.status}</b></div>
            </div>
            {c.status !== 'approved' && <button type="button" style={BTN_PRIMARY} onClick={() => setStatus(c.id, 'approved')}>Approve</button>}
            {c.status !== 'rejected' && <button type="button" style={BTN} onClick={() => setStatus(c.id, 'rejected')}>Reject</button>}
          </div>
        ))}
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

