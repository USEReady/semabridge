import {
    X, Box, Database, BarChart3,
    FileCode, Clock, HardDrive,
} from 'lucide-react';

/**
 * DetailPanel — right sidebar showing:
 *   • File preview (syntax-highlighted raw content) when a file is selected.
 *   • Node detail card when a graph node is clicked.
 */
export default function DetailPanel({ filePreview, selectedNode, onClose }) {

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
                        <FileCode size={14} style={{ color: '#818CF8' }} />
                        <span style={{ fontSize: 12, fontWeight: 700 }}>
                            {filePreview.name}
                        </span>
                    </div>
                    <button onClick={onClose} style={closeBtnStyle}>
                        <X size={14} />
                    </button>
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

    // ── Node detail ─────────────────────────────
    if (selectedNode) {
        const nodeType = selectedNode.nodeType;
        return (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                {/* Header */}
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--border-color)',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        {nodeType === 'model' && <Box size={14} style={{ color: '#3B82F6' }} />}
                        {nodeType === 'table' && <Database size={14} style={{ color: '#22C55E' }} />}
                        {nodeType === 'measure' && <BarChart3 size={14} style={{ color: '#EAB308' }} />}
                        <span style={{ fontSize: 13, fontWeight: 700 }}>
                            {selectedNode.label}
                        </span>
                    </div>
                    <button onClick={onClose} style={closeBtnStyle}>
                        <X size={14} />
                    </button>
                </div>

                <div style={{ flex: 1, overflow: 'auto', padding: 14 }}>
                    {/* ── Model detail ── */}
                    {nodeType === 'model' && (
                        <>
                            <Section title="Model Info">
                                <Row label="Model ID" value={selectedNode.model_id} />
                                <Row label="Workspace" value={selectedNode.workspace_id || 'local'} />
                                <Row label="Status" value={
                                    <StatusBadge status={selectedNode.status} />
                                } />
                                {selectedNode.description && (
                                    <Row label="Description" value={selectedNode.description} />
                                )}
                            </Section>

                            {selectedNode.source_tables?.length > 0 && (
                                <Section title="Source Tables">
                                    {selectedNode.source_tables.map((t, i) => (
                                        <div key={i} style={tableCardStyle}>
                                            <Database size={12} style={{ color: '#22C55E', flexShrink: 0 }} />
                                            <div>
                                                <div style={{ fontWeight: 600, fontSize: 12 }}>{t.table}</div>
                                                <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
                                                    {t.schema} · {t.source_type}
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </Section>
                            )}

                            {selectedNode.measures?.length > 0 && (
                                <Section title="Measures / Metrics">
                                    {selectedNode.measures.map((m, i) => (
                                        <div key={i} style={tableCardStyle}>
                                            <BarChart3 size={12} style={{ color: '#EAB308', flexShrink: 0 }} />
                                            <div>
                                                <div style={{ fontWeight: 600, fontSize: 12 }}>{m.name}</div>
                                                <code style={{ fontSize: 10, color: '#D4A017' }}>
                                                    {m.expression}
                                                </code>
                                            </div>
                                        </div>
                                    ))}
                                </Section>
                            )}
                        </>
                    )}

                    {/* ── Table detail ── */}
                    {nodeType === 'table' && (
                        <>
                            <Section title="Table Info">
                                <Row label="Table" value={selectedNode.table_name} />
                                <Row label="Schema" value={selectedNode.schema || 'PUBLIC'} />
                                <Row label="Source" value={selectedNode.source_type || 'snowflake'} />
                                <Row label="Origin" value={
                                    <OriginBadge
                                        tableName={selectedNode.table_name || selectedNode.label}
                                        schema={selectedNode.schema}
                                    />
                                } />
                                <Row label="Status" value={
                                    <StatusBadge status={selectedNode.status} />
                                } />
                            </Section>

                            {selectedNode.columns?.length > 0 && (
                                <Section title="Columns">
                                    <div style={{
                                        display: 'grid',
                                        gridTemplateColumns: '1fr auto',
                                        gap: '2px 8px',
                                        fontSize: 11,
                                    }}>
                                        <div style={{ fontWeight: 700, fontSize: 10, color: 'var(--text-tertiary)' }}>Name</div>
                                        <div style={{ fontWeight: 700, fontSize: 10, color: 'var(--text-tertiary)' }}>Type</div>
                                        {selectedNode.columns.map((col, i) => (
                                            <>
                                                <span key={`n-${i}`} style={{ color: 'var(--text-primary)' }}>{col.name}</span>
                                                <span key={`t-${i}`} style={{ color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: 10 }}>{col.data_type}</span>
                                            </>
                                        ))}
                                    </div>
                                </Section>
                            )}
                        </>
                    )}

                    {/* ── Measure detail ── */}
                    {nodeType === 'measure' && (
                        <Section title="Measure Detail">
                            <Row label="Name" value={selectedNode.label} />
                            <Row label="Parent Model" value={selectedNode.parent_model} />
                            <Row label="Expression" value={
                                <code style={{ fontSize: 11, color: '#D4A017', fontFamily: 'monospace' }}>
                                    {selectedNode.expression}
                                </code>
                            } />
                            {selectedNode.data_type && (
                                <Row label="Format/Type" value={selectedNode.data_type} />
                            )}
                        </Section>
                    )}
                </div>
            </div>
        );
    }

    return null;
}

// ── Shared sub-components ──

function Section({ title, children }) {
    return (
        <div style={{ marginBottom: 16 }}>
            <div style={{
                fontSize: 10, fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '.06em',
                color: 'var(--text-tertiary)',
                marginBottom: 8,
            }}>
                {title}
            </div>
            {children}
        </div>
    );
}

function Row({ label, value }) {
    return (
        <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
            padding: '4px 0',
            borderBottom: '1px solid var(--border-color)',
            fontSize: 12,
        }}>
            <span style={{ color: 'var(--text-secondary)', fontWeight: 500 }}>{label}</span>
            <span style={{ color: 'var(--text-primary)', textAlign: 'right', maxWidth: '60%' }}>
                {value}
            </span>
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
            color: isSystem ? '#F59E0B' : '#818CF8',
            border: `1px solid ${isSystem ? 'rgba(245,158,11,.35)' : 'rgba(99,102,241,.35)'}`,
        }}>
            {isSystem ? 'System Generated' : 'Original Schema'}
        </span>
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
