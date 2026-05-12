import { useState, useMemo, memo } from 'react';
import {
    ChevronRight, ChevronDown,
    Database, Table, Hash, Search,
} from 'lucide-react';

function formatProjectDisplayName(value) {
    return String(value || '').trim().replace(/^proj-/i, '');
}

const ComponentNode = memo(function ComponentNode({
    node, depth, onNodeClick, selectedId,
}) {
    const [expanded, setExpanded] = useState(depth < 1);
    if (!node) return null;

    const isDir = node.type === 'model';
    const isSelected = String(node.id) === String(selectedId);
    
    const IconComp = isDir ? Database : Table;
    const iconColor = isDir ? '#818CF8' : '#10B981';

    const handleClick = () => {
        if (isDir) {
            setExpanded(e => !e);
        }
        onNodeClick(node);
    };

    return (
        <>
            <div
                onClick={handleClick}
                style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '4px 8px 4px ' + (12 + depth * 16) + 'px',
                    cursor: 'pointer',
                    fontSize: 12,
                    color: isSelected ? '#fff' : 'var(--text-primary)',
                    background: isSelected
                        ? 'var(--accent-blue)'
                        : 'transparent',
                    borderRadius: 4,
                    transition: 'background .12s, color .12s',
                    userSelect: 'none',
                    overflow: 'hidden',
                    whiteSpace: 'nowrap',
                    margin: '1px 4px',
                }}
                onMouseEnter={e => {
                    if (!isSelected) e.currentTarget.style.background = 'var(--bg-surface-hover)';
                }}
                onMouseLeave={e => {
                    if (!isSelected) e.currentTarget.style.background = 'transparent';
                }}
            >
                {isDir ? (
                    expanded
                        ? <ChevronDown size={12} style={{ flexShrink: 0, opacity: .5 }} />
                        : <ChevronRight size={12} style={{ flexShrink: 0, opacity: .5 }} />
                ) : (
                    <span style={{ width: 12, flexShrink: 0 }} />
                )}

                <IconComp
                    size={14}
                    style={{ flexShrink: 0, color: isSelected ? '#fff' : iconColor }}
                />

                <span style={{
                    overflow: 'hidden', textOverflow: 'ellipsis',
                    fontWeight: isDir ? 600 : 400,
                    minWidth: 0,
                    flex: 1,
                }}>
                    {node.label}
                </span>
            </div>

            {isDir && expanded && node.children && (
                <div>
                    {node.children.map((child, i) => (
                        <ComponentNode
                            key={child.id || i}
                            node={child}
                            depth={depth + 1}
                            onNodeClick={onNodeClick}
                            selectedId={selectedId}
                        />
                    ))}
                </div>
            )}
        </>
    );
});

export default function ComponentTree({ nodes = [], snapshotModels = [], onNodeClick, selectedId }) {
    const [search, setSearch] = useState('');

    const treeData = useMemo(() => {
        const modelKeyOf = (value) => String(value || '').trim().toLowerCase();
        const models = nodes.filter(n => n.data?.nodeType === 'model').map(n => ({
            id: n.id,
            label: formatProjectDisplayName(n.data?.label || n.id),
            type: 'model',
            data: n.data,
            children: []
        }));

        // Ensure models discovered from snapshot history are visible even when
        // the currently loaded graph is scoped to a single snapshot/project.
        const existingModelIds = new Set(models.map((m) => modelKeyOf(m?.data?.model_id || m.id || '')));
        snapshotModels.forEach((modelName) => {
            const id = String(modelName || '').trim();
            const modelKey = modelKeyOf(id);
            if (!id || existingModelIds.has(modelKey)) return;
            models.push({
                id: `model-${id}`,
                label: formatProjectDisplayName(id),
                type: 'model',
                data: {
                    model_id: id,
                    label: formatProjectDisplayName(id),
                    nodeType: 'model',
                    status: 'valid',
                },
                children: [],
            });
            existingModelIds.add(modelKey);
        });

        const modelByKey = new Map(
            models.map((m) => [modelKeyOf(m?.data?.model_id || m.id), m])
        );

        const tables = nodes.filter(n => n.data?.nodeType === 'table');
        
        tables.forEach(t => {
            const modelId = modelKeyOf(t.data?.model_id);
            const model = modelByKey.get(modelId);
            if (model) {
                model.children.push({
                    id: t.id,
                    label: t.data?.label || t.id,
                    type: 'table',
                    data: t.data
                });
            } else {
                // Orphan table?
            }
        });

        // Filter by search
        if (!search.trim()) return models;
        
        const q = search.toLowerCase();
        return models.filter(m => {
            const matchModel = m.label.toLowerCase().includes(q);
            const matchingChildren = m.children.filter(c => c.label.toLowerCase().includes(q));
            if (matchModel || matchingChildren.length > 0) {
                m.children = matchingChildren;
                return true;
            }
            return false;
        });
    }, [nodes, search, snapshotModels]);

    return (
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ padding: '8px 12px' }}>
                <div style={{ position: 'relative' }}>
                    <Search size={12} style={{ position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-tertiary)' }} />
                    <input 
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        placeholder="Filter components..."
                        style={{
                            width: '100%',
                            background: 'var(--bg-input)',
                            border: '1px solid var(--border-main)',
                            borderRadius: 6,
                            padding: '4px 8px 4px 26px',
                            fontSize: 12,
                            color: 'var(--text-primary)',
                            outline: 'none'
                        }}
                    />
                </div>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', paddingBottom: 12 }}>
                {treeData.map(model => (
                    <ComponentNode 
                        key={model.id}
                        node={model}
                        depth={0}
                        onNodeClick={onNodeClick}
                        selectedId={selectedId}
                    />
                ))}
            </div>
        </div>
    );
}
