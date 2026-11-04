import { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Sigma, Code } from 'lucide-react';

/**
 * MeasureNode — Amber/gold metric node for semantic measures in the dependency graph.
 * Fully theme-adaptive (dark slate & clean modern light).
 */
export default function MeasureNode({ data, selected }) {
    const [hovered, setHovered] = useState(false);
    const diffStatus = data.diffStatus;

    let statusBorder = 'var(--er-node-border)';
    let statusAccent = '#F59E0B'; // Amber
    if (diffStatus === 'added') {
        statusBorder = '#10B981';
        statusAccent = '#10B981';
    } else if (diffStatus === 'removed') {
        statusBorder = '#EF4444';
        statusAccent = '#EF4444';
    } else if (diffStatus === 'modified') {
        statusBorder = '#F59E0B';
        statusAccent = '#F59E0B';
    } else if (selected) {
        statusBorder = 'var(--er-node-border-selected)';
    } else if (hovered) {
        statusBorder = 'var(--er-node-border-hover)';
    }

    const cardShadow = selected
        ? '0 0 0 2px #F59E0B, var(--shadow-lg)'
        : hovered
            ? 'var(--shadow-md), 0 4px 16px rgba(0, 0, 0, 0.15)'
            : 'var(--shadow-sm)';

    return (
        <div
            className="er-measure-node"
            style={{
                background: 'var(--er-node-bg)',
                border: `1px solid ${statusBorder}`,
                borderRadius: 12,
                padding: '10px 14px',
                minWidth: 180,
                maxWidth: 240,
                position: 'relative',
                boxShadow: cardShadow,
                transition: 'all 0.18s cubic-bezier(0.16, 1, 0.3, 1)',
                userSelect: 'none',
                overflow: 'hidden',
                transform: hovered ? 'translateY(-2px)' : 'none',
            }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
        >
            {/* Top Accent Stripe */}
            <div
                style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    right: 0,
                    height: 2.5,
                    background: statusAccent,
                }}
            />

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 4 }}>
                <div
                    style={{
                        width: 20,
                        height: 20,
                        borderRadius: 5,
                        background: `${statusAccent}18`,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                    }}
                >
                    <Sigma size={12} style={{ color: statusAccent }} />
                </div>
                <span
                    style={{
                        fontSize: 12,
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

            {/* DAX / Formula Expression */}
            {data.expression && (
                <div
                    style={{
                        fontSize: 10,
                        color: 'var(--text-secondary)',
                        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                        background: 'var(--er-node-meta-bg)',
                        border: '1px solid var(--border-color)',
                        padding: '3px 6px',
                        borderRadius: 4,
                        marginTop: 4,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                    }}
                    title={data.expression}
                >
                    {data.expression}
                </div>
            )}

            {data.data_type && (
                <div style={{ fontSize: 9.5, color: 'var(--text-tertiary)', marginTop: 4, fontFamily: 'monospace' }}>
                    {data.data_type}
                </div>
            )}

            <Handle type="target" position={Position.Left} id="target-left" style={{ ...handleStyle, left: -4 }} />
            <Handle type="source" position={Position.Right} id="source-right" style={{ ...handleStyle, right: -4 }} />
            <Handle type="target" position={Position.Top} id="target-top" style={{ ...handleStyle, top: -4 }} />
            <Handle type="source" position={Position.Bottom} id="source-bottom" style={{ ...handleStyle, bottom: -4 }} />
        </div>
    );
}

const handleStyle = {
    width: 8,
    height: 8,
    background: '#F59E0B',
    border: '2px solid var(--er-node-bg)',
    borderRadius: '50%',
};
