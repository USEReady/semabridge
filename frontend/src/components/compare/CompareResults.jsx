import { Fragment, useMemo, useState } from 'react';
import { Search, ChevronRight } from 'lucide-react';
import { Card, Segmented } from '../history/ui';
import { CATEGORIES, buildRows, changedCount } from './compareUtils';

function SummaryCard({ label, value, color }) {
    return (
        <Card style={{ padding: '12px 14px', flex: '1 1 140px', minWidth: 0 }}>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{label}</div>
            <div style={{ fontSize: 24, fontWeight: 700, marginTop: 2, color: value ? color : 'var(--text-primary)' }}>
                {Number(value ?? 0).toLocaleString()}
            </div>
        </Card>
    );
}

function Value({ v, muted, mono }) {
    if (v === null || v === undefined || v === '') {
        return <span style={{ color: 'var(--text-tertiary)' }}>—</span>;
    }
    if (v === true || v === false) return <span>{v ? 'Yes' : 'No'}</span>;
    return (
        <span style={{
            fontFamily: mono && !muted ? 'var(--font-mono, ui-monospace, monospace)' : 'inherit',
            fontSize: 12, color: muted ? 'var(--text-tertiary)' : 'inherit',
            whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
            {String(v)}
        </span>
    );
}

const MONO_PROPS = new Set(['expression', 'type', 'sql', 'expr']);

const VERDICT_TONE = {
    EQUIVALENT: { color: 'var(--color-success)', bg: 'var(--color-success-bg)', text: 'AI: same meaning' },
    PARTIAL: { color: 'var(--color-warning)', bg: 'var(--color-warning-bg)', text: 'AI: partly the same' },
    DIFFERENT: { color: 'var(--color-error)', bg: 'var(--color-error-bg)', text: 'AI: different' },
};

function VerdictPill({ verdict, open, onToggle }) {
    if (!verdict) return null;
    const t = verdict.error
        ? { color: 'var(--text-tertiary)', bg: 'var(--bg-surface-raised)', text: 'AI check unavailable' }
        : (VERDICT_TONE[verdict.verdict] || VERDICT_TONE.DIFFERENT);
    return (
        <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            title={verdict.error || verdict.reasoning}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 2, marginTop: 4,
                fontSize: 11, padding: '1px 7px', borderRadius: 999, border: 'none', cursor: 'pointer',
                color: t.color, background: t.bg,
            }}
        >
            <ChevronRight size={11} style={{ transform: open ? 'rotate(90deg)' : 'none' }} />{t.text}
        </button>
    );
}

/**
 * Results of a two-file compare: four summary cards, then one card with a
 * Tables/Columns/Measures/Relationships switch, a Changed/All filter and a
 * Name · Property · File 1 · File 2 table.
 */
export default function CompareResults({ results, file1Name, file2Name }) {
    const [category, setCategory] = useState('metrics');
    const [scope, setScope] = useState('changed');
    const [query, setQuery] = useState('');
    const [openVerdicts, setOpenVerdicts] = useState({});

    const s = results.summary || {};
    const cat = CATEGORIES.find((c) => c.id === category);

    const rows = useMemo(() => {
        const all = buildRows(category, results[category], scope === 'all');
        const q = query.trim().toLowerCase();
        return q ? all.filter((r) => r.name.toLowerCase().includes(q)) : all;
    }, [results, category, scope, query]);

    const th = { padding: '8px 12px', fontSize: 12, fontWeight: 600, color: 'var(--text-tertiary)', textAlign: 'left', borderBottom: '1px solid var(--border-light)', whiteSpace: 'nowrap' };
    const td = { padding: '8px 12px', fontSize: 13, verticalAlign: 'top', color: 'var(--text-primary)' };

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <SummaryCard label="Identical" value={s.identical} color="var(--color-success)" />
                <SummaryCard label="Changed" value={s.modified} color="var(--color-warning)" />
                <SummaryCard label="Only in file 1" value={s.only_in_1} color="var(--accent-blue)" />
                <SummaryCard label="Only in file 2" value={s.only_in_2} color="var(--accent-purple)" />
            </div>

            <Card style={{ overflow: 'hidden' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', padding: '10px 12px', borderBottom: '1px solid var(--border-light)' }}>
                    <Segmented
                        value={category}
                        onChange={setCategory}
                        options={CATEGORIES.map((c) => ({ id: c.id, label: c.label, count: changedCount(results[c.id]) || null }))}
                    />
                    <Segmented
                        value={scope}
                        onChange={setScope}
                        options={[{ id: 'changed', label: 'Changed' }, { id: 'all', label: 'All' }]}
                    />
                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-input)' }}>
                        <Search size={13} color="var(--text-tertiary)" />
                        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`Filter ${cat.label.toLowerCase()}…`} aria-label={`Filter ${cat.label.toLowerCase()}`}
                            style={{ border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'var(--text-primary)', width: 160 }} />
                    </div>
                </div>

                {rows.length === 0 ? (
                    <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>
                        {query ? 'Nothing matches this filter.' : scope === 'changed' ? `No differences in ${cat.label.toLowerCase()}.` : `No ${cat.label.toLowerCase()} in either file.`}
                    </div>
                ) : (
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed', minWidth: 640 }}>
                            <colgroup>
                                <col style={{ width: '28%' }} /><col style={{ width: '14%' }} /><col style={{ width: '29%' }} /><col style={{ width: '29%' }} />
                            </colgroup>
                            <thead>
                                <tr>
                                    <th style={th}>{cat.singular}</th>
                                    <th style={th}>Property</th>
                                    <th style={th} title={file1Name}>File 1</th>
                                    <th style={th} title={file2Name}>File 2</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((r) => {
                                    const verdict = r.first ? r.item._llm_verdict : null;
                                    const open = Boolean(openVerdicts[r.item._id]);
                                    return (
                                        <Fragment key={r.key}>
                                            <tr style={{ borderTop: r.first ? '1px solid var(--border-light)' : 'none' }}>
                                                <td style={{ ...td, fontWeight: 600, wordBreak: 'break-word' }}>
                                                    {r.first ? r.name : ''}
                                                    {verdict && (
                                                        <div><VerdictPill verdict={verdict} open={open} onToggle={() => setOpenVerdicts((o) => ({ ...o, [r.item._id]: !o[r.item._id] }))} /></div>
                                                    )}
                                                </td>
                                                <td style={{ ...td, color: 'var(--text-secondary)' }}>{r.property}</td>
                                                <td style={td}>
                                                    <Value v={r.missing === 1 ? null : r.v1} muted={r.same || r.missing === 2} mono={MONO_PROPS.has(r.property)} />
                                                </td>
                                                <td style={td}>
                                                    <Value v={r.missing === 2 ? null : r.v2} muted={r.same || r.missing === 1} mono={MONO_PROPS.has(r.property)} />
                                                </td>
                                            </tr>
                                            {verdict && open && (
                                                <tr>
                                                    <td colSpan={4} style={{ padding: '0 12px 10px' }}>
                                                        <div style={{ fontSize: 12, color: 'var(--text-secondary)', padding: '8px 10px', borderRadius: 6, background: 'var(--bg-surface-raised)' }}>
                                                            {verdict.error || verdict.reasoning}
                                                            {Array.isArray(verdict.key_differences) && verdict.key_differences.length > 0 && (
                                                                <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                                                                    {verdict.key_differences.map((d, i) => <li key={i}>{d}</li>)}
                                                                </ul>
                                                            )}
                                                            {verdict.model_used && <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-tertiary)' }}>via {verdict.model_used}</div>}
                                                        </div>
                                                    </td>
                                                </tr>
                                            )}
                                        </Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </Card>
        </div>
    );
}
