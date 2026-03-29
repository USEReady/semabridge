import {
    X, Box, Database, BarChart3,
    FileCode, Clock, HardDrive,
    ChevronDown, Info, Code2, Link2,
} from 'lucide-react';
import { useState } from 'react';

/**
 * DetailPanel — right sidebar showing:
 *   • File preview (syntax-highlighted raw content) when a file is selected.
 *   • Structured node detail (HLD & LLD) when a graph node is clicked.
 */
export default function DetailPanel({ filePreview, selectedNode, onClose }) {
    const [expandedSections, setExpandedSections] = useState({});

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

                <div style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '8px 14px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    color: 'var(--text-secondary)',
                    fontSize: 10,
                    fontWeight: 600,
                }}>
                    <Link2 size={11} style={{ color: '#818CF8' }} />
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

    // ── Node detail ─────────────────────────────
    if (selectedNode) {
        const rawNodeType = String(selectedNode.nodeType || '').toLowerCase();
        const nodeType = ['model', 'table', 'measure'].includes(rawNodeType) ? rawNodeType : 'model';
        const nodeTypeLabel = nodeType.charAt(0).toUpperCase() + nodeType.slice(1);
        const inspectionSource = selectedNode.parent_model ? 'Dependency Graph → Table/Measure' : 'Dependency Graph → Model';
        const contextLabel = nodeType === 'table'
            ? `${selectedNode.schema || 'PUBLIC'}.${selectedNode.table_name || selectedNode.label || 'table'}`
            : nodeType === 'measure'
                ? `${selectedNode.parent_model || 'Model'} :: ${selectedNode.label || 'Measure'}`
                : `${selectedNode.workspace_id || 'local'} :: ${selectedNode.model_id || selectedNode.label || 'model'}`;
        const quickStatus = selectedNode.status === 'broken' ? 'Needs Attention' : 'Healthy';
        
        const toggleSection = (section) => {
            setExpandedSections(prev => ({
                ...prev,
                [section]: !prev[section]
            }));
        };

        return (
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
                {/* ═══ HEADER: What You're Inspecting ═══ */}
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '16px 18px',
                    borderBottom: '2.5px solid var(--border-color)',
                    background:
                        nodeType === 'model'
                            ? 'linear-gradient(135deg, #3b82f6 0%, #1E293B 100%)'
                            : nodeType === 'table'
                                ? 'linear-gradient(135deg, #134e2e 0%, #0f172a 100%)'
                                : 'linear-gradient(135deg, #facc15 0%, #1E293B 100%)',
                    borderTopLeftRadius: 18,
                    borderTopRightRadius: 18,
                    boxShadow: nodeType === 'model'
                        ? '0 0 16px 0 #60a5fa33'
                        : nodeType === 'table'
                            ? '0 0 16px 0 #22c55e33'
                            : '0 0 16px 0 #fde04733',
                    transition: 'box-shadow .18s, border-color .18s',
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14, flex: 1, minWidth: 0 }}>
                        <div style={{
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            width: 44, height: 44, borderRadius: 14,
                            background: nodeType === 'model' ? '#1e293b' : nodeType === 'table' ? '#0f172a' : '#1e293b',
                            border: nodeType === 'model' ? '2.5px solid #3B82F6' : nodeType === 'table' ? '2.5px solid #22C55E' : '2.5px solid #EAB308',
                            boxShadow: nodeType === 'model' ? '0 0 8px #3B82F6' : nodeType === 'table' ? '0 0 8px #22C55E' : '0 0 8px #EAB308',
                            flexShrink: 0,
                        }}>
                            {nodeType === 'model' && <Box size={22} style={{ color: '#60A5FA' }} />}
                            {nodeType === 'table' && <Database size={22} style={{ color: '#4ADE80' }} />}
                            {nodeType === 'measure' && <BarChart3 size={22} style={{ color: '#FDE047' }} />}
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
                            <span style={{ fontSize: 9, fontWeight: 800, color: '#E0E7EF', textTransform: 'uppercase', letterSpacing: '.12em' }}>
                                🔍 Inspecting Now
                            </span>
                            <span style={{ fontSize: 16, fontWeight: 800, color: '#fff', wordBreak: 'break-word', lineHeight: 1.2, letterSpacing: '.01em' }}>
                                {nodeTypeLabel}
                            </span>
                        </div>
                    </div>
                    <button onClick={onClose} style={closeBtnStyle}>
                        <X size={18} />
                    </button>
                </div>

                {/* ═══ NAME/IDENTIFIER SECTION ═══ */}
                <div style={{
                    padding: '14px 14px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'linear-gradient(180deg, var(--bg-app) 0%, var(--bg-surface) 100%)',
                }}>
                    <div style={{ fontSize: 8, fontWeight: 800, color: 'var(--text-tertiary)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '.08em' }}>
                        📝 Selected Item
                    </div>
                    <div style={{
                        fontSize: 12, fontWeight: 700, color: 'var(--text-primary)',
                        padding: '10px 12px', background: 'var(--bg-app)', borderRadius: 8,
                        border: '1.5px solid var(--border-color)',
                        wordBreak: 'break-word',
                        fontFamily: 'monospace',
                        letterSpacing: '.3px',
                    }}>
                        {selectedNode.label}
                    </div>
                </div>

                <div style={{
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'var(--bg-app)',
                }}>
                    <div style={{
                        display: 'grid',
                        gridTemplateColumns: 'auto 1fr',
                        gap: '6px 10px',
                        alignItems: 'center',
                        fontSize: 10,
                    }}>
                        <span style={{ color: 'var(--text-tertiary)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.06em' }}>Source</span>
                        <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>{inspectionSource}</span>
                        <span style={{ color: 'var(--text-tertiary)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.06em' }}>Context</span>
                        <span style={{ color: '#818CF8', fontFamily: 'monospace', fontWeight: 700, wordBreak: 'break-word' }}>{contextLabel}</span>
                    </div>
                </div>

                <div style={{
                    padding: '10px 14px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                    gap: 8,
                }}>
                    <MiniInfoCard label="Type" value={nodeTypeLabel} />
                    <MiniInfoCard label="Current State" value={quickStatus} tone={selectedNode.status === 'broken' ? '#EF4444' : '#22C55E'} />
                    <MiniInfoCard label="From" value={nodeType === 'table' ? (selectedNode.source_type || 'source') : (selectedNode.workspace_id || 'workspace')} />
                </div>

                {/* ═══ CONTENT SECTIONS ═══ */}
                <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
                    {/* ── Model detail ── */}
                    {nodeType === 'model' && (
                        <>
                            {/* HLD - High Level Design */}
                            <CollapsibleSection
                                title="Quick Summary"
                                subtitle="What this model is and where it belongs"
                                icon={<Info size={14} />}
                                expanded={expandedSections.hld !== false}
                                onToggle={() => toggleSection('hld')}
                            >
                                <Section title="Overview">
                                    <Row label="Model ID" value={selectedNode.model_id} code />
                                    <Row label="Workspace" value={selectedNode.workspace_id || 'local'} />
                                    <Row label="Status" value={
                                        <StatusBadge status={selectedNode.status} />
                                    } />
                                    {selectedNode.description && (
                                        <Row label="Description" value={selectedNode.description} />
                                    )}
                                </Section>
                            </CollapsibleSection>

                            {/* LLD - Low Level Design */}
                            <CollapsibleSection
                                title="Technical Details"
                                subtitle="Data sources, measures, and dependencies"
                                icon={<Code2 size={14} />}
                                expanded={expandedSections.lld !== false}
                                onToggle={() => toggleSection('lld')}
                            >
                                {selectedNode.source_tables?.length > 0 && (
                                    <Section title="Source Tables">
                                        {selectedNode.source_tables.map((t, i) => (
                                            <div key={i} style={tableCardStyle}>
                                                <Database size={12} style={{ color: '#22C55E', flexShrink: 0 }} />
                                                <div style={{ flex: 1, minWidth: 0 }}>
                                                    <div style={{ fontWeight: 600, fontSize: 12 }}>{t.table}</div>
                                                    <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
                                                        {t.schema} · {t.source_type}
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </Section>
                                )}
                                {(!selectedNode.source_tables || selectedNode.source_tables.length === 0) && (
                                    <EmptyHint text="No source tables were detected for this model." />
                                )}

                                {selectedNode.measures?.length > 0 && (
                                    <Section title="Measures / Metrics">
                                        {selectedNode.measures.map((m, i) => (
                                            <div key={i} style={tableCardStyle}>
                                                <BarChart3 size={12} style={{ color: '#EAB308', flexShrink: 0 }} />
                                                <div style={{ flex: 1, minWidth: 0 }}>
                                                    <div style={{ fontWeight: 600, fontSize: 12 }}>{m.name}</div>
                                                    <code style={{ fontSize: 10, color: '#D4A017', wordBreak: 'break-word' }}>
                                                        {m.expression}
                                                    </code>
                                                </div>
                                            </div>
                                        ))}
                                    </Section>
                                )}
                                {(!selectedNode.measures || selectedNode.measures.length === 0) && (
                                    <EmptyHint text="No measures are attached to this model yet." />
                                )}
                            </CollapsibleSection>
                        </>
                    )}

                    {/* ── Table detail ── */}
                    {nodeType === 'table' && (
                        <>
                            {/* HLD - High Level Design */}
                            <CollapsibleSection
                                title="Quick Summary"
                                subtitle="Table basics and source information"
                                icon={<Info size={14} />}
                                expanded={expandedSections.hld !== false}
                                onToggle={() => toggleSection('hld')}
                            >
                                <Section title="Overview">
                                    <Row label="Table Name" value={selectedNode.table_name} code />
                                    <Row label="Schema" value={selectedNode.schema || 'PUBLIC'} />
                                    <Row label="Source Type" value={selectedNode.source_type || 'snowflake'} />
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
                            </CollapsibleSection>

                            {/* LLD - Low Level Design */}
                            {selectedNode.columns?.length > 0 && (
                                <CollapsibleSection
                                    title="Technical Details"
                                    subtitle="Column names and data types"
                                    icon={<Code2 size={14} />}
                                    expanded={expandedSections.lld !== false}
                                    onToggle={() => toggleSection('lld')}
                                >
                                    <Section title="Column Schema">
                                        <div style={{
                                            display: 'grid',
                                            gridTemplateColumns: '1fr auto',
                                            gap: '2px 8px',
                                            fontSize: 11,
                                        }}>
                                            <div style={{ fontWeight: 700, fontSize: 10, color: 'var(--text-tertiary)', padding: '4px 0' }}>Name</div>
                                            <div style={{ fontWeight: 700, fontSize: 10, color: 'var(--text-tertiary)', padding: '4px 0' }}>Type</div>
                                            {selectedNode.columns.map((col, i) => (
                                                <div
                                                    key={col.id || col.name || i}
                                                    style={{ display: 'contents' }}
                                                >
                                                    <span style={{ color: 'var(--text-primary)', padding: '4px 0', wordBreak: 'break-word' }}>{col.name}</span>
                                                    <span style={{ color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: 10, padding: '4px 0' }}>{col.data_type}</span>
                                                </div>
                                            ))}
                                        </div>
                                    </Section>
                                </CollapsibleSection>
                            )}
                            {(!selectedNode.columns || selectedNode.columns.length === 0) && (
                                <div style={{ padding: '12px 14px' }}>
                                    <EmptyHint text="No columns are available for this table in the current snapshot." />
                                </div>
                            )}

                            {/* Measures/Metrics for Table */}
                            {selectedNode.measures?.length > 0 && (
                                <CollapsibleSection
                                    title="Measures / Metrics"
                                    subtitle="All metrics/measures for this table"
                                    icon={<BarChart3 size={14} />}
                                    expanded={expandedSections.measures !== false}
                                    onToggle={() => toggleSection('measures')}
                                >
                                    <Section title="Measures / Metrics">
                                        {selectedNode.measures.map((m, i) => (
                                            <div key={i} style={tableCardStyle}>
                                                <BarChart3 size={12} style={{ color: '#EAB308', flexShrink: 0 }} />
                                                <div style={{ flex: 1, minWidth: 0 }}>
                                                    <div style={{ fontWeight: 600, fontSize: 12 }}>{m.name || m.label}</div>
                                                    {m.parent_model && (
                                                        <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
                                                            Model: {m.parent_model}
                                                        </div>
                                                    )}
                                                    {m.expression && (
                                                        <code style={{ fontSize: 10, color: '#D4A017', wordBreak: 'break-word' }}>
                                                            {m.expression}
                                                        </code>
                                                    )}
                                                </div>
                                            </div>
                                        ))}
                                    </Section>
                                </CollapsibleSection>
                            )}
                            {(!selectedNode.measures || selectedNode.measures.length === 0) && (
                                <EmptyHint text="No measures/metrics are attached to this table yet." />
                            )}
                        </>
                    )}

                    {/* ── Measure detail ── */}
                    {nodeType === 'measure' && (
                        <>
                            {/* HLD - High Level Design */}
                            <CollapsibleSection
                                title="Quick Summary"
                                subtitle="What this measure represents"
                                icon={<Info size={14} />}
                                expanded={expandedSections.hld !== false}
                                onToggle={() => toggleSection('hld')}
                            >
                                <Section title="Overview">
                                    <Row label="Measure Name" value={selectedNode.label} code />
                                    <Row label="Parent Model" value={selectedNode.parent_model} />
                                    {selectedNode.data_type && (
                                        <Row label="Data Type" value={selectedNode.data_type} />
                                    )}
                                </Section>
                            </CollapsibleSection>

                            {/* LLD - Low Level Design */}
                            <CollapsibleSection
                                title="Technical Details"
                                subtitle="Formula and calculation logic"
                                icon={<Code2 size={14} />}
                                expanded={expandedSections.lld !== false}
                                onToggle={() => toggleSection('lld')}
                            >
                                <Section title="Expression">
                                    <div style={{
                                        padding: '10px 12px',
                                        background: 'var(--bg-app)',
                                        borderRadius: 6,
                                        border: '1px solid var(--border-color)',
                                        fontSize: 11,
                                        fontFamily: 'monospace',
                                        color: '#D4A017',
                                        overflow: 'auto',
                                        maxHeight: 200,
                                        wordBreak: 'break-word',
                                        letterSpacing: '.3px',
                                    }}>
                                        {selectedNode.expression || 'No expression found for this measure.'}
                                    </div>
                                </Section>
                            </CollapsibleSection>
                        </>
                    )}
                </div>
            </div>
        );
    }

    return null;
}

// ── Shared sub-components ──

function CollapsibleSection({ title, icon, expanded, onToggle, children, subtitle }) {
    return (
        <div style={{
            borderBottom: '1px solid var(--border-color)',
            background: 'var(--bg-app)',
        }}>
            <button
                onClick={onToggle}
                style={{
                    width: '100%', padding: '12px 14px',
                    display: 'flex', alignItems: 'center', gap: 10,
                    border: 'none', background: expanded ? 'var(--bg-surface)' : 'transparent', cursor: 'pointer',
                    color: 'var(--text-primary)', fontSize: 12, fontWeight: 700,
                    textAlign: 'left', transition: 'all 0.2s ease',
                    borderLeft: expanded ? '3px solid #818CF8' : '3px solid transparent',
                    paddingLeft: '11px',
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = 'var(--bg-surface)'}
                onMouseLeave={(e) => e.currentTarget.style.background = expanded ? 'var(--bg-surface)' : 'transparent'}
            >
                <ChevronDown
                    size={14}
                    style={{
                        flexShrink: 0,
                        transform: expanded ? 'rotate(0deg)' : 'rotate(-90deg)',
                        transition: 'transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
                        color: 'var(--text-tertiary)',
                    }}
                />
                {icon && <span style={{ color: 'var(--text-secondary)' }}>{icon}</span>}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 700 }}>{title}</span>
                    {subtitle && <span style={{ fontSize: 9.5, color: 'var(--text-tertiary)', fontWeight: 500 }}>{subtitle}</span>}
                </div>
            </button>
            {expanded && (
                <div style={{
                    padding: '14px 14px',
                    borderTop: '1px solid var(--border-color)',
                    background: 'linear-gradient(180deg, var(--bg-app) 0%, var(--bg-surface) 50%)',
                    animation: 'fadeIn 0.2s ease-in-out',
                }}>
                    {children}
                </div>
            )}
        </div>
    );
}

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
                    color: '#818CF8',
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
            color: isSystem ? '#F59E0B' : '#818CF8',
            border: `1px solid ${isSystem ? 'rgba(245,158,11,.35)' : 'rgba(99,102,241,.35)'}`,
        }}>
            {isSystem ? 'System Generated' : 'Original Schema'}
        </span>
    );
}

function MiniInfoCard({ label, value, tone }) {
    return (
        <div style={{
            border: '1px solid var(--border-color)',
            borderRadius: 8,
            padding: '8px 9px',
            background: 'var(--bg-app)',
            minWidth: 0,
        }}>
            <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--text-tertiary)', fontWeight: 700 }}>
                {label}
            </div>
            <div style={{ fontSize: 11, fontWeight: 700, color: tone || 'var(--text-primary)', marginTop: 4, wordBreak: 'break-word' }}>
                {value || '-'}
            </div>
        </div>
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
