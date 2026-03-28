import { useCallback, useEffect, useMemo, useState } from 'react';
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

const thStyle = {
    textAlign: 'left',
    padding: '10px 12px',
    borderBottom: '1px solid var(--border-color)',
    color: 'var(--text-tertiary)',
    fontSize: 11,
    textTransform: 'uppercase',
    letterSpacing: '.04em',
    background: 'var(--bg-app)',
};

const tdStyle = {
    padding: '10px 12px',
    borderBottom: '1px solid var(--border-color)',
    color: 'var(--text-secondary)',
    fontSize: 12,
};

// ────────────────────────────────────────────
// Dagre hierarchical layout
// ────────────────────────────────────────────
function applyDagreLayout(nodes, edges, direction = 'TB', opts = {}) {
    const {
        nodeWidth = 240,
        nodeHeight = 80,
        nodeSep = 60,
        rankSep = 100,
    } = opts;
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: direction, nodesep: nodeSep, ranksep: rankSep });

    nodes.forEach(n => {
        g.setNode(n.id, { width: nodeWidth, height: nodeHeight });
    });

    edges.forEach(e => {
        g.setEdge(e.source, e.target);
    });

    dagre.layout(g);

    return nodes.map(n => {
        const pos = g.node(n.id);
        return {
            ...n,
            position: { x: pos.x - (nodeWidth / 2), y: pos.y - (nodeHeight / 2) },
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

// ────────────────────────────────────────────
// Compact ER grid layout (Power BI-like readability)
// ────────────────────────────────────────────
function applyERGridLayout(nodes, edges) {
    const degree = new Map();
    nodes.forEach(n => degree.set(n.id, 0));
    edges.forEach(e => {
        degree.set(e.source, (degree.get(e.source) || 0) + 1);
        degree.set(e.target, (degree.get(e.target) || 0) + 1);
    });

    const ordered = [...nodes].sort((a, b) => {
        const d = (degree.get(b.id) || 0) - (degree.get(a.id) || 0);
        if (d !== 0) return d;
        return String(a?.data?.label || a.id).localeCompare(String(b?.data?.label || b.id));
    });

    const count = Math.max(1, ordered.length);
    const cols = Math.max(3, Math.min(7, Math.ceil(Math.sqrt(count))));
    const cellW = 260;
    const cellH = 150;
    const startX = 100;
    const startY = 80;

    const posById = new Map();
    ordered.forEach((n, i) => {
        const row = Math.floor(i / cols);
        const col = i % cols;
        posById.set(n.id, {
            x: startX + col * cellW,
            y: startY + row * cellH,
        });
    });

    return nodes.map(n => ({
        ...n,
        position: posById.get(n.id) || n.position,
    }));
}

// ────────────────────────────────────────────
// Dense model grid layout (for very large model counts)
// ────────────────────────────────────────────
function applyModelGridLayout(nodes, denseMode = false) {
    const ordered = [...nodes].sort((a, b) =>
        String(a?.data?.label || a.id).localeCompare(String(b?.data?.label || b.id))
    );

    const count = Math.max(1, ordered.length);
    const cols = denseMode
        ? Math.max(10, Math.min(36, Math.ceil(Math.sqrt(count * 1.8))))
        : Math.max(6, Math.min(24, Math.ceil(Math.sqrt(count * 1.6))));
    const cellW = denseMode ? 132 : 180;
    const cellH = denseMode ? 68 : 92;
    const startX = 60;
    const startY = 60;

    const posById = new Map();
    ordered.forEach((n, i) => {
        const row = Math.floor(i / cols);
        const col = i % cols;
        posById.set(n.id, {
            x: startX + col * cellW,
            y: startY + row * cellH,
        });
    });

    return nodes.map(n => ({
        ...n,
        position: posById.get(n.id) || n.position,
    }));
}

// ────────────────────────────────────────────
// Dense table grid layout (for table-only browsing)
// ────────────────────────────────────────────
function applyTableGridLayout(nodes, edges) {
    const degree = new Map();
    nodes.forEach(n => degree.set(n.id, 0));
    edges.forEach(e => {
        degree.set(e.source, (degree.get(e.source) || 0) + 1);
        degree.set(e.target, (degree.get(e.target) || 0) + 1);
    });

    const ordered = [...nodes].sort((a, b) => {
        const d = (degree.get(b.id) || 0) - (degree.get(a.id) || 0);
        if (d !== 0) return d;
        return String(a?.data?.label || a.id).localeCompare(String(b?.data?.label || b.id));
    });

    const count = Math.max(1, ordered.length);
    const dense = count > 260;
    const cols = dense
        ? Math.max(8, Math.min(28, Math.ceil(Math.sqrt(count * 1.5))))
        : Math.max(5, Math.min(18, Math.ceil(Math.sqrt(count * 1.3))));
    const cellW = dense ? 210 : 250;
    const cellH = dense ? 132 : 154;
    const startX = 70;
    const startY = 70;

    const posById = new Map();
    ordered.forEach((n, i) => {
        const row = Math.floor(i / cols);
        const col = i % cols;
        posById.set(n.id, {
            x: startX + col * cellW,
            y: startY + row * cellH,
        });
    });

    return nodes.map(n => ({
        ...n,
        position: posById.get(n.id) || n.position,
    }));
}

/**
 * DependencyGraph — the React Flow canvas for the DAG.
 */
export default function DependencyGraph({
    graphData,
    layout,
    erMode = false,
    selectedModelId = '__all__',
    selectedTableId = '__all__',
    searchQuery,
    filterType,
    showVersionBadges,
    onNodeClick,
    isLoading = false,
    snapshotId = null,
    diffMode = false,
}) {
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [versionCounts, setVersionCounts] = useState({});
    const [mappingOpen, setMappingOpen] = useState(false);

    const tableRows = useMemo(() => {
        const allNodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
        let rows = allNodes.filter(n => n?.data?.nodeType === 'table');

        if (selectedModelId && selectedModelId !== '__all__') {
            rows = rows.filter(n => {
                const mid = String(n?.data?.model_id || '').trim();
                if (!mid) return true;
                return mid === String(selectedModelId);
            });
        }

        if (selectedTableId && selectedTableId !== '__all__') {
            rows = rows.filter(n => String(n?.id || '') === String(selectedTableId));
        }

        const q = String(searchQuery || '').trim().toLowerCase();
        if (q) {
            rows = rows.filter(n => {
                const d = n?.data || {};
                const name = String(d.label || '').toLowerCase();
                const schema = String(d.schema || '').toLowerCase();
                const source = String(d.source_type || '').toLowerCase();
                const cols = Array.isArray(d.columns)
                    ? d.columns.map(c => String(c?.name || '')).join(' ').toLowerCase()
                    : '';
                return name.includes(q) || schema.includes(q) || source.includes(q) || cols.includes(q);
            });
        }

        return rows.sort((a, b) => {
            const as = String(a?.data?.schema || 'PUBLIC');
            const bs = String(b?.data?.schema || 'PUBLIC');
            const bySchema = as.localeCompare(bs);
            if (bySchema !== 0) return bySchema;
            return String(a?.data?.label || a?.id || '').localeCompare(String(b?.data?.label || b?.id || ''));
        });
    }, [graphData, selectedModelId, selectedTableId, searchQuery]);

    const mappingRows = useMemo(() => {
        const allNodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
        const allEdges = Array.isArray(graphData?.edges) ? graphData.edges : [];
        const byId = new Map(allNodes.map(n => [String(n.id), n]));

        let rels = allEdges.filter(e => String(e?.id || '').startsWith('rel-'));
        if (selectedModelId && selectedModelId !== '__all__') {
            rels = rels.filter(e => {
                const s = byId.get(String(e.source));
                const t = byId.get(String(e.target));
                return String(s?.data?.model_id || '') === String(selectedModelId)
                    || String(t?.data?.model_id || '') === String(selectedModelId);
            });
        }
        if (selectedTableId && selectedTableId !== '__all__') {
            rels = rels.filter(e => String(e.source) === String(selectedTableId) || String(e.target) === String(selectedTableId));
        }

        return rels.map(e => {
            const s = byId.get(String(e.source));
            const t = byId.get(String(e.target));
            return {
                id: e.id,
                fromTable: s?.data?.label || e.source,
                fromSchema: s?.data?.schema || 'PUBLIC',
                fromColumn: e?.data?.from_column || '-',
                toTable: t?.data?.label || e.target,
                toSchema: t?.data?.schema || 'PUBLIC',
                toColumn: e?.data?.to_column || '-',
                cardinality: e?.data?.cardinality || '-',
            };
        });
    }, [graphData, selectedModelId, selectedTableId]);

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

        // Scope graph to one model when requested (mainly for ER readability)
        if (selectedModelId && selectedModelId !== '__all__') {
            const modelNode = filteredNodes.find(n =>
                n?.data?.nodeType === 'model' &&
                (String(n?.data?.model_id || '') === String(selectedModelId) || String(n?.id || '') === `model-${selectedModelId}`)
            );

            if (modelNode) {
                const modelNodeId = modelNode.id;

                const attachedTableIds = new Set(
                    filteredEdges
                        .filter(e => (e?.source === modelNodeId || e?.target === modelNodeId))
                        .map(e => (e.source === modelNodeId ? e.target : e.source))
                );

                const scopedNodeIds = new Set([modelNodeId, ...attachedTableIds]);

                const scopedNodes = filteredNodes.filter(n => {
                    if (scopedNodeIds.has(n.id)) return true;
                    // Keep measures connected to this model
                    if (n?.data?.nodeType === 'measure') {
                        return filteredEdges.some(e =>
                            (e.source === modelNodeId && e.target === n.id) ||
                            (e.target === modelNodeId && e.source === n.id)
                        );
                    }
                    return false;
                });

                const scopedNodeIdSet = new Set(scopedNodes.map(n => n.id));
                const scopedEdges = filteredEdges.filter(e =>
                    scopedNodeIdSet.has(e.source) && scopedNodeIdSet.has(e.target)
                );

                filteredNodes = scopedNodes;
                filteredEdges = scopedEdges;
            }
        }

        // ER View (Power BI-like): table-centric with relationship links only
        if (erMode) {
            const tableNodes = filteredNodes.filter(n => n?.data?.nodeType === 'table');
            const tableIds = new Set(tableNodes.map(n => n.id));
            const relationshipEdges = filteredEdges.filter(e => {
                const isRel = String(e?.id || '').startsWith('rel-');
                return isRel && tableIds.has(e.source) && tableIds.has(e.target);
            });

            filteredNodes = tableNodes;
            filteredEdges = relationshipEdges;

            if (selectedTableId && selectedTableId !== '__all__') {
                const focusedId = String(selectedTableId);
                const selectedExists = filteredNodes.some(n => String(n.id) === focusedId);
                if (selectedExists) {
                    const keep = new Set([focusedId]);
                    filteredEdges.forEach(e => {
                        if (String(e.source) === focusedId) keep.add(String(e.target));
                        if (String(e.target) === focusedId) keep.add(String(e.source));
                    });
                    filteredNodes = filteredNodes.filter(n => keep.has(String(n.id)));
                    filteredEdges = filteredEdges.filter(e => keep.has(String(e.source)) && keep.has(String(e.target)));
                }
            }
        }

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
        const modelView = filterType === 'models';
        const tableView = filterType === 'tables';
        const denseModelView = modelView && filteredNodes.length > 350;
        const denseTableView = tableView && filteredNodes.length > 260;

        if (erMode) {
            filteredNodes = applyERGridLayout(filteredNodes, filteredEdges);
        } else if (modelView) {
            filteredNodes = applyModelGridLayout(filteredNodes, denseModelView);
        } else if (tableView) {
            filteredNodes = applyTableGridLayout(filteredNodes, filteredEdges);
        } else if (layout === 'hierarchical') {
            filteredNodes = applyDagreLayout(
                filteredNodes,
                filteredEdges,
                'TB',
                { nodeWidth: 240, nodeHeight: 80, nodeSep: 60, rankSep: 100 }
            );
        } else {
            filteredNodes = applyForceLayout(filteredNodes);
        }

        const styledNodes = filteredNodes.map(n => {
            const status = n?.data?.diffStatus;
            const baseData = {
                ...n.data,
                compactModelView: modelView,
                denseModelView,
                compactTableView: tableView,
                denseTableView,
            };
            if (status === 'added') {
                return {
                    ...n,
                    data: { ...baseData, diffStatus: status },
                };
            }
            if (status === 'removed') {
                return {
                    ...n,
                    data: { ...baseData, diffStatus: status },
                    style: {
                        ...(n.style || {}),
                        opacity: 0.75,
                    },
                };
            }
            if (status === 'modified') {
                return {
                    ...n,
                    data: { ...baseData, diffStatus: status },
                };
            }
            return { ...n, data: baseData };
        });

        const styledEdges = filteredEdges.map(e => {
            const status = e?.data?.diffStatus;
            const isRelationship = String(e?.id || '').startsWith('rel-');
            if (status === 'added') {
                return {
                    ...e,
                    animated: false,
                    style: {
                        ...(e.style || {}),
                        stroke: '#22C55E',
                        strokeWidth: 2.5,
                    },
                };
            }
            if (status === 'removed') {
                return {
                    ...e,
                    animated: false,
                    style: {
                        ...(e.style || {}),
                        stroke: '#EF4444',
                        strokeDasharray: '6 4',
                        strokeWidth: 2,
                    },
                };
            }
            if (status === 'modified') {
                return {
                    ...e,
                    animated: false,
                    style: {
                        ...(e.style || {}),
                        stroke: '#EAB308',
                        strokeWidth: 2.5,
                    },
                };
            }
            if (isRelationship) {
                return {
                    ...e,
                    animated: false,
                    style: {
                        ...(e.style || {}),
                        stroke: (e.style && e.style.stroke) || '#818CF8',
                        strokeWidth: 1.8,
                    },
                    labelStyle: {
                        fill: '#A5B4FC',
                        fontSize: 10,
                        fontWeight: 700,
                    },
                    labelBgStyle: {
                        fill: 'rgba(15,23,42,0.88)',
                    },
                    labelBgPadding: [4, 2],
                    labelBgBorderRadius: 4,
                };
            }
            return e;
        });

        setNodes(styledNodes);
        setEdges(styledEdges);
    }, [graphData, layout, erMode, selectedModelId, selectedTableId, searchQuery, filterType, showVersionBadges, versionCounts, setNodes, setEdges]);

    // ── Node click handler ──────────────────────
    const handleNodeClick = useCallback((_, node) => {
        onNodeClick?.({ ...(node?.data || {}), id: node?.id });
    }, [onNodeClick]);

    if (filterType === 'tables' && !erMode) {
        return (
            <div style={{ width: '100%', height: '100%', position: 'relative', overflow: 'auto', background: 'var(--bg-app)' }}>
                <div style={{ padding: '10px 12px' }}>
                    <div style={{
                        border: '1px solid var(--border-color)',
                        borderRadius: 10,
                        overflow: 'hidden',
                        background: 'var(--bg-surface)',
                    }}>
                        <div style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '10px 12px',
                            borderBottom: '1px solid var(--border-color)',
                            fontSize: 12,
                            color: 'var(--text-secondary)',
                            fontWeight: 700,
                        }}>
                            <span>Tables</span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                                <span style={{ color: 'var(--text-tertiary)', fontWeight: 600 }}>{tableRows.length} total</span>
                                <button
                                    onClick={() => setMappingOpen(true)}
                                    style={{
                                        border: '1px solid rgba(129,140,248,.5)',
                                        background: 'rgba(129,140,248,.12)',
                                        color: '#A5B4FC',
                                        borderRadius: 6,
                                        padding: '4px 8px',
                                        fontSize: 11,
                                        cursor: 'pointer',
                                        fontWeight: 600,
                                    }}
                                >
                                    Mapping View
                                </button>
                            </div>
                        </div>

                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                            <thead>
                                <tr>
                                    <th style={thStyle}>Table</th>
                                    <th style={thStyle}>Model</th>
                                    <th style={thStyle}>Schema</th>
                                    <th style={thStyle}>Columns</th>
                                    <th style={thStyle}>Rows</th>
                                    <th style={thStyle}>Preview Columns</th>
                                </tr>
                            </thead>
                            <tbody>
                                {tableRows.map((n) => {
                                    const d = n?.data || {};
                                    const columns = Array.isArray(d.columns) ? d.columns : [];
                                    const rowCount = typeof d.row_count === 'number'
                                        ? d.row_count
                                        : Array.isArray(d.preview_rows)
                                            ? d.preview_rows.length
                                            : '-';
                                    const previewCols = columns.slice(0, 6).map(c => c?.name || c).filter(Boolean).join(', ');

                                    return (
                                        <tr
                                            key={n.id}
                                            style={{ cursor: 'pointer' }}
                                        >
                                            <td 
                                                style={tdStyle}
                                                onClick={() => onNodeClick?.({ ...d, id: n.id })}
                                            >
                                                {d.label || n.id}
                                            </td>
                                            <td 
                                                style={tdStyle}
                                                onClick={() => onNodeClick?.({ ...d, id: n.id })}
                                            >
                                                {d.model_label || d.model_name || d.model_id || '-'}
                                            </td>
                                            <td 
                                                style={tdStyle}
                                                onClick={() => onNodeClick?.({ ...d, id: n.id })}
                                            >
                                                {d.schema || 'PUBLIC'}
                                            </td>
                                            <td 
                                                style={tdStyle}
                                                onClick={() => onNodeClick?.({ ...d, id: n.id })}
                                            >
                                                {columns.length}
                                            </td>
                                            <td 
                                                style={tdStyle}
                                                onClick={() => onNodeClick?.({ ...d, id: n.id })}
                                            >
                                                {rowCount}
                                            </td>
                                            <td style={{ ...tdStyle, color: 'var(--text-tertiary)' }}>
                                                <details style={{ cursor: 'pointer' }}>
                                                    <summary style={{ userSelect: 'none', color: '#A5B4FC', fontWeight: 600 }}>
                                                        {columns.length > 0 ? 'View Schema' : 'No columns'}
                                                    </summary>
                                                    {columns.length > 0 && (
                                                        <div style={{
                                                            marginTop: 8,
                                                            paddingTop: 8,
                                                            borderTop: '1px solid var(--border-color)',
                                                            maxHeight: 300,
                                                            overflowY: 'auto',
                                                        }}>
                                                            <table style={{ 
                                                                width: '100%', 
                                                                borderCollapse: 'collapse',
                                                                fontSize: 11,
                                                                marginTop: 4,
                                                            }}>
                                                                <thead>
                                                                    <tr>
                                                                        <th style={{
                                                                            textAlign: 'left',
                                                                            padding: '4px 6px',
                                                                            color: 'var(--text-tertiary)',
                                                                            fontSize: 10,
                                                                            fontWeight: 600,
                                                                            textTransform: 'uppercase',
                                                                            borderBottom: '1px solid var(--border-color)',
                                                                        }}>Name</th>
                                                                        <th style={{
                                                                            textAlign: 'left',
                                                                            padding: '4px 6px',
                                                                            color: 'var(--text-tertiary)',
                                                                            fontSize: 10,
                                                                            fontWeight: 600,
                                                                            textTransform: 'uppercase',
                                                                            borderBottom: '1px solid var(--border-color)',
                                                                        }}>Type</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {columns.map((col, idx) => {
                                                                        const colName = typeof col === 'string' ? col : (col?.name || col?.label || `col_${idx}`);
                                                                        const colType = col?.type || col?.data_type || col?.dtype || '-';
                                                                        return (
                                                                            <tr key={`${n.id}-col-${idx}`}>
                                                                                <td style={{
                                                                                    padding: '4px 6px',
                                                                                    color: 'var(--text-secondary)',
                                                                                    borderBottom: '1px solid rgba(129,140,248,.1)',
                                                                                }}>
                                                                                    {colName}
                                                                                </td>
                                                                                <td style={{
                                                                                    padding: '4px 6px',
                                                                                    color: 'var(--text-tertiary)',
                                                                                    borderBottom: '1px solid rgba(129,140,248,.1)',
                                                                                    fontSize: 10,
                                                                                }}>
                                                                                    {colType}
                                                                                </td>
                                                                            </tr>
                                                                        );
                                                                    })}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                    )}
                                                </details>
                                            </td>
                                        </tr>
                                    );
                                })}
                                {tableRows.length === 0 && (
                                    <tr>
                                        <td colSpan={6} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                            No tables found for current filter/search.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>

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
                            <span>Loading tables...</span>
                        </div>
                    </div>
                )}

                {mappingOpen && (
                    <div style={{
                        position: 'fixed',
                        inset: 0,
                        background: 'rgba(0,0,0,.45)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        padding: 16,
                        zIndex: 1200,
                    }}>
                        <div style={{
                            width: '96vw',
                            height: '86vh',
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-color)',
                            borderRadius: 10,
                            overflow: 'hidden',
                            display: 'flex',
                            flexDirection: 'column',
                        }}>
                            <div style={{
                                padding: '10px 12px',
                                borderBottom: '1px solid var(--border-color)',
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                background: 'var(--bg-app)',
                            }}>
                                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-secondary)' }}>Table Mapping View</span>
                                <button onClick={() => setMappingOpen(false)} style={{ border: 'none', background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: 16 }}>✕</button>
                            </div>
                            <div style={{ overflow: 'auto', flex: 1 }}>
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                                    <thead>
                                        <tr>
                                            <th style={thStyle}>From</th>
                                            <th style={thStyle}>From Column</th>
                                            <th style={thStyle}>To</th>
                                            <th style={thStyle}>To Column</th>
                                            <th style={thStyle}>Cardinality</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {mappingRows.map((m) => (
                                            <tr key={m.id}>
                                                <td style={tdStyle}>{m.fromSchema}.{m.fromTable}</td>
                                                <td style={tdStyle}>{m.fromColumn}</td>
                                                <td style={tdStyle}>{m.toSchema}.{m.toTable}</td>
                                                <td style={tdStyle}>{m.toColumn}</td>
                                                <td style={tdStyle}>{m.cardinality}</td>
                                            </tr>
                                        ))}
                                        {mappingRows.length === 0 && (
                                            <tr><td colSpan={5} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-tertiary)' }}>No relationships available for current selection.</td></tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                )}
            </div>
        );
    }

    const shouldAutoFitView = true;

    return (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
            <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={handleNodeClick}
                nodeTypes={nodeTypes}
                fitView={shouldAutoFitView}
                fitViewOptions={{ padding: 0.16 }}
                minZoom={0.02}
                maxZoom={2}
                defaultEdgeOptions={{
                    type: 'smoothstep',
                    animated: !erMode,
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
                            ...(erMode ? [] : [{ color: '#3B82F6', label: 'Semantic Model' }]),
                            { color: '#22C55E', label: 'Source Table' },
                            ...(erMode ? [{ color: '#818CF8', label: 'Relationship' }] : [{ color: '#EAB308', label: 'Metric / Measure' }]),
                            { color: '#EF4444', label: 'Broken Reference' },
                            ...(diffMode ? [
                                { color: '#22C55E', label: 'Diff: Added (green)' },
                                { color: '#EAB308', label: 'Diff: Modified (yellow)' },
                                { color: '#EF4444', label: 'Diff: Removed (red/dotted)' },
                            ] : []),
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
