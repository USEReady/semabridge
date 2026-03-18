import { Handle, Position } from '@xyflow/react';
import { Database } from 'lucide-react';

/**
 * TableNode — green source-table node for the dependency graph.
 */
export default function TableNode({ data, selected }) {
    const isBroken = data.status === 'broken';
    const borderColor = isBroken ? '#EF4444' : selected ? '#4ADE80' : '#22C55E';

    return (
        <div style={{
            background: 'linear-gradient(135deg, #14432A 0%, #1E293B 100%)',
            border: `2px solid ${borderColor}`,
            borderRadius: 12,
            padding: '10px 14px',
            minWidth: 180,
            boxShadow: selected
                ? '0 0 20px rgba(34,197,94,.3)'
                : '0 4px 16px rgba(0,0,0,.25)',
            transition: 'box-shadow .2s, border-color .2s',
        }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                <Database size={13} style={{ color: '#4ADE80' }} />
                <span style={{
                    fontSize: 12, fontWeight: 700, color: '#D1FAE5',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                    {data.label}
                </span>
            </div>

            {/* Subtitle */}
            <div style={{ fontSize: 10, color: '#86EFAC', opacity: .7 }}>
                {data.schema || 'PUBLIC'} · {data.source_type || 'snowflake'}
            </div>

            {data.columns && data.columns.length > 0 && (
                <div style={{
                    marginTop: 6, fontSize: 9, color: '#94A3B8',
                    display: 'flex', flexWrap: 'wrap', gap: 3,
                }}>
                    {data.columns.slice(0, 4).map((col, i) => (
                        <span key={i} style={{
                            background: 'rgba(34,197,94,.12)',
                            padding: '1px 6px',
                            borderRadius: 4,
                            border: '1px solid rgba(34,197,94,.2)',
                        }}>
                            {col.name}
                        </span>
                    ))}
                    {data.columns.length > 4 && (
                        <span style={{ opacity: .5 }}>+{data.columns.length - 4}</span>
                    )}
                </div>
            )}

            {isBroken && (
                <div style={{
                    marginTop: 4, fontSize: 9, color: '#EF4444', fontWeight: 600,
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
