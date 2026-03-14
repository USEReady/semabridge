import { useState, useMemo, useEffect } from 'react';
import {
    ChevronRight,
    ChevronDown,
    FolderOpen,
    Folder,
    FileCode2,
    FileText,
    Box,
    Layers,
    Database,
    Search,
    RefreshCw,
    CheckSquare,
    Square,
    Plus,
    MoreHorizontal,
    ArrowRight,
    Loader2,
    AlertCircle,
    Snowflake,
    HardDrive,
    Eye,
    BrainCircuit,
    CheckCircle2,
    XCircle,
    GitMerge,
    Zap,
    ArrowLeftRight,
} from 'lucide-react';
import { api } from '../utils/api';
import { useWorkspace } from '../context/WorkspaceContext';
import { useLogs } from '../context/LogsContext';

const ICONS = {
    folder: Folder,
    folderOpen: FolderOpen,
    yaml: FileCode2,
    file: FileText,
    layers: Layers,
    database: Database,
    box: Box,
    // Semantic object types
    semantic_view: Eye,
    semantic_model: BrainCircuit,
    table: Database,
    view: Layers,
};

const TYPE_COLORS = {
    semantic_view: 'text-cyan-400',
    semantic_model: 'text-indigo-400',
    table: 'text-slate-400',
    view: 'text-slate-400',
};

const SEMANTIC_DIRECTIONS = [
    { value: 'fabric_to_snowflake',           label: 'Fabric → Snowflake' },
    { value: 'snowflake_to_fabric',           label: 'Snowflake → Fabric' },
    { value: 'fabric_snowflake_bidirectional', label: 'Bidirectional' },
];

const BADGE_STYLES = {
    Available: { bg: 'rgba(16,185,129,0.15)', color: '#047857' },
    Valid: { bg: 'rgba(16,185,129,0.15)', color: '#047857' },
    Warning: { bg: 'rgba(234,179,8,0.15)', color: '#a16207' },
    Modified: { bg: 'rgba(99,102,241,0.15)', color: '#6366f1' },
};

const SOURCE_BADGE = {
    fabric: { label: 'Power BI / Fabric', color: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30' },
    snowflake: { label: 'Snowflake', color: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30' },
    repository: { label: 'Local Repository', color: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' },
    pbix: { label: 'PBIX', color: 'bg-violet-500/15 text-violet-400 border-violet-500/30' },
    semantic: { label: 'Semantic Sync', color: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
};

function toRegexFromPattern(patternValue) {
    const safe = (patternValue || '*').trim();
    if (!safe || safe === '*') {
        return null;
    }

    const escaped = safe.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\*/g, '.*');
    try {
        return new RegExp(`^${escaped}$`, 'i');
    } catch {
        return null;
    }
}

async function resolvePbixFileCandidates(inputPath, patternValue) {
    const trimmedPath = inputPath.trim();
    if (!trimmedPath) {
        throw new Error('Enter a local .pbix file path or folder path before importing.');
    }

    if (trimmedPath.toLowerCase().endsWith('.pbix')) {
        return [trimmedPath];
    }

    const browse = await api.browsePbixFiles(trimmedPath);
    const files = Array.isArray(browse?.files) ? browse.files : [];
    if (files.length === 0) {
        throw new Error(`No .pbix files found in folder: ${trimmedPath}`);
    }

    const filePattern = toRegexFromPattern(patternValue);
    const matching = filePattern
        ? files.filter((file) => filePattern.test(file?.name || ''))
        : files;

    if (matching.length === 0) {
        throw new Error(`No .pbix files in ${trimmedPath} match pattern: ${patternValue || '*'}`);
    }

    matching.sort((a, b) => (a?.name || '').localeCompare(b?.name || ''));
    return matching
        .map((file) => file?.path)
        .filter((pathValue) => typeof pathValue === 'string' && pathValue.trim().length > 0);
}

function TreeItem({
    item,
    depth = 0,
    selectedItems,
    onToggleSelect,
    expandedItems,
    onToggleExpand,
    highlightedItem,
    onHighlight
}) {
    const isFolder = !!item.children;
    const isExpanded = expandedItems.includes(item.id);
    const isSelected = selectedItems.includes(item.name);
    const isHighlighted = highlightedItem === item.id;

    // Determine icon
    let IconComponent;
    if (isFolder) {
        IconComponent = isExpanded ? FolderOpen : Folder;
    } else if (ICONS[item.type]) {
        IconComponent = ICONS[item.type];
    } else if (item.type === 'yaml') {
        IconComponent = FileCode2;
    } else {
        IconComponent = FileText;
    }

    const typeColor = TYPE_COLORS[item.type] || '';

    return (
        <div>
            <div
                className={`flex items-center gap-1.5 px-2 py-1 rounded-lg cursor-pointer group transition-all duration-150`}
                style={{
                    paddingLeft: `${depth * 16 + 8}px`,
                    background: isHighlighted ? 'var(--color-primary-muted)' : 'transparent',
                }}
                onClick={() => {
                    if (isFolder) {
                        onToggleExpand(item.id);
                    } else {
                        onToggleSelect(item);
                        onHighlight(item.id);
                    }
                }}
                onMouseEnter={e => {
                    if (!isHighlighted) e.currentTarget.style.background = 'var(--bg-surface-hover)';
                }}
                onMouseLeave={e => {
                    if (!isHighlighted) e.currentTarget.style.background = 'transparent';
                }}
            >
                <div className="flex items-center gap-1 min-w-[20px]">
                    {isFolder ? (
                        isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />
                    ) : (
                        <div
                            className="p-0.5 rounded hover:bg-surface-hover"
                            onClick={(e) => {
                                e.stopPropagation();
                                onToggleSelect(item);
                            }}
                        >
                            {isSelected ? (
                                <CheckSquare size={13} className="text-indigo-500" />
                            ) : (
                                <Square size={13} className="var(--text-primary) opacity-50 group-hover:opacity-100" />
                            )}
                        </div>
                    )}
                </div>

                <IconComponent
                    size={14}
                    className={isHighlighted ? 'text-indigo-400' : (typeColor || 'text-slate-400')}
                />
                <span className={`text-[12px] font-medium truncate flex-1`} style={{
                    color: isHighlighted ? 'var(--accent-blue, #6366f1)' : 'var(--text-primary)'
                }}>
                    {item.name}
                </span>

                {/* In-sync badge for semantic objects */}
                {item.type === 'semantic_view' || item.type === 'semantic_model' ? (
                    item.in_sync === true ? (
                        <CheckCircle2 size={11} className="text-emerald-400 shrink-0" title="In sync" />
                    ) : item.in_sync === false ? (
                        <XCircle size={11} className="text-amber-400 shrink-0" title="Out of sync" />
                    ) : null
                ) : null}

                {/* Platform badge for semantic objects */}
                {item.platform && (
                    <span
                        className={`px-1.5 py-0.5 rounded text-[9px] font-semibold shrink-0 border ${
                            item.platform === 'fabric'
                                ? 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30'
                                : 'bg-cyan-500/15 text-cyan-400 border-cyan-500/30'
                        }`}
                    >
                        {item.platform === 'fabric' ? 'Fabric' : 'Snowflake'}
                    </span>
                )}

                {item.status && !item.platform && (
                    <span
                        className="px-1.5 py-0.5 rounded text-[9px] font-semibold shrink-0"
                        style={BADGE_STYLES[item.status] || {}}
                    >
                        {item.status}
                    </span>
                )}
            </div>

            {isFolder && isExpanded && item.children.map((child) => (
                <TreeItem
                    key={child.id}
                    item={child}
                    depth={depth + 1}
                    selectedItems={selectedItems}
                    onToggleSelect={onToggleSelect}
                    expandedItems={expandedItems}
                    onToggleExpand={onToggleExpand}
                    highlightedItem={highlightedItem}
                    onHighlight={onHighlight}
                />
            ))}
        </div>
    );
}

export default function SourceBrowser({ selectedItems, onSelectItems, onOpenModel, onSourceTypeChange, onTargetTypeChange, onPbixPathChange, onPbixImported }) {
    const [sourceType, setSourceType] = useState('fabric');
    const [targetType, setTargetType] = useState('snowflake');
    const [pattern, setPattern] = useState('*');
    const [pbixPath, setPbixPath] = useState('');
    const [isPbixPathSaved, setIsPbixPathSaved] = useState(false);
    const filter = '';
    const [isDiscovering, setIsDiscovering] = useState(false);
    const [expandedItems, setExpandedItems] = useState(['root-fabric', 'root-snowflake', 'root-repository', 'root-semantic-fabric', 'root-semantic-snowflake']);
    const [highlightedItem, setHighlightedItem] = useState(null);
    const [discoveredFromApi, setDiscoveredFromApi] = useState(null);
    const [discoveryError, setDiscoveryError] = useState(null);
    // Semantic-sync state
    const [semanticDirection, setSemanticDirection] = useState('fabric_to_snowflake');
    const [isSyncingSemantics, setIsSyncingSemantics] = useState(false);
    const [semanticSyncResult, setSemanticSyncResult] = useState(null);

    const { activeWorkspaceId } = useWorkspace();
    const { addLog } = useLogs();

    // Auto-discovery on mount or source type change
    // Debounce avoids firing on rapid type switches; AbortController cancels stale requests
    useEffect(() => {
        if (sourceType === 'pbix') {
            setDiscoveredFromApi([]);
            setDiscoveryError(null);
            if (onSourceTypeChange) onSourceTypeChange(sourceType);
            return;
        }

        const abortController = new AbortController();
        const timer = setTimeout(() => {
            if (!abortController.signal.aborted) {
                handleDiscover();
            }
        }, 300);

        if (onSourceTypeChange) onSourceTypeChange(sourceType);

        return () => {
            clearTimeout(timer);
            abortController.abort();
        };
    }, [sourceType]);

    useEffect(() => {
        if (onTargetTypeChange) onTargetTypeChange(targetType);
    }, [targetType]);

    const discoveredData = useMemo(() => {
        if (sourceType === 'semantic') {
            // Semantic discovery returns { fabric: [...], snowflake: [...], mappings: [...] }
            const raw = discoveredFromApi;
            if (!raw || (!raw.fabric && !raw.snowflake)) return [];

            const patternLower = pattern === '*' ? '' : pattern.replace('*', '').toLowerCase();
            const filterFn = (item) => !patternLower || item.name.toLowerCase().includes(patternLower);

            // Build mapping lookup: fabric_id / snowflake_view → mapping
            const mappingByFabric = {};
            const mappingBySf = {};
            (raw.mappings || []).forEach(m => {
                if (m.fabric_id) mappingByFabric[m.fabric_id] = m;
                if (m.snowflake_view) mappingBySf[m.snowflake_view] = m;
            });

            const fabricChildren = (raw.fabric || [])
                .filter(filterFn)
                .map((m, i) => ({
                    id: `semantic-fabric-${i}`,
                    name: m.name,
                    type: 'semantic_model',
                    status: m.status || 'Available',
                    platform: 'fabric',
                    in_sync: mappingByFabric[m.id]?.in_sync,
                }));

            const sfChildren = (raw.snowflake || [])
                .filter(filterFn)
                .map((m, i) => ({
                    id: `semantic-sf-${i}`,
                    name: m.name,
                    type: 'semantic_view',
                    status: m.status || 'Available',
                    platform: 'snowflake',
                    in_sync: mappingBySf[m.name]?.in_sync,
                }));

            return [
                { id: 'root-semantic-fabric',    name: 'Fabric Semantic Models',    children: fabricChildren },
                { id: 'root-semantic-snowflake', name: 'Snowflake Semantic Views', children: sfChildren },
            ];
        }

        const baseData = discoveredFromApi || [];
        const root = {
            id: `root-${sourceType}`,
            name: sourceType.charAt(0).toUpperCase() + sourceType.slice(1),
            children: baseData.filter(item =>
                (pattern === '*' || item.name.toLowerCase().includes(pattern.replace('*', '').toLowerCase())) &&
                (item.name.toLowerCase().includes(filter.toLowerCase()))
            )
        };
        return [root];
    }, [sourceType, pattern, filter, discoveredFromApi]);

    const handleDiscover = async () => {
        if (sourceType === 'pbix') {
            setIsDiscovering(true);
            setDiscoveryError(null);
            try {
                const pbixCandidates = await resolvePbixFileCandidates(pbixPath, pattern);
                const files = pbixCandidates.map((candidatePath, index) => {
                    const normalized = String(candidatePath || '');
                    const fileName = normalized.split(/[\\/]/).pop() || `model_${index + 1}.pbix`;
                    return {
                        id: `pbix-file-${index}`,
                        name: fileName,
                        type: 'file',
                        status: 'Available',
                        pbixPath: normalized,
                    };
                });

                setDiscoveredFromApi(files);
                addLog('info', 'PBIX Discovery', `Loaded ${files.length} PBIX file(s) from folder input`);

                if (onPbixImported) {
                    onPbixImported(null);
                }
            } catch (err) {
                console.error(err);
                const errorMessage = err?.message || 'Failed to import PBIX.';
                setDiscoveryError(null);
                addLog('error', 'PBIX Discovery', `Failed to load PBIX files: ${errorMessage}`);
            } finally {
                setIsDiscovering(false);
            }
            return;
        }

        setIsDiscovering(true);
        setDiscoveryError(null);
        try {
            if (sourceType === 'semantic') {
                // Unified semantic discovery — returns { fabric, snowflake, mappings }
                const data = await api.getSemanticDiscovery();
                setDiscoveredFromApi(data);
                const total = (data.fabric?.length || 0) + (data.snowflake?.length || 0);
                addLog('info', 'Semantic Discovery', `Discovered ${total} semantic object(s) across Fabric and Snowflake`);
            } else {
                const data = await api.getDiscovery(sourceType, activeWorkspaceId);
                setDiscoveredFromApi(data);
                addLog('info', 'Discovery', `Discovered ${data.length} models from ${sourceType}`);
            }
        } catch (err) {
            console.error(err);
            setDiscoveryError(err.message);
            addLog('error', 'Discovery', `Failed to discover models: ${err.message}`);
        } finally {
            setIsDiscovering(false);
        }
    };

    const handleSemanticSync = async () => {
        setIsSyncingSemantics(true);
        setSemanticSyncResult(null);
        try {
            const result = await api.triggerSemanticSync({ direction: semanticDirection });
            setSemanticSyncResult({ ok: true, message: result.message || `Sync job ${result.job_id} started.` });
            addLog('info', 'Semantic Sync', `Started: ${result.message || result.job_id}`);
            // Refresh discovery after sync
            handleDiscover();
        } catch (err) {
            setSemanticSyncResult({ ok: false, message: err.message });
            addLog('error', 'Semantic Sync', `Failed: ${err.message}`);
        } finally {
            setIsSyncingSemantics(false);
        }
    };

    const handleSemanticRefresh = async () => {
        setIsDiscovering(true);
        try {
            const result = await api.refreshSemanticMetadata({});
            addLog('info', 'Semantic Refresh', `Refreshed ${(result.refreshed_fabric || 0) + (result.refreshed_snowflake || 0)} model(s)`);
            handleDiscover();
        } catch (err) {
            addLog('error', 'Semantic Refresh', `Failed: ${err.message}`);
        } finally {
            setIsDiscovering(false);
        }
    };

    const handlePbixPathSaveToggle = () => {
        if (!isPbixPathSaved) {
            if (!pbixPath.trim()) {
                addLog('warning', 'PBIX Path', 'Enter a folder path before saving.');
                return;
            }
            setIsPbixPathSaved(true);
            if (onPbixPathChange) {
                onPbixPathChange(pbixPath.trim());
            }
            addLog('info', 'PBIX Path', `Saved PBIX path: ${pbixPath.trim()}`);
            return;
        }

        setIsPbixPathSaved(false);
        addLog('info', 'PBIX Path', 'PBIX path unlocked for editing');
    };

    // When highlighting a repository item, notify parent to open the model
    const handleHighlight = (itemId) => {
        setHighlightedItem(itemId);
        if (sourceType === 'repository' && onOpenModel) {
            // itemId for leaf items is the model stem (e.g. "sales_analysis")
            const item = (discoveredFromApi || []).find(i => i.id === itemId);
            if (item) {
                onOpenModel(item.id);
                addLog('info', 'Repository', `Opened model: ${item.name}`);
            }
        }
    };

    const badge = SOURCE_BADGE[sourceType];

    return (
        <aside
            className="theme-transition flex flex-col shrink-0 select-none overflow-hidden h-full bg-surface"
            style={{ width: 280 }}
        >
            {/* Section 1: Filters */}
            <div className="p-4 border-b border-main space-y-3">
                <div className="flex items-center justify-between">
                    <h2 className="text-[11px] font-bold uppercase tracking-wider text-tertiary">1. Navigation</h2>
                    <ChevronDown size={12} className="text-tertiary" />
                </div>

                <div className="space-y-2">
                    <div className="flex flex-col gap-1">
                        <label className="text-[10px] font-bold text-secondary uppercase px-1">Source Type</label>
                        <select
                            value={sourceType}
                            onChange={(e) => setSourceType(e.target.value)}
                            className="bg-app border border-main rounded-md px-2 py-1.5 text-[12px] text-primary focus:outline-none focus:border-accent-blue transition-all"
                        >
                            <option value="fabric">Power BI</option>
                            <option value="snowflake">Snowflake</option>
                            <option value="repository">Local Repository</option>
                            <option value="pbix">PBIX</option>
                            <option value="semantic">↔ Semantic Sync</option>
                        </select>
                    </div>
                    {sourceType === 'pbix' && (
                        <div className="flex flex-col gap-1">
                            <label className="text-[10px] font-bold text-secondary uppercase px-1">PBIX Path</label>
                            <div className="flex items-center gap-2">
                                <input
                                    type="text"
                                    value={pbixPath}
                                    readOnly={isPbixPathSaved}
                                    onChange={(e) => {
                                        setPbixPath(e.target.value);
                                        setIsPbixPathSaved(false);
                                    }}
                                    className={`w-full bg-app border border-main rounded-md px-2 py-1.5 text-[12px] text-primary focus:outline-none focus:border-accent-blue ${isPbixPathSaved ? 'opacity-90 cursor-default' : ''}`}
                                    placeholder="C:/path/to/pbix-folder"
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter') {
                                            handleDiscover();
                                        }
                                    }}
                                />
                                <button
                                    type="button"
                                    onClick={handlePbixPathSaveToggle}
                                    className="shrink-0 px-3 py-1.5 rounded-md text-[11px] font-bold border border-main bg-app text-primary hover:bg-surface-hover"
                                >
                                    {isPbixPathSaved ? 'Edit' : 'Save'}
                                </button>
                            </div>
                        </div>
                    )}
                    {/* Semantic Sync: direction selector + action buttons */}
                    {sourceType === 'semantic' && (
                        <div className="flex flex-col gap-2 p-3 rounded-lg bg-amber-500/5 border border-amber-500/20">
                            <div className="flex items-center gap-1.5 mb-0.5">
                                <ArrowLeftRight size={11} className="text-amber-400" />
                                <span className="text-[10px] font-bold text-amber-400 uppercase tracking-wide">Sync Direction</span>
                            </div>
                            <select
                                value={semanticDirection}
                                onChange={(e) => setSemanticDirection(e.target.value)}
                                className="bg-app border border-main rounded-md px-2 py-1.5 text-[12px] text-primary focus:outline-none focus:border-amber-400 transition-all"
                            >
                                {SEMANTIC_DIRECTIONS.map(d => (
                                    <option key={d.value} value={d.value}>{d.label}</option>
                                ))}
                            </select>

                            <div className="flex gap-1.5">
                                <button
                                    onClick={handleSemanticSync}
                                    disabled={isSyncingSemantics || isDiscovering}
                                    className={`flex-1 flex items-center justify-center gap-1.5 py-2 rounded-md text-[11px] font-bold transition-all ${
                                        isSyncingSemantics
                                            ? 'bg-app text-tertiary cursor-not-allowed border border-main'
                                            : 'bg-amber-500 text-white hover:brightness-110 shadow-sm'
                                    }`}
                                >
                                    {isSyncingSemantics
                                        ? <Loader2 size={12} className="animate-spin" />
                                        : <Zap size={12} />}
                                    {isSyncingSemantics ? 'Syncing…' : 'Sync Now'}
                                </button>
                                <button
                                    onClick={handleSemanticRefresh}
                                    disabled={isDiscovering}
                                    className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-[11px] font-bold border border-main bg-app text-primary hover:bg-surface-hover transition-all disabled:opacity-50"
                                    title="Refresh metadata from both platforms"
                                >
                                    <RefreshCw size={12} />
                                </button>
                            </div>

                            {/* Sync result message */}
                            {semanticSyncResult && (
                                <div className={`flex items-start gap-1.5 text-[10px] rounded px-2 py-1.5 ${
                                    semanticSyncResult.ok
                                        ? 'bg-emerald-500/10 text-emerald-400'
                                        : 'bg-red-500/10 text-red-400'
                                }`}>
                                    {semanticSyncResult.ok
                                        ? <CheckCircle2 size={11} className="shrink-0 mt-0.5" />
                                        : <AlertCircle size={11} className="shrink-0 mt-0.5" />}
                                    <span>{semanticSyncResult.message}</span>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Target Type (hidden for semantic mode — direction is set above) */}
                    {sourceType !== 'semantic' && (
                        <div className="flex flex-col gap-1">
                            <label className="text-[10px] font-bold text-secondary uppercase px-1">Target Type</label>
                            <select
                                value={targetType}
                                onChange={(e) => setTargetType(e.target.value)}
                                className="bg-app border border-main rounded-md px-2 py-1.5 text-[12px] text-primary focus:outline-none focus:border-accent-blue transition-all"
                            >
                                <option value="snowflake">Snowflake</option>
                                <option value="fabric">Power BI / Fabric</option>
                            </select>
                        </div>
                    )}
                    <div className="flex flex-col gap-1">
                        <label className="text-[10px] font-bold text-secondary uppercase px-1">Source Model Pattern</label>
                        <div className="relative">
                            <Layers size={11} className="absolute left-2 top-2.5 text-tertiary" />
                            <input
                                type="text"
                                value={pattern}
                                onChange={(e) => setPattern(e.target.value)}
                                className="w-full bg-app border border-main rounded-md pl-7 pr-2 py-1.5 text-[12px] text-primary focus:outline-none focus:border-accent-blue"
                                placeholder="e.g. sales_*"
                            />
                        </div>
                    </div>

                    <button
                        onClick={handleDiscover}
                        disabled={isDiscovering}
                        className={`w-full flex items-center justify-center gap-2 py-2 rounded-md text-[12px] font-bold transition-all ${isDiscovering
                            ? 'bg-app text-tertiary cursor-not-allowed border border-main'
                            : 'bg-accent-blue text-inverse hover:brightness-110 shadow-sm'
                            }`}
                    >
                        {isDiscovering ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                        {isDiscovering
                            ? 'Discovering...'
                            : sourceType === 'pbix'
                                ? 'Load PBIX'
                                : sourceType === 'semantic'
                                    ? 'Refresh Discovery'
                                    : 'Discover Models'}
                    </button>
                </div>
            </div>

            {/* Section 2: Models List */}
            <div className="flex-1 flex flex-col overflow-hidden">
                <div className="p-3 bg-surface-raised flex items-center justify-between">
                    <div className="flex items-center gap-2">
                        <h2 className="text-[11px] font-bold uppercase tracking-wider text-tertiary">
                            {sourceType === 'semantic' ? '2. Semantic Objects' : '2. Source Models'}
                        </h2>
                        {/* Source Badge */}
                        {badge && (
                            <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold border ${badge.color}`}>
                                {badge.label}
                            </span>
                        )}
                    </div>
                </div>

                {/* Error state */}
                {discoveryError && sourceType !== 'pbix' && (
                    <div className="mx-3 mt-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 flex items-start gap-2">
                        <AlertCircle size={14} className="text-red-400 shrink-0 mt-0.5" />
                        <div>
                            <p className="text-[11px] font-bold text-red-400">Discovery Failed</p>
                            <p className="text-[10px] text-red-300/80 mt-0.5 line-clamp-3">{discoveryError}</p>
                        </div>
                    </div>
                )}

                {/* Loading state */}
                {isDiscovering && (
                    <div className="flex flex-col items-center justify-center py-8 text-tertiary">
                        <Loader2 size={24} className="animate-spin text-accent-blue mb-2" />
                        <p className="text-xs">Discovering models...</p>
                    </div>
                )}

                {/* Tree */}
                {!isDiscovering && (
                    <div className="flex-1 overflow-y-auto px-2 py-2 custom-scrollbar space-y-0.5">
                        {discoveredData.map(node => (
                            <TreeItem
                                key={node.id}
                                item={node}
                                depth={0}
                                selectedItems={selectedItems}
                                onToggleSelect={item => {
                                    const exists = selectedItems.includes(item.name);
                                    const next = exists
                                        ? selectedItems.filter(name => name !== item.name)
                                        : [...selectedItems, item.name];

                                    if (onSelectItems) {
                                        onSelectItems(next);
                                    }
                                }}
                                expandedItems={expandedItems}
                                onToggleExpand={id => setExpandedItems(prev => prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id])}
                                highlightedItem={highlightedItem}
                                onHighlight={handleHighlight}
                            />
                        ))}
                    </div>
                )}
            </div>

            {/* Selection Footer */}
            <div className="p-3 border-t border-main bg-app flex items-center justify-between">
                <div className="flex flex-col">
                    <span className="text-[10px] font-bold text-tertiary uppercase">
                        {sourceType === 'semantic'
                            ? `${(discoveredFromApi?.fabric?.length || 0) + (discoveredFromApi?.snowflake?.length || 0)} Semantic Objects`
                            : `${discoveredFromApi?.length || 0} Models Found`}
                    </span>
                    <span className="text-[11px] font-medium text-secondary">
                        {selectedItems.length} Selected
                    </span>
                </div>

                <div className="flex gap-2">
                    <button
                        onClick={() => {
                            const allItems = discoveredData[0]?.children || [];
                            const allNames = allItems.map(i => i.name);
                            if (onSelectItems) {
                                onSelectItems(allNames);
                            }

                        }}
                        className="text-[10px] text-accent-blue font-bold hover:underline"
                    >
                        All
                    </button>

                    <button
                        onClick={() => {
                            if (onSelectItems) {
                                onSelectItems([]);
                            }
                        }}
                        className="text-[10px] text-tertiary hover:underline"
                    >
                        None
                    </button>
                </div>
            </div>
        </aside>
    );
}
