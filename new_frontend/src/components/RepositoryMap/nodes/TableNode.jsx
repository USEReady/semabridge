import { Handle, Position } from '@xyflow/react';
import { Database } from 'lucide-react';

/**
 * TableNode — green source-table node for the dependency graph.
 */

import { useState } from 'react';
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
    // Color palette
    const borderColor =
        diffStatus === 'added' ? '#4ade80'
            : diffStatus === 'removed' ? '#f87171'
                : diffStatus === 'modified' ? '#eab308'
                    : isBroken ? '#f87171' : selected ? '#6467f2' : hovered ? '#22c55e' : '#334155';
    const headerBg = isBroken ? '#f87171' : '#18181b';
    const badgeBg = isBroken ? '#f87171' : '#22c55e';
    const badgeColor = isBroken ? '#fff' : '#22c55e';
    const shadow = selected
        ? '0 0 24px 0 #6467f2cc'
        : hovered
            ? '0 0 16px 0 #22c55e55'
            : '0 2px 16px 0 #0006';
    const scale = hovered ? 1.025 : 1;

    return (
        <div
            className="er-node"
            style={{
                background: 'linear-gradient(135deg, #134e2e 0%, #0f172a 100%)',
                border: `2.5px solid ${borderColor}`,
                borderRadius: 16,
                minWidth: 240,
                maxWidth: 270,
                boxShadow: shadow,
                transition: 'box-shadow .18s, border-color .18s, transform .18s',
                opacity: diffStatus === 'removed' ? 0.78 : 1,
                userSelect: 'none',
                overflow: 'hidden',
                fontFamily: 'Inter, sans-serif',
                transform: `scale(${scale})`,
            }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
        >
            {/* Header */}
            <div style={{
                background: headerBg,
                borderBottom: `1.5px solid ${badgeBg}33`,
                padding: '10px 16px 8px 16px',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Database size={16} style={{ color: badgeBg, flexShrink: 0 }} />
                    <span style={{
                        fontWeight: 700, fontSize: 14, color: '#fff', letterSpacing: '.01em',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        maxWidth: 140,
                    }}>{data.label}</span>
                </div>
                <span style={{
                    fontSize: 10, fontWeight: 700, background: badgeBg + '22', color: badgeColor,
                    borderRadius: 6, padding: '2px 8px', border: `1px solid ${badgeBg}55`,
                    textTransform: 'uppercase', letterSpacing: '.04em',
                }}>
                    TABLE
                </span>
            </div>
            {/* Schema/Meta */}
            <div style={{
                fontSize: 11, color: '#a5b4fc', background: '#18181b',
                padding: '4px 16px', borderBottom: '1px solid #1e293b',
                display: 'flex', alignItems: 'center', gap: 10,
            }}>
                <span style={{ color: '#38bdf8', fontWeight: 600 }}>{data.schema || 'PUBLIC'}</span>
                <span style={{ color: '#64748b' }}>{columnCount} cols</span>
                {rowCount !== null && <span style={{ color: '#64748b' }}>{rowCount} rows</span>}
            </div>
            {/* Columns List */}
            <div style={{
                padding: '8px 16px 10px 16px',
                background: 'transparent',
                fontSize: 11,
                color: '#cbd5e1',
                minHeight: 38,
                display: 'flex', flexWrap: 'wrap', gap: 5,
            }}>
                {data.columns && data.columns.length > 0 ? (
                    <>
                        {data.columns.slice(0, 6).map((col, i) => (
                            <span key={i} style={{
                                background: '#18181b',
                                color: '#22c55e',
                                borderRadius: 4,
                                border: '1px solid #22c55e33',
                                padding: '2px 8px',
                                fontWeight: 600,
                                fontSize: 10,
                                letterSpacing: '.01em',
                            }}>{col.name}</span>
                        ))}
                        {data.columns.length > 6 && (
                            <span style={{ color: '#64748b', fontWeight: 600, fontSize: 10 }}>+{data.columns.length - 6} more</span>
                        )}
                    </>
                ) : (
                    <span style={{ color: '#64748b', fontStyle: 'italic' }}>No columns</span>
                )}
            </div>
            {/* Broken Table Warning */}
            {isBroken && (
                <div style={{
                    background: '#f87171', color: '#fff', fontWeight: 700,
                    fontSize: 11, padding: '6px 0', textAlign: 'center',
                }}>
                    ⚠ Table not found
                </div>
            )}
            <Handle type="source" position={Position.Bottom} style={handleStyle} />
            <Handle type="target" position={Position.Top} style={handleStyle} />
        </div>
    );
}

const handleStyle = {
    width: 8, height: 8,
    background: '#22C55E',
    border: '2px solid #1E293B',
};
