import { useMemo } from 'react';
import { Pin, Search } from 'lucide-react';
import { groupRunsByDay, shortTime, statusLabel } from './historyUtils';

/**
 * Left column of the Versions tab: runs grouped by day, newest first.
 * Props:
 *   runs          newest-first run list (already filtered)
 *   runNumber     (run) => number shown as "Run #N"
 *   selectedId    run_id of the selected version
 *   onSelect      (run) => void
 *   summaries     { [run_id]: string }  short change summary per run, when known
 *   pinInfo       (run) => { pinned, tag }
 *   query/onQuery filter text
 */
export default function VersionList({ runs, runNumber, selectedId, onSelect, summaries, pinInfo, query, onQuery, loading }) {
    const groups = useMemo(() => groupRunsByDay(runs), [runs]);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <div style={{ padding: 10, borderBottom: '1px solid var(--border-light)' }}>
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 6,
                    border: '1px solid var(--border-main)', background: 'var(--bg-input)',
                }}>
                    <Search size={14} color="var(--text-tertiary)" />
                    <input
                        type="text"
                        value={query}
                        onChange={(e) => onQuery(e.target.value)}
                        placeholder="Filter by run or tag…"
                        aria-label="Filter versions by run or tag"
                        style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'var(--text-primary)' }}
                    />
                </div>
            </div>

            <div style={{ overflowY: 'auto', maxHeight: 'calc(100vh - 300px)', minHeight: 200 }}>
                {loading && runs.length === 0 && (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>Loading versions…</div>
                )}
                {!loading && runs.length === 0 && (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>
                        {query ? 'No versions match this filter.' : 'No versions yet. A version is saved every time this project runs.'}
                    </div>
                )}
                {groups.map((g) => (
                    <div key={g.label}>
                        <div style={{
                            padding: '10px 14px 4px', fontSize: 11, fontWeight: 600,
                            color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.04em',
                        }}>
                            {g.label}
                        </div>
                        {g.runs.map((run) => {
                            const active = run.run_id === selectedId;
                            const { pinned, tag } = pinInfo(run);
                            const st = statusLabel(run.status);
                            const summary = summaries[run.run_id];
                            return (
                                <button
                                    key={run.run_id}
                                    type="button"
                                    onClick={() => onSelect(run)}
                                    aria-current={active ? 'true' : undefined}
                                    style={{
                                        display: 'block', width: '100%', textAlign: 'left',
                                        padding: '8px 14px', border: 'none',
                                        borderLeft: `3px solid ${active ? 'var(--accent-blue)' : 'transparent'}`,
                                        background: active ? 'var(--color-accent-faint)' : 'transparent',
                                        cursor: 'pointer',
                                    }}
                                    onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                                    onMouseLeave={(e) => { e.currentTarget.style.background = active ? 'var(--color-accent-faint)' : 'transparent'; }}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>Run #{runNumber(run)}</span>
                                        {(pinned || tag) && (
                                            <span style={{
                                                display: 'inline-flex', alignItems: 'center', gap: 3,
                                                fontSize: 11, padding: '0 6px', borderRadius: 999,
                                                background: 'var(--color-warning-bg)', color: 'var(--color-warning)',
                                            }}>
                                                {pinned && <Pin size={10} />}{tag || 'Pinned'}
                                            </span>
                                        )}
                                        {st.tone === 'error' && (
                                            <span style={{ fontSize: 11, color: 'var(--color-error)' }}>Failed</span>
                                        )}
                                        <span style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--text-tertiary)' }}>{shortTime(run.started_at)}</span>
                                    </div>
                                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2, minHeight: 16 }}>
                                        {summary || ' '}
                                    </div>
                                </button>
                            );
                        })}
                    </div>
                ))}
            </div>
        </div>
    );
}
