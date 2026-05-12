import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Map as MapIcon, RefreshCw,
    LayoutGrid, Waypoints,
    Search, X, Filter,
    Eye, EyeOff,
    Database, FolderOpen, PanelRight, Files, ArrowLeft,
} from 'lucide-react';
import { api } from '../../utils/api';
import { emitTelemetryEvent, TELEMETRY_EVENTS } from '../../utils/graphTelemetry';
import YAML from 'yaml';
import ComponentTree from './ComponentTree';
import DependencyGraph from './DependencyGraph';
import dagre from 'dagre';
import DetailPanel from './DetailPanel';
import ModelDataPanel from './ModelDataPanel';
import {
    buildSnapshotScopes,
    filterSnapshotsByProjectCandidates,
    graphMatchesProjectCandidates,
    resolveProjectCandidates,
    normalizeProjectKey,
} from './graphScopeHelpers';
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

        const fallbackModelId = (graphModelId && graphModelId !== '__all__')
            ? graphModelId
            : '';
        const defaultModelId = incomingData.model_id
            || (nodeType === 'modelNode' ? (fallbackModelId || nodeId) : '');

        return {
            ...node,
            id: nodeId,
            type: nodeType,
            data: {
                ...incomingData,
                nodeType: incomingData.nodeType || (nodeType === 'tableNode' ? 'table' : nodeType === 'measureNode' ? 'measure' : 'model'),
                label: incomingData.label || nodeId,
                model_id: defaultModelId,
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
        const nodeType = String(node?.data?.nodeType || '').toLowerCase();
        const graphScopeFallback = (nodeType === 'model' && graphModelId && graphModelId !== '__all__')
            ? graphModelId
            : '';
        const resolvedModelId = existingModelId || inferredModelId || graphScopeFallback;

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

function hasGraphNodes(payload) {
    return Array.isArray(payload?.nodes) && payload.nodes.length > 0;
}

function buildGraphFromProjectConfig(configYaml, projectId) {
    const pid = String(projectId || '').trim();
    const fallback = {
        nodes: [
            {
                id: `model-${pid || 'project'}`,
                type: 'modelNode',
                position: { x: 420, y: 140 },
                data: {
                    label: pid || 'project',
                    model_id: pid || 'project',
                    nodeType: 'model',
                    status: 'valid',
                },
            },
        ],
        edges: [],
        meta: { reason: 'config_fallback' },
    };

    if (!configYaml || typeof configYaml !== 'string') return fallback;

    let parsed;
    try {
        parsed = YAML.parse(configYaml);
    } catch {
        return fallback;
    }
    if (!parsed || typeof parsed !== 'object') return fallback;

    const source = (parsed.source && typeof parsed.source === 'object') ? parsed.source : {};
    const rawModels = Array.isArray(source.models) ? source.models : [];
    const sourceType = String(source.type || parsed.source_type || 'unknown');
    const modelLabel = String(parsed.project_id || parsed.model_name || pid || 'project');

    const nodes = [
        {
            id: `model-${modelLabel}`,
            type: 'modelNode',
            position: { x: 420, y: 140 },
            data: {
                label: modelLabel,
                model_id: modelLabel,
                nodeType: 'model',
                source_type: sourceType,
                status: 'valid',
            },
        },
    ];
    const edges = [];

    const tableNodes = new Map();
    const upsertTable = (schema, tableName, columns = []) => {
        const sch = String(schema || 'PUBLIC').trim() || 'PUBLIC';
        const tbl = String(tableName || '').trim();
        if (!tbl) return null;
        const key = `${sch}.${tbl}`;
        if (tableNodes.has(key)) {
            const existing = tableNodes.get(key);
            if ((!existing.data.columns || existing.data.columns.length === 0) && Array.isArray(columns) && columns.length) {
                existing.data.columns = columns;
            }
            return existing.id;
        }
        const tableId = `table-${modelLabel}-${tableNodes.size}`;
        const node = {
            id: tableId,
            type: 'tableNode',
            position: { x: 120 + tableNodes.size * 240, y: 320 },
            data: {
                label: `${sch}.${tbl}`,
                table_name: tbl,
                schema: sch,
                source_type: sourceType,
                columns: Array.isArray(columns) ? columns : [],
                nodeType: 'table',
                model_id: modelLabel,
                status: 'valid',
            },
        };
        tableNodes.set(key, node);
        nodes.push(node);
        edges.push({
            id: `e-model-${modelLabel}-${tableId}`,
            source: tableId,
            target: `model-${modelLabel}`,
            type: 'smoothstep',
            style: { stroke: '#10B981' },
        });
        return tableId;
    };

    rawModels.forEach((entry) => {
        let tableName = '';
        let schema = 'PUBLIC';
        let columns = [];
        if (typeof entry === 'string') {
            tableName = entry;
        } else if (entry && typeof entry === 'object') {
            tableName = String(entry.name || entry.model || entry.table || entry.source_table || '');
            schema = String(entry.schema || entry.source_schema || entry.database_schema || 'PUBLIC');
            columns = Array.isArray(entry.columns) ? entry.columns : [];
        }
        tableName = String(tableName || '').trim();
        if (!tableName) return;
        upsertTable(schema, tableName, columns);
    });

    // Semantic datasets/entities fallback (often richer than source.models).
    const semanticDatasets = Array.isArray(parsed.datasets) ? parsed.datasets : [];
    semanticDatasets.forEach((ds, idx) => {
        if (!ds || typeof ds !== 'object') return;
        const schema = String(ds.source_schema || ds.schema || ds.database_schema || 'PUBLIC');
        const table = String(ds.source_table || ds.table || ds.name || ds.unique_name || `dataset_${idx + 1}`);
        const columns = Array.isArray(ds.columns) ? ds.columns : [];
        upsertTable(schema, table, columns);
    });

    const entities = (parsed.entities && typeof parsed.entities === 'object') ? parsed.entities : {};
    Object.entries(entities).forEach(([name, ent]) => {
        if (!ent || typeof ent !== 'object') return;
        const schema = String(ent.schema || ent.database_schema || 'PUBLIC');
        const table = String(ent.table || name || '').trim();
        const columns = Array.isArray(ent.columns) ? ent.columns : [];
        upsertTable(schema, table, columns);
    });

    // Metrics/measures as measure nodes linked to model.
    const metricRows = [
        ...(Array.isArray(parsed.metrics) ? parsed.metrics : []),
        ...(Array.isArray(parsed.measures) ? parsed.measures : []),
    ];
    metricRows.forEach((m, idx) => {
        if (!m || typeof m !== 'object') return;
        const label = String(m.name || m.unique_name || m.label || `metric_${idx + 1}`);
        const metricId = `measure-${modelLabel}-${idx}`;
        nodes.push({
            id: metricId,
            type: 'measureNode',
            position: { x: 240 + idx * 200, y: 520 },
            data: {
                label,
                nodeType: 'measure',
                model_id: modelLabel,
                data_type: String(m.data_type || m.type || 'metric'),
                expression: String(m.expression || m.sql || ''),
                status: 'valid',
            },
        });
        edges.push({
            id: `e-model-${modelLabel}-${metricId}`,
            source: metricId,
            target: `model-${modelLabel}`,
            type: 'smoothstep',
            style: { stroke: '#818CF8' },
        });
    });

    // Relationships between table nodes.
    const relRows = Array.isArray(parsed.relationships) ? parsed.relationships : [];
    const resolveTableRef = (value) => {
        const raw = String(value || '').trim();
        if (!raw) return null;
        if (tableNodes.has(raw)) return tableNodes.get(raw).id;
        const matchByTable = [...tableNodes.values()].find((n) => String(n?.data?.table_name || '').toLowerCase() === raw.toLowerCase());
        return matchByTable ? matchByTable.id : null;
    };
    relRows.forEach((r, idx) => {
        if (!r || typeof r !== 'object') return;
        const fromRef = r.from || r.source || r.left || r.from_table;
        const toRef = r.to || r.target || r.right || r.to_table;
        const fromId = resolveTableRef(fromRef);
        const toId = resolveTableRef(toRef);
        if (!fromId || !toId || fromId === toId) return;
        const cardinality = String(r.cardinality || r.type || '').trim();
        edges.push({
            id: `rel-${modelLabel}-${idx}`,
            source: fromId,
            target: toId,
            type: 'smoothstep',
            label: cardinality || 'relationship',
            data: {
                cardinality: cardinality || 'unknown',
                from_column: String(r.from_column || r.source_column || r.left_key || ''),
                to_column: String(r.to_column || r.target_column || r.right_key || ''),
            },
            style: { stroke: '#818CF8', strokeDasharray: '6 3' },
        });
    });

    return { nodes, edges, meta: { reason: 'config_fallback', source_type: sourceType } };
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

function getPrimaryModelIdFromGraph(graph, fallback = '') {
    const nodes = Array.isArray(graph?.nodes) ? graph.nodes : [];
    const modelNode = nodes.find((n) => String(n?.data?.nodeType || '') === 'model');
    const modelId = String(modelNode?.data?.model_id || '').trim();
    return modelId || String(fallback || '').trim();
}

function buildProjectCatalogGraph(projectIds = []) {
    const uniqueIds = [...new Set((projectIds || []).map((id) => String(id || '').trim()).filter(Boolean))];
    return {
        nodes: uniqueIds.map((id, idx) => ({
            id: `model-${id}`,
            type: 'modelNode',
            position: { x: 220 + ((idx % 4) * 240), y: 120 + (Math.floor(idx / 4) * 140) },
            data: {
                label: id,
                model_id: id,
                nodeType: 'model',
                status: 'valid',
            },
        })),
        edges: [],
        meta: { reason: 'project_catalog' },
    };
}

function mergeProjectIds(primaryIds = [], snapshotRows = []) {
    const merged = new Map();
    (primaryIds || []).forEach((id) => {
        const value = String(id || '').trim();
        if (!value) return;
        merged.set(normalizeProjectKey(value), value);
    });
    (snapshotRows || []).forEach((row) => {
        const candidates = [
            row?.project_id,
            row?.projectId,
            row?.model_id,
            row?.modelId,
            row?.model_name,
            row?.modelName,
        ]
            .map((v) => String(v || '').trim())
            .filter(Boolean)
            .filter((v) => v !== '__all__');
        candidates.forEach((value) => {
            const key = normalizeProjectKey(value);
            if (!merged.has(key)) {
                merged.set(key, value);
            }
        });
    });
    return [...merged.values()];
}

/**
 * RepositoryMap — master container for the visual repo explorer.
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
    const [allProjectIds, setAllProjectIds] = useState([]);
    const [projectAliasesById, setProjectAliasesById] = useState({});
    const [selectedProjectId, setSelectedProjectId] = useState('__all__');
    const [selectedSemanticModel, setSelectedSemanticModel] = useState('__all__');
    const [selectedSnapshotId, setSelectedSnapshotId] = useState(null);
    const [hasUserSelectedModel, setHasUserSelectedModel] = useState(false);

    const [syncing, setSyncing] = useState(false);
    const [loading, setLoading] = useState(true);
    const [treeLoading, setTreeLoading] = useState(true);
    const [graphLoading, setGraphLoading] = useState(true);
    const [diffLoading, setDiffLoading] = useState(false);
    const [diffReport, setDiffReport] = useState(null);
    const [graphMismatchReason, setGraphMismatchReason] = useState(null);  // null | 'no_match' | 'empty'
    const [inspectorWidth, setInspectorWidth] = usePageCache('explore:inspectorWidth', 400);
    const [isResizing, setIsResizing] = useState(false);

    const startResizing = useCallback((e) => {
        setIsResizing(true);
        e.preventDefault();
    }, []);

    const stopResizing = useCallback(() => {
        setIsResizing(false);
    }, []);

    const resize = useCallback((e) => {
        if (isResizing) {
            const newWidth = window.innerWidth - e.clientX;
            if (newWidth > 200 && newWidth < 1200) {
                setInspectorWidth(newWidth);
            }
        }
    }, [isResizing, setInspectorWidth]);

    useEffect(() => {
        if (isResizing) {
            window.addEventListener('mousemove', resize);
            window.addEventListener('mouseup', stopResizing);
        } else {
            window.removeEventListener('mousemove', resize);
            window.removeEventListener('mouseup', stopResizing);
        }
        return () => {
            window.removeEventListener('mousemove', resize);
            window.removeEventListener('mouseup', stopResizing);
        };
    }, [isResizing, resize, stopResizing]);

    const loadData = useCallback(async () => {
        setLoading(true);
        setTreeLoading(true);
        setGraphLoading(true);
        try {
            const [snapshotResp, projectsResp] = await Promise.all([
                api.getSnapshotTree().catch(() => ({ root: null })),
                api.listProjects().catch(() => []),
            ]);

            setSnapshotTreeData(snapshotResp?.root || null);
            setTreeLoading(false);

            const projectIds = (Array.isArray(projectsResp) ? projectsResp : [])
                .map((p) => String(p?.id || p?.project_id || '').trim())
                .filter(Boolean);
            const aliasMap = {};
            (Array.isArray(projectsResp) ? projectsResp : []).forEach((p) => {
                const pid = String(p?.id || p?.project_id || '').trim();
                if (!pid) return;
                const aliases = [
                    p?.name,
                    p?.display_name,
                    p?.project_name,
                ]
                    .map((v) => String(v || '').trim())
                    .filter(Boolean);
                if (aliases.length) aliasMap[pid] = aliases;
            });
            setProjectAliasesById(aliasMap);
            let uniqueProjectIds = [...new Set(projectIds)];

            // Enrich from snapshot metadata because some backends encode project
            // identity in project_id/model_id while model_name is reused (e.g. "client").
            const allSnapshotRows = await api.getGraphSnapshots('__all__').catch(() => []);
            uniqueProjectIds = mergeProjectIds(uniqueProjectIds, allSnapshotRows);

            // Additional fallback: include repo model identifiers when available.
            const repoModels = await api.getRepoModels().catch(() => []);
            const repoModelIds = (Array.isArray(repoModels) ? repoModels : [])
                .map((m) => String(m?.project_id || m?.projectId || m?.model_id || m?.modelId || m?.id || m?.name || '').trim())
                .filter(Boolean);
            uniqueProjectIds = mergeProjectIds(uniqueProjectIds, repoModelIds.map((id) => ({ model_id: id })));

            setAllProjectIds(uniqueProjectIds);
            const defaultProjectId = uniqueProjectIds[0] || '__all__';
            setSelectedProjectId(defaultProjectId);
            setSelectedSemanticModel('__all__');
            setAllSnapshots([]);
            setSelectedSnapshotId(null);
            setGraphData(normalizeGraphPayload(buildProjectCatalogGraph(uniqueProjectIds)));
            setSelectedModelId('__all__');
            setHasUserSelectedModel(false);
            setSelectedTableId('__all__');

            if (snapshotId && String(snapshotId).includes('|')) {
                await handleSnapshotChange(String(snapshotId));
            } else if (defaultProjectId !== '__all__') {
                await loadProjectContext(defaultProjectId);
            }
        } catch (err) {
            console.error('Failed to load repo map data:', err);
        } finally {
            setTreeLoading(false);
            setGraphLoading(false);
            setLoading(false);
        }
    }, [snapshotId, setSelectedModelId, setSelectedTableId]);

    useEffect(() => { loadData(); }, [loadData]);

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

    const handleProjectChange = async (nextProjectId) => {
        const pid = String(nextProjectId || '').trim();
        if (!pid) return;
        setSelectedProjectId(pid);
        setSelectedSemanticModel('__all__');
        setSelectedSnapshotId(null);
        await loadProjectContext(pid);
    };

    const loadProjectContext = useCallback(async (projectId) => {
        const pid = String(projectId || '').trim();
        if (!pid) return;

        setGraphLoading(true);
        try {
            const aliasCandidates = projectAliasesById[pid] || [];
            const candidateIds = resolveProjectCandidates(pid, allProjectIds, aliasCandidates);
            let projectSnapshots = [];
            for (const candidateId of candidateIds) {
                const rows = await api.getGraphSnapshots(candidateId).catch(() => []);
                const candidateSnapshots = filterSnapshotsByProjectCandidates(rows, [candidateId]);
                if (candidateSnapshots.length > 0) {
                    projectSnapshots = candidateSnapshots;
                    break;
                }
            }
            if (!projectSnapshots.length) {
                const globalRows = await api.getGraphSnapshots('__all__').catch(() => []);
                projectSnapshots = filterSnapshotsByProjectCandidates(globalRows, candidateIds);
            }

            const ordered = (Array.isArray(projectSnapshots) ? projectSnapshots : [])
                .slice()
                .map((snap) => ({
                    ...snap,
                    model_name: String(snap?.model_name || pid),
                    snapshot_scope: String(snap?.project_id || snap?.projectId || snap?.model_id || snap?.modelId || snap?.model_name || pid),
                }))
                .sort((a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime());
            if (!ordered.length) {
                console.warn('No snapshots found for selected model candidates:', candidateIds);
            }
            setAllSnapshots(ordered);

            const latest = ordered[0] || null;
            let graphResp = null;

            if (latest?.snapshot_id) {
                const graphScopes = [...new Set([
                    ...buildSnapshotScopes(latest, pid),
                    ...candidateIds,
                ])];
                for (const scope of graphScopes) {
                    const scopedGraph = await api.getGraphSnapshot(scope, latest.snapshot_id, includeSystemTables).catch(() => null);
                    if (hasGraphNodes(scopedGraph) && graphMatchesProjectCandidates(scopedGraph, candidateIds)) {
                        graphResp = scopedGraph;
                        emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_SUCCESS, {
                            scope,
                            candidate_id: pid,
                            node_count: scopedGraph.nodes.length,
                        });
                        break;
                    }
                }

                if (!hasGraphNodes(graphResp)) {
                    const modelGraph = await api.getModelGraph(pid).catch(() => null);
                    if (hasGraphNodes(modelGraph) && graphMatchesProjectCandidates(modelGraph, candidateIds, { requireStrong: true })) {
                        graphResp = modelGraph;
                        emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_SUCCESS, {
                            scope: 'model_graph',
                            candidate_id: pid,
                            node_count: modelGraph.nodes.length,
                        });
                    }
                }

                if (!hasGraphNodes(graphResp)) {
                    const fallbackGraph = await api.getGraphSnapshot('__all__', latest.snapshot_id, includeSystemTables).catch(() => null);
                    if (hasGraphNodes(fallbackGraph)) {
                        // Require a strong match when accepting the global fallback to avoid weak-name false positives
                        if (graphMatchesProjectCandidates(fallbackGraph, candidateIds, { requireStrong: true })) {
                            graphResp = fallbackGraph;
                            emitTelemetryEvent(TELEMETRY_EVENTS.FALLBACK_TO_GLOBAL, {
                                candidate_id: pid,
                                node_count: fallbackGraph.nodes.length,
                            });
                        } else {
                            // Weak fallback match — treat as mismatch to surface empty UI instead of silently showing unrelated graph
                            emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_MISMATCH, {
                                reason: 'weak_fallback',
                                candidate_id: pid,
                            });
                        }
                    }
                }

                if (!hasGraphNodes(graphResp)) {
                    console.warn('No matching graph payload found for selected model candidates:', candidateIds);
                    emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_EMPTY, {
                        candidate_id: pid,
                        candidate_count: candidateIds.length,
                    });
                    setGraphMismatchReason('empty');
                } else {
                    setGraphMismatchReason(null);
                }

                const key = `${String(latest?.snapshot_scope || latest?.model_name || pid)}|${latest.snapshot_id}`;
                setSelectedSnapshotId(key);
            }

            const normalizedGraph = normalizeGraphPayload(graphResp);
            setGraphData(normalizedGraph);
            setSelectedModelId(getPrimaryModelIdFromGraph(normalizedGraph, pid));
            setHasUserSelectedModel(true);
            setSelectedTableId('__all__');
        } catch (err) {
            console.error('Failed to load project context:', err);
        } finally {
            setGraphLoading(false);
        }
    }, [allProjectIds, includeSystemTables, projectAliasesById, setSelectedModelId, setSelectedTableId]);

    const handleSnapshotChange = async (snapshotKey) => {
        if (!snapshotKey || !String(snapshotKey).includes('|')) return;
        const separatorIndex = String(snapshotKey).lastIndexOf('|');
        if (separatorIndex <= 0) return;
        const scopeModelId = String(snapshotKey).slice(0, separatorIndex);
        const snapshotId = String(snapshotKey).slice(separatorIndex + 1);
        if (!scopeModelId || !snapshotId) return;
        setSelectedSnapshotId(snapshotKey);
        setGraphLoading(true);
        try {
            const selectedSnap = (allSnapshots || []).find((snap) => (
                `${String(snap?.snapshot_scope || snap?.model_name)}|${String(snap?.snapshot_id || '')}` === snapshotKey
            ));
            const graphScopes = buildSnapshotScopes(selectedSnap || {}, scopeModelId);
            const scopeAliasCandidates = projectAliasesById[scopeModelId] || [];
            const scopeCandidates = resolveProjectCandidates(scopeModelId, allProjectIds, scopeAliasCandidates);
            let graphResp = null;
            for (const scope of graphScopes) {
                const scopedGraph = await api.getGraphSnapshot(scope, snapshotId, includeSystemTables).catch(() => null);
                if (hasGraphNodes(scopedGraph) && graphMatchesProjectCandidates(scopedGraph, scopeCandidates)) {
                    graphResp = scopedGraph;
                    emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_SUCCESS, {
                        scope,
                        model_id: scopeModelId,
                        snapshot_id: snapshotId,
                        node_count: scopedGraph.nodes.length,
                    });
                    break;
                }
            }
            if (!hasGraphNodes(graphResp)) {
                const modelGraph = await api.getModelGraph(scopeModelId).catch(() => null);
                if (hasGraphNodes(modelGraph) && graphMatchesProjectCandidates(modelGraph, scopeCandidates, { requireStrong: true })) {
                    graphResp = modelGraph;
                    emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_SUCCESS, {
                        scope: 'model_graph',
                        model_id: scopeModelId,
                        snapshot_id: snapshotId,
                        node_count: modelGraph.nodes.length,
                    });
                }
            }

            if (!hasGraphNodes(graphResp)) {
                const fallbackGraph = await api.getGraphSnapshot('__all__', snapshotId, includeSystemTables).catch(() => null);
                if (hasGraphNodes(fallbackGraph)) {
                    if (graphMatchesProjectCandidates(fallbackGraph, scopeCandidates, { requireStrong: true })) {
                        graphResp = fallbackGraph;
                        emitTelemetryEvent(TELEMETRY_EVENTS.FALLBACK_TO_GLOBAL, {
                            model_id: scopeModelId,
                            snapshot_id: snapshotId,
                            node_count: fallbackGraph.nodes.length,
                        });
                    } else {
                        emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_MISMATCH, {
                            reason: 'weak_fallback',
                            model_id: scopeModelId,
                            snapshot_id: snapshotId,
                        });
                    }
                }
            }
            if (!hasGraphNodes(graphResp)) {
                emitTelemetryEvent(TELEMETRY_EVENTS.GRAPH_LOAD_EMPTY, {
                    model_id: scopeModelId,
                    snapshot_id: snapshotId,
                });
            }
            const normalizedGraph = normalizeGraphPayload(graphResp);
            setGraphData(normalizedGraph);
            if (!hasGraphNodes(graphResp)) {
                setGraphMismatchReason('empty');
            } else {
                setGraphMismatchReason(null);
            }
            const snapshotListScope = graphScopes[0] || scopeModelId;
            const modelSnapshots = await api.getGraphSnapshots(snapshotListScope).catch(() => []);
            const ordered = (Array.isArray(modelSnapshots) ? modelSnapshots : [])
                .slice()
                .map((snap) => ({
                    ...snap,
                    model_name: String(snap?.model_name || scopeModelId),
                    snapshot_scope: String(snap?.project_id || snap?.projectId || snap?.model_id || snap?.modelId || snap?.model_name || scopeModelId),
                }))
                .sort((a, b) => new Date(b?.timestamp || 0).getTime() - new Date(a?.timestamp || 0).getTime());
            setAllSnapshots(ordered);
            const resolvedModelId = getPrimaryModelIdFromGraph(normalizedGraph, scopeModelId);
            if (resolvedModelId) {
                setSelectedModelId(resolvedModelId);
                setHasUserSelectedModel(true);
            }
            setSelectedTableId('__all__');
        } catch (err) {
            console.error('Failed to load snapshot:', err);
        } finally {
            setGraphLoading(false);
        }
    };

    const handleNodeClick = async (nodeData) => {
        setSelectedFile(null);
        setFilePreview(null);
        if (nodeData?.nodeType === 'model' && nodeData?.model_id) {
            const targetModelId = String(nodeData.model_id).trim();
            setSelectedModelId(targetModelId);
            setHasUserSelectedModel(true);
            await loadProjectContext(targetModelId);
        }
        if (nodeData?.nodeType === 'table' && nodeData?.id) {
            setSelectedTableId(String(nodeData.id));
        }
        setSelectedNode(nodeData);
    };

    const focusTableInER = useCallback((tableId) => {
        if (!tableId) return;
        setSelectedTableId(String(tableId));
        setErMode(true);
        setFilterType('tables');
    }, [setSelectedTableId, setErMode]);

    const getDagreLayoutedGraph = (rawGraph) => {
        const g = new dagre.graphlib.Graph();
        g.setDefaultEdgeLabel(() => ({}));
        const direction = 'LR';
        g.setGraph({ rankdir: direction, nodesep: 80, ranksep: 120, edgesep: 40 });

        const nodeRanks = { model: 0, table: 1, measure: 2 };
        const modelGroups = {};

        (rawGraph.nodes || []).forEach((node) => {
            const type = String(node?.type || node?.data?.nodeType || '').toLowerCase();
            const nodeType = type.includes('model') ? 'model' : type.includes('table') ? 'table' : type.includes('measure') ? 'measure' : 'other';
            let width = 180, height = 60;
            if (nodeType === 'model') height = 70;
            if (nodeType === 'measure') height = 50;
            g.setNode(node.id, { ...node, width, height, rank: nodeRanks[nodeType] ?? 1 });
            const modelId = node?.data?.model_id || node.id;
            if (!modelGroups[modelId]) modelGroups[modelId] = [];
            modelGroups[modelId].push(node.id);
        });
        (rawGraph.edges || []).forEach((edge) => {
            g.setEdge(edge.source, edge.target, { ...edge });
        });

        dagre.layout(g);

        const modelOffsets = {};
        let groupIdx = 0;
        const groupSpacing = 320;
        Object.keys(modelGroups).forEach((modelId) => {
            modelOffsets[modelId] = groupIdx * groupSpacing;
            groupIdx++;
        });

        const nodes = (rawGraph.nodes || []).map((node) => {
            const dagreNode = g.node(node.id);
            const modelId = node?.data?.model_id || node.id;
            const offsetX = modelOffsets[modelId] || 0;
            return {
                ...node,
                position: { x: (dagreNode?.x || 0) + offsetX, y: dagreNode?.y || 0 },
                positionAbsolute: { x: (dagreNode?.x || 0) + offsetX, y: dagreNode?.y || 0 },
                draggable: false,
            };
        });
        const edges = (rawGraph.edges || []).map((edge) => ({ ...edge, type: 'step' }));
        return { ...rawGraph, nodes, edges };
    };

    const renderedGraphData = useMemo(() => {
        let baseGraph = graphData;
        if (erMode && baseGraph.nodes && baseGraph.nodes.length > 0) {
            return getDagreLayoutedGraph(baseGraph);
        }
        return baseGraph;
    }, [graphData, erMode]);

    const snapshotModelNames = useMemo(() => {
        const names = new Set();
        (allProjectIds || []).forEach((id) => {
            const name = String(id || '').trim();
            if (name) names.add(name);
        });
        (allSnapshots || []).forEach((s) => {
            const candidates = [
                s?.snapshot_scope,
                s?.project_id,
                s?.projectId,
                s?.model_id,
                s?.modelId,
                s?.model_name,
            ];
            candidates.forEach((v) => {
                const name = String(v || '').trim();
                if (name) names.add(name);
            });
        });
        return [...names].sort((a, b) => a.localeCompare(b));
    }, [allSnapshots, allProjectIds]);

    const semanticModelOptions = useMemo(() => {
        const names = new Set();
        (allSnapshots || []).forEach((s) => {
            const semanticList = Array.isArray(s?.semantic_models) ? s.semantic_models : [];
            semanticList.forEach((v) => {
                const name = String(v || '').trim();
                if (name) names.add(name);
            });
            const fallbackName = String(s?.model_label || s?.semantic_model || s?.model_name || '').trim();
            if (fallbackName) names.add(fallbackName);
        });
        return [...names].sort((a, b) => a.localeCompare(b));
    }, [allSnapshots]);

    const visibleSnapshots = useMemo(() => {
        if (selectedSemanticModel === '__all__') return allSnapshots;
        const target = String(selectedSemanticModel || '').trim().toLowerCase();
        return (allSnapshots || []).filter((s) => {
            const semanticList = Array.isArray(s?.semantic_models) ? s.semantic_models : [];
            const semanticMatch = semanticList.some((v) => String(v || '').trim().toLowerCase() === target);
            if (semanticMatch) return true;
            const modelName = String(s?.model_label || s?.semantic_model || s?.model_name || '').trim().toLowerCase();
            return modelName === target;
        });
    }, [allSnapshots, selectedSemanticModel]);

    useEffect(() => {
        if (!selectedSnapshotId) return;
        const exists = (visibleSnapshots || []).some((s) => (
            `${String(s.snapshot_scope || s.model_name)}|${s.snapshot_id}` === selectedSnapshotId
        ));
        if (!exists) {
            setSelectedSnapshotId(null);
        }
    }, [visibleSnapshots, selectedSnapshotId]);

    const [fullScreen, setFullScreen] = useState(false);

    const chips = [
        { id: 'all', label: 'All' },
        { id: 'broken', label: 'Broken Refs' },
    ];

    useEffect(() => {
        if (filterType === 'models' && erMode) setErMode(false);
        if (filterType === 'tables' && !erMode) setErMode(true);
    }, [filterType, erMode, setErMode]);

    return (
        <div style={{
            display: 'flex', flexDirection: 'column',
            height: '100%', width: '100%',
            background: 'var(--bg-app)', color: 'var(--text-primary)',
        }}>
            {/* ─── TOP BAR ─── */}
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

                <select
                    value={selectedProjectId}
                    onChange={(e) => { void handleProjectChange(e.target.value); }}
                    disabled={allProjectIds.length === 0}
                    style={{
                        fontSize: 11,
                        padding: '4px 8px',
                        borderRadius: 4,
                        border: '1px solid var(--border-color)',
                        background: 'var(--bg-app)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                        minWidth: 180,
                    }}
                >
                    {allProjectIds.length === 0 ? (
                        <option value="__all__">Select project</option>
                    ) : (
                        allProjectIds.map((pid) => (
                            <option key={pid} value={pid}>
                                {pid}
                            </option>
                        ))
                    )}
                </select>

                <select
                    value={selectedSemanticModel}
                    onChange={(e) => setSelectedSemanticModel(e.target.value)}
                    disabled={semanticModelOptions.length === 0}
                    style={{
                        fontSize: 11,
                        padding: '4px 8px',
                        borderRadius: 4,
                        border: '1px solid var(--border-color)',
                        background: 'var(--bg-app)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                        minWidth: 180,
                    }}
                >
                    <option value="__all__">All models</option>
                    {semanticModelOptions.map((modelName) => (
                        <option key={modelName} value={modelName}>
                            {modelName}
                        </option>
                    ))}
                </select>

                <select
                    value={selectedSnapshotId || ''}
                    onChange={(e) => handleSnapshotChange(e.target.value)}
                    disabled={visibleSnapshots.length === 0}
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
                    {visibleSnapshots.length === 0 ? (
                        <option value="">Select a model to load snapshots</option>
                    ) : (
                        visibleSnapshots.map(snap => (
                            <option
                                key={`${String(snap.snapshot_scope || snap.model_name)}|${snap.snapshot_id}`}
                                value={`${String(snap.snapshot_scope || snap.model_name)}|${snap.snapshot_id}`}
                            >
                                {String(snap.snapshot_scope || snap.model_name)} • {new Date(snap.timestamp).toLocaleString()} {snap.version_tag ? `(${snap.version_tag})` : ''}
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
                            snapshotModels={snapshotModelNames}
                            onNodeClick={(node) => handleNodeClick(node.data)}
                            selectedId={selectedNode?.id}
                        />
                    </div>
                </div>

                {/* 2. Relationship Canvas (Center) */}
                <div style={{ flex: 1, position: 'relative', background: 'var(--bg-app)' }}>
                    {graphMismatchReason === 'empty' ? (
                        <div style={{
                            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                            width: '100%', height: '100%',
                            color: 'var(--text-tertiary)', gap: 16, padding: 32,
                        }}>
                            <div style={{ fontSize: 48, opacity: 0.3 }}>⚠️</div>
                            <div style={{ textAlign: 'center' }}>
                                <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8 }}>
                                    No Graph Data Available
                                </div>
                                <div style={{ fontSize: 12, color: 'var(--text-tertiary)', maxWidth: 320, lineHeight: 1.5 }}>
                                    {selectedModelId && selectedModelId !== '__all__' ? (
                                        <>The selected model has no dependency graph loaded. This may mean:</>
                                    ) : (
                                        <>Select a model to view its dependency graph.</>
                                    )}
                                </div>
                                {selectedModelId && selectedModelId !== '__all__' && (
                                    <ul style={{ fontSize: 11, marginTop: 12, textAlign: 'left', color: 'var(--text-tertiary)', listStyle: 'none', paddingLeft: 0 }}>
                                        <li>• No snapshot exists for this model</li>
                                        <li>• Snapshot data is incomplete or empty</li>
                                        <li>• Model identifier mismatch</li>
                                    </ul>
                                )}
                                <button
                                    onClick={() => loadProjectContext(selectedModelId)}
                                    style={{
                                        marginTop: 16, padding: '6px 12px', fontSize: 11, fontWeight: 600,
                                        border: '1px solid var(--accent-blue)', background: 'var(--accent-blue)', color: '#fff',
                                        borderRadius: 4, cursor: 'pointer',
                                    }}
                                >
                                    Retry
                                </button>
                            </div>
                        </div>
                    ) : (
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
                    )}
                </div>

                {!graphMismatchReason && (
                    <>
                        {/* 3. Resizer */}
                        <div 
                            onMouseDown={startResizing}
                            onMouseEnter={(e) => { e.currentTarget.firstChild.style.opacity = '1'; e.currentTarget.firstChild.style.background = 'var(--accent-blue)'; }}
                            onMouseLeave={(e) => { if(!isResizing) { e.currentTarget.firstChild.style.opacity = '0.5'; e.currentTarget.firstChild.style.background = 'var(--border-color)'; } }}
                            className="inspector-resizer"
                            style={{
                                width: 12,
                                margin: '0 -6px',
                                cursor: 'col-resize',
                                zIndex: 1000,
                                position: 'relative',
                                display: 'flex',
                                justifyContent: 'center',
                                transition: 'all 0.2s',
                            }}
                        >
                            <div style={{
                                width: 2,
                                height: '100%',
                                background: isResizing ? 'var(--accent-blue)' : 'var(--border-color)',
                                opacity: 0.5,
                            }} />
                        </div>

                        {/* 4. Inspector Panel (Right) */}
                        <div style={{
                            width: inspectorWidth,
                            minWidth: 200,
                            maxWidth: 1200,
                            background: 'var(--bg-surface)',
                            display: 'flex',
                            flexDirection: 'column',
                            position: 'relative',
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
                    </>
                )}
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
