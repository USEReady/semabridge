import { Handle, Position } from '@xyflow/react';
import { BarChart3 } from 'lucide-react';

/**
 * MeasureNode — yellow metric/measure node for the dependency graph.
 */
export default function MeasureNode({ data, selected }) {
    const borderColor = selected ? '#FDE047' : '#EAB308';

    return (
        <div style={{
            background: 'linear-gradient(135deg, #422006 0%, #1E293B 100%)',
            border: `2px solid ${borderColor}`,
            borderRadius: 12,
            padding: '10px 14px',
            minWidth: 170,
            boxShadow: selected
                ? '0 0 20px rgba(234,179,8,.3)'
                : '0 4px 16px rgba(0,0,0,.25)',
            transition: 'box-shadow .2s, border-color .2s',
        }}>
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
