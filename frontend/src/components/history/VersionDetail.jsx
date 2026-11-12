import { useMemo, useState } from 'react';
import { Download, Pin, RotateCcw, Tag, Loader2, AlertTriangle } from 'lucide-react';
import { Btn, Card, MenuButton, Pill, Segmented } from './ui';
import { countDiff, longTime, modelWord, statusLabel, triggerLabel } from './historyUtils';

const ROW_STYLE = {
    ADDED: { sign: '+', color: 'var(--color-success)', word: 'new model' },
    MODIFIED: { sign: '~', color: 'var(--color-warning)', word: 'changed' },
    REMOVED: { sign: '−', color: 'var(--color-error)', word: 'model removed' },
};

const FILTERS = [
    { id: 'ALL', label: 'All' },
    { id: 'ADDED', label: 'Added' },
    { id: 'MODIFIED', label: 'Changed' },
    { id: 'REMOVED', label: 'Removed' },
];

function CountCard({ label, value, color }) {
    return (
        <Card style={{ padding: '12px 14px', flex: '1 1 120px', minWidth: 0 }}>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{label}</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, marginTop: 4 }}>
                <span style={{ fontSize: 22, fontWeight: 700, color: value ? color : 'var(--text-primary)' }}>{value ?? '—'}</span>
                {value != null && <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{modelWord(value)}</span>}
            </div>
        </Card>
    );
}

/**
 * Right column of the Versions tab: the selected version, its actions, the
 * four count cards and the "Changes vs <base>" list.
 */
export default function VersionDetail({
    run, runNumber, pinned, tag,
    compareItems, baseLabel,
    diff, onModelClick,
    onDownload, modelReports, onDownloadModel,
    onTogglePin, onAddTag, onRestore, canRestore,
}) {
    const [filter, setFilter] = useState('ALL');
    const [tagDraft, setTagDraft] = useState(null);

    const counts = useMemo(() => (diff?.models ? countDiff(diff.models) : null), [diff]);
    const changed = useMemo(
        () => (diff?.models || []).filter((m) => m.status !== 'UNCHANGED' && (filter === 'ALL' || m.status === filter)),
        [diff, filter],
    );

    const st = statusLabel(run.status);
    const hasPerFileReports = Array.isArray(modelReports) && modelReports.length > 0;

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
            {/* Title + actions */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'flex-start', justifyContent: 'space-between' }}>
                <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>Run #{runNumber}</h2>
                        <Pill tone={st.tone}>{st.text}</Pill>
                        {(pinned || tag) && (
                            <Pill tone="warning">{pinned && <Pin size={10} />}{tag || 'Pinned'}</Pill>
                        )}
                    </div>
                    <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
                        {longTime(run.started_at)} · {triggerLabel(run)}
                        <span style={{ color: 'var(--text-tertiary)', fontFamily: 'monospace', fontSize: 11, marginLeft: 8 }}>{run.run_id}</span>
                    </div>
                    {tagDraft == null ? (
                        <button
                            type="button"
                            onClick={() => setTagDraft(tag || '')}
                            style={{ marginTop: 4, padding: 0, border: 'none', background: 'none', cursor: 'pointer', fontSize: 12, color: 'var(--accent-blue)', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                        >
                            <Tag size={11} /> {tag ? 'Edit label' : 'Add label'}
                        </button>
                    ) : (
                        <form
                            onSubmit={(e) => { e.preventDefault(); if (tagDraft.trim()) onAddTag(tagDraft.trim()); setTagDraft(null); }}
                            style={{ display: 'flex', gap: 6, marginTop: 6 }}
                        >
                            <input
                                autoFocus
                                value={tagDraft}
                                onChange={(e) => setTagDraft(e.target.value)}
                                placeholder="e.g. Q3 baseline"
                                aria-label="Version label"
                                style={{ padding: '4px 8px', fontSize: 12, borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-input)', color: 'var(--text-primary)' }}
                            />
                            <Btn type="submit" style={{ padding: '3px 10px', fontSize: 12 }}>Save</Btn>
                            <Btn variant="ghost" onClick={() => setTagDraft(null)} style={{ padding: '3px 8px', fontSize: 12 }}>Cancel</Btn>
                        </form>
                    )}
                </div>

                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <MenuButton label="Compare with…" items={compareItems} />
                    {hasPerFileReports ? (
                        <MenuButton
                            label="Download"
                            icon={<Download size={14} />}
                            items={modelReports.map((entry) => ({
                                key: entry.model,
                                label: entry.model,
                                hint: 'Run report for this file',
                                onClick: () => onDownloadModel(entry.model),
                            }))}
                        />
                    ) : (
                        <Btn onClick={onDownload} title="Download the run report (available for every run, success or failure)">
                            <Download size={14} /> Download
                        </Btn>
                    )}
                    <Btn onClick={onTogglePin} aria-pressed={pinned} title={pinned ? 'Unpin: allow this version to be cleaned up' : 'Pin: keep this version when old versions are cleaned up'}>
                        <Pin size={14} style={pinned ? { fill: 'currentColor' } : undefined} /> {pinned ? 'Unpin' : 'Pin'}
                    </Btn>
                    <Btn variant="danger" onClick={onRestore} disabled={!canRestore} title={canRestore ? undefined : 'This version has no saved model to restore'}>
                        <RotateCcw size={14} /> Restore this version
                    </Btn>
                </div>
            </div>

            {/* Count cards */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <CountCard label="Added" value={counts?.added} color="var(--color-success)" />
                <CountCard label="Changed" value={counts?.changed} color="var(--color-warning)" />
                <CountCard label="Removed" value={counts?.removed} color="var(--color-error)" />
                <CountCard label="Unchanged" value={counts?.unchanged} color="var(--text-primary)" />
            </div>

            {/* Changes list */}
            <Card>
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                    padding: '10px 14px', borderBottom: '1px solid var(--border-light)',
                }}>
                    <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Changes vs {baseLabel || '—'}</span>
                    <div style={{ marginLeft: 'auto' }}>
                        <Segmented options={FILTERS} value={filter} onChange={setFilter} />
                    </div>
                </div>

                {diff?.loading && (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Loader2 size={14} className="animate-spin" /> Working out what changed…
                    </div>
                )}
                {diff?.error && (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--color-warning)', display: 'flex', alignItems: 'center', gap: 8 }}>
                        <AlertTriangle size={14} /> {diff.error}
                    </div>
                )}
                {!diff && (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>
                        There is no earlier version to compare with. Pick one from “Compare with…”.
                    </div>
                )}
                {diff?.models && !diff.loading && changed.length === 0 && (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>
                        {filter === 'ALL' ? 'No changes — the models are identical.' : 'Nothing in this category.'}
                    </div>
                )}
                {diff?.models && !diff.loading && changed.map((m) => {
                    const s = ROW_STYLE[m.status] || ROW_STYLE.MODIFIED;
                    return (
                        <button
                            key={`${m.status}:${m.name}`}
                            type="button"
                            onClick={() => onModelClick?.(m.name)}
                            title="Show this model's history"
                            style={{
                                display: 'flex', alignItems: 'baseline', gap: 10, width: '100%', textAlign: 'left',
                                padding: '8px 14px', border: 'none', borderTop: '1px solid var(--border-light)',
                                background: 'transparent', cursor: 'pointer',
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                        >
                            <span style={{ width: 12, fontFamily: 'monospace', fontWeight: 700, color: s.color }}>{s.sign}</span>
                            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', wordBreak: 'break-word' }}>{m.name}</span>
                            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{s.word}</span>
                        </button>
                    );
                })}
            </Card>
        </div>
    );
}
