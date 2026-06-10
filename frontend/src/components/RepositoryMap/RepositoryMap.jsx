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

    const [syncing, setSyncing] = useState(false);
    const [loading, setLoading] = useState(true);
    const [treeLoading, setTreeLoading] = useState(true);
    const [graphLoading, setGraphLoading] = useState(true);
    const [diffLoading, setDiffLoading] = useState(false);
    const [diffReport, setDiffReport] = useState(null);
    const [inspectorResetToken, setInspectorResetToken] = useState(0);
    // Map of workspace_id → display name for the Explore detail panel
    const [workspaceNames, setWorkspaceNames] = useState({});
    // Configured Account records for the connector dropdown
    const [accounts, setAccounts] = useState([]);
    // Projects list — used to map workspace+model → project snapshot
    const [projects, setProjects] = useState([]);
    // Override snapshot when user picks a workspace model — null means use prop
    const [overrideSnapshotId, setOverrideSnapshotId] = useState(null);
    // Tracks the raw dropdown value for the model select (may be 'discovered:xxx')
    const [modelDropdownValue, setModelDropdownValue] = useState('__all__');
    // Workspace selection (Fabric only — Snowflake has no workspace concept)
    const [availableWorkspaces, setAvailableWorkspaces] = useState([]);
    const [selectedWorkspaceId, setSelectedWorkspaceId] = usePageCache('explore:selectedWorkspaceId', '__all__');
    const [workspacesLoading, setWorkspacesLoading] = useState(false);
    // Available models discovered from the selected workspace / connector
    const [availableModels, setAvailableModels] = useState([]);  // [{id, name, type}]
    const [modelsLoading, setModelsLoading] = useState(false);
    // When model chosen from discovery but has no synced snapshot yet
    const [unsyncedModel, setUnsyncedModel] = useState(null);    // {id, name} | null

    // ── initial load ────────────────────────────────
    const loadData = useCallback(async () => {
        setLoading(true);
        // Preload workspace names so the Detail panel can show human-readable names instead of raw GUIDs.
        // Uses the fabric workspaces endpoint — requires a bearer token; silently skips if unavailable.
        // Load configured accounts for the connector dropdown
        api.getAccounts().then((resp) => {
            const list = Array.isArray(resp) ? resp : (resp?.accounts || resp?.items || []);
            setAccounts(list);
        }).catch(() => { /* accounts optional */ });

        api.listProjects({ limit: 200 }).then((resp) => {
            const list = Array.isArray(resp) ? resp : (resp?.projects || resp?.items || []);
            setProjects(list);
        }).catch(() => { /* projects optional */ });

        api.discoverFabricWorkspaces().then((resp) => {
            const wsList = Array.isArray(resp) ? resp : (resp?.workspaces || []);
            if (!wsList.length) return;
            const nameMap = {};
            wsList.forEach((ws) => {
                const wsId = ws?.id || ws?.workspace_id;
                const wsName = ws?.name || ws?.displayName || ws?.display_name;
                if (wsId && wsName) nameMap[wsId] = wsName;
            });
            setWorkspaceNames(nameMap);
        }).catch(() => { /* workspace names are optional — fall back to raw IDs silently */ });
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
            let effectiveSnapshotId = overrideSnapshotId || snapshotId;
            const snapshots = Array.isArray(snapshotsResp) ? snapshotsResp : [];
            if (!effectiveSnapshotId) {
                const sorted = [...snapshots].sort(
                    (a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime()
                );
                effectiveSnapshotId = sorted[0]?.snapshot_id || null;
            }

            const effectiveSnapshot = snapshots.find((s) => s?.snapshot_id === effectiveSnapshotId) || null;
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
    }, [snapshotId, overrideSnapshotId, includeSystemTables]);

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

        // Enrich table nodes with only the measures that belong to this table.
        // Each measure node carries a `dataset` field (from SMLMetric.dataset)
        // that matches the dataset's unique_name. We match against table_name,
        // label, and the unqualified part of the qualified label (e.g.
        // "PUBLIC.SalesFact" → "SalesFact") so the lookup is robust to
        // schema-prefixed node labels.
        let enrichedNode = nodeData;
        if (nodeData?.nodeType === 'table') {
            const allNodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
            const allMeasures = allNodes
                .filter(n => n.data?.nodeType === 'measure')
                .map(n => n.data);

            if (allMeasures.length > 0) {
                // Candidate names for this table (unqualified + qualified)
                const tableLabel = String(nodeData.label || nodeData.table_name || '');
                const unqualified = tableLabel.includes('.')
                    ? tableLabel.split('.').pop()
                    : tableLabel;
                const candidates = new Set(
                    [tableLabel, unqualified, nodeData.table_name].filter(Boolean).map(s => s.toLowerCase())
                );

                const tableMeasures = allMeasures.filter(m => {
                    const ds = String(m.dataset || '').toLowerCase();
                    return ds && candidates.has(ds);
                });

                // Fall back to all measures only when no measure has dataset info
                const hasDsInfo = allMeasures.some(m => m.dataset);
                enrichedNode = {
                    ...nodeData,
                    measures: hasDsInfo ? tableMeasures : allMeasures,
                };
            }
        }
        setSelectedNode(enrichedNode);
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

    // Stable structural key — only changes when node/edge IDs actually change,
    // preventing Dagre from re-running on unrelated state updates.
    const graphStructureKey = useMemo(() => {
        const nodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
        const edges = Array.isArray(graphData?.edges) ? graphData.edges : [];
        return nodes.map(n => n.id).join(',') + '|' + edges.map(e => `${e.source}-${e.target}`).join(',');
    }, [graphData]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [diffMode, diffReport, graphStructureKey, erMode]);

    const connectorOptions = useMemo(() => {
        // Prefer configured Account records — they show the user's actual named connections
        if (accounts.length > 0) {
            return accounts.map(a => ({
                id: String(a.id || a.account_id || a.tag || ''),
                label: String(a.tag || a.name || a.identity_email || a.id || 'Account'),
                subtitle: String(a.connector_type || a.type || ''),
            })).filter(a => a.id).sort((a, b) => a.label.localeCompare(b.label));
        }
        // Fallback: derive connector types from graph node metadata
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
                label: c.charAt(0).toUpperCase() + c.slice(1).toLowerCase(),
                subtitle: '',
            }))
            .sort((a, b) => a.label.localeCompare(b.label));
    }, [accounts, graphData]);

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
        // If connectorOptions are Account records, match by account_id on the node or by connector type string
        const selectedAccount = accounts.find(a => String(a.id || a.account_id || a.tag || '') === selectedConnector);
        return byId.filter(m => {
            if (selectedAccount) {
                const node = nodes.find(n => n.id === m.id || n.data?.model_id === m.id);
                const nodeAccountId = node?.data?.account_id;
                if (nodeAccountId) return String(nodeAccountId) === String(selectedAccount.id || selectedAccount.account_id || '');
                // Fallback: match by connector_type
                const connType = String(selectedAccount.connector_type || selectedAccount.type || '').toLowerCase();
                return (m.connector || '').toLowerCase() === connType;
            }
            return m.connector === selectedConnector;
        });
    }, [renderedGraphData, selectedConnector, accounts]);

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

    // ── Load workspaces when a connector (Account) is selected ──────────────
    useEffect(() => {
        if (selectedConnector === '__all__') {
            setAvailableWorkspaces([]);
            setAvailableModels([]);
            setSelectedWorkspaceId('__all__');
            setUnsyncedModel(null);
            setModelDropdownValue('__all__');
            setOverrideSnapshotId(null);
            return;
        }
        const account = accounts.find(a => String(a.id || a.tag || '') === selectedConnector);
        if (!account) return;

        const connType = String(account.connector_type || '').toUpperCase();

        if (connType === 'FABRIC') {
            // Fabric: load workspaces first, then models inside workspace
            setWorkspacesLoading(true);
            setAvailableWorkspaces([]);
            setAvailableModels([]);
            setSelectedWorkspaceId('__all__');
            setUnsyncedModel(null);
            setModelDropdownValue('__all__');
            setOverrideSnapshotId(null);
            api.fabricListWorkspaces(String(account.id || '')).then(resp => {
                const list = Array.isArray(resp) ? resp : (resp?.workspaces || []);
                setAvailableWorkspaces(list.map(ws => ({
                    id: ws.id || ws.workspace_id || '',
                    name: ws.displayName || ws.name || ws.id || 'Workspace',
                })).filter(ws => ws.id));
            }).catch(() => setAvailableWorkspaces([]))
              .finally(() => setWorkspacesLoading(false));
        } else {
            // Snowflake / Databricks: no workspace concept — load models directly
            setAvailableWorkspaces([]);
            setSelectedWorkspaceId('__all__');
            setModelsLoading(true);
            setAvailableModels([]);
            setUnsyncedModel(null);
            setModelDropdownValue('__all__');
            setOverrideSnapshotId(null);
            api.discoverSnowflakeModels(String(account.id || '')).then(resp => {
                const list = Array.isArray(resp) ? resp : [];
                setAvailableModels(list.map(m => ({
                    id: m.id || m.name || '',
                    name: m.name || m.id || 'Model',
                    type: m.type || 'semantic_view',
                })).filter(m => m.id));
            }).catch(() => setAvailableModels([]))
              .finally(() => setModelsLoading(false));
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedConnector, accounts]);

    // ── Load models when a workspace is selected (Fabric) ───────────────────
    useEffect(() => {
        if (selectedWorkspaceId === '__all__') {
            if (availableWorkspaces.length > 0) setAvailableModels([]);
            return;
        }
        const account = accounts.find(a => String(a.id || a.tag || '') === selectedConnector);
        if (!account) return;

        setModelsLoading(true);
        setAvailableModels([]);
        setUnsyncedModel(null);
        setModelDropdownValue('__all__');
        setOverrideSnapshotId(null);
        api.discoverFabricModels(selectedWorkspaceId, String(account.id || '')).then(resp => {
            const list = Array.isArray(resp) ? resp : [];
            setAvailableModels(list.map(m => ({
                id: m.id || m.name || '',
                name: m.name || m.displayName || m.id || 'Model',
                type: m.type || 'semantic_model',
            })).filter(m => m.id));
        }).catch(() => setAvailableModels([]))
          .finally(() => setModelsLoading(false));

        // Update the workspaceNames map with this workspace
        const ws = availableWorkspaces.find(w => w.id === selectedWorkspaceId);
        if (ws) setWorkspaceNames(prev => ({ ...prev, [ws.id]: ws.name }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedWorkspaceId]);

    // ── When a discovered model is chosen, find the matching project snapshot ──
    const handleDiscoveredModelSelect = useCallback(async (modelId) => {
        const model = availableModels.find(m => m.id === modelId);
        if (!model) { setSelectedModelId('__all__'); setUnsyncedModel(null); return; }

        const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        const normModelName = norm(model.name);

        // 1. Find matching project by workspace_id + name similarity
        const matchingProject = projects.find(p => {
            const wsMatch = selectedWorkspaceId && selectedWorkspaceId !== '__all__'
                ? String(p.workspace_id || p.source_workspace_id || '').toLowerCase() === String(selectedWorkspaceId).toLowerCase()
                : true;
            const nameFields = [p.name, p.model_name, p.source_model_name, p.display_name];
            const nameMatch = nameFields.some(f => {
                if (!f) return false;
                const n = norm(f);
                return n === normModelName || n.includes(normModelName) || normModelName.includes(n);
            });
            return wsMatch && nameMatch;
        });

        if (matchingProject) {
            const projId = matchingProject.id || matchingProject.project_id;
            try {
                const snaps = await api.getGraphSnapshots(projId).catch(() => []);
                const sorted = (Array.isArray(snaps) ? snaps : []).sort(
                    (a, b) => new Date(b?.timestamp || 0) - new Date(a?.timestamp || 0)
                );
                const latestSnap = sorted[0];
                if (latestSnap?.snapshot_id) {
                    setOverrideSnapshotId(latestSnap.snapshot_id);
                    setModelDropdownValue(`discovered:${modelId}`);
                    setUnsyncedModel(null);
                    return;
                }
            } catch { /* fall through */ }
        }

        // 2. No matching project or no snapshot yet — clear graph and mark unsynced
        setOverrideSnapshotId(null);
        setModelDropdownValue(`discovered:${modelId}`);
        setGraphData({ nodes: [], edges: [], meta: {} });
        setUnsyncedModel({ id: model.id, name: model.name });
    }, [availableModels, projects, selectedWorkspaceId]);

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
                    <MapIcon size={16} style={{ color: '#2563EB' }} />
                    <span style={{ fontWeight: 700, fontSize: 13 }}>Repository Map</span>
                </div>

                <div style={{ width: 1, height: 20, background: 'var(--border-color)' }} />


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
                        color: workbenchOpen ? '#2563EB' : 'var(--text-tertiary)',
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
                        color: showExplorer ? '#2563EB' : 'var(--text-tertiary)',
                    }}
                >
                    <Files size={15} />
                    <span style={{ fontSize: 11 }}>Explorer</span>
                </button>

                {connectorOptions.length > 0 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 8, flexShrink: 0 }}>
                        <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', whiteSpace: 'nowrap' }}>
                            Connector
                        </span>
                        <select
                            value={selectedConnector}
                            onChange={(e) => {
                                setSelectedConnector(e.target.value);
                                setSelectedModelId('__all__');
                                setSelectedTableId('__all__');
                                setSelectedWorkspaceId('__all__');
                                setAvailableWorkspaces([]);
                                setAvailableModels([]);
                                setUnsyncedModel(null);
                                setModelDropdownValue('__all__');
                                setOverrideSnapshotId(null);
                            }}
                            style={dropdownStyle}
                            title="Choose connector filter"
                        >
                            <option value="__all__">Select connector</option>
                            {connectorOptions.map(c => (
                                <option key={c.id} value={c.id}>
                                    {c.label}{c.subtitle ? ` (${c.subtitle})` : ''}
                                </option>
                            ))}
                        </select>

                        {/* Workspace dropdown — shown for Fabric connectors when workspaces loaded */}
                        {selectedConnector !== '__all__' && (workspacesLoading || availableWorkspaces.length > 0) && (
                            <>
                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>›</span>
                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', whiteSpace: 'nowrap' }}>
                                    Workspace
                                </span>
                                <select
                                    value={selectedWorkspaceId}
                                    onChange={(e) => {
                                        setSelectedWorkspaceId(e.target.value);
                                        setSelectedModelId('__all__');
                                        setSelectedTableId('__all__');
                                        setUnsyncedModel(null);
                                        setModelDropdownValue('__all__');
                                        setOverrideSnapshotId(null);
                                    }}
                                    style={dropdownStyle}
                                    disabled={workspacesLoading}
                                    title="Choose workspace"
                                >
                                    <option value="__all__">{workspacesLoading ? 'Loading…' : 'Select workspace'}</option>
                                    {availableWorkspaces.map(w => (
                                        <option key={w.id} value={w.id}>{w.name || w.displayName || w.id}</option>
                                    ))}
                                </select>
                            </>
                        )}

                        {/* Model dropdown */}
                        {selectedConnector !== '__all__' && (
                            <>
                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>›</span>
                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', whiteSpace: 'nowrap' }}>
                                    Model
                                </span>
                                <select
                                    value={modelDropdownValue !== '__all__' ? modelDropdownValue : selectedModelId}
                                    onChange={(e) => {
                                        const val = e.target.value;
                                        setModelDropdownValue(val);
                                        if (val.startsWith('discovered:')) {
                                            handleDiscoveredModelSelect(val.slice('discovered:'.length));
                                        } else {
                                            setSelectedModelId(val);
                                            setSelectedTableId('__all__');
                                            setUnsyncedModel(null);
                                            setOverrideSnapshotId(null);
                                        }
                                    }}
                                    style={dropdownStyle}
                                    disabled={modelsLoading}
                                    title="Choose model"
                                >
                                    <option value="__all__">
                                        {modelsLoading ? 'Loading…' :
                                         availableWorkspaces.length > 0 && selectedWorkspaceId === '__all__' ? 'Select workspace first' :
                                         'Select model'}
                                    </option>
                                    {/* Synced models from graph */}
                                    {modelOptions.length > 0 && (
                                        <optgroup label="Synced models">
                                            {modelOptions.map(m => (
                                                <option key={m.id} value={m.id}>{m.label}</option>
                                            ))}
                                        </optgroup>
                                    )}
                                    {/* Live discovered models not yet matched to a synced graph model */}
                                    {availableModels.filter(m => {
                                        const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                                        const normM = norm(m.name);
                                        // Matched if any modelOption label is an exact or partial match
                                        const matchedInOptions = modelOptions.some(mo => {
                                            const normL = norm(mo.label);
                                            return normL === normM || normL.includes(normM) || normM.includes(normL);
                                        });
                                        if (matchedInOptions) return false;
                                        // Also check raw graph model nodes
                                        const nodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
                                        return !nodes.some(n => {
                                            if (n?.data?.nodeType !== 'model') return false;
                                            const normL = norm(n?.data?.label);
                                            return normL === normM || normL.includes(normM) || normM.includes(normL);
                                        });
                                    }).length > 0 && (
                                        <optgroup label="Not yet synced">
                                            {availableModels.filter(m => {
                                                const norm = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                                                const normM = norm(m.name);
                                                const matchedInOptions = modelOptions.some(mo => {
                                                    const normL = norm(mo.label);
                                                    return normL === normM || normL.includes(normM) || normM.includes(normL);
                                                });
                                                if (matchedInOptions) return false;
                                                const nodes = Array.isArray(graphData?.nodes) ? graphData.nodes : [];
                                                return !nodes.some(n => {
                                                    if (n?.data?.nodeType !== 'model') return false;
                                                    const normL = norm(n?.data?.label);
                                                    return normL === normM || normL.includes(normM) || normM.includes(normL);
                                                });
                                            }).map(m => (
                                                <option key={`discovered:${m.id}`} value={`discovered:${m.id}`}>{m.name || m.displayName}</option>
                                            ))}
                                        </optgroup>
                                    )}
                                </select>
                            </>
                        )}

                        {/* Fallback model dropdown when no connector selected */}
                        {selectedConnector === '__all__' && modelOptions.length > 0 && (
                            <>
                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em', whiteSpace: 'nowrap', marginLeft: 8 }}>
                                    Model
                                </span>
                                <select
                                    value={selectedModelId}
                                    onChange={(e) => { setSelectedModelId(e.target.value); setSelectedTableId('__all__'); }}
                                    style={dropdownStyle}
                                    title="Choose model"
                                >
                                    <option value="__all__">Select model</option>
                                    {modelOptions.map(m => (
                                        <option key={m.id} value={m.id}>{m.label}</option>
                                    ))}
                                </select>
                            </>
                        )}

                        {selectedModelId !== '__all__' && (
                            <span style={{
                                fontSize: 10,
                                color: '#2563EB',
                                border: '1px solid rgba(37, 99, 235, 0.35)',
                                background: 'rgba(37, 99, 235, 0.10)',
                                padding: '4px 8px',
                                borderRadius: 999,
                                whiteSpace: 'nowrap',
                                marginLeft: 4,
                            }}>
                                Model filtered
                            </span>
                        )}
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
                            color: showVersionBadges ? '#2563EB' : 'var(--text-tertiary)',
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
                            color: includeSystemTables ? '#2563EB' : 'var(--text-tertiary)',
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
                        color: erMode ? '#2563EB' : 'var(--text-tertiary)',
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
                                color: layout === 'hierarchical' ? '#2563EB' : 'var(--text-tertiary)',
                            }}
                        >
                            <LayoutGrid size={15} />
                        </button>
                        <button
                            onClick={() => setLayout('force')}
                            title="Force-directed layout"
                            style={{
                                ...toolBtnStyle,
                                color: layout === 'force' ? '#2563EB' : 'var(--text-tertiary)',
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
                        background: '#2563EB',
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
                    <AuditCard label="Total Tables" value={snapshotAudit.totalTables} tone="var(--accent-blue)" />
                    <AuditCard label="Relationships" value={snapshotAudit.totalRelationships} tone="#22C55E" />
                    <AuditCard label="Broken Refs" value={snapshotAudit.brokenTables} tone={snapshotAudit.brokenTables > 0 ? '#EF4444' : '#22C55E'} />
                    <AuditCard
                        label={includeSystemTables ? 'System Included' : 'System Excluded'}
                        value={includeSystemTables ? snapshotAudit.systemDetected : snapshotAudit.systemExcluded}
                        tone={includeSystemTables ? '#F59E0B' : 'var(--accent-blue)'}
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
                    <span style={{ fontWeight: 700, color: 'var(--accent-blue)' }}>Diff Mode</span>
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
                    {treeLoading ? (
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

                {/* Unsynced model banner */}
                {unsyncedModel && (
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: 12,
                        padding: '10px 20px',
                        background: 'rgba(245, 158, 11, 0.08)',
                        borderBottom: '1px solid rgba(245, 158, 11, 0.25)',
                        flexShrink: 0,
                    }}>
                        <span style={{ fontSize: 18 }}>📋</span>
                        <div style={{ flex: 1 }}>
                            <div style={{ fontSize: 13, fontWeight: 600, color: '#92400E' }}>
                                {unsyncedModel.name} — not yet synced
                            </div>
                            <div style={{ fontSize: 11, color: '#B45309', marginTop: 2 }}>
                                This model exists in your connector but has no lineage data yet. Create a project and run a sync to explore its tables and metrics.
                            </div>
                        </div>
                        <button
                            onClick={() => setUnsyncedModel(null)}
                            style={{ fontSize: 16, background: 'none', border: 'none', cursor: 'pointer', color: '#B45309', padding: '0 4px' }}
                        >✕</button>
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
                            <span style={{ fontWeight: 700, fontSize: 15, color: 'var(--accent-blue)' }}>Model Relationship Explorer (Full Screen)</span>
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
                                    isLoading={graphLoading}
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
                                                            isLoading={graphLoading}
                                                            snapshotId={snapshotId}
                                                            diffMode={diffMode}
                                                            defaultEdgeOptions={{ type: 'step' }}
                                                            fullScreenMode={false}
                                                            onRequestFullScreen={() => setFullScreen(true)}
                                                            showFullScreenButton={(erMode || filterType === 'tables' || filterType === 'metrics') && !fullScreen}
                                                    />
                                                </ReactFlowProvider>
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
                                workspaceNames={workspaceNames}
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
