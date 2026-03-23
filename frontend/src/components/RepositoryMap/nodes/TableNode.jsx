import { Handle, Position } from '@xyflow/react';
import { Database } from 'lucide-react';

/**
 * TableNode — green source-table node for the dependency graph.
 */
export default function TableNode({ data, selected }) {
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
    const borderColor =
        diffStatus === 'added' ? '#22C55E'
            : diffStatus === 'removed' ? '#EF4444'
                : diffStatus === 'modified' ? '#EAB308'
                    : isBroken ? '#EF4444' : selected ? '#4ADE80' : '#22C55E';

    return (
        <div style={{
            background: 'linear-gradient(135deg, #14432A 0%, #1E293B 100%)',
            border: `2px solid ${borderColor}`,
            borderRadius: compact ? 10 : 12,
            padding: compact ? (dense ? '8px 10px' : '9px 12px') : '10px 14px',
            minWidth: compact ? (dense ? 165 : 190) : 180,
            boxShadow: selected
                ? '0 0 20px rgba(34,197,94,.3)'
                : '0 4px 16px rgba(0,0,0,.25)',
            transition: 'box-shadow .2s, border-color .2s',
            opacity: diffStatus === 'removed' ? 0.78 : 1,
        }}>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: compact ? 2 : 4 }}>
                <Database size={compact ? 12 : 13} style={{ color: '#4ADE80' }} />
                <span style={{
                    fontSize: compact ? 11 : 12, fontWeight: 700, color: '#D1FAE5',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                    {data.label}
                </span>
            </div>

            {/* Subtitle */}
            <div style={{ fontSize: compact ? 9 : 10, color: '#86EFAC', opacity: .8 }}>
                {data.schema || 'PUBLIC'} · {columnCount} cols{rowCount !== null ? ` · ${rowCount} rows` : ''}
            </div>

            {data.columns && data.columns.length > 0 && (
                <div style={{
                    marginTop: compact ? 4 : 6, fontSize: compact ? 8 : 9, color: '#94A3B8',
                    display: 'flex', flexWrap: 'wrap', gap: 3,
                }}>
                    {data.columns.slice(0, compact ? 5 : 6).map((col, i) => (
                        <span key={i} style={{
                            background: 'rgba(34,197,94,.12)',
                            padding: '1px 6px',
                            borderRadius: 4,
                            border: '1px solid rgba(34,197,94,.2)',
                        }}>
                            {col.name}
                        </span>
                    ))}
                    {data.columns.length > (compact ? 5 : 6) && (
                        <span style={{ opacity: .5 }}>+{data.columns.length - (compact ? 5 : 6)}</span>
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
