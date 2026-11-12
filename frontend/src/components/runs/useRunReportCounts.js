/**
 * Lazily fetches GET /projects/{pid}/runs/{rid}/report-summary for the runs
 * you pass in (the visible rows, the open drawer) and caches the result for
 * the whole session. Finished runs only, at most 2 requests in flight, and a
 * run is never asked twice — unavailable / crashed answers are cached too.
 *
 * Returns { [runId]: { counts } | { unavailable: true } }.
 */
import { useEffect, useState } from 'react';
import { api } from '../../utils/api';
import { normStatus } from './runsFormat';

const cache = new Map(); // runId -> entry
const inFlight = new Set();
const queue = [];
const listeners = new Set();
const MAX_PARALLEL = 2;

function notify() {
  listeners.forEach(fn => fn());
}

function toEntry(data) {
  if (!data || data.crashed || data.unavailable || !data.counts) {
    return { unavailable: true, error: data?.error || '' };
  }
  return { counts: data.counts, status: data.status };
}

function pump() {
  while (inFlight.size < MAX_PARALLEL && queue.length) {
    const { pid, rid } = queue.shift();
    if (cache.has(rid) || inFlight.has(rid)) continue;
    inFlight.add(rid);
    api.getRunReportSummary(pid, rid)
      .then(data => cache.set(rid, toEntry(data)))
      .catch(() => cache.set(rid, { unavailable: true }))
      .finally(() => {
        inFlight.delete(rid);
        notify();
        pump();
      });
  }
}

export function requestRunCounts(run) {
  const rid = run?.id || run?.run_id;
  if (!rid || !run.project_id) return;
  const st = normStatus(run.status);
  if (st === 'running' || st === 'unknown') return;
  if (cache.has(rid) || inFlight.has(rid) || queue.some(q => q.rid === rid)) return;
  queue.push({ pid: run.project_id, rid });
  pump();
}

export function getRunCounts(runId) {
  return cache.get(runId);
}

export function clearRunCounts() {
  cache.clear();
  queue.length = 0;
  notify();
}

export default function useRunReportCounts(runs) {
  const [, setTick] = useState(0);

  useEffect(() => {
    const fn = () => setTick(t => t + 1);
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);

  const ids = (runs || []).map(r => `${r?.id}:${normStatus(r?.status)}`).join('|');
  useEffect(() => {
    (runs || []).forEach(requestRunCounts);
    // ids captures every input that matters; runs itself changes identity on each poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids]);

  const out = {};
  (runs || []).forEach(r => {
    const e = cache.get(r?.id);
    if (e) out[r.id] = e;
  });
  return out;
}
