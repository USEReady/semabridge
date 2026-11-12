import { useEffect, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { api } from '../../utils/api';
import { longTime } from './historyUtils';

/**
 * Collapsed "Storage" footer for the History screen. Replaces the old
 * "Pruning Intelligence" box and the Stats tab; shows only values the
 * backend actually returns (nothing when a call fails).
 */
export default function StorageDisclosure({ projectId, oldestRun, versionCount }) {
    const [open, setOpen] = useState(false);
    const [stats, setStats] = useState(null);
    const [vcStats, setVcStats] = useState(null);

    useEffect(() => {
        if (!open || !projectId) return undefined;
        let cancelled = false;
        const load = async (path, set) => {
            try {
                const res = await api.apiFetch(path);
                if (!res.ok) return;
                const json = await res.json();
                if (!cancelled) set(json);
            } catch {
                /* leave empty: show nothing rather than a made-up number */
            }
        };
        load(`/projects/${projectId}/stats`, setStats);
        load(`/projects/${projectId}/vc/stats`, setVcStats);
        return () => { cancelled = true; };
    }, [open, projectId]);

    const rows = [
        ['Versions (runs)', versionCount],
        ['Saved snapshots', stats?.total_snapshots ?? vcStats?.snapshot_count],
        ['Pinned', stats?.pinned_count],
        ['Storage used', vcStats?.estimated_size_kb != null ? `${Number(vcStats.estimated_size_kb).toLocaleString(undefined, { maximumFractionDigits: 1 })} KB` : stats?.storage_estimate],
        ['Oldest version', oldestRun ? longTime(oldestRun.started_at) : null],
    ].filter(([, v]) => v != null && v !== '');

    return (
        <div style={{ marginTop: 20, borderTop: '1px solid var(--border-light)', paddingTop: 10 }}>
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: 0, border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)' }}
            >
                <ChevronRight size={14} style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />
                Storage
            </button>
            {open && (
                <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: '6px 28px' }}>
                    {rows.map(([k, v]) => (
                        <div key={k} style={{ fontSize: 12 }}>
                            <span style={{ color: 'var(--text-tertiary)' }}>{k}: </span>
                            <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{v}</span>
                        </div>
                    ))}
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', flexBasis: '100%' }}>
                        Pinned versions are kept when old versions are cleaned up.
                    </div>
                </div>
            )}
        </div>
    );
}
