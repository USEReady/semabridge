import { useCallback, useEffect, useState } from 'react';
import {
    ReactFlow,
    MiniMap,
    Controls,
    Background,
    useNodesState,
    useEdgesState,
    Panel,
} from '@xyflow/react';
import { Loader2, Database } from 'lucide-react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';

import ModelNode from './nodes/ModelNode';
import TableNode from './nodes/TableNode';
import MeasureNode from './nodes/MeasureNode';
import { api } from '../../utils/api';

// Custom node type registry
const nodeTypes = {
    modelNode: ModelNode,
    tableNode: TableNode,
    measureNode: MeasureNode,
};

// ────────────────────────────────────────────
// Dagre hierarchical layout
// ────────────────────────────────────────────
function applyDagreLayout(nodes, edges, direction = 'TB') {
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100 });

    nodes.forEach(n => {
        g.setNode(n.id, { width: 240, height: 80 });
    });

    edges.forEach(e => {
        g.setEdge(e.source, e.target);
    });

    dagre.layout(g);

    return nodes.map(n => {
        const pos = g.node(n.id);
        return {
            ...n,
            position: { x: pos.x - 120, y: pos.y - 40 },
        };
    });
}

// ────────────────────────────────────────────
// Simple force-directed simulation (lightweight)
// ────────────────────────────────────────────
function applyForceLayout(nodes) {
    // Place nodes in a roughly circular pattern
    const cx = 500, cy = 400;
    const radius = Math.max(250, nodes.length * 30);
    return nodes.map((n, i) => {
        const angle = (2 * Math.PI * i) / nodes.length;
        return {
            ...n,
            position: {
                x: cx + radius * Math.cos(angle),
                y: cy + radius * Math.sin(angle),
            },
        };
    });
}

/**
 * DependencyGraph — the React Flow canvas for the DAG.
 */
export default function DependencyGraph({
    graphData,
    layout,
    searchQuery,
    filterType,
    showVersionBadges,
    onNodeClick,
    isLoading = false,
    snapshotId = null,
}) {
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [versionCounts, setVersionCounts] = useState({});

    // ── Load version counts ──
    useEffect(() => {
        if (!showVersionBadges) return;
        (async () => {
            try {
                const versions = await api.getModelVersions('', '', 500);
                const counts = {};
                (versions || []).forEach(v => {
                    counts[v.model_id] = (counts[v.model_id] || 0) + 1;
                });
                setVersionCounts(counts);
            } catch { /* ignore */ }
        })();
    }, [showVersionBadges]);

    // ── Apply filter + search + layout ──────────
    useEffect(() => {
        if (!graphData) return;

        let filteredNodes = [...graphData.nodes];
        let filteredEdges = [...graphData.edges];

        // Filter by type
        if (filterType === 'models') {
            const ids = new Set(filteredNodes.filter(n => n.data.nodeType === 'model').map(n => n.id));
            filteredNodes = filteredNodes.filter(n => n.data.nodeType === 'model');
            filteredEdges = filteredEdges.filter(e => ids.has(e.source) && ids.has(e.target));
        } else if (filterType === 'tables') {
            const ids = new Set(filteredNodes.filter(n => n.data.nodeType === 'table').map(n => n.id));
            filteredNodes = filteredNodes.filter(n => n.data.nodeType === 'table');
            filteredEdges = filteredEdges.filter(e => ids.has(e.source) && ids.has(e.target));
        } else if (filterType === 'broken') {
            filteredNodes = filteredNodes.filter(n =>
                n.data.status === 'broken' || n.data.nodeType === 'model' && n.data.status === 'broken'
            );
            const ids = new Set(filteredNodes.map(n => n.id));
            filteredEdges = filteredEdges.filter(e => ids.has(e.source) || ids.has(e.target));
        }

        // Search — highlight / dim
        if (searchQuery.trim()) {
            const q = searchQuery.toLowerCase();
            filteredNodes = filteredNodes.map(n => {
                const label = (n.data.label || '').toLowerCase();
                const modelId = (n.data.model_id || '').toLowerCase();
                const wsId = (n.data.workspace_id || '').toLowerCase();
                const match = label.includes(q) || modelId.includes(q) || wsId.includes(q);
                return {
                    ...n,
                    style: {
                        ...n.style,
                        opacity: match ? 1 : 0.2,
                        transition: 'opacity .25s ease',
                    },
                };
            });
        }

        // Version badges — inject into model nodes
        if (showVersionBadges) {
            filteredNodes = filteredNodes.map(n => {
                if (n.data.nodeType === 'model' && n.data.model_id) {
                    return {
                        ...n,
                        data: {
                            ...n.data,
                            versionCount: versionCounts[n.data.model_id] || 0,
                        },
                    };
                }
                return n;
            });
        }

        // Layout
        if (layout === 'hierarchical') {
            filteredNodes = applyDagreLayout(filteredNodes, filteredEdges, 'TB');
        } else {
            filteredNodes = applyForceLayout(filteredNodes);
        }

        setNodes(filteredNodes);
        setEdges(filteredEdges);
    }, [graphData, layout, searchQuery, filterType, showVersionBadges, versionCounts, setNodes, setEdges]);

    // ── Node click handler ──────────────────────
    const handleNodeClick = useCallback((_, node) => {
        onNodeClick?.(node.data);
    }, [onNodeClick]);

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={handleNodeClick}
                nodeTypes={nodeTypes}
                fitView
                minZoom={0.1}
                maxZoom={2}
                defaultEdgeOptions={{
                    type: 'smoothstep',
                    animated: true,
                }}
                style={{ background: 'var(--bg-app)' }}
            >
                {snapshotId && (
                    <Panel position="top-left">
                        <div style={{
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 8,
                            padding: '8px 10px',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 6,
                            fontSize: 11,
                            color: 'var(--text-secondary)',
                        }}>
                            <Database size={12} style={{ color: '#818CF8' }} />
                            <span style={{ textTransform: 'uppercase', letterSpacing: '.04em', fontWeight: 700, color: 'var(--text-tertiary)' }}>Snapshot</span>
                            <span style={{ color: '#818CF8', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}>
                                {String(snapshotId).slice(0, 8)}
                            </span>
                        </div>
                    </Panel>
                )}

                <Controls
                    position="bottom-left"
                    style={{
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                    }}
                />
                <MiniMap
                    position="bottom-right"
                    style={{
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                    }}
                    nodeColor={n => {
                        if (n.data?.nodeType === 'model') return '#3B82F6';
                        if (n.data?.nodeType === 'table') return '#22C55E';
                        if (n.data?.nodeType === 'measure') return '#EAB308';
                        return '#6B7280';
                    }}
                    maskColor="rgba(0,0,0,0.15)"
                />
                <Background gap={20} size={1} color="var(--border-color)" />

                {/* Legend */}
                <Panel position="top-right">
                    <div style={{
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-color)',
                        borderRadius: 8,
                        padding: '10px 14px',
                        fontSize: 11,
                        display: 'flex', flexDirection: 'column', gap: 5,
                    }}>
                        <span style={{ fontWeight: 700, fontSize: 10, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--text-tertiary)' }}>
                            Legend
                        </span>
                        {[
                            { color: '#3B82F6', label: 'Semantic Model' },
                            { color: '#22C55E', label: 'Source Table' },
                            { color: '#EAB308', label: 'Metric / Measure' },
                            { color: '#EF4444', label: 'Broken Reference' },
                        ].map(item => (
                            <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{
                                    width: 10, height: 10, borderRadius: 3,
                                    background: item.color, flexShrink: 0,
                                }} />
                                <span style={{ color: 'var(--text-secondary)' }}>{item.label}</span>
                            </div>
                        ))}
                    </div>
                </Panel>
            </ReactFlow>

            {isLoading && (
                <div style={{
                    position: 'absolute',
                    inset: 0,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: 'rgba(2, 6, 23, 0.55)',
                    backdropFilter: 'blur(2px)',
                    zIndex: 5,
                }}>
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        padding: '10px 14px',
                        borderRadius: 10,
                        background: 'var(--bg-surface)',
                        border: '1px solid var(--border-color)',
                        color: 'var(--text-secondary)',
                        fontSize: 12,
                    }}>
                        <Loader2 size={14} style={{ animation: 'spin 1s linear infinite', color: '#818CF8' }} />
                        <span>{snapshotId ? 'Loading snapshot graph...' : 'Loading dependency graph...'}</span>
                    </div>
                </div>
            )}
        </div>
    );
}
