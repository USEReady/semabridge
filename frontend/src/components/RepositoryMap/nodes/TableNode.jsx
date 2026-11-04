import { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Table2, AlertCircle, Hash, Calendar, Type, Key, Layers } from 'lucide-react';

/**
 * TableNode — Production-grade semantic table card for the ER diagram & repository map.
 * Fully theme-adaptive (executive dark slate & clean modern light).
 */
export default function TableNode({ data, selected }) {
    const [hovered, setHovered] = useState(false);
    const isBroken = data.status === 'broken';
    const diffStatus = data.diffStatus;
    const compact = !!data.compactTableView;
    const dense = !!data.denseTableView;
    const columnCount = Array.isArray(data.columns) ? data.columns.length : 0;
    const rowCount =
        typeof data.row_count === 'number'
            ? data.row_count
            : Array.isArray(data.preview_rows)
                ? data.preview_rows.length
                : null;

    // Diff / Status border color
    let statusBorder = 'var(--er-node-border)';
    let statusAccent = '#10B981'; // default emerald for source table
    if (diffStatus === 'added') {
        statusBorder = '#10B981';
        statusAccent = '#10B981';
    } else if (diffStatus === 'removed') {
        statusBorder = '#EF4444';
        statusAccent = '#EF4444';
    } else if (diffStatus === 'modified') {
        statusBorder = '#F59E0B';
        statusAccent = '#F59E0B';
    } else if (isBroken) {
        statusBorder = '#EF4444';
        statusAccent = '#EF4444';
    } else if (selected) {
        statusBorder = 'var(--er-node-border-selected)';
        statusAccent = 'var(--accent-blue)';
    } else if (hovered) {
        statusBorder = 'var(--er-node-border-hover)';
    }

    const cardShadow = selected
        ? '0 0 0 2px var(--accent-blue), var(--shadow-lg)'
        : hovered
            ? 'var(--shadow-md), 0 4px 16px rgba(0, 0, 0, 0.15)'
            : 'var(--shadow-sm)';

    function getColIcon(colName, colType) {
        const name = String(colName || '').toLowerCase();
        const type = String(colType || '').toLowerCase();
        if (name.endsWith('id') || name.startsWith('id_') || name === 'id' || name.endsWith('key') || name.startsWith('key_')) {
            return <Key size={10} style={{ color: '#F59E0B', flexShrink: 0 }} />;
        }
        if (type.includes('date') || type.includes('time') || name.includes('date') || name.includes('year') || name.includes('month')) {
            return <Calendar size={10} style={{ color: '#38BDF8', flexShrink: 0 }} />;
        }
        if (type.includes('int') || type.includes('num') || type.includes('float') || type.includes('double') || name.includes('units') || name.includes('revenue') || name.includes('score') || name.includes('count')) {
            return <Hash size={10} style={{ color: '#10B981', flexShrink: 0 }} />;
        }
        return <Type size={10} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />;
    }

    return (
        <div
            className="er-table-node group"
            style={{
                background: 'var(--er-node-bg)',
                border: `1px solid ${statusBorder}`,
                borderRadius: compact ? 10 : 12,
                minWidth: compact ? (dense ? 160 : 200) : 250,
                maxWidth: compact ? (dense ? 200 : 250) : 290,
                boxShadow: cardShadow,
                transition: 'all 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
                userSelect: 'none',
                overflow: 'hidden',
                position: 'relative',
                transform: hovered ? 'translateY(-2px)' : 'none',
            }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
        >
            {/* Top Accent Stripe */}
            <div
                style={{
                    height: 2.5,
                    width: '100%',
                    background: statusAccent,
                }}
            />

            {/* Header */}
            <div
                style={{
                    background: 'var(--er-node-header-bg)',
                    padding: compact ? '6px 10px' : '10px 14px',
                    borderBottom: '1px solid var(--border-color)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                    <div
                        style={{
                            width: 22,
                            height: 22,
                            borderRadius: 6,
                            background: `${statusAccent}18`,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            flexShrink: 0,
                        }}
                    >
                        <Table2 size={13} style={{ color: statusAccent }} />
                    </div>
                    <span
                        style={{
                            fontSize: compact ? 12 : 13,
                            fontWeight: 600,
                            color: 'var(--er-node-text-primary)',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            letterSpacing: '-0.01em',
                        }}
                        title={data.label}
                    >
                        {data.label}
                    </span>
                </div>

                <span
                    style={{
                        fontSize: 9.5,
                        fontWeight: 700,
                        padding: '1.5px 6px',
                        borderRadius: 4,
                        background: `${statusAccent}14`,
                        color: statusAccent,
                        border: `1px solid ${statusAccent}33`,
                        letterSpacing: '0.04em',
                        textTransform: 'uppercase',
                        flexShrink: 0,
                    }}
                >
                    Table
                </span>
            </div>

            {/* Schema / Metadata Strip */}
            {!dense && (
                <div
                    style={{
                        background: 'var(--er-node-meta-bg)',
                        padding: '4px 14px',
                        borderBottom: '1px solid var(--border-color)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        fontSize: 10.5,
                    }}
                >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span
                            style={{
                                color: 'var(--accent-blue)',
                                fontWeight: 600,
                                fontFamily: 'ui-monospace, SFMono-Regular, monospace',
                                fontSize: 10,
                            }}
                        >
                            {data.schema || 'PUBLIC'}
                        </span>
                        <span style={{ color: 'var(--text-tertiary)' }}>•</span>
                        <span style={{ color: 'var(--text-secondary)' }}>
                            {columnCount} cols
                        </span>
                    </div>

                    {rowCount !== null && (
                        <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>
                            {rowCount.toLocaleString()} rows
                        </span>
                    )}
                </div>
            )}

            {/* Column Field Preview */}
            <div
                style={{
                    padding: compact ? '6px 10px' : '8px 12px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 3,
                    background: 'var(--er-node-bg)',
                }}
            >
                {data.columns && data.columns.length > 0 ? (
                    <>
                        {data.columns.slice(0, compact ? 4 : 6).map((col, idx) => {
                            const colName = typeof col === 'string'
                                ? col
                                : (col.unique_name || col.label || col.name || col.column_name || `field_${idx}`);
                            const colType = typeof col === 'object' ? (col.data_type || col.type || '') : '';

                            return (
                                <div
                                    key={idx}
                                    style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        justifyContent: 'space-between',
                                        padding: '2.5px 6px',
                                        borderRadius: 4,
                                        background: 'var(--er-node-pill-bg)',
                                        border: '1px solid var(--er-node-pill-border)',
                                        fontSize: 10.5,
                                    }}
                                >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                                        {getColIcon(colName, colType)}
                                        <span
                                            style={{
                                                color: 'var(--er-node-pill-text)',
                                                fontWeight: 500,
                                                overflow: 'hidden',
                                                textOverflow: 'ellipsis',
                                                whiteSpace: 'nowrap',
                                            }}
                                            title={colName}
                                        >
                                            {colName}
                                        </span>
                                    </div>
                                    {colType && (
                                        <span
                                            style={{
                                                fontSize: 9,
                                                color: 'var(--text-tertiary)',
                                                fontFamily: 'ui-monospace, monospace',
                                                marginLeft: 6,
                                                flexShrink: 0,
                                            }}
                                        >
                                            {colType}
                                        </span>
                                    )}
                                </div>
                            );
                        })}

                        {data.columns.length > (compact ? 4 : 6) && (
                            <div
                                style={{
                                    fontSize: 10,
                                    color: 'var(--text-tertiary)',
                                    fontWeight: 500,
                                    textAlign: 'center',
                                    paddingTop: 2,
                                }}
                            >
                                +{data.columns.length - (compact ? 4 : 6)} more fields
                            </div>
                        )}
                    </>
                ) : (
                    <div style={{ fontSize: 10.5, color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '4px 6px' }}>
                        No columns declared
                    </div>
                )}
            </div>

            {/* Broken Reference Warning */}
            {isBroken && (
                <div
                    style={{
                        background: 'rgba(239, 68, 68, 0.12)',
                        borderTop: '1px solid rgba(239, 68, 68, 0.25)',
                        color: '#EF4444',
                        fontWeight: 600,
                        fontSize: 10.5,
                        padding: '6px 12px',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 5,
                    }}
                >
                    <AlertCircle size={12} />
                    <span>Table definition broken</span>
                </div>
            )}

            {/* Multi-Directional Handles for Fluid Routing (Left-to-Right + Top-to-Bottom) */}
            <Handle
                type="target"
                position={Position.Left}
                id="target-left"
                style={{ ...handleStyle, left: -4 }}
            />
            <Handle
                type="source"
                position={Position.Right}
                id="source-right"
                style={{ ...handleStyle, right: -4 }}
            />
            <Handle
                type="target"
                position={Position.Top}
                id="target-top"
                style={{ ...handleStyle, top: -4 }}
            />
            <Handle
                type="source"
                position={Position.Bottom}
                id="source-bottom"
                style={{ ...handleStyle, bottom: -4 }}
            />
        </div>
    );
}

const handleStyle = {
    width: 8,
    height: 8,
    background: 'var(--accent-blue)',
    border: '2px solid var(--er-node-bg)',
    borderRadius: '50%',
    transition: 'transform 0.15s ease',
};
