import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity, Play, Check, X, Clock, AlertTriangle, CheckCircle2,
  Trash2, RefreshCw, Sparkles, Database, Layers, ArrowRight,
  ShieldCheck, ShieldAlert, Search, ChevronRight, FileText, Zap,
} from 'lucide-react';
import { authFetch } from '../utils/api';
import Modal from '../components/common/Modal';
import { ImpactReport } from '../components/ontology/GovernancePanels';
const CARD = {
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  borderRadius: 10,
  padding: 16,
};

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

const INPUT = {
  padding: '6px 10px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

function formatOccurred(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    const dateStr = d.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    });
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    let ago = '';
    if (diffSec < 60) ago = 'just now';
    else if (diffSec < 3600) ago = `${Math.floor(diffSec / 60)}m ago`;
    else if (diffSec < 86400) ago = `${Math.floor(diffSec / 3600)}h ago`;
    else ago = `${Math.floor(diffSec / 86400)}d ago`;

    return `${dateStr}, ${timeStr} (${ago})`;
  } catch {
    return isoStr;
  }
}

export default function DriftMonitoringPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [projectSearch, setProjectSearch] = useState('');
  const [allEvents, setAllEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState('all');
  const [checkMsg, setCheckMsg] = useState('');

  // Blast Radius Modal state & simulation in mock mode
  const [blastModalOpen, setBlastModalOpen] = useState(false);
  const [blastTarget, setBlastTarget] = useState('');
  const [blastType, setBlastType] = useState('METRIC');
  const [blastChangeType, setBlastChangeType] = useState('MODIFY_EXPRESSION');
  const [blastLoading, setBlastLoading] = useState(false);
  const [blastReport, setBlastReport] = useState(null);
  const [blastError, setBlastError] = useState('');
  const [projectMetrics, setProjectMetrics] = useState([]);

  useEffect(() => {
    if (!selectedProjectId) {
      setProjectMetrics([]);
      return;
    }
    authFetch(`/api/reconciliation/project-metrics?project_id=${encodeURIComponent(selectedProjectId)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => setProjectMetrics(d?.metrics || []))
      .catch(() => setProjectMetrics([]));
  }, [selectedProjectId, authFetch]);

  const runBlastSimulation = async (targetOverride, changeTypeOverride, typeOverride) => {
    const target = (targetOverride ?? blastTarget).trim();
    if (!target) return;
    const cType = changeTypeOverride ?? blastChangeType;
    const tType = typeOverride ?? blastType;
    setBlastLoading(true);
    setBlastError('');
    try {
      const res = await authFetch('/api/governance/blast-radius', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_name: target,
          target_type: tType,
          change_type: cType,
          project_id: selectedProjectId || undefined,
          mock_mode: true,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setBlastError(data.detail || 'Blast radius simulation failed.');
      } else {
        setBlastReport(data);
      }
    } catch (err) {
      setBlastError(String(err));
    } finally {
      setBlastLoading(false);
    }
  };

  const handleOpenBlast = (initialTarget = '') => {
    const candidate = initialTarget || projectEvents[0]?.metric_name || quickTargets[0] || '';
    setBlastTarget(candidate);
    setBlastType('METRIC');
    setBlastChangeType('MODIFY_EXPRESSION');
    setBlastReport(null);
    setBlastError('');
    setBlastModalOpen(true);
    if (candidate) {
      runBlastSimulation(candidate, 'MODIFY_EXPRESSION', 'METRIC');
    }
  };

  // 1. Load project list
  useEffect(() => {
    authFetch('/api/projects?limit=200&offset=0')
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        const list = Array.isArray(data) ? data : (data?.projects || []);
        setProjects(list);
        const urlPid = searchParams.get('project');
        if (urlPid) {
          setSelectedProjectId(urlPid);
        } else if (list.length > 0) {
          setSelectedProjectId(list[0].id || list[0].name);
        }
      })
      .catch(() => setProjects([]))
      .finally(() => setLoading(false));
  }, []);

  // 2. Load all drift events across projects
  const refreshEvents = useCallback(async () => {
    try {
      const res = await authFetch('/api/drift/events');
      if (res.ok) {
        const data = await res.json();
        setAllEvents(data.events || []);
      }
    } catch (err) {
      console.error('Failed to load drift events:', err);
    }
  }, []);

  useEffect(() => {
    refreshEvents();
    const interval = setInterval(refreshEvents, 10000);
    return () => clearInterval(interval);
  }, [refreshEvents]);

  const selectProject = (pid) => {
    setSelectedProjectId(pid);
    setSearchParams({ project: pid });
    setCheckMsg('');
  };

  // Match project ID flexibly
  const cleanPid = (id) => String(id || '').toLowerCase().removeprefix?.('proj-') || String(id || '').toLowerCase().replace(/^proj-/, '');

  const projectEvents = allEvents.filter((e) => {
    if (!selectedProjectId) return true;
    return cleanPid(e.project_id) === cleanPid(selectedProjectId);
  });

  const driftMetrics = projectEvents.map((e) => e.metric_name).filter(Boolean);
  const projectMetricNames = projectMetrics.map((m) => m.name || m.logical_name).filter(Boolean);
  const quickTargets = Array.from(new Set([...driftMetrics, ...projectMetricNames]));

  const pendingEvents = projectEvents.filter((e) => e.status === 'pending');
  const approvedEvents = projectEvents.filter((e) => e.status === 'approved');
  const ignoredEvents = projectEvents.filter((e) => e.status === 'ignored');

  const filteredEvents = projectEvents.filter((ev) => {
    if (statusFilter === 'all') return true;
    return ev.status === statusFilter;
  });

  // Event approval / ignore handler
  const handleResolve = async (id, action) => {
    const reason = action === 'ignore'
      ? (window.prompt('Reason for ignoring this drift:') || 'Accepted difference')
      : 'Approved for sync';
    try {
      await authFetch(`/api/drift/events/${id}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      });
      await refreshEvents();
    } catch (err) {
      alert(`Action failed: ${err}`);
    }
  };

  // Check drift now
  const handleCheckNow = async () => {
    setBusy(true);
    setCheckMsg('');
    try {
      const res = await authFetch('/api/drift/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: selectedProjectId || undefined }),
      });
      const data = await res.json();
      setCheckMsg(`Checked ${data.checked_metrics} metric(s) · ${data.drift_detected} drift event(s) detected.`);
      await refreshEvents();
    } catch (err) {
      setCheckMsg(`Check failed: ${err}`);
    } finally {
      setBusy(false);
    }
  };

  // Refresh drift logs
  const handleSeedMock = async () => {
    try {
      const pid = selectedProjectId || (projects[0]?.id || projects[0]?.name || '');
      if (!pid) return;
      const res = await authFetch(`/api/drift/events/seed-mock?project_id=${encodeURIComponent(pid)}`, { method: 'POST' });
      if (res.ok) {
        setCheckMsg('Drift logs refreshed for project.');
        await refreshEvents();
      }
    } catch (err) {
      alert(`Failed to refresh drift logs: ${err}`);
    }
  };

  // Clear drift history
  const handleClearHistory = async () => {
    if (!window.confirm(`Clear all drift monitoring history for ${selectedProjectId || 'this project'}?`)) return;
    try {
      const qs = selectedProjectId ? `?project_id=${encodeURIComponent(selectedProjectId)}` : '';
      await authFetch(`/api/drift/events${qs}`, { method: 'DELETE' });
      setCheckMsg('Drift monitoring history cleared.');
      await refreshEvents();
    } catch (err) {
      alert(`Failed to clear: ${err}`);
    }
  };

  // Filter projects by search
  const visibleProjects = projects.filter((p) => {
    if (!projectSearch.trim()) return true;
    const term = projectSearch.toLowerCase();
    return (p.name || '').toLowerCase().includes(term) || (p.id || '').toLowerCase().includes(term);
  });

  const selectedProject = projects.find((p) => p.id === selectedProjectId || cleanPid(p.id) === cleanPid(selectedProjectId)) || {
    name: selectedProjectId,
    id: selectedProjectId,
  };

  return (
    <div style={{ height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--bg-app)' }}>
      {/* ── TOP BREADCRUMB & TITLE BAR ────────────────────────────────────── */}
      <div style={{
        padding: '16px 24px',
        borderBottom: '1px solid var(--border-main)',
        background: 'var(--bg-surface)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 36,
            height: 36,
            borderRadius: 8,
            background: 'rgba(59, 130, 246, 0.1)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-blue)',
          }}>
            <Activity size={20} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 800, color: 'var(--text-primary)' }}>
              Drift Monitoring
            </h1>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
              Logs of metric drifts detected during scheduled runs · Review and approve changes before warehouse sync.
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            style={{
              ...BTN,
              background: 'rgba(168, 85, 247, 0.1)',
              borderColor: 'rgba(168, 85, 247, 0.35)',
              color: 'var(--accent-purple, #a855f7)',
            }}
            onClick={() => handleOpenBlast(projectEvents[0]?.metric_name || 'TOTAL_UNITS')}
            title="Check Blast Radius: see how metric changes affect downstream BI reports & AI agents"
          >
            <Zap size={13} style={{ color: 'var(--accent-purple, #a855f7)' }} /> Check Blast Radius
          </button>
          <button type="button" style={BTN} onClick={refreshEvents} title="Refresh drift status">
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </div>

      {/* ── MAIN TWO-PANE LAYOUT ─────────────────────────────────────────── */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden' }}>
        {/* ── LEFT PANE: PROJECTS LIST ───────────────────────────────────── */}
        <div style={{
          width: 320,
          borderRight: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
          display: 'flex',
          flexDirection: 'column',
          flexShrink: 0,
        }}>
          {/* Projects Search Header */}
          <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border-main)' }}>
            <div style={{ fontSize: 11.5, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 8 }}>
              Monitored Projects ({projects.length})
            </div>
            <div style={{ position: 'relative' }}>
              <Search size={14} style={{ position: 'absolute', left: 10, top: 9, color: 'var(--text-tertiary)' }} />
              <input
                type="text"
                placeholder="Filter projects…"
                value={projectSearch}
                onChange={(e) => setProjectSearch(e.target.value)}
                style={{ ...INPUT, width: '100%', boxSizing: 'border-box', paddingLeft: 30 }}
              />
            </div>
          </div>

          {/* Projects Scrollable List */}
          <div style={{ flex: 1, overflowY: 'auto', padding: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {visibleProjects.map((p) => {
              const pid = p.id || p.name;
              const isSelected = selectedProjectId === pid || cleanPid(selectedProjectId) === cleanPid(pid);
              const pEvs = allEvents.filter((e) => cleanPid(e.project_id) === cleanPid(pid));
              const pPending = pEvs.filter((e) => e.status === 'pending').length;

              return (
                <button
                  key={pid}
                  type="button"
                  onClick={() => selectProject(pid)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 12px',
                    borderRadius: 8,
                    border: '1px solid',
                    borderColor: isSelected ? 'var(--accent-blue)' : 'transparent',
                    background: isSelected ? 'rgba(59, 130, 246, 0.08)' : 'transparent',
                    cursor: 'pointer',
                    textAlign: 'left',
                    transition: 'all 0.15s ease',
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{
                      fontSize: 13,
                      fontWeight: isSelected ? 700 : 600,
                      color: isSelected ? 'var(--accent-blue)' : 'var(--text-primary)',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}>
                      {p.name || pid}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                      {pid}
                    </div>
                  </div>

                  {/* Status Badge */}
                  <div style={{ flexShrink: 0, marginLeft: 8 }}>
                    {pPending > 0 ? (
                      <span style={{
                        fontSize: 10.5,
                        fontWeight: 700,
                        padding: '2px 7px',
                        borderRadius: 12,
                        background: 'rgba(234, 179, 8, 0.15)',
                        color: 'var(--color-warning)',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                      }}>
                        <AlertTriangle size={11} /> {pPending} Drift{pPending > 1 ? 's' : ''}
                      </span>
                    ) : pEvs.length > 0 ? (
                      <span style={{
                        fontSize: 10.5,
                        fontWeight: 700,
                        padding: '2px 7px',
                        borderRadius: 12,
                        background: 'rgba(34, 197, 94, 0.12)',
                        color: 'var(--color-success)',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                      }}>
                        <CheckCircle2 size={11} /> In Sync
                      </span>
                    ) : (
                      <span style={{
                        fontSize: 10.5,
                        fontWeight: 600,
                        padding: '2px 7px',
                        borderRadius: 12,
                        background: 'var(--bg-surface-raised)',
                        color: 'var(--text-tertiary)',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4,
                        border: '1px solid var(--border-main)',
                      }}>
                        <Clock size={11} /> Not checked
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* ── RIGHT PANE: PROJECT DRIFT MONITORING DETAILS ───────────────── */}
        <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Project Header Bar */}
          <div style={{
            ...CARD,
            padding: 16,
            background: 'var(--bg-surface)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: 12,
          }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <h2 style={{ margin: 0, fontSize: 17, fontWeight: 800, color: 'var(--text-primary)' }}>
                  {selectedProject.name || selectedProjectId}
                </h2>
                <code style={{ fontSize: 11.5, padding: '2px 6px', borderRadius: 4, background: 'var(--bg-app)', color: 'var(--text-secondary)' }}>
                  {selectedProjectId}
                </code>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Database size={13} style={{ color: 'var(--accent-blue)' }} /> Target: Snowflake SEMABRIDGE_WORKSPACE · Auto-monitored on scheduled runs
              </div>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button
                type="button"
                style={BTN_PRIMARY}
                disabled={busy}
                onClick={handleCheckNow}
                title="Execute immediate drift inspection against Snowflake"
              >
                <Play size={13} /> {busy ? 'Checking…' : 'Check Drift Now'}
              </button>
              <button
                type="button"
                style={BTN}
                onClick={handleSeedMock}
                title="Refresh drift logs from warehouse and semantic models"
              >
                <RefreshCw size={13} /> Refresh
              </button>
              <button
                type="button"
                style={{ ...BTN, color: 'var(--color-error)' }}
                onClick={handleClearHistory}
                title="Wipe drift events history for this project"
              >
                <Trash2 size={13} /> Clear History
              </button>
            </div>
          </div>

          {/* Quick Metrics KPI Bar */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
            <div style={{ ...CARD, padding: 14 }}>
              <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontWeight: 600, textTransform: 'uppercase' }}>Drift Status</div>
              <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4, color: pendingEvents.length > 0 ? 'var(--color-warning)' : projectEvents.length > 0 ? 'var(--color-success)' : 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
                {pendingEvents.length > 0 ? (
                  <><AlertTriangle size={18} /> {pendingEvents.length} Action Needed</>
                ) : projectEvents.length > 0 ? (
                  <><ShieldCheck size={18} /> In Sync (Clean)</>
                ) : (
                  <><Clock size={18} /> Not Checked</>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                {projectEvents.length === 0
                  ? 'No drift comparison run yet for this project'
                  : (pendingEvents.length > 0 ? 'Direct upsert paused on schedule' : 'Direct upsert permitted')}
              </div>
            </div>

            <div style={{ ...CARD, padding: 14 }}>
              <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontWeight: 600, textTransform: 'uppercase' }}>Pending Approvals</div>
              <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4, color: 'var(--text-primary)' }}>
                {pendingEvents.length}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>Awaiting review &amp; sync</div>
            </div>

            <div style={{ ...CARD, padding: 14 }}>
              <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontWeight: 600, textTransform: 'uppercase' }}>Approved &amp; Synced</div>
              <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4, color: 'var(--color-success)' }}>
                {approvedEvents.length}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>Deployed to warehouse</div>
            </div>

            <div style={{ ...CARD, padding: 14 }}>
              <div style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontWeight: 600, textTransform: 'uppercase' }}>Ignored / Dismissed</div>
              <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4, color: 'var(--text-secondary)' }}>
                {ignoredEvents.length}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>Accepted variance</div>
            </div>
          </div>

          {checkMsg && (
            <div style={{ padding: '8px 12px', borderRadius: 6, background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-blue)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
              <CheckCircle2 size={14} /> {checkMsg}
            </div>
          )}

          {/* ── DRIFT EVENTS LIST / TABLE ─────────────────────────────────── */}
          <div style={CARD}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Clock size={16} style={{ color: 'var(--accent-blue)' }} />
                <strong style={{ fontSize: 13.5 }}>
                  Drift Occurrences &amp; Approvals ({projectEvents.length})
                </strong>
              </div>

              {/* Filter Tabs */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {[
                  { id: 'all', label: `All (${projectEvents.length})` },
                  { id: 'pending', label: `Pending (${pendingEvents.length})` },
                  { id: 'approved', label: `Approved (${approvedEvents.length})` },
                  { id: 'ignored', label: `Ignored (${ignoredEvents.length})` },
                ].map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    onClick={() => setStatusFilter(f.id)}
                    style={{
                      ...BTN,
                      padding: '3px 8px',
                      fontSize: 11,
                      background: statusFilter === f.id ? 'var(--bg-surface-hover)' : 'transparent',
                      borderColor: statusFilter === f.id ? 'var(--accent-blue)' : 'var(--border-main)',
                      color: statusFilter === f.id ? 'var(--accent-blue)' : 'var(--text-secondary)',
                    }}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            {filteredEvents.length === 0 ? (
              <div style={{
                padding: 36,
                textAlign: 'center',
                borderRadius: 8,
                background: 'var(--bg-app)',
                border: '1px dashed var(--border-main)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 8,
              }}>
                <ShieldCheck size={32} style={{ color: 'var(--color-success)' }} />
                <strong style={{ fontSize: 14, color: 'var(--text-primary)' }}>
                  All metrics in sync with Snowflake
                </strong>
                <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)', maxWidth: 460 }}>
                  No drift detected between your BI semantic models and deployed Snowflake semantic views.
                  Scheduled parity checks will monitor and display any drift here automatically.
                </p>
                <button
                  type="button"
                  style={{ ...BTN, marginTop: 8 }}
                  onClick={handleSeedMock}
                >
                  <RefreshCw size={13} /> Refresh Logs
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
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
                        border: `1px solid ${isPending
                          ? 'rgba(234, 179, 8, 0.4)'
                          : isApproved
                            ? 'rgba(34, 197, 94, 0.3)'
                            : 'var(--border-main)'
                          }`,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 10,
                      }}
                    >
                      {/* Event Header: Metric name, drift type, status, occurred timestamp */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                            {ev.metric_name}
                          </span>
                          <code style={{ fontSize: 11, padding: '2px 6px', borderRadius: 4, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                            {ev.id}
                          </code>
                          {(ev.badges || [ev.drift_type])
                            .filter((b) => !['PENDING_APPROVAL', 'APPROVED', 'IGNORED', 'PENDING'].includes(String(b).toUpperCase()))
                            .map((badge, bIdx) => (
                              <span
                                key={bIdx}
                                style={{
                                  fontSize: 10,
                                  fontWeight: 700,
                                  padding: '2px 6px',
                                  borderRadius: 4,
                                  textTransform: 'uppercase',
                                  background: badge.includes('MISMATCH')
                                    ? 'rgba(234, 179, 8, 0.15)'
                                    : badge.includes('FILTER')
                                      ? 'rgba(59, 130, 246, 0.15)'
                                      : badge.includes('DATE') || badge.includes('WINDOW')
                                        ? 'rgba(168, 85, 247, 0.15)'
                                        : badge.includes('APPROVED')
                                          ? 'rgba(34, 197, 94, 0.15)'
                                          : badge.includes('IGNORED')
                                            ? 'rgba(239, 68, 68, 0.15)'
                                            : 'rgba(107, 114, 128, 0.15)',
                                  color: badge.includes('MISMATCH')
                                    ? 'var(--color-warning)'
                                    : badge.includes('FILTER')
                                      ? 'var(--accent-blue)'
                                      : badge.includes('DATE') || badge.includes('WINDOW')
                                        ? 'var(--accent-purple)'
                                        : badge.includes('APPROVED')
                                          ? 'var(--color-success)'
                                          : badge.includes('IGNORED')
                                            ? 'var(--color-error)'
                                            : 'var(--text-secondary)',
                                }}
                              >
                                {badge.replace(/_/g, ' ')}
                              </span>
                            ))}
                          <span style={{
                            fontSize: 10,
                            fontWeight: 700,
                            padding: '2px 6px',
                            borderRadius: 4,
                            textTransform: 'uppercase',
                            background: isApproved ? 'rgba(34,197,94,0.15)' : isIgnored ? 'rgba(239,68,68,0.15)' : 'rgba(234,179,8,0.15)',
                            color: isApproved ? 'var(--color-success)' : isIgnored ? 'var(--color-error)' : 'var(--color-warning)',
                          }}>
                            {isPending ? 'Pending Approval' : ev.status}
                          </span>
                        </div>

                        {/* Occurrence Timestamp */}
                        <div style={{
                          fontSize: 11.5,
                          color: 'var(--text-secondary)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 5,
                          background: 'var(--bg-surface)',
                          padding: '3px 8px',
                          borderRadius: 6,
                          border: '1px solid var(--border-main)',
                        }}>
                          <Clock size={12} style={{ color: 'var(--accent-blue)' }} />
                          <span>Occurred: <strong>{formatOccurred(ev.detected_at)}</strong></span>
                        </div>
                      </div>

                      {/* Business Story */}
                      {ev.story && (
                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                          <strong style={{ color: 'var(--text-primary)' }}>Story:</strong> {ev.story}
                        </div>
                      )}

                      {/* Pending Drift Warning Banner */}
                      {isPending && (
                        <div style={{
                          padding: '8px 12px',
                          borderRadius: 6,
                          background: 'rgba(234, 179, 8, 0.1)',
                          border: '1px solid rgba(234, 179, 8, 0.25)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                          fontSize: 12,
                          color: 'var(--color-warning)',
                        }}>
                          <AlertTriangle size={14} style={{ flexShrink: 0 }} />
                          <span>
                            <strong>Drift Approval Required:</strong> Metric formula difference detected during scheduled sync. Direct upsert is paused until approved.
                          </span>
                        </div>
                      )}

                      {/* Side-by-side Formula Comparison (BI Modified vs Snowflake Current vs SemaBridge Logic) */}
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10, fontSize: 11.5 }}>
                        <div style={{ padding: '8px 12px', borderRadius: 6, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                          <div style={{ color: 'var(--accent-blue)', fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                            <FileText size={12} /> BI Semantic Model (Modified):
                          </div>
                          <code style={{ color: 'var(--text-primary)', wordBreak: 'break-all', fontFamily: 'monospace' }}>
                            {ev.bi_definition || '—'}
                          </code>
                        </div>

                        <div style={{ padding: '8px 12px', borderRadius: 6, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                          <div style={{ color: 'var(--text-secondary)', fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
                            <Database size={12} /> Snowflake Warehouse (Current):
                          </div>
                          <code style={{ color: 'var(--text-secondary)', wordBreak: 'break-all', fontFamily: 'monospace' }}>
                            {ev.snowflake_definition || '—'}
                          </code>
                        </div>

                        <div style={{
                          padding: '8px 12px',
                          borderRadius: 6,
                          background: 'var(--bg-surface)',
                          border: '1px solid var(--border-main)',
                        }}>
                          <div style={{ color: 'var(--text-primary)', fontWeight: 600, marginBottom: 4, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
                            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                              <Sparkles size={12} style={{ color: 'var(--accent-blue)' }} /> Proposed Resolution:
                            </span>
                            <span style={{ fontSize: 9.5, padding: '1px 5px', borderRadius: 3, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontWeight: 700 }}>
                              REDEPLOY
                            </span>
                          </div>
                          <code style={{ color: 'var(--text-primary)', wordBreak: 'break-all', fontFamily: 'monospace', fontWeight: 600 }}>
                            {ev.proposed_snowflake_definition || 'redeploy from source'}
                          </code>
                        </div>
                      </div>

                      {/* What Changed & Value Impact */}
                      {((ev.what_changed && ev.what_changed.length > 0) || ev.value_impact) && (
                        <div style={{
                          padding: '10px 12px',
                          borderRadius: 6,
                          background: 'rgba(255, 255, 255, 0.02)',
                          border: '1px solid var(--border-main)',
                          fontSize: 11.5,
                          display: 'flex',
                          flexDirection: 'column',
                          gap: 6,
                        }}>
                          {ev.what_changed && ev.what_changed.length > 0 && (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                              <span style={{ color: 'var(--text-secondary)', fontWeight: 600, minWidth: 92 }}>What changed:</span>
                              <span style={{ color: 'var(--text-tertiary)', flex: 1 }}>{ev.what_changed.join(' · ')}</span>
                            </div>
                          )}
                          {ev.value_impact && (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                              <span style={{ color: 'var(--text-secondary)', fontWeight: 600, minWidth: 92 }}>Value impact:</span>
                              <span style={{ color: 'var(--accent-blue, #60a5fa)', fontWeight: 600, flex: 1 }}>{ev.value_impact}</span>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Blast Radius / Impacted Objects */}
                      {ev.affected_objects?.length > 0 && (
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          <Layers size={12} style={{ color: 'var(--text-secondary)' }} />
                          <span>Impacted Objects:</span>
                          {ev.affected_objects.map((obj, i) => (
                            <span key={i} style={{ padding: '1px 5px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                              {obj}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Action Bar / Resolution Details */}
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 8, marginTop: 2 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <button
                            type="button"
                            style={{
                              ...BTN,
                              padding: '4px 9px',
                              fontSize: 11,
                              background: 'rgba(168, 85, 247, 0.1)',
                              borderColor: 'rgba(168, 85, 247, 0.3)',
                              color: 'var(--accent-purple, #a855f7)',
                            }}
                            onClick={() => handleOpenBlast(ev.metric_name)}
                            title="Check Blast Radius: See downstream BI & AI impact of this metric drift"
                          >
                            <Zap size={11} style={{ color: 'var(--accent-purple, #a855f7)' }} /> Blast Radius
                          </button>
                        </div>
                        {isPending ? (
                          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                            <button
                              type="button"
                              style={{ ...BTN_PRIMARY, background: 'var(--color-success, #22c55e)', borderColor: 'var(--color-success, #22c55e)', color: '#fff', padding: '5px 12px' }}
                              onClick={() => handleResolve(ev.id, 'approve')}
                              title="Approve changes and redeploy from source"
                            >
                              <Check size={13} /> Approve &amp; Redeploy
                            </button>
                            <button
                              type="button"
                              style={{ ...BTN, padding: '5px 12px' }}
                              onClick={() => handleResolve(ev.id, 'ignore')}
                              title="Ignore drift and keep current warehouse definition"
                            >
                              <X size={13} /> Ignore
                            </button>
                          </div>
                        ) : (
                          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                            {ev.resolution_reason ? `Resolution: ${ev.resolution_reason}` : ''}
                            {ev.resolved_at ? ` · Resolved ${formatOccurred(ev.resolved_at)}` : ''}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── BLAST RADIUS IMPACT MODAL ────────────────────────────────────── */}
      <Modal
        open={blastModalOpen}
        onClose={() => setBlastModalOpen(false)}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Zap size={16} style={{ color: 'var(--accent-purple, #a855f7)' }} />
            <span>Drift Blast Radius &amp; Downstream Impact</span>
          </div>
        }
        width={960}
        style={{ maxWidth: 960, width: '95vw' }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
            Check how approving this metric drift propagates downstream across BI dashboards, semantic layers, and Cortex AI agents.
          </div>

          {/* Target & Change Type Controls */}
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 2fr) minmax(110px, 1fr) minmax(140px, 1.2fr) auto', gap: 8, alignItems: 'center' }}>
            <input
              type="text"
              list="drift-blast-metrics"
              value={blastTarget}
              onChange={(e) => setBlastTarget(e.target.value)}
              placeholder="Target metric, table or column"
              style={{
                padding: '6px 10px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 12,
                width: '100%',
                boxSizing: 'border-box',
              }}
            />
            <datalist id="drift-blast-metrics">
              {quickTargets.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>

            <select
              value={blastType}
              onChange={(e) => {
                const newType = e.target.value;
                setBlastType(newType);
                runBlastSimulation(blastTarget, blastChangeType, newType);
              }}
              style={{
                padding: '6px 10px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              <option value="METRIC">Metric</option>
              <option value="COLUMN">Column</option>
              <option value="TABLE">Table</option>
            </select>

            <select
              value={blastChangeType}
              onChange={(e) => {
                const newChange = e.target.value;
                setBlastChangeType(newChange);
                runBlastSimulation(blastTarget, newChange, blastType);
              }}
              style={{
                padding: '6px 10px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              <option value="MODIFY_EXPRESSION">MODIFY_EXPRESSION</option>
              <option value="MODIFY_TYPE">MODIFY_TYPE</option>
              <option value="RENAME">RENAME</option>
              <option value="DROP">DROP</option>
            </select>

            <button
              type="button"
              disabled={blastLoading || !blastTarget.trim()}
              onClick={() => runBlastSimulation()}
              style={{
                ...BTN_PRIMARY,
                background: 'var(--accent-purple, #a855f7)',
                borderColor: 'var(--accent-purple, #a855f7)',
                color: '#fff',
                opacity: blastLoading || !blastTarget.trim() ? 0.6 : 1,
                whiteSpace: 'nowrap',
              }}
            >
              <Play size={12} /> {blastLoading ? 'Checking…' : 'Check'}
            </button>
          </div>

          {/* Quick Target Chips */}
          {quickTargets.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', fontSize: 11 }}>
              <span style={{ color: 'var(--text-secondary)' }}>Quick Targets:</span>
              {quickTargets.slice(0, 12).map((t) => (
                <button
                  key={t}
                  type="button"
                  style={{
                    ...BTN,
                    padding: '2px 7px',
                    fontSize: 10.5,
                    fontFamily: 'monospace',
                    background: blastTarget === t ? 'rgba(168, 85, 247, 0.15)' : 'var(--bg-surface-raised)',
                    borderColor: blastTarget === t ? 'var(--accent-purple)' : 'var(--border-main)',
                    color: blastTarget === t ? 'var(--accent-purple)' : 'var(--text-secondary)',
                  }}
                  onClick={() => {
                    setBlastTarget(t);
                    runBlastSimulation(t, blastChangeType, blastType);
                  }}
                >
                  {t}
                </button>
              ))}
            </div>
          )}

          {/* Error Message */}
          {blastError && (
            <div style={{
              padding: '8px 12px',
              borderRadius: 6,
              background: 'rgba(239, 68, 68, 0.1)',
              color: 'var(--color-error)',
              fontSize: 12,
            }}>
              {blastError}
            </div>
          )}

          {/* Impact Report View */}
          {blastReport && (
            <div style={{ marginTop: 4 }}>
              <ImpactReport
                report={blastReport}
                onPickSuggestion={(sug) => {
                  setBlastTarget(sug);
                  runBlastSimulation(sug);
                }}
              />
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
