import { Handle, Position } from '@xyflow/react';
import { Box } from 'lucide-react';

/**
 * ModelNode — blue semantic model node for the dependency graph.
 */
export default function ModelNode({ data, selected }) {
    const isBroken = data.status === 'broken';
    const borderColor = isBroken ? '#EF4444' : selected ? '#60A5FA' : '#3B82F6';

    return (
        <div style={{
            background: 'linear-gradient(135deg, #1E3A5F 0%, #1E293B 100%)',
            border: `2px solid ${borderColor}`,
            borderRadius: 12,
            padding: '12px 16px',
            minWidth: 200,
            position: 'relative',
            boxShadow: selected
                ? '0 0 20px rgba(59,130,246,.35)'
                : '0 4px 16px rgba(0,0,0,.25)',
            transition: 'box-shadow .2s, border-color .2s',
        }}>
            {/* Version badge */}
            {data.versionCount > 0 && (
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
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                <Box size={14} style={{ color: '#60A5FA' }} />
                <span style={{
                    fontSize: 13, fontWeight: 700, color: '#E2E8F0',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                    {data.label}
                </span>
            </div>

            {/* Subtitle */}
            <div style={{
                fontSize: 10, color: '#94A3B8',
                display: 'flex', gap: 8,
            }}>
                <span>📦 {data.workspace_id || 'local'}</span>
                {data.measures && <span>📊 {data.measures.length} metrics</span>}
            </div>

            {isBroken && (
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
