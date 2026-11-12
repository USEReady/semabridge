/**
 * Right-side run detail drawer for the Runs page: status + sync mode in plain
 * words, the conversion bar, "What changed" chips, the execution stages as a
 * vertical timeline, and Full report / Logs / Download .md. Per-model details
 * (reconciliation, dropped fields, update-only changes, raw stages) sit in a
 * collapsed "Per model" section below.
 * Mount with key={runId} so per-run state starts fresh.
 */
import { useEffect, useMemo, useState } from 'react';
import { X, FileText, ScrollText, Download, AlertTriangle, Info, RefreshCw } from 'lucide-react';
import Modal from '../common/Modal';
import ModelRunSection from './ModelRunSection';
import { ConversionBar, ConversionLegend } from '../projects/projectHealth';
import { api } from '../../utils/api';
import { getPerModelRunEntries, getRunLogs } from '../../utils/runLogs';
import { cleanPbixLabel } from '../../utils/pbixLabel';
import { isInternalAutoTable } from '../../utils/placeholderMetric';
import {
  formatDuration, formatDateTime, normStatus, statusPill, syncModeWords,
  parseStages, formatStageTook,
} from './runsFormat';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

export function Pill({ children, color = 'var(--text-secondary)', bg = 'var(--bg-surface-raised)', title }) {
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 9px', borderRadius: 999,
        fontSize: 11, fontWeight: 600, color, background: bg, whiteSpace: 'nowrap', lineHeight: 1.6,
      }}
    >
      {children}
    </span>
  );
}

export function StatusPill({ status }) {
  const p = statusPill(status);
  return <Pill color={p.color} bg={p.bg}>{p.label}</Pill>;
}

function SectionLabel({ children }) {
  return (
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '18px 0 8px' }}>
      {children}
    </div>
  );
}

const btn = {
  display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 8,
  fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)', color: 'var(--text-primary)',
};

function isUpsertFocusedRun(run) {
  return String(run?.sync_mode || '').toLowerCase() === 'upsert'
    && (run?.results || []).some(r => r?.upsert_delta || r?.summary?.upsert_delta);
}

/* "What changed": the snapshot comparison the old "Run Summary & Diffs" card showed. */
function useSnapshotDiff(run) {
  const [stats, setStats] = useState(null);
  const beforeId = run?.before_target_snapshot_ids?.[0];
  const afterId = run?.after_target_snapshot_ids?.[0] || run?.after_tgt_snapshots?.[0];
  const pid = run?.project_id;
  const enabled = normStatus(run?.status) === 'success' && !isUpsertFocusedRun(run);

  useEffect(() => {
    if (!enabled || !beforeId || !afterId || !pid) return undefined;
    let alive = true;
    api.compareProjectSnapshots(pid, beforeId, afterId)
      .then(diff => {
        if (!alive) return;
        let removed = 0;
        let added = 0;
        Object.values(diff?.models || {}).forEach(m => {
          if (m.status === 'removed') removed += 1;
          else if (m.status === 'added') added += 1;
        });
        const before = diff?.metadata_diff?.snapshot_a?.model_count || 0;
        const after = diff?.metadata_diff?.snapshot_b?.model_count || 0;
        setStats({ added, removed, kept: Math.max(0, before - removed), after });
      })
      .catch(() => { if (alive) setStats({ error: true }); });
    return () => { alive = false; };
  }, [enabled, beforeId, afterId, pid]);

  return enabled ? (stats || { loading: true }) : null;
}

/* Per-model log lines fetched once when the drawer opens (and refreshed while running). */
function useModelLogs(run, entries) {
  const [logs, setLogs] = useState({});
  const rid = run?.id;
  const pid = run?.project_id;
  const running = normStatus(run?.status) === 'running';
  const models = entries.filter(e => e.hasResult && e.model).map(e => e.model);
  const modelKey = models.join('\u0001');

  useEffect(() => {
    if (!rid || !pid || !modelKey) return undefined;
    let alive = true;
    const load = () => Promise.all(modelKey.split('\u0001').map(m =>
      api.getRunModelLogs(pid, rid, m)
        .then(d => [m, Array.isArray(d?.lines) ? d.lines : []])
        .catch(() => [m, null])
    )).then(pairs => {
      if (!alive) return;
      setLogs(prev => {
        const next = { ...prev };
        pairs.forEach(([m, lines]) => { if (lines && lines.length) next[m] = lines; });
        return next;
      });
    });
    load();
    const t = running ? setInterval(load, 4000) : null;
    return () => { alive = false; if (t) clearInterval(t); };
  }, [rid, pid, modelKey, running]);

  return logs;
}

function Timeline({ stages, running }) {
  if (!stages.length) {
    return (
      <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
        {running ? 'Waiting for the first stage…' : 'No stage details were recorded for this run.'}
      </div>
    );
  }
  const colorOf = s => (s.state === 'failed' ? 'var(--color-error)'
    : s.state === 'running' ? 'var(--accent-blue)'
      : s.state === 'skipped' ? 'var(--text-quaternary)'
        : /mismatch|warning|review/i.test(s.detail) ? 'var(--color-warning)' : 'var(--color-success)');
  return (
    <div style={{ position: 'relative', paddingLeft: 18 }}>
      <div style={{ position: 'absolute', left: 4, top: 6, bottom: 6, width: 2, background: 'var(--border-main)' }} />
      {stages.map((s, i) => (
        <div key={`${s.num}-${i}`} style={{ position: 'relative', display: 'flex', gap: 10, alignItems: 'baseline', padding: '5px 0' }}>
          <span
            style={{
              position: 'absolute', left: -18, top: 9, width: 10, height: 10, borderRadius: '50%',
              background: colorOf(s), boxShadow: '0 0 0 3px var(--bg-surface)',
            }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, color: 'var(--text-primary)', fontWeight: 500 }}>{s.name}</div>
            {s.detail && (
              <div
                title={s.detail}
                style={{
                  fontSize: 11, color: s.state === 'failed' ? 'var(--color-error)' : 'var(--text-tertiary)', overflowWrap: 'anywhere',
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                }}
              >
                {s.detail}
              </div>
            )}
          </div>
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO, flexShrink: 0 }}>
            {formatStageTook(s.tookSec)}
          </span>
        </div>
      ))}
      {running && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--accent-blue)', padding: '5px 0' }}>
          <RefreshCw size={12} className="animate-spin" /> Working…
        </div>
      )}
    </div>
  );
}

function LogsModal({ open, onClose, run, sources }) {
  const names = Object.keys(sources);
  const [pick, setPick] = useState(names[0] || '');
  const current = sources[pick] || sources[names[0]] || [];
  return (
    <Modal open={open} onClose={onClose} title={`Logs · ${run?.id || ''}`} size="xl">
      {names.length > 1 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {names.map(n => (
            <button
              key={n}
              type="button"
              onClick={() => setPick(n)}
              style={{ ...btn, padding: '4px 10px', ...(n === pick ? { borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' } : {}) }}
            >
              {cleanPbixLabel(n) || n}
            </button>
          ))}
        </div>
      )}
      <div
        style={{
          borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-input)', padding: 12,
          fontFamily: MONO, fontSize: 12, lineHeight: 1.6, color: 'var(--text-primary)', whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere', maxHeight: '60vh', overflowY: 'auto',
        }}
      >
        {current.length ? current.join('\n') : 'No logs recorded for this run.'}
      </div>
    </Modal>
  );
}

export default function RunDrawer({
  runId, run, runNumber, liveManifest, counts, onClose, onOpenReport, onDownload, onOpenDropped, modalOpen = false,
}) {
  const [logsOpen, setLogsOpen] = useState(false);
  const [openModels, setOpenModels] = useState({});

  useEffect(() => {
    if (modalOpen || logsOpen) return undefined;
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, modalOpen, logsOpen]);

  const status = normStatus(run?.status);
  const running = status === 'running';
  const effectiveRun = useMemo(
    () => (run && liveManifest && running ? { ...run, results: liveManifest } : run),
    [run, liveManifest, running],
  );
  const entries = useMemo(() => (effectiveRun ? getPerModelRunEntries(effectiveRun) : []), [effectiveRun]);
  const modelLogs = useModelLogs(effectiveRun, entries);
  const diff = useSnapshotDiff(run);

  const runLogs = useMemo(() => (run ? getRunLogs(run) : []), [run]);
  const logSources = useMemo(() => {
    const out = {};
    entries.forEach(e => {
      const lines = (e.model && modelLogs[e.model]) || e.logs;
      if (lines?.length) out[e.model || e.label] = lines;
    });
    if (!Object.keys(out).length && runLogs.length) out.Run = runLogs;
    return out;
  }, [entries, modelLogs, runLogs]);

  const timelines = useMemo(() => {
    const err = run?.error || run?.message || '';
    const names = Object.keys(logSources);
    if (!names.length) return [{ name: '', stages: parseStages([], { failed: status === 'failed', error: err }) }];
    return names.map(n => {
      const entry = entries.find(e => (e.model || e.label) === n);
      const failed = entry ? entry.status === 'failed' : status === 'failed';
      return { name: names.length > 1 ? n : '', stages: parseStages(logSources[n], { failed, error: entry?.reconciliation?.error || err }) };
    });
  }, [logSources, entries, run, status]);

  const dropped = useMemo(() => {
    if (!run) return null;
    const fromEntries = entries.flatMap(e => (Array.isArray(e.droppedEntities) ? e.droppedEntities : []));
    const list = fromEntries.length ? fromEntries
      : (run.dropped_entities || run.summary?.dropped_entities || run.summary?.drop_ledger || []);
    const total = Array.isArray(list) ? list.length : 0;
    const actionable = total ? list.filter(e => !e?.by_design && !isInternalAutoTable(e)).length : 0;
    return { total, actionable, byDesign: total - actionable };
  }, [run, entries]);

  const hasReport = Boolean(run && (run.has_report || run.report_path || (Array.isArray(run.model_reports) && run.model_reports.length)));
  const isMulti = entries.length > 1;

  return (
    <aside
      aria-label="Run details"
      style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(520px, 100vw)', zIndex: 45,
        background: 'var(--bg-surface)', borderLeft: '1px solid var(--border-main)',
        boxShadow: '-12px 0 32px rgba(0,0,0,0.18)', display: 'flex', flexDirection: 'column',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '16px 20px', borderBottom: '1px solid var(--border-main)' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', overflowWrap: 'anywhere' }}>
            Run {runNumber ? `#${runNumber}` : ''}{run?.project_name ? ` · ${run.project_name}` : ''}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: MONO, marginTop: 2 }}>{runId}</div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close run details" style={{ ...btn, padding: 6, background: 'transparent', border: 'none', color: 'var(--text-tertiary)' }}>
          <X size={16} />
        </button>
      </div>

      <div className="custom-scrollbar" style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 24px' }}>
        {!run ? (
          <div style={{ fontSize: 13, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <RefreshCw size={13} className="animate-spin" /> Loading this run…
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <StatusPill status={run.status} />
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {syncModeWords(run.sync_mode)}{run.duration_ms ? ` · ${formatDuration(run.duration_ms)}` : ''}
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 6 }}>
              Started {formatDateTime(run.started_at)} · {run.schedule && run.schedule !== 'Manual' ? 'Scheduled' : 'Started by hand'}
              {run.total_models ? ` · ${run.models_synced ?? 0} of ${run.total_models} model${run.total_models === 1 ? '' : 's'} synced` : ''}
            </div>

            {status === 'failed' && (run.error || run.message) && (
              <div style={{ marginTop: 12, padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12, lineHeight: 1.5, overflowWrap: 'anywhere' }}>
                {run.error || run.message}
              </div>
            )}

            {counts?.counts && (
              <>
                <SectionLabel>Result</SectionLabel>
                <ConversionBar counts={counts.counts} height={10} />
                <div style={{ marginTop: 8 }}><ConversionLegend counts={counts.counts} /></div>
              </>
            )}

            {(diff || isUpsertFocusedRun(run)) && (
              <>
                <SectionLabel>What changed</SectionLabel>
                {isUpsertFocusedRun(run) ? (
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    Only changed items were updated. See each model under Per model below.
                  </div>
                ) : diff.loading ? (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Comparing with the previous version…</div>
                ) : diff.error ? (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Could not compare with the previous version.</div>
                ) : !diff.added && !diff.removed && !diff.kept && !diff.after ? (
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>No model-level changes were recorded for this run.</div>
                ) : (
                  <>
                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                      <Pill color="var(--color-success)" bg="var(--color-success-bg)">+{diff.added} model{diff.added === 1 ? '' : 's'}</Pill>
                      <Pill>{diff.kept} kept</Pill>
                      <Pill color="var(--color-error)" bg="var(--color-error-bg)">−{diff.removed} removed</Pill>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6 }}>
                      {String(run.sync_mode || 'copy').toLowerCase() === 'upsert'
                        ? `Kept ${diff.kept} models from the previous target and added ${diff.added} new ones. ${diff.after} in total now.`
                        : `The target was replaced with the source: ${diff.removed} target-only model${diff.removed === 1 ? '' : 's'} removed, ${diff.after} in total now.`}
                    </div>
                  </>
                )}
              </>
            )}

            {dropped && dropped.total > 0 && !isUpsertFocusedRun(run) && (
              <div
                style={{
                  marginTop: 14, padding: '9px 12px', borderRadius: 8, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                  background: dropped.actionable ? 'var(--color-error-bg)' : 'var(--bg-surface-raised)',
                  border: `1px solid ${dropped.actionable ? 'rgba(239,68,68,0.35)' : 'var(--border-main)'}`,
                }}
              >
                {dropped.actionable
                  ? <AlertTriangle size={14} style={{ color: 'var(--color-error)', flexShrink: 0 }} />
                  : <Info size={14} style={{ color: 'var(--color-info)', flexShrink: 0 }} />}
                <span style={{ flex: 1, minWidth: 180, fontSize: 12, color: dropped.actionable ? 'var(--color-error)' : 'var(--text-secondary)' }}>
                  {dropped.actionable
                    ? `${dropped.actionable} item${dropped.actionable === 1 ? ' was' : 's were'} dropped and need${dropped.actionable === 1 ? 's' : ''} a look.`
                    : `${dropped.byDesign} Power BI system table${dropped.byDesign === 1 ? ' was' : 's were'} left out by design. Nothing to do.`}
                </span>
                <button type="button" onClick={() => onOpenDropped(run)} style={{ ...btn, padding: '4px 10px', fontSize: 11 }}>
                  {dropped.actionable ? 'See dropped items' : 'See excluded tables'}
                </button>
              </div>
            )}

            <SectionLabel>Stages</SectionLabel>
            {timelines.map((t, i) => (
              <div key={t.name || i} style={{ marginBottom: 10 }}>
                {t.name && <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>{cleanPbixLabel(t.name) || t.name}</div>}
                <Timeline stages={t.stages} running={running} />
              </div>
            ))}

            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
              <button type="button" disabled={!hasReport} onClick={() => onOpenReport(run, null, 'summary')} style={{ ...btn, opacity: hasReport ? 1 : 0.5, cursor: hasReport ? 'pointer' : 'not-allowed' }} title={hasReport ? 'Summary and raw report' : 'No report for this run yet'}>
                <FileText size={13} /> Full report
              </button>
              <button type="button" onClick={() => setLogsOpen(true)} style={btn}>
                <ScrollText size={13} /> Logs
              </button>
              <button type="button" disabled={!hasReport} onClick={() => onDownload(run)} style={{ ...btn, opacity: hasReport ? 1 : 0.5, cursor: hasReport ? 'pointer' : 'not-allowed' }}>
                <Download size={13} /> Download .md
              </button>
            </div>

            {entries.length > 0 && (
              <>
                <SectionLabel>{isMulti ? `Per model (${entries.length})` : 'Model details'}</SectionLabel>
                {entries.map(entry => {
                  const lines = (entry.model && modelLogs[entry.model]) || entry.logs;
                  const isOpen = Boolean(openModels[entry.key]);
                  return (
                    <div key={entry.key}>
                      <ModelRunSection
                        entry={{ ...entry, logs: lines }}
                        projectId={run.project_id}
                        runId={run.id}
                        isRunActive={running}
                        isOpen={isOpen}
                        onToggle={() => setOpenModels(prev => ({ ...prev, [entry.key]: !prev[entry.key] }))}
                        formatDuration={formatDuration}
                      />
                      {isOpen && isMulti && entry.reportModel && (
                        <div style={{ display: 'flex', gap: 8, margin: '-4px 0 12px' }}>
                          <button type="button" onClick={() => onOpenReport(run, entry.reportModel, 'summary')} style={{ ...btn, padding: '4px 10px', fontSize: 11 }}>
                            <FileText size={12} /> Report for this file
                          </button>
                          <button type="button" onClick={() => onOpenDropped(run, entry.model)} style={{ ...btn, padding: '4px 10px', fontSize: 11 }}>
                            Dropped fields
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </>
            )}
          </>
        )}
      </div>

      {logsOpen && <LogsModal open onClose={() => setLogsOpen(false)} run={run} sources={logSources} />}
    </aside>
  );
}
