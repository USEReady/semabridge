import { useEffect, useState } from 'react';
import { Loader2, RotateCcw } from 'lucide-react';
import { api } from '../../utils/api';
import { Btn } from './ui';

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/**
 * The single confirmation step for "Restore this version": loads the dry-run
 * impact (current state vs. the chosen version) and states it in plain words.
 */
export default function RestoreDialog({ projectId, projectName, snapshotId, versionLabel, busy, onConfirm, onCancel }) {
    const [preview, setPreview] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    useEffect(() => {
        // Mounted fresh for each restore, so loading starts as true.
        let cancelled = false;
        api.previewRestore(projectId, snapshotId)
            .then((data) => { if (!cancelled) setPreview(data); })
            .catch((e) => { if (!cancelled) setError(e?.message || 'Could not work out the impact of this restore.'); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [projectId, snapshotId]);

    useEffect(() => {
        const onKey = (e) => { if (e.key === 'Escape' && !busy) onCancel(); };
        document.addEventListener('keydown', onKey);
        return () => document.removeEventListener('keydown', onKey);
    }, [busy, onCancel]);

    const s = preview?.impact_summary;
    const affected = (preview?.models || []).filter((m) => m.status !== 'UNCHANGED');

    // previewRestore diffs CURRENT -> TARGET, so "ADDED" means the model
    // comes back with the restore and "REMOVED" means it goes away.
    const sentences = [];
    if (s) {
        if (s.added) sentences.push(`${plural(s.added, 'model comes', 'models come')} back`);
        if (s.modified) sentences.push(`${plural(s.modified, 'model is', 'models are')} rolled back to the earlier definition`);
        if (s.removed) sentences.push(`${plural(s.removed, 'model', 'models')} added since then ${s.removed === 1 ? 'is' : 'are'} removed`);
        if (s.unchanged) sentences.push(`${plural(s.unchanged, 'model stays', 'models stay')} the same`);
    }

    return (
        <div role="dialog" aria-modal="true" aria-labelledby="restore-title" style={{ position: 'fixed', inset: 0, zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
            <div onClick={() => !busy && onCancel()} style={{ position: 'absolute', inset: 0, background: 'var(--bg-backdrop, rgba(0,0,0,0.5))' }} />
            <div style={{
                position: 'relative', width: '100%', maxWidth: 520,
                background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 12,
                boxShadow: 'var(--shadow-lg)', padding: 20,
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
                    <RotateCcw size={18} color="var(--color-error)" />
                    <h3 id="restore-title" style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                        Restore {versionLabel}?
                    </h3>
                </div>

                {loading ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-tertiary)', padding: '12px 0' }}>
                        <Loader2 size={14} className="animate-spin" /> Checking what would change…
                    </div>
                ) : error ? (
                    <p style={{ fontSize: 13, color: 'var(--color-warning)', margin: '0 0 12px' }}>
                        {error} You can still restore, but the impact could not be shown.
                    </p>
                ) : (
                    <>
                        <p style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.55, margin: '0 0 10px' }}>
                            This replaces the current model of <strong style={{ color: 'var(--text-primary)' }}>{projectName || 'this project'}</strong> with {versionLabel} and pushes it to the target.
                            {sentences.length > 0 ? ` ${sentences.join('; ')}.` : ' Nothing would change — the current model already matches this version.'}
                        </p>
                        {affected.length > 0 && (
                            <ul style={{
                                listStyle: 'none', margin: '0 0 10px', padding: '6px 10px', maxHeight: 160, overflowY: 'auto',
                                border: '1px solid var(--border-light)', borderRadius: 8, background: 'var(--bg-surface-raised)',
                            }}>
                                {affected.map((m) => (
                                    <li key={`${m.status}:${m.name}`} style={{ fontSize: 12, padding: '2px 0', color: 'var(--text-primary)' }}>
                                        <span style={{ display: 'inline-block', width: 14, fontFamily: 'monospace', color: m.status === 'ADDED' ? 'var(--color-success)' : m.status === 'REMOVED' ? 'var(--color-error)' : 'var(--color-warning)' }}>
                                            {m.status === 'ADDED' ? '+' : m.status === 'REMOVED' ? '−' : '~'}
                                        </span>
                                        {m.name}
                                    </li>
                                ))}
                            </ul>
                        )}
                        <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '0 0 4px' }}>
                            The restore runs like a normal run, so it is saved as a new version you can undo from here.
                        </p>
                    </>
                )}

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                    <Btn onClick={onCancel} disabled={busy}>Cancel</Btn>
                    <Btn variant="dangerSolid" onClick={onConfirm} disabled={busy || loading}>
                        {busy ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />} Restore this version
                    </Btn>
                </div>
            </div>
        </div>
    );
}
