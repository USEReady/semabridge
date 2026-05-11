import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Map as MapIcon, RefreshCw,
    LayoutGrid, Waypoints,
    Search, X, Filter,
    Eye, EyeOff,
    Database, FolderOpen, PanelRight, Files, ArrowLeft,
} from 'lucide-react';
import { api } from '../../utils/api';
import ComponentTree from './ComponentTree';
import DependencyGraph from './DependencyGraph';
import dagre from 'dagre';
import DetailPanel from './DetailPanel';
import ModelDataPanel from './ModelDataPanel';
import { ReactFlowProvider } from '@xyflow/react';
import usePageCache from '../../hooks/usePageCache';


function normalizeGraphPayload(rawGraph) {
    const rawNodes = Array.isArray(rawGraph?.nodes) ? rawGraph.nodes : [];
    const rawEdges = Array.isArray(rawGraph?.edges) ? rawGraph.edges : [];
    const graphModelId = String(rawGraph?.model || rawGraph?.model_id || '').trim();

    const provisionalNodes = rawNodes.map((node, idx) => {
        const nodeId = String(node?.id ?? `n-${idx}`);
        const incomingData = node?.data && typeof node.data === 'object' ? node.data : {};
        const semanticType = String(incomingData.nodeType || incomingData.type || '').toLowerCase();

        let nodeType = node?.type;
        if (!['modelNode', 'tableNode', 'measureNode'].includes(nodeType)) {
            if (semanticType === 'table') nodeType = 'tableNode';
            else if (semanticType === 'measure') nodeType = 'measureNode';
            else nodeType = 'modelNode';
        }

        return {
            ...node,
            id: nodeId,
            type: nodeType,
            data: {
                ...incomingData,
                nodeType: incomingData.nodeType || (nodeType === 'tableNode' ? 'table' : nodeType === 'measureNode' ? 'measure' : 'model'),
                label: incomingData.label || nodeId,
                model_id: incomingData.model_id || (graphModelId && graphModelId !== '__all__' ? graphModelId : nodeId),
            },
            position: node?.position && typeof node.position.x === 'number' && typeof node.position.y === 'number'
                ? node.position
                : { x: 0, y: 0 },
        };
    });

    const edges = rawEdges
        .filter((edge) => edge?.source && edge?.target)
        .map((edge, idx) => ({
            ...edge,
            id: String(edge.id || `e-${idx}-${edge.source}-${edge.target}`),
            source: String(edge.source),
            target: String(edge.target),
        }));

    const nodesById = new Map(provisionalNodes.map((node) => [node.id, node]));
    const modelIdsByNodeId = new Map();

    provisionalNodes.forEach((node) => {
        if (node?.data?.nodeType === 'model') {
            const modelId = String(node?.data?.model_id || node.id || '').trim();
            if (modelId) {
                modelIdsByNodeId.set(node.id, modelId);
            }
        }
    });

    edges.forEach((edge) => {
        const sourceNode = nodesById.get(edge.source);
        const targetNode = nodesById.get(edge.target);

        if (sourceNode?.data?.nodeType === 'model' && targetNode && !modelIdsByNodeId.has(targetNode.id)) {
            const modelId = String(sourceNode?.data?.model_id || '').trim();
            if (modelId) modelIdsByNodeId.set(targetNode.id, modelId);
        }

        if (targetNode?.data?.nodeType === 'model' && sourceNode && !modelIdsByNodeId.has(sourceNode.id)) {
            const modelId = String(targetNode?.data?.model_id || '').trim();
            if (modelId) modelIdsByNodeId.set(sourceNode.id, modelId);
        }
    });

    const nodes = provisionalNodes.map((node) => {
        const inferredModelId = modelIdsByNodeId.get(node.id);
        const existingModelId = String(node?.data?.model_id || '').trim();
        const resolvedModelId = existingModelId || inferredModelId || (graphModelId && graphModelId !== '__all__' ? graphModelId : '');

        return {
            ...node,
            data: {
                ...node.data,
                model_id: resolvedModelId || node.data.model_id,
            },
        };
    });

    return {
        nodes,
        edges,
        meta: rawGraph?.meta && typeof rawGraph.meta === 'object' ? rawGraph.meta : {},
    };
}

function isSystemTableName(name) {
    const v = String(name || '').trim().toLowerCase();
    if (!v) return false;
    return (
        v.startsWith('information_schema.') ||
        v.startsWith('pg_') ||
        v.startsWith('sqlite_') ||
        v.startsWith('duckdb_') ||
        v.startsWith('sys.') ||
        v.startsWith('__')
    );
}

/**
 * RepositoryMap — master container for the visual repo explorer.
 *
 * Top bar: search / filter / workspace / layout toggle / sync.
 * Left:   collapsible file tree.
 * Center: React-Flow dependency graph.
 * Right:  detail / file preview panel (slides in on selection).
 */
export default function RepositoryMap({ onClose, snapshotId, compareSnapshotId = null, diffMode = false }) {
    // ── data ──
    const [snapshotTreeData, setSnapshotTreeData] = useState(null);
    const [graphData, setGraphData] = useState({ nodes: [], edges: [], meta: {} });

    // ── UI state ──
    const [selectedFile, setSelectedFile] = useState(null);
    const [selectedNode, setSelectedNode] = useState(null);
    const [filePreview, setFilePreview] = useState(null);
    const [workbenchOpen, setWorkbenchOpen] = usePageCache('explore:workbenchOpen', false);
    const [showExplorer, setShowExplorer] = usePageCache('explore:showExplorer', true);

    const [layout, setLayout] = useState('hierarchical');           // hierarchical | force
    const [erMode, setErMode] = usePageCache('explore:erMode', true);                     // Power BI-like relationship view
    const [selectedModelId, setSelectedModelId] = usePageCache('explore:selectedModelId', '__all__');
    const [selectedConnector, setSelectedConnector] = usePageCache('explore:selectedConnector', '__all__');
    const [searchQuery, setSearchQuery] = useState('');
    const [filterType, setFilterType] = useState('all');            // all | models | tables | broken
    const [selectedTableId, setSelectedTableId] = usePageCache('explore:selectedTableId', '__all__');
    const [showVersionBadges, setShowVersionBadges] = useState(false);
    const [includeSystemTables, setIncludeSystemTables] = usePageCache('explore:includeSystemTables', false);
    const [allSnapshots, setAllSnapshots] = useState([]);
    const [selectedSnapshotId, setSelectedSnapshotId] = useState(null);

    const [syncing, setSyncing] = useState(false);
    const [loading, setLoading] = useState(true);
    const [treeLoading, setTreeLoading] = useState(true);
    const [graphLoading, setGraphLoading] = useState(true);
    const [diffLoading, setDiffLoading] = useState(false);
    const [diffReport, setDiffReport] = useState(null);
    const [inspectorResetToken, setInspectorResetToken] = useState(0);

    // ── initial load ────────────────────────────────
    const loadData = useCallback(async () => {
        setLoading(true);
        setTreeLoading(true);
        setGraphLoading(true);
        try {
            const [snapshotResp, snapshotsResp] = await Promise.all([
                api.getSnapshotTree().catch(() => ({ root: null })),
                api.getGraphSnapshots('__all__').catch(() => []),
            ]);

            setSnapshotTreeData(snapshotResp?.root || null);
            setTreeLoading(false);

            let graphResp;
            let effectiveSnapshotId = snapshotId;
            const snapshots = Array.isArray(snapshotsResp) ? snapshotsResp : [];
            setAllSnapshots(snapshots);
            if (!effectiveSnapshotId) {
                const sorted = [...snapshots].sort(
                    (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
                );
                effectiveSnapshotId = sorted[0]?.snapshot_id || null;
            }
            
            // Find the snapshot and set the composite key (model_name|snapshot_id)
            const effectiveSnapshot = snapshots.find((s) => s?.snapshot_id === effectiveSnapshotId) || null;
            if (effectiveSnapshot) {
                const compositeKey = `${effectiveSnapshot.model_name}|${effectiveSnapshotId}`;
                setSelectedSnapshotId(compositeKey);
            } else {
                setSelectedSnapshotId(null);
            }

            const modelScope = String(effectiveSnapshot?.model_name || '').trim() || '__all__';

            if (effectiveSnapshotId) {
                graphResp = await api.getGraphSnapshot(modelScope, effectiveSnapshotId, includeSystemTables).catch(() => null);
            }

            // Fallback only when no snapshot graph available
            if (!graphResp) {
                graphResp = await api.getModelGraph(modelScope).catch((err) => {
                    console.warn('Primary graph fallback failed:', err);
                    return null;
                });
            }

            const normalizedGraph = normalizeGraphPayload(graphResp);
            setGraphData(normalizedGraph);
            setGraphLoading(false);

            // Snowflake-only environments may have no repository graph yet.
            // Run discovery in the background so Explore doesn't stay blocked on it.
            if ((normalizedGraph.nodes || []).length === 0) {
                void api.discoverSnowflakeModels()
                    .then((discovered) => {
                        const models = Array.isArray(discovered) ? discovered : [];
                        if (!models.length) return;

                        const modelNodeId = 'model-snowflake-discovery';
                        const nodes = [{
                            id: modelNodeId,
                            type: 'modelNode',
                            position: { x: 420, y: 130 },
                            data: {
                                label: 'Snowflake Discovery',
                                model_id: 'snowflake',
                                workspace_id: '',
                                description: 'Discovered semantic objects',
                                status: 'valid',
                                nodeType: 'model',
                            },
                        }];
                        const edges = [];

                        models.forEach((m, idx) => {
                            const objName = String(m?.name || m?.displayName || m?.id || `object_${idx + 1}`);
                            const nodeId = `table-snowflake-${idx}`;
                            nodes.push({
                                id: nodeId,
                                type: 'tableNode',
                                position: { x: 120 + (idx % 4) * 260, y: 320 + Math.floor(idx / 4) * 130 },
                                data: {
                                    label: objName,
                                    table_name: objName,
                                    schema: String(m?.schema || m?.database_schema || 'PUBLIC'),
                                    source_type: 'snowflake',
                                    columns: Array.isArray(m?.columns) ? m.columns : [],
                                    nodeType: 'table',
                                    model_id: 'snowflake',
                                    status: 'valid',
                                },
                            });
                            edges.push({
                                id: `e-${modelNodeId}-${nodeId}`,
                                source: nodeId,
                                target: modelNodeId,
                                animated: false,
                                style: { stroke: '#22C55E' },
                            });
                        });

                        setGraphData({ nodes, edges, meta: { source: 'snowflake-discovery-fallback' } });
                    })
                    .catch(() => {
                        // Keep empty graph if discovery is unavailable.
                    });
            }
        } catch (err) {
            console.error('Failed to load repo map data:', err);
        } finally {
            setTreeLoading(false);
            setGraphLoading(false);
            setLoading(false);
        }
    }, [snapshotId, includeSystemTables]);

    useEffect(() => { loadData(); }, [loadData]);


    useEffect(() => {
        let active = true;
        async function loadDiff() {
            if (!diffMode || !snapshotId || !compareSnapshotId || snapshotId === compareSnapshotId) {
                setDiffReport(null);
                return;
            }
            setDiffLoading(true);
            try {
                const report = await api.compareGraphSnapshots('__all__', compareSnapshotId, snapshotId, includeSystemTables);
                if (active) setDiffReport(report || null);
            } catch {
                if (active) setDiffReport(null);
            } finally {
                if (active) setDiffLoading(false);
            }
        }
        loadDiff();
        return () => { active = false; };
    }, [diffMode, snapshotId, compareSnapshotId, includeSystemTables]);

    // ── sync handler ────────────────────────────────
    const handleSync = async () => {
        setSyncing(true);
        try {
            await api.syncRepo();
            await loadData();
        } catch (err) {
            console.error('Sync failed:', err);
        } finally {
            setSyncing(false);
        }
    };

    // ── snapshot selector handler ────────────────────
    const handleSnapshotChange = async (snapshotKey) => {
        // snapshotKey format: "model_name|snapshot_id" to support multi-project snapshots
        const [modelName, snapshotId] = snapshotKey.split('|');
        setSelectedSnapshotId(snapshotKey);
        setGraphLoading(true);
        try {
            const selectedSnap = allSnapshots.find(s => 
                s?.snapshot_id === snapshotId && s?.model_name === modelName
            );
            if (!selectedSnap) throw new Error('Snapshot not found');
            const modelScope = String(selectedSnap?.model_name || '').trim() || '__all__';
            const graphResp = await api.getGraphSnapshot(modelScope, snapshotId, includeSystemTables).catch(() => null);
            const normalizedGraph = graphResp ? normalizeGraphPayload(graphResp) : { nodes: [], edges: [], meta: {} };
            setGraphData(normalizedGraph);
        } catch (err) {
            console.error('Failed to load snapshot:', err);
        } finally {
            setGraphLoading(false);
        }
    };

    // ── file click ──────────────────────────────────
    const handleFileClick = async (filePath) => {
        setSelectedNode(null);
        setSelectedFile(filePath);
        try {
            let data;
            if (filePath.startsWith('snapshots/')) {
                // Extract model_id from snapshot path: snapshots/{ws}/{model_id}
                const parts = filePath.split('/');
                const modelId = parts[parts.length - 1];
                data = await api.getSnapshotFile(modelId);
            } else {
                data = await api.getRepoFile(filePath);
            }
            setFilePreview(data);
        } catch {
            setFilePreview({ name: filePath, content: '(failed to load)', language: 'text' });
        }
    };

    // ── node click ──────────────────────────────────
    const handleNodeClick = (nodeData) => {
        setSelectedFile(null);
        setFilePreview(null);
        if (nodeData?.nodeType === 'table' && nodeData?.id) {
            setSelectedTableId(String(nodeData.id));
        }
        setSelectedNode(nodeData);
    };

    const handleCloseDetail = () => {
        setSelectedFile(null);
        setFilePreview(null);
        setSelectedNode(null);
    };

    const showDetail = !!(filePreview || selectedNode);

    const snapshotAudit = useMemo(() => {
        if (!snapshotId) return null;

        const allNodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
        const allEdges = Array.isArray(graphData?.edges) ? graphData.edges : [];
        const tableNodes = allNodes.filter(n => n?.data?.nodeType === 'table');
        const brokenTables = tableNodes.filter(n => n?.data?.status === 'broken').length;
        const relationshipEdges = allEdges.filter(e => String(e?.id || '').startsWith('rel-')).length;
        const systemDetected = tableNodes.filter(n => {
            const schema = n?.data?.schema ? `${n.data.schema}.` : '';
            const table = n?.data?.table_name || n?.data?.label || '';
            return isSystemTableName(`${schema}${table}`);
        }).length;

        return {
            totalTables: tableNodes.length,
            totalRelationships: relationshipEdges,
            brokenTables,
            systemDetected,
            systemExcluded: Number(graphData?.meta?.system_tables_excluded || 0),
        };
    }, [graphData, snapshotId]);

    // ── filter chips ────────────────────────────────
    const chips = [
        { id: 'all', label: 'All' },
        { id: 'broken', label: 'Broken Refs' },
    ];

    // --- FULL SCREEN STATE ---
    const [fullScreen, setFullScreen] = useState(false);

    // --- DAGRE LAYOUT FOR CLEAN RELATIONSHIP DIAGRAM ---
    const getDagreLayoutedGraph = (rawGraph) => {
        const g = new dagre.graphlib.Graph();
        g.setDefaultEdgeLabel(() => ({}));
        // Layout direction: TB (top-bottom) or LR (left-right)
        const direction = 'LR';
        g.setGraph({ rankdir: direction, nodesep: 80, ranksep: 120, edgesep: 40 });

        // Assign rank for hierarchy: model=0, table=1, measure=2
        const nodeRanks = { model: 0, table: 1, measure: 2 };
        // Group nodes by model for visual clustering
        const modelGroups = {};

        (rawGraph.nodes || []).forEach((node) => {
            const type = String(node?.type || node?.data?.nodeType || '').toLowerCase();
            const nodeType = type.includes('model') ? 'model' : type.includes('table') ? 'table' : type.includes('measure') ? 'measure' : 'other';
            // Default node size (width, height)
            let width = 180, height = 60;
            if (nodeType === 'model') height = 70;
            if (nodeType === 'measure') height = 50;
            g.setNode(node.id, { ...node, width, height, rank: nodeRanks[nodeType] ?? 1 });
            // Group by model for later offset
            const modelId = node?.data?.model_id || node.id;
            if (!modelGroups[modelId]) modelGroups[modelId] = [];
            modelGroups[modelId].push(node.id);
        });
        (rawGraph.edges || []).forEach((edge) => {
            g.setEdge(edge.source, edge.target, { ...edge });
        });

        dagre.layout(g);

        // Apply dagre-calculated positions, then offset by model group for visual clustering
        const modelOffsets = {};
        let groupIdx = 0;
        const groupSpacing = 320; // space between model clusters
        Object.keys(modelGroups).forEach((modelId) => {
            modelOffsets[modelId] = groupIdx * groupSpacing;
            groupIdx++;
        });

        const nodes = (rawGraph.nodes || []).map((node) => {
            const dagreNode = g.node(node.id);
            const modelId = node?.data?.model_id || node.id;
            // Offset x by model group for visual grouping
            const offsetX = modelOffsets[modelId] || 0;
            return {
                ...node,
                position: {
                    x: (dagreNode?.x || 0) + offsetX,
                    y: dagreNode?.y || 0,
                },
                // For React Flow, must set positionAbsolute for deterministic layout
                positionAbsolute: {
                    x: (dagreNode?.x || 0) + offsetX,
                    y: dagreNode?.y || 0,
                },
                // Optionally, lock nodes to prevent drag (optional)
                draggable: false,
            };
        });
        // Edges: force type 'step' for orthogonal routing
        const edges = (rawGraph.edges || []).map((edge) => ({
            ...edge,
            type: 'step',
        }));
        return { ...rawGraph, nodes, edges };
    };

    const renderedGraphData = useMemo(() => {
        // Use diff graph if in diff mode
        let baseGraph = graphData;
        if (diffMode) {
            const styled = diffReport?.styled_graph;
            if (styled && Array.isArray(styled.nodes) && styled.nodes.length > 0) {
                baseGraph = normalizeGraphPayload(styled);
            }
        }
        // Only apply dagre layout in ER mode (relationship view)
        if (erMode && baseGraph.nodes && baseGraph.nodes.length > 0) {
            return getDagreLayoutedGraph(baseGraph);
        }
        // For non-ER mode, keep original positions (e.g., force layout)
        return baseGraph;
    }, [diffMode, diffReport, graphData, erMode]);

    const connectorOptions = useMemo(() => {
        const nodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
        const connectors = new Set();
        nodes.forEach(n => {
            if (n.data?.nodeType === 'model') {
                const connector = n.data?.source_type || n.data?.target_type || 'Generic';
                connectors.add(connector);
            }
        });
        return Array.from(connectors)
            .map(c => ({ 
                id: c, 
                label: c.charAt(0).toUpperCase() + c.slice(1).toLowerCase() 
            }))
            .sort((a,b) => a.label.localeCompare(b.label));
    }, [graphData]);

    const modelOptions = useMemo(() => {
        const nodes = Array.isArray(renderedGraphData?.nodes) ? renderedGraphData.nodes : [];
        const explicitModels = nodes
            .filter(n => n?.data?.nodeType === 'model')
            .map(n => ({
                id: String(n?.data?.model_id || n.id || ''),
                label: String(n?.data?.label || n?.data?.model_id || n.id || 'Model'),
            }))
            .filter(m => m.id);

        const inferredModels = nodes
            .filter(n => n?.data?.nodeType === 'table' || n?.data?.nodeType === 'measure')
            .map(n => {
                const id = String(n?.data?.model_id || '').trim();
                return id ? { id, label: id } : null;
            })
            .filter(Boolean);

        const models = explicitModels.length ? explicitModels : inferredModels;

        const dedup = new Map();
        models.forEach(m => {
            if (!dedup.has(m.id)) dedup.set(m.id, m);
        });

        const dedupValues = [...dedup.values()];
        const counts = {};
        dedupValues.forEach(m => {
            const key = String(m.label || '').trim().toLowerCase();
            counts[key] = (counts[key] || 0) + 1;
        });

        const byId = dedupValues.map(m => {
            const rawLabel = String(m.label || '').trim() || 'Model';
            const key = rawLabel.toLowerCase();
            const shortId = String(m.id).slice(0, 8);
            const isGeneric = key === 'fabricmodel' || key === 'model' || key === 'semanticmodel';
            const label = (counts[key] > 1 || isGeneric)
                ? `${rawLabel} (${shortId})`
                : rawLabel;
                
            // Find connector for this model
            const node = nodes.find(n => n.id === m.id || n.data?.model_id === m.id);
            const connector = node?.data?.source_type || node?.data?.target_type || 'Generic';
            
            return { ...m, label, connector };
        });

        if (selectedConnector === '__all__') return byId;
        return byId.filter(m => m.connector === selectedConnector);
    }, [renderedGraphData, selectedConnector]);

    const tableOptions = useMemo(() => {
        const nodes = Array.isArray(renderedGraphData?.nodes) ? renderedGraphData.nodes : [];
        return nodes
            .filter(n => n?.data?.nodeType === 'table')
            .filter(n => {
                if (selectedModelId === '__all__') return true;
                const mid = String(n?.data?.model_id || '').trim();
                // If model_id is missing on table node, don't hide it from selector.
                if (!mid) return true;
                return mid === String(selectedModelId);
            })
            .map(n => ({
                id: String(n.id),
                name: String(n?.data?.label || n.id),
                schema: String(n?.data?.schema || 'PUBLIC'),
                model: String(n?.data?.model_label || n?.data?.model_name || n?.data?.model_id || selectedModelId || 'unknown'),
            }))
            .sort((a, b) => {
                const s = a.schema.localeCompare(b.schema);
                if (s !== 0) return s;
                return a.name.localeCompare(b.name);
            });
    }, [renderedGraphData, selectedModelId]);

    useEffect(() => {
        if (selectedTableId === '__all__') return;
        if (!tableOptions.some(t => t.id === selectedTableId)) {
            setSelectedTableId('__all__');
        }
    }, [tableOptions, selectedTableId]);

    const focusTableInER = useCallback((tableId) => {
        if (!tableId) return;
        setSelectedTableId(String(tableId));
        setErMode(true);
        setFilterType('tables');
    }, []);

    useEffect(() => {
        if (selectedModelId !== '__all__' && !modelOptions.some((m) => m.id === selectedModelId)) {
            setSelectedModelId('__all__');
            setSelectedTableId('__all__');
            return;
        }
        if (selectedModelId === '__all__' && erMode && modelOptions.length > 1) {
            setSelectedModelId(modelOptions[0].id);
        }
    }, [erMode, modelOptions, selectedModelId]);

    useEffect(() => {
        if (filterType === 'models' && erMode) {
            setErMode(false);
        }
        if (filterType === 'tables' && !erMode) {
            // Tables tab is better in ER view for this workspace.
            setErMode(true);
        }
    }, [filterType, erMode]);

    const dropdownStyle = {
        border: '1px solid var(--border-color)',
        borderRadius: 6,
        background: 'var(--bg-app)',
        color: 'var(--text-secondary)',
        fontSize: 11,
        padding: '4px 8px',
        maxWidth: 220,
        minWidth: 120,
    };

    return (
        <div style={{
            display: 'flex', flexDirection: 'column',
            height: '100%', width: '100%',
            background: 'var(--bg-app)', color: 'var(--text-primary)',
        }}>
            {/* ─── TOP BAR (CLEAN & TECHNICAL) ─── */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '10px 16px',
                borderBottom: '1px solid var(--border-color)',
                background: 'var(--bg-surface)',
                flexShrink: 0,
            }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Waypoints size={18} style={{ color: '#2563EB' }} />
                    <span style={{ fontWeight: 700, fontSize: 14, letterSpacing: '-0.01em' }}>Semantic Explorer</span>
                </div>

                <div style={{ width: 1, height: 20, background: 'var(--border-color)', margin: '0 8px' }} />

                <div style={{ display: 'flex', gap: 4 }}>
                    {chips.map(c => (
                        <button key={c.id}
                            onClick={() => setFilterType(c.id)}
                            style={{
                                ...chipStyle,
                                ...(filterType === c.id ? activeChipStyle : {}),
                            }}
                        >
                            {c.label}
                        </button>
                    ))}
                </div>

                <div style={{ flex: 1 }} />

                {/* Snapshot Selector Dropdown */}
                <select
                    value={selectedSnapshotId || ''}
                    onChange={(e) => handleSnapshotChange(e.target.value)}
                    style={{
                        fontSize: 11,
                        padding: '4px 8px',
                        borderRadius: 4,
                        border: '1px solid var(--border-color)',
                        background: 'var(--bg-app)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                        minWidth: 200,
                    }}
                >
                    {allSnapshots.length === 0 ? (
                        <option value="">No snapshots</option>
                    ) : (
                        allSnapshots.map(snap => (
                            <option key={`${snap.model_name}|${snap.snapshot_id}`} value={snap.snapshot_id}>
                                {snap.model_name} • {new Date(snap.timestamp).toLocaleString()} {snap.version_tag ? `(${snap.version_tag})` : ''}
                            </option>
                        ))
                    )}
                </select>

                <button
                    onClick={() => setErMode(v => !v)}
                    style={{
                        ...toolBtnStyle,
                        color: erMode ? '#2563EB' : 'var(--text-tertiary)',
                        background: erMode ? 'rgba(37, 99, 235, 0.1)' : 'transparent',
                        padding: '4px 10px',
                    }}
                >
                    <Database size={15} />
                    <span style={{ fontSize: 11, fontWeight: 600 }}>ER View</span>
                </button>

                <button
                    onClick={handleSync}
                    disabled={syncing}
                    style={{
                        ...toolBtnStyle,
                        background: '#2563EB',
                        color: '#fff',
                        padding: '4px 12px',
                        borderRadius: 6,
                    }}
                >
                    <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
                    <span style={{ fontSize: 11, fontWeight: 600 }}>Sync Metadata</span>
                </button>
            </div>

            {/* ─── BODY (3-PANEL LAYOUT) ─── */}
            <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
                
                {/* 1. Component Tree (Left) */}
                <div style={{
                    width: 280, minWidth: 280,
                    borderRight: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    display: 'flex', flexDirection: 'column',
                }}>
                    <div style={{
                        padding: '12px 16px', borderBottom: '1px solid var(--border-color)',
                        fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
                        color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8
                    }}>
                        <Files size={14} />
                        Metadata Components
                    </div>
                    <div style={{ flex: 1, overflow: 'hidden' }}>
                        <ComponentTree 
                            nodes={graphData.nodes}
                            onNodeClick={(node) => handleNodeClick(node.data)}
                            selectedId={selectedNode?.id}
                        />
                    </div>
                </div>

                {/* 2. Relationship Canvas (Center) */}
                <div style={{ flex: 1, position: 'relative', background: 'var(--bg-app)' }}>
                    <ReactFlowProvider>
                        <DependencyGraph
                            graphData={renderedGraphData}
                            layout={layout}
                            erMode={erMode}
                            selectedModelId={selectedModelId}
                            selectedTableId={selectedTableId}
                            searchQuery={searchQuery}
                            filterType={filterType}
                            showVersionBadges={showVersionBadges}
                            onNodeClick={handleNodeClick}
                            isLoading={graphLoading}
                            snapshotId={snapshotId}
                            diffMode={diffMode}
                            defaultEdgeOptions={{ type: 'step' }}
                            fullScreenMode={fullScreen}
                            onRequestFullScreen={() => setFullScreen(true)}
                        />
                    </ReactFlowProvider>
                </div>

                {/* 3. Metadata Inspector (Right) */}
                <div style={{
                    width: 400, minWidth: 400,
                    borderLeft: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    display: 'flex', flexDirection: 'column',
                    transition: 'width 0.3s ease',
                }}>
                    <div style={{
                        padding: '12px 16px', borderBottom: '1px solid var(--border-color)',
                        fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
                        color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'space-between'
                    }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <PanelRight size={14} />
                            Inspector
                        </div>
                        {selectedNode && (
                            <button onClick={() => setSelectedNode(null)} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                                <X size={14} />
                            </button>
                        )}
                    </div>
                    <div style={{ flex: 1, overflow: 'hidden' }}>
                        <ModelDataPanel
                            graphData={renderedGraphData}
                            selectedModelId={selectedModelId}
                            erMode={erMode}
                            selectedTableId={selectedNode?.id || selectedTableId}
                            onSelectTable={setSelectedTableId}
                            onOpenTableER={focusTableInER}
                            compact
                            showTabHeader={false}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}

// ── Inline micro-styles ──
const iconBtnStyle = {
    border: 'none', background: 'transparent', cursor: 'pointer',
    color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center',
    padding: 2,
};
const toolBtnStyle = {
    border: 'none', background: 'transparent', cursor: 'pointer',
    display: 'flex', alignItems: 'center', gap: 4,
    padding: '4px 6px', borderRadius: 4,
    transition: 'all .15s',
};
const chipStyle = {
    border: '1px solid var(--border-color)',
    background: 'transparent',
    color: 'var(--text-secondary)',
    fontSize: 11,
    padding: '3px 10px',
    borderRadius: 12,
    cursor: 'pointer',
    transition: 'all .15s',
};
const activeChipStyle = {
    background: 'var(--accent-blue)',
    border: '1px solid var(--accent-blue)',
    color: '#fff',
    fontWeight: 600,
};

function AuditCard({ label, value, tone }) {
    return (
        <div style={{
            border: '1px solid var(--border-color)',
            borderRadius: 8,
            padding: '8px 10px',
            background: 'var(--bg-app)',
        }}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.05em', color: 'var(--text-tertiary)', marginBottom: 4 }}>
                {label}
            </div>
            <div style={{ fontSize: 16, fontWeight: 800, color: tone }}>
                {value}
            </div>
        </div>
    );
}

const thStyle = {
    textAlign: 'left',
    borderBottom: '1px solid var(--border-color)',
    padding: '6px 8px',
    color: 'var(--text-tertiary)',
    textTransform: 'uppercase',
    letterSpacing: '.04em',
    fontSize: 10,
};

const tdStyle = {
    borderBottom: '1px solid var(--border-color)',
    padding: '6px 8px',
    color: 'var(--text-secondary)',
};
