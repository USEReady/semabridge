import { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Box, Layers, AlertCircle } from 'lucide-react';

/**
 * ModelNode — Blue semantic model node for the dependency graph.
 * Fully theme-adaptive (dark slate & clean modern light).
 */
export default function ModelNode({ data, selected }) {
    const [hovered, setHovered] = useState(false);
    const isBroken = data.status === 'broken';
    const diffStatus = data.diffStatus;
    const compact = !!data.compactModelView;
    const dense = !!data.denseModelView;

    let statusBorder = 'var(--er-node-border)';
    let statusAccent = 'var(--accent-blue)';
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
    } else if (hovered) {
        statusBorder = 'var(--er-node-border-hover)';
    }

    const cardShadow = selected
        ? '0 0 0 2px var(--accent-blue), var(--shadow-lg)'
        : hovered
            ? 'var(--shadow-md), 0 4px 16px rgba(0, 0, 0, 0.15)'
            : 'var(--shadow-sm)';

    return (
        <div
            className="er-model-node"
            style={{
                background: 'var(--er-node-bg)',
                border: `1px solid ${statusBorder}`,
                borderRadius: compact ? 10 : 12,
                padding: compact ? (dense ? '6px 10px' : '8px 12px') : '14px 18px',
                minWidth: compact ? (dense ? 130 : 160) : 220,
                maxWidth: compact ? 220 : 280,
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

            {/* Version Badge */}
            {data.versionCount > 0 && !dense && (
                <div
                    style={{
                        position: 'absolute',
                        top: 8,
                        right: 8,
                        background: 'var(--color-accent-faint)',
                        color: 'var(--accent-blue)',
                        border: '1px solid var(--accent-blue)',
                        fontSize: 9.5,
                        fontWeight: 700,
                        padding: '1px 6px',
                        borderRadius: 999,
                    }}
                >
                    v{data.versionCount}
                </div>
            )}

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: compact ? 0 : 6 }}>
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
                    <Box size={13} style={{ color: statusAccent }} />
                </div>
                <span
                    style={{
                        fontSize: compact ? 11 : 13,
                        fontWeight: 600,
                        color: 'var(--er-node-text-primary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                        maxWidth: compact ? (dense ? 90 : 120) : 180,
                        letterSpacing: '-0.01em',
                    }}
                    title={data.label}
                >
                    {data.label}
                </span>
            </div>

            {/* Subtitle / Metadata */}
            {!compact && (
                <div
                    style={{
                        fontSize: 10.5,
                        color: 'var(--text-secondary)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        marginTop: 4,
                    }}
                >
                    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Layers size={10} style={{ color: 'var(--text-tertiary)' }} />
                        <span style={{ color: 'var(--text-tertiary)' }}>{data.workspace_id || 'local'}</span>
                    </span>
                    {data.measures && (
                        <>
                            <span style={{ color: 'var(--border-main)' }}>•</span>
                            <span>{data.measures.length} metrics</span>
                        </>
                    )}
                </div>
            )}

            {/* Broken warning */}
            {isBroken && !dense && (
                <div
                    style={{
                        marginTop: 8,
                        fontSize: 10,
                        fontWeight: 600,
                        color: '#EF4444',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 4,
                    }}
                >
                    <AlertCircle size={11} />
                    <span>Broken references detected</span>
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
    background: 'var(--accent-blue)',
    border: '2px solid var(--er-node-bg)',
    borderRadius: '50%',
};
