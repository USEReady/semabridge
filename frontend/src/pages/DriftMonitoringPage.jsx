import React, { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity, Play, Check, X, Clock, AlertTriangle, CheckCircle2,
  Trash2, RefreshCw, Sparkles, Database, Layers, ArrowRight,
  ShieldCheck, ShieldAlert, Search, ChevronRight, FileText,
} from 'lucide-react';
import { authFetch } from '../utils/api';

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
          // Default to tiral_comp if available, otherwise first project
          const tiral = list.find((p) => (p.name || p.id || '').includes('tiral') || (p.name || p.id || '').includes('comp'));
          setSelectedProjectId(tiral?.id || list[0].id || list[0].name);
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

  // Seed mock drift logs for demonstration
  const handleSeedMock = async () => {
    try {
      const pid = selectedProjectId || 'proj-tiral_comp';
      const res = await authFetch(`/api/drift/events/seed-mock?project_id=${encodeURIComponent(pid)}`, { method: 'POST' });
      if (res.ok) {
        setCheckMsg('Sample mock drift logs loaded for project.');
        await refreshEvents();
      }
    } catch (err) {
      alert(`Failed to seed mock logs: ${err}`);
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
              Data Monitoring
            </h1>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
              Logs of metric drifts detected during scheduled runs · Review and approve changes before warehouse sync.
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
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
                    ) : (
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
                title="Load sample mock drift logs for demonstration"
              >
                <RefreshCw size={13} /> Seed Mock Logs
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
              <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4, color: pendingEvents.length > 0 ? 'var(--color-warning)' : 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 6 }}>
                {pendingEvents.length > 0 ? (
                  <><AlertTriangle size={18} /> {pendingEvents.length} Action Needed</>
                ) : (
                  <><ShieldCheck size={18} /> In Sync (Clean)</>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                {pendingEvents.length > 0 ? 'Direct upsert paused on schedule' : 'Direct upsert permitted'}
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
                  <RefreshCw size={13} /> Load Sample Mock Logs
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
                        border: `1px solid ${
                          isPending
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

                      {/* Side-by-side Formula Comparison */}
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, fontSize: 11.5 }}>
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
                      </div>

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
                        {isPending ? (
                          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                            <button
                              type="button"
                              style={{ ...BTN_PRIMARY, background: 'var(--color-success, #22c55e)', borderColor: 'var(--color-success, #22c55e)', color: '#fff', padding: '5px 12px' }}
                              onClick={() => handleResolve(ev.id, 'approve')}
                              title="Approve changes and automatically sync to Snowflake warehouse"
                            >
                              <Check size={13} /> Approve &amp; Sync
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
    </div>
  );
}

