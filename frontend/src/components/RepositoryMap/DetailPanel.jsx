import {
    X, Box, Database, BarChart3,
    FileCode, Clock, HardDrive, Link2,
} from 'lucide-react';
import { useState } from 'react';

/**
 * DetailPanel — right sidebar showing:
 *   • File preview (syntax-highlighted raw content) when a file is selected.
 *   • Node detail with Columns | Measures | Lineage tabs when a graph node is clicked.
 */
export default function DetailPanel({ filePreview, selectedNode, workspaceNames = {}, onClose }) {
    // Active tab; null = default for the node type. Parent re-keys this panel per node.
    const [tab, setTab] = useState(null);

    // ── File preview ────────────────────────────
    if (filePreview) {
        return (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                {/* Header */}
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--border-color)',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <FileCode size={14} style={{ color: 'var(--accent-blue)' }} />
                        <span style={{ fontSize: 12, fontWeight: 700 }}>
                            {filePreview.name}
                        </span>
                    </div>
                    <button onClick={onClose} style={closeBtnStyle}>
                        <X size={14} />
                    </button>
                </div>

                <div style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '8px 14px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    color: 'var(--text-secondary)',
                    fontSize: 10,
                    fontWeight: 600,
                }}>
                    <Link2 size={11} style={{ color: 'var(--accent-blue)' }} />
                    Inspector Source: Snapshot Explorer File Selection
                </div>

                {/* Meta */}
                <div style={{
                    display: 'flex', gap: 12, padding: '6px 14px',
                    fontSize: 10, color: 'var(--text-tertiary)',
                    borderBottom: '1px solid var(--border-color)',
                }}>
                    {filePreview.size != null && (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                            <HardDrive size={10} />
                            {(filePreview.size / 1024).toFixed(1)} KB
                        </span>
                    )}
                    {filePreview.modified && (
                        <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                            <Clock size={10} />
                            {new Date(filePreview.modified).toLocaleString()}
                        </span>
                    )}
                </div>

                {/* Code */}
                <div style={{
                    flex: 1, overflow: 'auto',
                    padding: '10px 14px',
                }}>
                    <pre style={{
                        margin: 0,
                        fontSize: 11.5,
                        lineHeight: 1.55,
                        fontFamily: '"Fira Code", "JetBrains Mono", "Cascadia Code", Consolas, monospace',
                        color: 'var(--text-primary)',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        background: 'var(--bg-app)',
                        padding: 12,
                        borderRadius: 8,
                        border: '1px solid var(--border-color)',
                    }}>
                        {filePreview.content}
                    </pre>
                </div>
            </div>
        );
    }

    // ── Node detail: compact header + Columns | Measures | Lineage tabs ──
    if (selectedNode) {
        const rawNodeType = String(selectedNode.nodeType || '').toLowerCase();
        const nodeType = ['model', 'table', 'measure'].includes(rawNodeType) ? rawNodeType : 'model';
        const nodeTypeLabel = nodeType.charAt(0).toUpperCase() + nodeType.slice(1);
        // Prefer the pre-resolved name in node data, fall back to workspaceNames map, then raw GUID
        const resolvedWsName = selectedNode.workspace_name || workspaceNames[selectedNode.workspace_id] || selectedNode.workspace_id || 'local';
        const isBroken = selectedNode.status === 'broken';
        const TypeIcon = nodeType === 'model' ? Box : nodeType === 'table' ? Database : BarChart3;
        const typeTone = nodeType === 'model' ? 'var(--accent-blue)' : nodeType === 'table' ? 'var(--color-success)' : 'var(--color-warning)';

        const columns = Array.isArray(selectedNode.columns) ? selectedNode.columns : [];
        const measures = Array.isArray(selectedNode.measures) ? selectedNode.measures : [];
        const sourceTables = Array.isArray(selectedNode.source_tables) ? selectedNode.source_tables : [];

        const subtitleParts = [];
        if (nodeType === 'table') {
            subtitleParts.push(`${columns.length} col${columns.length === 1 ? '' : 's'}`);
            if (measures.length) subtitleParts.push(`${measures.length} measure${measures.length === 1 ? '' : 's'}`);
        } else if (nodeType === 'model') {
            subtitleParts.push(`${sourceTables.length} table${sourceTables.length === 1 ? '' : 's'}`);
            subtitleParts.push(`${measures.length} measure${measures.length === 1 ? '' : 's'}`);
        } else if (selectedNode.parent_model) {
            subtitleParts.push(`in ${selectedNode.parent_model}`);
        }

        const defaultTab = nodeType === 'measure' ? 'measures' : 'columns';
        const activeTab = tab || defaultTab;
        const tabs = [
            { id: 'columns', label: 'Columns' },
            { id: 'measures', label: 'Measures' },
            { id: 'lineage', label: 'Lineage' },
        ];

        return (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
                {/* Header */}
                <div style={{
                    display: 'flex', alignItems: 'flex-start', gap: 10,
                    padding: '14px 16px 10px',
                    borderBottom: '1px solid var(--border-main)',
                }}>
                    <div style={{
                        width: 30, height: 30, borderRadius: 8, flexShrink: 0,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
                    }}>
                        <TypeIcon size={15} style={{ color: typeTone }} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                            fontSize: 14, fontWeight: 700, color: 'var(--text-primary)',
                            fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                            wordBreak: 'break-word', lineHeight: 1.3,
                        }}>
                            {selectedNode.label}
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3, fontSize: 11.5, color: 'var(--text-tertiary)', flexWrap: 'wrap' }}>
                            <span>{nodeTypeLabel}</span>
                            {subtitleParts.map(p => <span key={p}>· {p}</span>)}
                            {isBroken && (
                                <span style={{ color: 'var(--color-error)', fontWeight: 600 }}>· broken reference</span>
                            )}
                        </div>
                    </div>
                    <button onClick={onClose} style={closeBtnStyle} title="Close" aria-label="Close details">
                        <X size={16} />
                    </button>
                </div>

                {/* Tabs */}
                <div role="tablist" style={{ display: 'flex', gap: 2, padding: '0 12px', borderBottom: '1px solid var(--border-main)' }}>
                    {tabs.map(t => (
                        <button
                            key={t.id}
                            role="tab"
                            aria-selected={activeTab === t.id}
                            onClick={() => setTab(t.id)}
                            style={{
                                border: 'none', background: 'transparent', cursor: 'pointer',
                                padding: '9px 10px', fontSize: 12.5, fontWeight: 600,
                                color: activeTab === t.id ? 'var(--text-primary)' : 'var(--text-tertiary)',
                                borderBottom: `2px solid ${activeTab === t.id ? 'var(--accent-blue)' : 'transparent'}`,
                                marginBottom: -1,
                            }}
                        >
                            {t.label}
                        </button>
                    ))}
                </div>

                <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px 16px' }}>
                    {/* ── Columns ── */}
                    {activeTab === 'columns' && (
                        <>
                            {nodeType === 'table' && (columns.length > 0 ? (
                                <ColumnTable columns={columns} />
                            ) : (
                                <EmptyHint text="No columns are available for this table in the current snapshot." />
                            ))}
                            {nodeType === 'model' && (sourceTables.length > 0 ? (
                                sourceTables.map((t, i) => (
                                    <Section key={`${t.schema}.${t.table}.${i}`} title={`${t.schema ? `${t.schema}.` : ''}${t.table}`}>
                                        {t.columns?.length > 0
                                            ? <ColumnTable columns={t.columns} />
                                            : <EmptyHint text="No columns recorded for this table." />}
                                    </Section>
                                ))
                            ) : (
                                <EmptyHint text="No source tables were detected for this model." />
                            ))}
                            {nodeType === 'measure' && (
                                <EmptyHint text="Measures don't have columns. See the Measures tab for the formula." />
                            )}
                        </>
                    )}

                    {/* ── Measures ── */}
                    {activeTab === 'measures' && (
                        <>
                            {nodeType === 'measure' ? (
                                <>
                                    <Section title="Formula">
                                        <pre style={{
                                            margin: 0, padding: '10px 12px',
                                            background: 'var(--bg-app)', borderRadius: 6,
                                            border: '1px solid var(--border-main)',
                                            fontSize: 11.5, fontFamily: 'var(--font-mono, ui-monospace, monospace)',
                                            color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                                            maxHeight: 320, overflow: 'auto',
                                        }}>
                                            {selectedNode.expression || 'No expression found for this measure.'}
                                        </pre>
                                    </Section>
                                    {selectedNode.data_type && <Row label="Data type" value={selectedNode.data_type} />}
                                </>
                            ) : measures.length > 0 ? (
                                measures.map((m, i) => (
                                    <div key={`${m.name || m.label}-${i}`} style={tableCardStyle}>
                                        <BarChart3 size={12} style={{ color: 'var(--color-warning)', flexShrink: 0, marginTop: 2 }} />
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontWeight: 600, fontSize: 12 }}>{m.name || m.label}</div>
                                            {m.expression && (
                                                <code style={{ display: 'block', marginTop: 2, fontSize: 10.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>
                                                    {m.expression}
                                                </code>
                                            )}
                                        </div>
                                    </div>
                                ))
                            ) : (
                                <EmptyHint text={`No measures are attached to this ${nodeType} yet.`} />
                            )}
                        </>
                    )}

                    {/* ── Lineage ── */}
                    {activeTab === 'lineage' && (
                        <>
                            {nodeType === 'table' && (
                                <>
                                    <Row label="Table" value={selectedNode.table_name || selectedNode.label} code />
                                    <Row label="Schema" value={selectedNode.schema || 'PUBLIC'} />
                                    <Row label="Comes from" value={selectedNode.source_type || '—'} />
                                    {selectedNode.model_id && <Row label="Model" value={selectedNode.model_label || selectedNode.model_name || selectedNode.model_id} />}
                                    <Row label="Origin" value={<OriginBadge tableName={selectedNode.table_name || selectedNode.label} schema={selectedNode.schema} />} />
                                    <Row label="Status" value={<StatusBadge status={selectedNode.status} />} />
                                </>
                            )}
                            {nodeType === 'model' && (
                                <>
                                    <Row label="Model ID" value={selectedNode.model_id} code />
                                    <Row label="Workspace" value={resolvedWsName !== 'local' ? resolvedWsName : '—'} />
                                    <Row label="Source platform" value={sourceTables[0]?.source_type || selectedNode.source_type || '—'} />
                                    <Row label="Status" value={<StatusBadge status={selectedNode.status} />} />
                                    {selectedNode.description && <Row label="Description" value={selectedNode.description} />}
                                    {sourceTables.length > 0 && (
                                        <div style={{ marginTop: 14 }}>
                                            <Section title="Built from">
                                                {sourceTables.map((t, i) => (
                                                    <div key={`${t.table}-${i}`} style={tableCardStyle}>
                                                        <Database size={12} style={{ color: 'var(--color-success)', flexShrink: 0, marginTop: 2 }} />
                                                        <div style={{ flex: 1, minWidth: 0 }}>
                                                            <div style={{ fontWeight: 600, fontSize: 12 }}>{t.table}</div>
                                                            <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>
                                                                {[t.schema, t.source_type].filter(Boolean).join(' · ')}
                                                            </div>
                                                        </div>
                                                    </div>
                                                ))}
                                            </Section>
                                        </div>
                                    )}
                                </>
                            )}
                            {nodeType === 'measure' && (
                                <>
                                    <Row label="Measure" value={selectedNode.label} code />
                                    <Row label="Model" value={selectedNode.parent_model || '—'} />
                                    {selectedNode.dataset && <Row label="Table" value={selectedNode.dataset} />}
                                    <Row label="Status" value={<StatusBadge status={selectedNode.status} />} />
                                </>
                            )}
                        </>
                    )}
                </div>
            </div>
        );
    }

    return null;
}

// ── Shared sub-components ──

function ColumnTable({ columns }) {
    return (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
                <tr>
                    <th style={colThStyle}>Column</th>
                    <th style={{ ...colThStyle, textAlign: 'right' }}>Type</th>
                </tr>
            </thead>
            <tbody>
                {columns.map((col, i) => {
                    const colName = typeof col === 'string'
                        ? col
                        : (col.unique_name || col.label || col.name || col.column_name || '');
                    const colType = typeof col === 'string' ? '' : (col.data_type || col.type || '');
                    return (
                        <tr key={(typeof col === 'object' && col.id) || colName || i}>
                            <td style={{ ...colTdStyle, color: 'var(--text-primary)', wordBreak: 'break-word' }}>{colName}</td>
                            <td style={{ ...colTdStyle, textAlign: 'right', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 11 }}>{colType}</td>
                        </tr>
                    );
                })}
            </tbody>
        </table>
    );
}

const colThStyle = {
    textAlign: 'left', padding: '6px 0', fontSize: 10.5, fontWeight: 700,
    textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--text-tertiary)',
    borderBottom: '1px solid var(--border-main)',
};
const colTdStyle = { padding: '6px 0', borderBottom: '1px solid var(--border-light, var(--border-main))' };

function Section({ title, children }) {
    return (
        <div style={{ marginBottom: 14 }}>
            <div style={{
                fontSize: 9, fontWeight: 800,
                textTransform: 'uppercase',
                letterSpacing: '.06em',
                color: 'var(--text-tertiary)',
                marginBottom: 8,
                padding: '0 0 4px 0',
            }}>
                {title}
            </div>
            {children}
        </div>
    );
}

function Row({ label, value, code }) {
    return (
        <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
            padding: '8px 0',
            borderBottom: '1px solid var(--border-color)',
            fontSize: 12,
            gap: 8,
        }}>
            <span style={{ color: 'var(--text-secondary)', fontWeight: 600, flexShrink: 0 }}>{label}</span>
            <div style={{
                color: 'var(--text-primary)',
                textAlign: 'right',
                maxWidth: '60%',
                overflow: 'auto',
                ...(code && {
                    fontFamily: 'monospace',
                    fontSize: 11,
                    background: 'var(--bg-app)',
                    padding: '4px 6px',
                    borderRadius: 4,
                    border: '1px solid var(--border-color)',
                    color: 'var(--accent-blue)',
                    letterSpacing: '.2px',
                })
            }}>
                {value}
            </div>
        </div>
    );
}

function StatusBadge({ status }) {
    const isBroken = status === 'broken';
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            padding: '2px 8px',
            borderRadius: 10,
            fontSize: 10, fontWeight: 700,
            background: isBroken ? 'rgba(239,68,68,.15)' : 'rgba(34,197,94,.15)',
            color: isBroken ? '#EF4444' : '#22C55E',
            border: `1px solid ${isBroken ? 'rgba(239,68,68,.3)' : 'rgba(34,197,94,.3)'}`,
        }}>
            {isBroken ? '✕ Broken' : '✓ Valid'}
        </span>
    );
}

function OriginBadge({ tableName, schema }) {
    const fullName = `${schema ? `${schema}.` : ''}${tableName || ''}`.toLowerCase();
    const isSystem =
        fullName.startsWith('information_schema.') ||
        fullName.startsWith('pg_') ||
        fullName.startsWith('sqlite_') ||
        fullName.startsWith('duckdb_') ||
        fullName.startsWith('sys.') ||
        fullName.startsWith('__');

    return (
        <span style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 8px',
            borderRadius: 10,
            fontSize: 10,
            fontWeight: 700,
            background: isSystem ? 'rgba(245,158,11,.15)' : 'rgba(99,102,241,.15)',
            color: isSystem ? '#F59E0B' : 'var(--accent-blue)',
            border: `1px solid ${isSystem ? 'rgba(245,158,11,.35)' : 'rgba(99,102,241,.35)'}`,
        }}>
            {isSystem ? 'System Generated' : 'Original Schema'}
        </span>
    );
}


function EmptyHint({ text }) {
    return (
        <div style={{
            border: '1px dashed var(--border-color)',
            borderRadius: 8,
            padding: '10px 12px',
            fontSize: 11,
            color: 'var(--text-tertiary)',
            background: 'var(--bg-app)',
        }}>
            {text}
        </div>
    );
}

// ── styles ──
const closeBtnStyle = {
    border: 'none', background: 'transparent', cursor: 'pointer',
    color: 'var(--text-tertiary)', display: 'flex', padding: 2,
};
const tableCardStyle = {
    display: 'flex', alignItems: 'flex-start', gap: 8,
    padding: '8px 10px',
    borderRadius: 6,
    background: 'var(--bg-app)',
    border: '1px solid var(--border-color)',
    marginBottom: 4,
};
