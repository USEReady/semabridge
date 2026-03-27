import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Map as MapIcon, RefreshCw,
    LayoutGrid, Waypoints,
    Search, X, Filter,
    Eye, EyeOff,
    Database, FolderOpen, PanelRight, Files, ArrowLeft,
} from 'lucide-react';
import { api } from '../../utils/api';
import FileTreePanel from './FileTreePanel';
import DependencyGraph from './DependencyGraph';
import dagre from 'dagre';
import DetailPanel from './DetailPanel';
import ModelDataPanel from './ModelDataPanel';
import { ReactFlowProvider } from '@xyflow/react';

const EXPLORE_UI_PREFS_KEY = 'semabridge:explore-ui-prefs';

function readExploreUiPrefs() {
    try {
        const raw = localStorage.getItem(EXPLORE_UI_PREFS_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
        return {};
    }
}

function normalizeGraphPayload(rawGraph) {
    const rawNodes = Array.isArray(rawGraph?.nodes) ? rawGraph.nodes : [];
    const rawEdges = Array.isArray(rawGraph?.edges) ? rawGraph.edges : [];

    const nodes = rawNodes.map((node, idx) => {
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
                model_id: incomingData.model_id || nodeId,
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
    const uiPrefs = readExploreUiPrefs();

    // ── data ──
    const [snapshotTreeData, setSnapshotTreeData] = useState(null);
    const [graphData, setGraphData] = useState({ nodes: [], edges: [], meta: {} });

    // ── UI state ──
    const [selectedFile, setSelectedFile] = useState(null);
    const [selectedNode, setSelectedNode] = useState(null);
    const [filePreview, setFilePreview] = useState(null);
    const [workbenchOpen, setWorkbenchOpen] = useState(Boolean(uiPrefs?.workbenchOpen));
    const [showExplorer, setShowExplorer] = useState(uiPrefs?.showExplorer !== false);

    const [layout, setLayout] = useState('hierarchical');           // hierarchical | force
    const [erMode, setErMode] = useState(uiPrefs?.erMode ?? true);                     // Power BI-like relationship view
    const [selectedModelId, setSelectedModelId] = useState(uiPrefs?.selectedModelId || '__all__');
    const [searchQuery, setSearchQuery] = useState('');
    const [filterType, setFilterType] = useState('all');            // all | models | tables | broken
    const [selectedTableId, setSelectedTableId] = useState(uiPrefs?.selectedTableId || '__all__');
    const [showVersionBadges, setShowVersionBadges] = useState(false);
    const [includeSystemTables, setIncludeSystemTables] = useState(Boolean(uiPrefs?.includeSystemTables));

    const [syncing, setSyncing] = useState(false);
    const [loading, setLoading] = useState(true);
    const [diffLoading, setDiffLoading] = useState(false);
    const [diffReport, setDiffReport] = useState(null);
    const [inspectorResetToken, setInspectorResetToken] = useState(0);

    // ── initial load ────────────────────────────────
    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [, snapshotResp, snapshotsResp] = await Promise.all([
                api.getRepoTree(),
                api.getSnapshotTree().catch(() => ({ root: null })),
                api.getGraphSnapshots('__all__').catch(() => []),
            ]);

            // Always load full graph so model/table selectors can render complete lists.
            // Scoping to selected model is handled in frontend rendering.
            const modelScope = '__all__';

            let graphResp;
            let effectiveSnapshotId = snapshotId;
            if (!effectiveSnapshotId) {
                const snapshots = Array.isArray(snapshotsResp) ? snapshotsResp : [];
                const sorted = [...snapshots].sort(
                    (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
                );
                effectiveSnapshotId = sorted[0]?.snapshot_id || null;
            }

            if (effectiveSnapshotId) {
                graphResp = await api.getGraphSnapshot(modelScope, effectiveSnapshotId, includeSystemTables).catch(() => null);
            }

            // Fallback only when no snapshot graph available
            if (!graphResp) {
                graphResp = await api.getModelGraph(modelScope);
            }

            let normalizedGraph = normalizeGraphPayload(graphResp);

            // Snowflake-only environments may have no repository graph yet.
            // Fall back to discovery so Model Explorer still renders entities.
            if ((normalizedGraph.nodes || []).length === 0) {
                try {
                    const discovered = await api.discoverSnowflakeModels();
                    const models = Array.isArray(discovered) ? discovered : [];
                    if (models.length > 0) {
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

                        normalizedGraph = { nodes, edges, meta: { source: 'snowflake-discovery-fallback' } };
                    }
                } catch {
                    // Keep empty graph if discovery is unavailable.
                }
            }

            setSnapshotTreeData(snapshotResp.root);
            setGraphData(normalizedGraph);
        } catch (err) {
            console.error('Failed to load repo map data:', err);
        } finally {
            setLoading(false);
        }
    }, [snapshotId, includeSystemTables]);

    useEffect(() => { loadData(); }, [loadData]);

    useEffect(() => {
        try {
            localStorage.setItem(EXPLORE_UI_PREFS_KEY, JSON.stringify({
                workbenchOpen,
                showExplorer,
                erMode,
                selectedModelId,
                selectedTableId,
                includeSystemTables,
            }));
        } catch {
            // ignore persistence failures
        }
    }, [workbenchOpen, showExplorer, erMode, selectedModelId, selectedTableId, includeSystemTables]);

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
        { id: 'models', label: 'Models' },
        { id: 'tables', label: 'Tables' },
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

        const byId = [...dedup.values()];

        // If multiple models share same display label (e.g., many "FabricModel"),
        // append a short id suffix so dropdown entries are distinguishable.
        const counts = {};
        byId.forEach(m => {
            const key = String(m.label || '').trim().toLowerCase();
            counts[key] = (counts[key] || 0) + 1;
        });

        return byId.map(m => {
            const rawLabel = String(m.label || '').trim() || 'Model';
            const key = rawLabel.toLowerCase();
            const shortId = String(m.id).slice(0, 8);
            const isGeneric = key === 'fabricmodel' || key === 'model' || key === 'semanticmodel';
            const label = (counts[key] > 1 || isGeneric)
                ? `${rawLabel} (${shortId})`
                : rawLabel;
            return { ...m, label };
        });
    }, [renderedGraphData]);

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
        if (!erMode && filterType !== 'tables') {
            setSelectedModelId('__all__');
            return;
        }
        // In ER mode default to a specific model when multiple are present for readability.
        if (selectedModelId === '__all__' && modelOptions.length > 1) {
            setSelectedModelId(modelOptions[0].id);
        }
    }, [erMode, filterType, modelOptions, selectedModelId]);

    useEffect(() => {
        if (filterType === 'models' && erMode) {
            setErMode(false);
        }
        if (filterType === 'tables' && !erMode) {
            // Tables tab is better in ER view for this workspace.
            setErMode(true);
        }
    }, [filterType, erMode]);

    return (
        <div style={{
            display: 'flex', flexDirection: 'column',
            height: '100%', width: '100%',
            background: 'var(--bg-app)', color: 'var(--text-primary)',
        }}>
            {/* ─── TOP BAR ─── */}
            <div style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '8px 16px',
                borderBottom: '1px solid var(--border-color)',
                background: 'var(--bg-surface)',
                flexShrink: 0,
                overflowX: 'auto',
                overflowY: 'hidden',
            }}>
                {/* Title */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <MapIcon size={16} style={{ color: '#818CF8' }} />
                    <span style={{ fontWeight: 700, fontSize: 13 }}>Repository Map</span>
                </div>

                <div style={{ width: 1, height: 20, background: 'var(--border-color)' }} />

                {/* Search */}
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '4px 10px',
                    borderRadius: 6,
                    background: 'var(--bg-app)',
                    border: '1px solid var(--border-color)',
                    flex: '0 1 280px',
                }}>
                    <Search size={13} style={{ color: 'var(--text-tertiary)' }} />
                    <input
                        type="text"
                        placeholder="Search models, tables, measures..."
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        style={{
                            border: 'none', outline: 'none', background: 'transparent',
                            color: 'var(--text-primary)', fontSize: 12, flex: 1,
                        }}
                    />
                    {searchQuery && (
                        <button onClick={() => setSearchQuery('')}
                            style={iconBtnStyle}>
                            <X size={12} />
                        </button>
                    )}
                </div>

                {/* Filter chips */}
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

                <button
                    onClick={() => setWorkbenchOpen(v => !v)}
                    title="Open model/data inspector"
                    style={{
                        ...toolBtnStyle,
                        color: workbenchOpen ? '#818CF8' : 'var(--text-tertiary)',
                        marginLeft: 4,
                    }}
                >
                    <PanelRight size={15} />
                    <span style={{ fontSize: 11 }}>Inspector</span>
                </button>

                <button
                    onClick={() => setShowExplorer(v => !v)}
                    title="Toggle snapshot/files explorer"
                    style={{
                        ...toolBtnStyle,
                        color: showExplorer ? '#818CF8' : 'var(--text-tertiary)',
                    }}
                >
                    <Files size={15} />
                    <span style={{ fontSize: 11 }}>Explorer</span>
                </button>

                {(erMode || filterType === 'tables') && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 8, flexShrink: 0 }}>
                        <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', whiteSpace: 'nowrap' }}>
                            Model
                        </span>
                        <select
                            value={selectedModelId}
                            onChange={(e) => setSelectedModelId(e.target.value)}
                            style={{
                                border: '1px solid var(--border-color)',
                                borderRadius: 6,
                                background: 'var(--bg-app)',
                                color: 'var(--text-secondary)',
                                fontSize: 11,
                                padding: '4px 8px',
                                maxWidth: 220,
                                minWidth: 120,
                            }}
                            title="Choose model"
                        >
                            <option value="__all__">All models</option>
                            {modelOptions.map(m => (
                                <option key={m.id} value={m.id}>{m.label}</option>
                            ))}
                        </select>

                        <select
                            value={selectedTableId}
                            onChange={(e) => setSelectedTableId(e.target.value)}
                            style={{
                                border: '1px solid var(--border-color)',
                                borderRadius: 6,
                                background: 'var(--bg-app)',
                                color: 'var(--text-secondary)',
                                fontSize: 11,
                                padding: '4px 8px',
                                maxWidth: 220,
                                minWidth: 120,
                            }}
                            title="Choose table"
                        >
                            <option value="__all__">All tables</option>
                            {tableOptions.map(t => (
                                <option key={t.id} value={t.id}>{t.schema}.{t.name} · {t.model}</option>
                            ))}
                        </select>
                    </div>
                )}

                <div style={{ flex: 1 }} />

                {/* Version badge toggle */}
                {!erMode && (
                    <button
                        onClick={() => setShowVersionBadges(v => !v)}
                        title="Toggle version history badges"
                        style={{
                            ...toolBtnStyle,
                            color: showVersionBadges ? '#818CF8' : 'var(--text-tertiary)',
                        }}
                    >
                        {showVersionBadges ? <Eye size={15} /> : <EyeOff size={15} />}
                        <span style={{ fontSize: 11 }}>Versions</span>
                    </button>
                )}

                {snapshotId && (
                    <button
                        onClick={() => setIncludeSystemTables(v => !v)}
                        title="Include system-generated tables in snapshot"
                        style={{
                            ...toolBtnStyle,
                            color: includeSystemTables ? '#818CF8' : 'var(--text-tertiary)',
                        }}
                    >
                        <Filter size={15} />
                        <span style={{ fontSize: 11 }}>{includeSystemTables ? 'System: On' : 'System: Off'}</span>
                    </button>
                )}

                {/* Layout toggles */}
                <button
                    onClick={() => {
                        setErMode(v => {
                            const next = !v;
                            return next;
                        });
                    }}
                    title="Toggle ER relationship view"
                    style={{
                        ...toolBtnStyle,
                        color: erMode ? '#818CF8' : 'var(--text-tertiary)',
                    }}
                >
                    <Database size={15} />
                    <span style={{ fontSize: 11 }}>ER View</span>
                </button>

                {!erMode && (
                    <>
                        <button
                            onClick={() => setLayout('hierarchical')}
                            title="Hierarchical layout"
                            style={{
                                ...toolBtnStyle,
                                color: layout === 'hierarchical' ? '#818CF8' : 'var(--text-tertiary)',
                            }}
                        >
                            <LayoutGrid size={15} />
                        </button>
                        <button
                            onClick={() => setLayout('force')}
                            title="Force-directed layout"
                            style={{
                                ...toolBtnStyle,
                                color: layout === 'force' ? '#818CF8' : 'var(--text-tertiary)',
                            }}
                        >
                            <Waypoints size={15} />
                        </button>
                    </>
                )}

                {/* Sync */}
                <button
                    onClick={handleSync}
                    disabled={syncing}
                    title="Refresh Map"
                    style={{
                        ...toolBtnStyle,
                        background: '#818CF8',
                        color: '#fff',
                        padding: '4px 10px',
                        borderRadius: 6,
                        opacity: syncing ? .6 : 1,
                    }}
                >
                    <RefreshCw size={13} className={syncing ? 'animate-spin' : ''} />
                    <span style={{ fontSize: 11, fontWeight: 600 }}>
                        {syncing ? 'Syncing...' : 'Refresh'}
                    </span>
                </button>

                {onClose && (
                    <button onClick={onClose} style={iconBtnStyle} title="Close map">
                        <X size={16} />
                    </button>
                )}
            </div>

            {snapshotAudit && (
                <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(4, minmax(120px, 1fr))',
                    gap: 10,
                    padding: '10px 16px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                }}>
                    <AuditCard label="Total Tables" value={snapshotAudit.totalTables} tone="#818CF8" />
                    <AuditCard label="Relationships" value={snapshotAudit.totalRelationships} tone="#22C55E" />
                    <AuditCard label="Broken Refs" value={snapshotAudit.brokenTables} tone={snapshotAudit.brokenTables > 0 ? '#EF4444' : '#22C55E'} />
                    <AuditCard
                        label={includeSystemTables ? 'System Included' : 'System Excluded'}
                        value={includeSystemTables ? snapshotAudit.systemDetected : snapshotAudit.systemExcluded}
                        tone={includeSystemTables ? '#F59E0B' : '#818CF8'}
                    />
                </div>
            )}

            {diffMode && (
                <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '8px 16px',
                    borderBottom: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    fontSize: 11,
                }}>
                    <span style={{ fontWeight: 700, color: '#818CF8' }}>Diff Mode</span>
                    {diffLoading ? (
                        <span style={{ color: 'var(--text-tertiary)' }}>Comparing snapshots...</span>
                    ) : (
                        <>
                            <span style={{ color: '#22C55E' }}>+ {diffReport?.summary?.added || 0} added</span>
                            <span style={{ color: '#EF4444' }}>- {diffReport?.summary?.removed || 0} removed</span>
                            <span style={{ color: '#EAB308' }}>~ {diffReport?.summary?.modified || 0} modified</span>
                            <span style={{ color: 'var(--text-tertiary)' }}>Relationships: +{diffReport?.summary?.relationships_added || 0} / -{diffReport?.summary?.relationships_removed || 0}</span>
                        </>
                    )}
                </div>
            )}

            {/* ─── BODY ─── */}
            <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
                {/* File Tree */}
                {showExplorer && (
                <div style={{
                    width: 260, minWidth: 200,
                    borderRight: '1px solid var(--border-color)',
                    overflow: 'hidden',
                    background: 'var(--bg-surface)',
                    display: 'flex', flexDirection: 'column',
                }}>
                    {/* Snapshots Header */}
                    <div style={{
                        display: 'flex', borderBottom: '1px solid var(--border-color)',
                        background: 'var(--bg-app)', flexShrink: 0,
                        padding: '6px 8px', alignItems: 'center', gap: 4,
                    }}>
                        <Database size={12} />
                        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>Snapshots</span>
                    </div>
                    <div style={{ flex: 1, overflow: 'auto' }}>
                    {loading ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
                            Loading tree...
                        </div>
                    ) : (
                        <FileTreePanel
                            tree={snapshotTreeData}
                            onFileClick={handleFileClick}
                            selectedPath={selectedFile}
                        />
                    )}
                    </div>
                </div>
                )}

                {/* Dependency Graph */}
                {/* Full Screen Relationship Diagram (ER/Table View) */}
                {(erMode || filterType === 'tables') && fullScreen && (
                    <div style={{
                        position: 'fixed',
                        top: 0, left: 0, right: 0, bottom: 0,
                        background: 'var(--bg-app)',
                        zIndex: 2000,
                        display: 'flex', flexDirection: 'column',
                    }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 20px', background: 'var(--bg-surface)', borderBottom: '1px solid var(--border-color)' }}>
                            <span style={{ fontWeight: 700, fontSize: 15, color: '#818CF8' }}>Model Relationship Explorer (Full Screen)</span>
                            <button onClick={() => setFullScreen(false)} style={{ ...iconBtnStyle, fontSize: 18, color: '#EF4444', border: '1px solid #EF4444', borderRadius: 6, padding: '4px 12px', fontWeight: 700 }}>Exit Full Screen ✕</button>
                        </div>
                        <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
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
                                    isLoading={loading}
                                    snapshotId={snapshotId}
                                    diffMode={diffMode}
                                    defaultEdgeOptions={{ type: 'step' }}
                                    // Pass a prop to enable zoom/pan/minimap in full screen
                                    fullScreenMode={true}
                                />
                            </ReactFlowProvider>
                        </div>
                    </div>
                )}
                {/* Normal (non-fullscreen) diagram */}
                {!(erMode || filterType === 'tables') || !fullScreen ? (
                    <div style={{ flex: 1, position: 'relative' }}>
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
                              isLoading={loading}
                              snapshotId={snapshotId}
                              diffMode={diffMode}
                              defaultEdgeOptions={{ type: 'step' }}
                              fullScreenMode={false}
                          />
                        </ReactFlowProvider>
                        {/* Full Screen Toggle Button (only in ER/Table view) */}
                        {(erMode || filterType === 'tables') && (
                            <button
                                onClick={() => setFullScreen(true)}
                                title="Full Screen Relationship Diagram"
                                style={{
                                    position: 'absolute',
                                    top: 18, right: 18, zIndex: 100,
                                    background: '#18181b', color: '#fff',
                                    border: '1px solid #818CF8', borderRadius: 8,
                                    padding: '7px 18px', fontWeight: 700, fontSize: 15,
                                    boxShadow: '0 2px 8px rgba(0,0,0,.18)',
                                    cursor: 'pointer',
                                    opacity: 0.92,
                                }}
                            >
                                ⛶ Full Screen
                            </button>
                        )}
                    </div>
                ) : null}

                {/* Detail / Preview Panel - Modal Overlay */}
                {showDetail && !workbenchOpen && (
                    <div style={{
                        position: 'fixed',
                        top: 0,
                        left: 0,
                        right: 0,
                        bottom: 0,
                        background: 'rgba(0,0,0,.4)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        padding: 16,
                        zIndex: 999,
                    }}>
                        <div style={{
                            width: '98vw',
                            maxWidth: '98vw',
                            height: '92vh',
                            maxHeight: '92vh',
                            borderRadius: 10,
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-color)',
                            overflow: 'auto',
                            boxShadow: '0 20px 60px rgba(0,0,0,.3)',
                        }}>
                            <DetailPanel
                                filePreview={filePreview}
                                selectedNode={selectedNode}
                                onClose={handleCloseDetail}
                            />
                        </div>
                    </div>
                )}

                {workbenchOpen && (
                    <div style={{
                        position: 'fixed',
                        top: 0,
                        left: 0,
                        right: 0,
                        bottom: 0,
                        background: 'rgba(0,0,0,.4)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        padding: 16,
                        zIndex: 1000,
                    }}>
                        <div style={{
                            width: '98vw',
                            maxWidth: '98vw',
                            height: '92vh',
                            maxHeight: '92vh',
                            borderRadius: 10,
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-color)',
                            display: 'flex',
                            flexDirection: 'column',
                            overflow: 'hidden',
                            boxShadow: '0 20px 60px rgba(0,0,0,.3)',
                        }}>
                            <div style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                padding: '12px 16px',
                                borderBottom: '1px solid var(--border-color)',
                                background: 'var(--bg-app)',
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                                    <button
                                        onClick={() => setInspectorResetToken(v => v + 1)}
                                        style={{
                                            ...iconBtnStyle,
                                            border: '1px solid var(--border-color)',
                                            borderRadius: 6,
                                            padding: '4px 8px',
                                            gap: 6,
                                        }}
                                        title="Back one step inside inspector"
                                    >
                                        <ArrowLeft size={14} />
                                        <span style={{ fontSize: 11, fontWeight: 700 }}>Back</span>
                                    </button>
                                    <div style={{ minWidth: 0 }}>
                                        <div style={{ fontSize: 13, fontWeight: 800, color: 'var(--text-secondary)' }}>Schema Inspector</div>
                                        <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Explore tables, relationships, and measures in plain view</div>
                                    </div>
                                </div>
                                <button onClick={() => setWorkbenchOpen(false)} style={iconBtnStyle} title="Close">
                                    <X size={16} />
                                </button>
                            </div>

                            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                                <ModelDataPanel
                                    key={`inspector-${inspectorResetToken}`}
                                    graphData={renderedGraphData}
                                    selectedModelId={selectedModelId}
                                    erMode={erMode}
                                    selectedTableId={selectedTableId}
                                    onSelectTable={setSelectedTableId}
                                    onOpenTableER={focusTableInER}
                                    compact
                                    showTabHeader
                                />
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {diffMode && !diffLoading && diffReport && (
                <div style={{
                    borderTop: '1px solid var(--border-color)',
                    background: 'var(--bg-surface)',
                    maxHeight: 220,
                    overflow: 'auto',
                    padding: '10px 16px',
                }}>
                    <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 8, color: 'var(--text-secondary)' }}>
                        Structured Change Log
                    </div>

                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                        <thead>
                            <tr>
                                <th style={thStyle}>Type</th>
                                <th style={thStyle}>Entity</th>
                                <th style={thStyle}>Change</th>
                                <th style={thStyle}>Details</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(diffReport.changes || []).map((row, idx) => (
                                <tr key={`chg-${idx}`}>
                                    <td style={tdStyle}>{row.entity_type}</td>
                                    <td style={tdStyle}>{row.entity_name}</td>
                                    <td style={{ ...tdStyle, color: row.change_type === 'added' ? '#22C55E' : row.change_type === 'removed' ? '#EF4444' : '#EAB308', fontWeight: 700 }}>
                                        {row.change_type}
                                    </td>
                                    <td style={tdStyle}>{row.details}</td>
                                </tr>
                            ))}
                            {(diffReport.relationships || []).map((row, idx) => (
                                <tr key={`rel-${idx}`}>
                                    <td style={tdStyle}>relationship</td>
                                    <td style={tdStyle}>{row.relationship}</td>
                                    <td style={{ ...tdStyle, color: row.change_type === 'added' ? '#22C55E' : '#EF4444', fontWeight: 700 }}>
                                        {row.change_type}
                                    </td>
                                    <td style={tdStyle}>Relationship link updated</td>
                                </tr>
                            ))}
                            {(!diffReport.changes?.length && !diffReport.relationships?.length) && (
                                <tr>
                                    <td colSpan={4} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-tertiary)' }}>
                                        No changes detected between selected snapshots.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}

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
    background: '#818CF8',
    border: '1px solid #818CF8',
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

