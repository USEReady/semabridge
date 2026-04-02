import { Handle, Position } from '@xyflow/react';
import { Box } from 'lucide-react';

/**
 * ModelNode — blue semantic model node for the dependency graph.
 */
import { useState } from 'react';
export default function ModelNode({ data, selected }) {
    const [hovered, setHovered] = useState(false);
    const isBroken = data.status === 'broken';
    const diffStatus = data.diffStatus;
    const compact = !!data.compactModelView;
    const dense = !!data.denseModelView;
    const borderColor =
        diffStatus === 'added' ? '#22C55E'
            : diffStatus === 'removed' ? '#EF4444'
                : diffStatus === 'modified' ? '#EAB308'
                    : isBroken ? '#EF4444' : selected ? '#60A5FA' : hovered ? '#60A5FA' : '#3B82F6';
    const shadow = selected
        ? '0 0 20px 0 #60a5facc'
        : hovered
            ? '0 0 14px 0 #60a5fa55'
            : '0 4px 16px rgba(0,0,0,.25)';
    const scale = hovered ? 1.025 : 1;

    return (
        <div
            style={{
                background: 'linear-gradient(135deg, #3b82f6 0%, #1E293B 100%)',
                border: `2.5px solid ${borderColor}`,
                borderRadius: compact ? 13 : 16,
                padding: compact ? (dense ? '7px 10px' : '10px 12px') : '16px 20px',
                minWidth: compact ? (dense ? 120 : 145) : 200,
                position: 'relative',
                boxShadow: shadow,
                transition: 'box-shadow .18s, border-color .18s, transform .18s',
                opacity: diffStatus === 'removed' ? 0.78 : 1,
                transform: `scale(${scale})`,
            }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
        >
            {/* Version badge */}
            {data.versionCount > 0 && !dense && (
                <div style={{
                    position: 'absolute', top: -8, right: -8,
                    background: '#6366F1',
                    color: '#fff',
                    fontSize: 9, fontWeight: 700,
                    width: 22, height: 22,
                    borderRadius: '50%',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    border: '2px solid #1E293B',
                    boxShadow: '0 2px 6px rgba(99,102,241,.4)',
                }}>
                    {data.versionCount}
                </div>
            )}

            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: compact ? 0 : 6 }}>
                <Box size={compact ? 12 : 14} style={{ color: '#60A5FA' }} />
                <span style={{
                    fontSize: compact ? 10 : 13, fontWeight: 700, color: '#E2E8F0',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    maxWidth: compact ? (dense ? 94 : 118) : 'none',
                }}>
                    {data.label}
                </span>
            </div>

            {/* Subtitle */}
            {!compact && (
                <div style={{
                    fontSize: 10, color: '#94A3B8',
                    display: 'flex', gap: 8,
                }}>
                    <span>📦 {data.workspace_id || 'local'}</span>
                    {data.measures && <span>📊 {data.measures.length} metrics</span>}
                </div>
            )}

            {isBroken && !dense && (
                <div style={{
                    marginTop: 6, fontSize: 10, fontWeight: 600,
                    color: '#EF4444', display: 'flex', alignItems: 'center', gap: 4,
                }}>
                    ⚠ Broken references detected
                </div>
            )}

            <Handle type="target" position={Position.Top} style={handleStyle} />
            <Handle type="source" position={Position.Bottom} style={handleStyle} />
        </div>
    );
}

const handleStyle = {
    width: 8, height: 8,
    background: '#3B82F6',
    border: '2px solid #1E293B',
};
