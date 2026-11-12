/**
 * Data helpers for the one-page project view (/projects/:id):
 * runs, their report summaries, ontology health and folder name.
 */
import { useEffect, useState } from 'react';
import { api, authFetch } from '../../utils/api';

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');

// How many recent finished runs get a report summary (result bars, trend line).
const SUMMARY_LIMIT = 10;
const FINISHED = new Set(['success', 'completed', 'partial', 'warning', 'failed', 'error', 'cancelled']);
const OK = new Set(['success', 'completed', 'partial', 'warning']);

export const runId = (run) => String(run?.run_id || run?.id || '');
export const isFinished = (run) => FINISHED.has(String(run?.status || '').toLowerCase());
export const isOk = (run) => OK.has(String(run?.status || '').toLowerCase());

export function conversionPct(counts) {
  if (!counts) return null;
  const n = (k) => Number(counts[k]) || 0;
  const converted = n('standard') + n('ai_assisted');
  const total = converted + n('needs_review') + n('dropped');
  return total ? Math.round((100 * converted) / total) : null;
}

export function measureTotal(counts) {
  if (!counts) return 0;
  return ['standard', 'ai_assisted', 'needs_review', 'dropped'].reduce((s, k) => s + (Number(counts[k]) || 0), 0);
}

export function durationLabel(run) {
  let ms = Number(run?.duration_ms);
  if (!ms && run?.started_at && run?.completed_at) {
    ms = Date.parse(run.completed_at) - Date.parse(run.started_at);
  }
  if (!ms || Number.isNaN(ms) || ms < 0) return '—';
  const secs = Math.round(ms / 1000);
  const m = Math.floor(secs / 60);
  return m ? `${m}m ${secs % 60}s` : `${secs}s`;
}

export function relTime(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? 'yesterday' : `${days}d ago`;
}

export function shortDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Status → label used across the project page ("Succeeded", "Failed", …). */
export function statusLabel(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'success' || s === 'completed') return 'Succeeded';
  if (s === 'partial' || s === 'warning') return 'Partly succeeded';
  if (s === 'failed' || s === 'error') return 'Failed';
  if (s === 'running' || s === 'queued' || s === 'pending') return 'Running';
  if (s === 'cancelled') return 'Cancelled';
  return s ? s[0].toUpperCase() + s.slice(1) : 'Unknown';
}

/** Human text for a project's schedule, or null when it only runs by hand. */
export function scheduleText(schedule) {
  if (!schedule || schedule.enabled === false) return null;
  const type = String(schedule.schedule_type || 'manual').toLowerCase();
  if (type === 'manual') return null;
  if (type === 'cron' && schedule.cron) return `Schedule: ${schedule.cron}`;
  const when = schedule.next_run_at || schedule.scheduled_time;
  if (when) {
    const d = new Date(when);
    if (!Number.isNaN(d.getTime())) return `Next run ${d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}`;
  }
  return 'Scheduled';
}

/**
 * Loads everything the project page needs. Runs are newest first, previews
 * excluded. `summaries` maps run id → report summary (or { crashed: true }).
 */
export function useProjectPageData(projectId, project, reloadKey = 0) {
  // Results are keyed by project id, so loading flags are derived instead of
  // being reset inside the effects.
  const [runsState, setRunsState] = useState({ id: null, runs: [] });
  const [summaries, setSummaries] = useState({});
  const [health, setHealth] = useState(null);
  const [folder, setFolder] = useState({ id: null, name: null });
  const [schedule, setSchedule] = useState(null);

  useEffect(() => {
    if (!projectId) return undefined;
    let cancelled = false;
    api.getProjectRuns(projectId)
      .then((rows) => {
        if (cancelled) return;
        const sorted = rows
          .filter(r => String(r.run_type || '').toUpperCase() !== 'DRY_RUN')
          .sort((a, b) => (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
        setRunsState({ id: projectId, runs: sorted });
        const wanted = sorted.filter(isFinished).slice(0, SUMMARY_LIMIT);
        Promise.all(wanted.map(r =>
          api.getRunReportSummary(projectId, runId(r))
            .then(data => [runId(r), data])
            .catch(() => [runId(r), { crashed: true, unavailable: true }])
        )).then((pairs) => {
          if (!cancelled) setSummaries(Object.fromEntries(pairs));
        });
      })
      .catch(() => { if (!cancelled) setRunsState({ id: projectId, runs: [] }); });

    authFetch(`${API_BASE}/ontology/projects/${encodeURIComponent(projectId)}/health`)
      .then(res => (res.ok ? res.json() : null))
      .then(data => { if (!cancelled) setHealth(data); })
      .catch(() => {});

    api.getProjectSchedule(projectId)
      .then(data => { if (!cancelled) setSchedule(data); })
      .catch(() => {});

    return () => { cancelled = true; };
  }, [projectId, reloadKey]);

  const folderId = project?.folder_id;
  useEffect(() => {
    if (!folderId) return undefined;
    let cancelled = false;
    api.listFolders()
      .then((folders) => {
        if (cancelled) return;
        const f = (folders || []).find(x => String(x.id) === String(folderId));
        setFolder({ id: folderId, name: f?.name || null });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [folderId]);

  const runsLoading = runsState.id !== projectId;
  const runs = runsLoading ? [] : runsState.runs;
  const folderName = folderId && folder.id === folderId ? folder.name : null;

  return { runs, runsLoading, summaries, health, folderName, schedule };
}
