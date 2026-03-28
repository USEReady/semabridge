import { Handle, Position } from '@xyflow/react';
import { BarChart3 } from 'lucide-react';

/**
 * MeasureNode — yellow metric/measure node for the dependency graph.
 */
import { useState } from 'react';
export default function MeasureNode({ data, selected }) {
    const [hovered, setHovered] = useState(false);
    const diffStatus = data.diffStatus;
    const borderColor =
        diffStatus === 'added' ? '#22C55E'
            : diffStatus === 'removed' ? '#EF4444'
                : diffStatus === 'modified' ? '#EAB308'
                    : selected ? '#FDE047' : hovered ? '#FDE047' : '#EAB308';
    const shadow = selected
        ? '0 0 20px 0 #fde047cc'
        : hovered
            ? '0 0 14px 0 #fde04755'
            : '0 4px 16px rgba(0,0,0,.25)';
    const scale = hovered ? 1.025 : 1;

    return (
        <div
            style={{
                background: 'linear-gradient(135deg, #facc15 0%, #1E293B 100%)',
                border: `2.5px solid ${borderColor}`,
                borderRadius: 16,
                padding: '12px 16px',
                minWidth: 170,
                boxShadow: shadow,
                transition: 'box-shadow .18s, border-color .18s, transform .18s',
                opacity: diffStatus === 'removed' ? 0.78 : 1,
                transform: `scale(${scale})`,
            }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
        >
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <BarChart3 size={13} style={{ color: '#FDE047' }} />
                <span style={{
                    fontSize: 12, fontWeight: 700, color: '#FEF3C7',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                    {data.label}
                </span>
            </div>

            {/* Expression */}
            {data.expression && (
                <div style={{
                    fontSize: 10, color: '#D4A017',
                    fontFamily: 'monospace',
                    background: 'rgba(234,179,8,.08)',
                    padding: '3px 6px',
                    borderRadius: 4,
                    marginTop: 4,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    maxWidth: 200,
                }}>
                    {data.expression}
                </div>
            )}

            {data.data_type && (
                <div style={{ fontSize: 9, color: '#94A3B8', marginTop: 3 }}>
                    {data.data_type}
                </div>
            )}

            <Handle type="target" position={Position.Top} style={handleStyle} />
        </div>
    );
}

const handleStyle = {
    width: 8, height: 8,
    background: '#EAB308',
    border: '2px solid #1E293B',
};
