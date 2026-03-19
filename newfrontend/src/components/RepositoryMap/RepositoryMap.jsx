import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Map, RefreshCw, GitBranch, Clock,
    LayoutGrid, Waypoints,
    Search, X, Filter,
    Eye, EyeOff,
    Database, FolderOpen,
} from 'lucide-react';
import { api } from '../../utils/api';
import FileTreePanel from './FileTreePanel';
import DependencyGraph from './DependencyGraph';
import DetailPanel from './DetailPanel';

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
export default function RepositoryMap({ onClose, snapshotId }) {
    // ── data ──
    const [treeData, setTreeData] = useState(null);
    const [snapshotTreeData, setSnapshotTreeData] = useState(null);
    const [graphData, setGraphData] = useState({ nodes: [], edges: [], meta: {} });

    // ── UI state ──
    const [treeSource, setTreeSource] = useState('snapshots');      // 'filesystem' | 'snapshots'
    const [selectedFile, setSelectedFile] = useState(null);
    const [selectedNode, setSelectedNode] = useState(null);
    const [filePreview, setFilePreview] = useState(null);

    const [layout, setLayout] = useState('hierarchical');           // hierarchical | force
    const [searchQuery, setSearchQuery] = useState('');
    const [filterType, setFilterType] = useState('all');            // all | models | tables | broken
    const [showVersionBadges, setShowVersionBadges] = useState(false);
    const [includeSystemTables, setIncludeSystemTables] = useState(false);

    const [syncing, setSyncing] = useState(false);
    const [lastSynced, setLastSynced] = useState(null);
    const [gitBranch, setGitBranch] = useState(null);
    const [gitCommit, setGitCommit] = useState(null);
    const [loading, setLoading] = useState(true);

    // ── initial load ────────────────────────────────
    const loadData = useCallback(async () => {
        setLoading(true);
        try {
            const [treeResp, snapshotResp] = await Promise.all([
                api.getRepoTree(),
                api.getSnapshotTree().catch(() => ({ root: null })),
            ]);
            
            let graphResp;
            if (snapshotId) {
                graphResp = await api.getGraphSnapshot('__all__', snapshotId, includeSystemTables).catch(() => null);
            }
            if (!graphResp) {
                 graphResp = await api.getModelGraph('__all__');
            }

            setTreeData(treeResp.root);
            setSnapshotTreeData(snapshotResp.root);
            setGraphData(normalizeGraphPayload(graphResp));
            
            if (treeResp.last_synced) setLastSynced(treeResp.last_synced);
            if (treeResp.git_branch) setGitBranch(treeResp.git_branch);
            if (treeResp.git_commit) setGitCommit(treeResp.git_commit);
        } catch (err) {
            console.error('Failed to load repo map data:', err);
        } finally {
            setLoading(false);
        }
    }, [snapshotId, includeSystemTables]);

    useEffect(() => { loadData(); }, [loadData]);

    // ── sync handler ────────────────────────────────
    const handleSync = async () => {
        setSyncing(true);
        try {
            const resp = await api.syncRepo();
            setLastSynced(resp.last_synced);
            setGitBranch(resp.git_branch);
            setGitCommit(resp.git_commit);
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
            }}>
                {/* Title */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Map size={16} style={{ color: '#818CF8' }} />
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

                <div style={{ flex: 1 }} />

                {/* Version badge toggle */}
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

                <div style={{ width: 1, height: 20, background: 'var(--border-color)' }} />

                {/* Git info */}
                {gitBranch && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: 'var(--text-secondary)' }}>
                        <GitBranch size={12} />
                        <span>{gitBranch}</span>
                        {gitCommit && <code style={{ fontSize: 10, opacity: .7 }}>{gitCommit}</code>}
                    </div>
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

                {/* Last synced */}
                {lastSynced && (
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: 4,
                        fontSize: 10, color: 'var(--text-tertiary)',
                    }}>
                        <Clock size={10} />
                        <span>{new Date(lastSynced).toLocaleTimeString()}</span>
                    </div>
                )}

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

            {/* ─── BODY ─── */}
            <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
                {/* File Tree */}
                <div style={{
                    width: 260, minWidth: 200,
                    borderRight: '1px solid var(--border-color)',
                    overflow: 'hidden',
                    background: 'var(--bg-surface)',
                    display: 'flex', flexDirection: 'column',
                }}>
                    {/* Source Toggle Tabs */}
                    <div style={{
                        display: 'flex', borderBottom: '1px solid var(--border-color)',
                        background: 'var(--bg-app)', flexShrink: 0,
                    }}>
                        <button
                            onClick={() => setTreeSource('snapshots')}
                            style={{
                                flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
                                padding: '6px 8px', fontSize: 11, fontWeight: 600, cursor: 'pointer',
                                border: 'none', borderBottom: treeSource === 'snapshots' ? '2px solid #818CF8' : '2px solid transparent',
                                background: 'transparent',
                                color: treeSource === 'snapshots' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            }}
                        >
                            <Database size={12} /> Snapshots
                        </button>
                        <button
                            onClick={() => setTreeSource('filesystem')}
                            style={{
                                flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4,
                                padding: '6px 8px', fontSize: 11, fontWeight: 600, cursor: 'pointer',
                                border: 'none', borderBottom: treeSource === 'filesystem' ? '2px solid #818CF8' : '2px solid transparent',
                                background: 'transparent',
                                color: treeSource === 'filesystem' ? 'var(--text-primary)' : 'var(--text-tertiary)',
                            }}
                        >
                            <FolderOpen size={12} /> Files
                        </button>
                    </div>
                    <div style={{ flex: 1, overflow: 'auto' }}>
                    {loading ? (
                        <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
                            Loading tree...
                        </div>
                    ) : (
                        <FileTreePanel
                            tree={treeSource === 'snapshots' ? snapshotTreeData : treeData}
                            onFileClick={handleFileClick}
                            selectedPath={selectedFile}
                        />
                    )}
                    </div>
                </div>

                {/* Dependency Graph */}
                <div style={{ flex: 1, position: 'relative' }}>
                    <DependencyGraph
                        graphData={graphData}
                        layout={layout}
                        searchQuery={searchQuery}
                        filterType={filterType}
                        showVersionBadges={showVersionBadges}
                        onNodeClick={handleNodeClick}
                        isLoading={loading}
                        snapshotId={snapshotId}
                    />
                </div>

                {/* Detail / Preview Panel */}
                {showDetail && (
                    <div style={{
                        width: 360, minWidth: 280,
                        borderLeft: '1px solid var(--border-color)',
                        overflow: 'auto',
                        background: 'var(--bg-surface)',
                    }}>
                        <DetailPanel
                            filePreview={filePreview}
                            selectedNode={selectedNode}
                            onClose={handleCloseDetail}
                        />
                    </div>
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
    background: '#818CF8',
    borderColor: '#818CF8',
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
