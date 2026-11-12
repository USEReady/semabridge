// Pure helpers for the History screen (Screen 9 of the app redesign).
// Kept free of React so they stay trivially testable.

/** Snapshot that represents the model state a run produced (or saw). */
export function snapshotOf(run) {
    return run?.after_tgt_snapshots?.[0] || run?.before_tgt_snapshots?.[0] || null;
}

/** Snapshot taken just before the run changed anything. */
export function beforeSnapshotOf(run) {
    return run?.before_tgt_snapshots?.[0] || null;
}

function startOfDay(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x.getTime();
}

/** "Today" / "Yesterday" / "11 Nov 2026" for a timestamp. */
export function dayLabel(dateString, now = new Date()) {
    const d = new Date(dateString);
    if (Number.isNaN(d.getTime())) return 'Unknown date';
    const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** Groups an already-sorted (newest first) run list by calendar day. */
export function groupRunsByDay(runs, now = new Date()) {
    const groups = [];
    for (const run of runs) {
        const label = dayLabel(run.started_at, now);
        const last = groups[groups.length - 1];
        if (last && last.label === label) last.runs.push(run);
        else groups.push({ label, runs: [run] });
    }
    return groups;
}

/** "2h ago" for today's runs, otherwise "09:10". */
export function shortTime(dateString, now = new Date()) {
    const d = new Date(dateString);
    if (Number.isNaN(d.getTime())) return '';
    const diffMin = Math.round((now.getTime() - d.getTime()) / 60000);
    if (dayLabel(dateString, now) === 'Today') {
        if (diffMin < 1) return 'just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        return `${Math.round(diffMin / 60)}h ago`;
    }
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** "11 Nov, 10:42" */
export function longTime(dateString) {
    const d = new Date(dateString);
    if (Number.isNaN(d.getTime())) return '—';
    return `${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}, ${d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`;
}

export function statusLabel(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'success' || s === 'succeeded' || s === 'completed') return { text: 'Succeeded', tone: 'success' };
    if (s === 'warning' || s === 'partial') return { text: 'Partly succeeded', tone: 'warning' };
    if (s === 'running' || s === 'pending' || s === 'queued') return { text: 'Running', tone: 'info' };
    if (s === 'failed' || s === 'error') return { text: 'Failed', tone: 'error' };
    return { text: status ? String(status) : 'Unknown', tone: 'default' };
}

/** How the run was started, in plain words. */
export function triggerLabel(run) {
    const type = String(run?.run_type || '').toLowerCase();
    if (type === 'restore') return 'by restore';
    const schedule = String(run?.schedule || '').toLowerCase();
    if (!schedule || schedule === 'manual') return 'manually';
    return 'by schedule';
}

/** Count models per diff status ({ added, changed, removed, unchanged }). */
export function countDiff(models) {
    const counts = { added: 0, changed: 0, removed: 0, unchanged: 0 };
    for (const m of models || []) {
        if (m.status === 'ADDED') counts.added += 1;
        else if (m.status === 'REMOVED') counts.removed += 1;
        else if (m.status === 'MODIFIED') counts.changed += 1;
        else counts.unchanged += 1;
    }
    return counts;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** "+3 models · 1 removed" style one-liner for the version list. */
export function summarizeCounts(counts) {
    if (!counts) return null;
    const parts = [];
    if (counts.added) parts.push(`+${plural(counts.added, 'model')}`);
    if (counts.changed) parts.push(`${counts.changed} changed`);
    if (counts.removed) parts.push(`${counts.removed} removed`);
    return parts.length ? parts.join(' · ') : 'No changes';
}

export function modelWord(n) {
    return n === 1 ? 'model' : 'models';
}
