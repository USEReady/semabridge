import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import usePageCache from '../hooks/usePageCache';
import {
    History,
    Database,
    Zap,
    Search,
    RefreshCw,
    Loader2,
    AlertTriangle,
    CheckCircle2,
    ArrowRight,
    Info,
    X,
    Pin,
    Save,
} from 'lucide-react';
import { api, formatDate } from '../utils/api';
import { useUIStore } from '../store/uiStore';
import { useLogs } from '../context/LogsContext';
import RepositoryBrowser from '../components/RepositoryBrowser';
import VersionList from '../components/history/VersionList';
import VersionDetail from '../components/history/VersionDetail';
import RestoreDialog from '../components/history/RestoreDialog';
import StorageDisclosure from '../components/history/StorageDisclosure';
import { Btn, Card, MenuButton } from '../components/history/ui';
import {
    snapshotOf,
    beforeSnapshotOf,
    countDiff,
    summarizeCounts,
    longTime,
} from '../components/history/historyUtils';


// --- SHARED STYLED COMPONENTS ---
// Exported because RepositoryBrowser (shown under History › More) reuses them.

export const GlassCard = ({ children, className = "", style = {} }) => (
    <div
        className={`bg-[var(--bg-surface)]/40 backdrop-blur-md border border-[var(--border-main)] rounded-2xl shadow-sm hover:shadow-md transition-all duration-300 ${className}`}
        style={style}
    >
        {children}
    </div>
);

export const Badge = ({ children, variant = "default" }) => {
    const variants = {
        success: "bg-emerald-500/10 text-emerald-500 border-emerald-500/20",
        error: "bg-rose-500/10 text-rose-500 border-rose-500/20",
        warning: "bg-amber-500/10 text-amber-500 border-amber-500/20",
        info: "bg-sky-500/10 text-sky-500 border-sky-500/20",
        default: "bg-[var(--bg-button)] text-[var(--text-secondary)] border-[var(--border-light)]"
    };
    return (
        <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border ${variants[variant] || variants.default}`}>
            {children}
        </span>
    );
};

export const ActionButton = ({ onClick, children, variant = "default", disabled = false, className = "", style = {} }) => {
    const variants = {
        primary: "bg-[var(--accent-blue)] hover:bg-[var(--accent-blue-hover)] text-white border-none shadow-sm shadow-blue-500/20",
        danger: "bg-rose-600 hover:bg-rose-500 text-white border-none shadow-sm shadow-rose-500/20",
        ghost: "bg-transparent hover:bg-[var(--bg-surface-raised)] text-[var(--text-secondary)] border-[var(--border-main)]",
        default: "bg-[var(--bg-button)] hover:bg-[var(--bg-button-hover)] text-[var(--text-primary)] border-[var(--border-main)]"
    };
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            className={`px-4 py-2 rounded-xl text-[12px] font-bold transition-all flex items-center justify-center gap-2 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
            style={style}
        >
            {children}
        </button>
    );
};

// --- MODEL HISTORY DRAWER ---
// Opened by clicking a row in "Changes vs …" or in Mappings.

function ModelHistoryDrawer({ projectId, modelName, onClose }) {
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const res = await api.apiFetch(`/projects/${projectId}/models/${encodeURIComponent(modelName)}/history`);
                const json = await res.json();
                setHistory(Array.isArray(json) ? json : []);
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        load();
    }, [projectId, modelName]);

    return (
        <div style={{
            position: 'fixed', top: 0, bottom: 0, right: 0, width: 'min(440px, 100vw)', zIndex: 300,
            background: 'var(--bg-surface)', borderLeft: '1px solid var(--border-main)', boxShadow: 'var(--shadow-lg)',
            display: 'flex', flexDirection: 'column',
        }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '16px 20px', borderBottom: '1px solid var(--border-light)' }}>
                <History size={18} color="var(--accent-blue)" />
                <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{modelName}</div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Versions that contain this model</div>
                </div>
                <button type="button" onClick={onClose} aria-label="Close" style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
                    <X size={18} />
                </button>
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: 20 }}>
                {loading ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-tertiary)' }}>
                        <Loader2 size={14} className="animate-spin" /> Loading…
                    </div>
                ) : history.length === 0 ? (
                    <div style={{ fontSize: 13, color: 'var(--text-tertiary)' }}>No saved versions contain this model.</div>
                ) : (
                    [...history].reverse().map((item, i) => (
                        <div key={`${item.snapshot_id}-${i}`} style={{ padding: '10px 0', borderBottom: '1px solid var(--border-light)' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                                <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontFamily: 'monospace' }}>{String(item.run_id || '').substring(0, 18) || 'Manual save'}</span>
                                <span style={{ color: 'var(--text-tertiary)' }}>{formatDate(item.timestamp)}</span>
                            </div>
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace', marginTop: 2 }}>{item.snapshot_id}</div>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}

// --- MAPPINGS TAB ---

function MappingsView({ projectId, onSelectModel }) {
    const [mappings, setMappings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState('');

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const data = await api.listMappings(projectId);
                // The backend returns { mappings: [...], collisions: [...] }
                // or a direct array in some legacy paths.
                setMappings(Array.isArray(data) ? data : (data?.mappings || []));
            } catch (e) {
                console.error(e);
                setMappings([]);
            } finally {
                setLoading(false);
            }
        };
        load();
    }, [projectId]);

    const visible = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return mappings;
        return mappings.filter((m) => `${m.source} ${m.target}`.toLowerCase().includes(q));
    }, [mappings, query]);

    const th = { padding: '8px 14px', fontSize: 12, fontWeight: 600, color: 'var(--text-tertiary)', textAlign: 'left', borderBottom: '1px solid var(--border-light)' };
    const td = { padding: '8px 14px', fontSize: 13, borderBottom: '1px solid var(--border-light)' };

    return (
        <Card>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 14px', borderBottom: '1px solid var(--border-light)', flexWrap: 'wrap' }}>
                <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Mappings</div>
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>How each source object is named in the target</div>
                </div>
                <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-input)' }}>
                    <Search size={14} color="var(--text-tertiary)" />
                    <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter mappings…" aria-label="Filter mappings"
                        style={{ border: 'none', outline: 'none', background: 'transparent', fontSize: 13, color: 'var(--text-primary)', width: 180 }} />
                </div>
            </div>
            {loading ? (
                <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Loader2 size={14} className="animate-spin" /> Loading mappings…
                </div>
            ) : visible.length === 0 ? (
                <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)' }}>
                    {query ? 'No mappings match this filter.' : 'No mappings recorded for this project yet.'}
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr><th style={th}>Source</th><th style={th}>Target</th><th style={th}>Name clash</th></tr>
                        </thead>
                        <tbody>
                            {visible.map((m, i) => (
                                <tr key={`${m.source}-${i}`} onClick={() => onSelectModel(m.source)} style={{ cursor: 'pointer' }}
                                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}>
                                    <td style={{ ...td, fontFamily: 'monospace', color: 'var(--text-primary)' }}>{m.source}</td>
                                    <td style={{ ...td, fontFamily: 'monospace', color: 'var(--accent-blue)' }}>
                                        <ArrowRight size={12} style={{ display: 'inline', marginRight: 6, color: 'var(--text-tertiary)' }} />{m.target}
                                    </td>
                                    <td style={{ ...td, color: m.collision_detected ? 'var(--color-warning)' : 'var(--text-tertiary)' }}>
                                        {m.collision_detected ? (
                                            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><AlertTriangle size={13} /> Resolved with a suffix</span>
                                        ) : (
                                            <CheckCircle2 size={14} color="var(--color-success)" />
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </Card>
    );
}

// --- LINEAGE VIEW (History › More) ---

function LineageView({ projectId }) {
    const [data, setData] = useState({ nodes: [], edges: [] });
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const load = async () => {
            setLoading(true);
            try {
                const json = await api.getProjectLineage(projectId);
                setData(json && Array.isArray(json.nodes) ? json : { nodes: [], edges: [] });
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        load();
    }, [projectId]);

    if (loading) return (
        <div style={{ padding: 16, fontSize: 13, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Loader2 size={14} className="animate-spin" /> Loading lineage…
        </div>
    );

    const nodes = [...data.nodes].sort((a, b) =>
        new Date(a.metadata?.created_at || a.metadata?.started_at || 0) - new Date(b.metadata?.created_at || b.metadata?.started_at || 0));

    return (
        <Card style={{ padding: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Lineage</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginBottom: 12 }}>Runs and the snapshots they produced, oldest first</div>
            {nodes.length === 0 ? (
                <div style={{ fontSize: 13, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Info size={14} /> Not enough history to show lineage yet.
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                    {nodes.map((node) => (
                        <div key={node.id} style={{
                            width: 'min(320px, 100%)', padding: '8px 12px', borderRadius: 8,
                            border: '1px solid var(--border-main)',
                            background: node.type === 'snapshot' ? 'var(--bg-surface-raised)' : 'var(--color-accent-faint)',
                        }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
                                {node.type === 'snapshot' ? <Database size={12} /> : <Zap size={12} />}
                                {node.type === 'snapshot' ? 'Snapshot' : 'Run'}
                                {node.metadata?.is_pinned && <Pin size={11} color="var(--color-warning)" />}
                                <span style={{ marginLeft: 'auto' }}>{formatDate(node.metadata?.created_at || node.metadata?.started_at)}</span>
                            </div>
                            <div style={{ fontSize: 13, fontFamily: 'monospace', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{node.label}</div>
                        </div>
                    ))}
                </div>
            )}
        </Card>
    );
}

// --- MAIN PAGE ---

const MAIN_TABS = ['versions', 'mappings'];
const MORE_VIEWS = { repository: 'Saved snapshots', lineage: 'Lineage' };

export default function VersionControlPage() {
    const activeProjectId = useUIStore(state => state.activeProjectId);
    const [searchParams, setSearchParams] = useSearchParams();
    const [projects, setProjects] = useState([]);
    const [versions, setVersions] = useState([]);
    const [snapshotMeta, setSnapshotMeta] = useState({}); // snapshot_id -> { is_pinned, tag }
    const [isLoading, setIsLoading] = useState(false);
    const [projectsLoadError, setProjectsLoadError] = useState('');
    const [versionsLoadError, setVersionsLoadError] = useState('');
    const [selectedRunId, setSelectedRunId] = useState(searchParams.get('v') || null);
    const [baseChoice, setBaseChoice] = useState(null); // null = previous version; else { kind: 'before' } | { kind: 'run', runId }
    const [diffs, setDiffs] = useState({}); // `${from}|${to}` -> { loading, error, models }
    const [restoreOpen, setRestoreOpen] = useState(false);
    const [isRestoring, setIsRestoring] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [notice, setNotice] = useState(null); // { tone, text }
    const [selectedModelHistory, setSelectedModelHistory] = useState(null);
    const [hasLoadedProjects, setHasLoadedProjects] = useState(false);

    // Cached UI state — survives SPA navigation within the same tab.
    const [vcCache, setVcCache] = usePageCache('version-control-page', {
      selectedProjectId: searchParams.get('project') || activeProjectId || '',
      viewMode: 'versions',
      searchQuery: '',
    });
    const { selectedProjectId, searchQuery } = vcCache;
    // Older cached values ('history', 'diff', 'stats') map onto the new tabs.
    const viewMode = MAIN_TABS.includes(vcCache.viewMode) || MORE_VIEWS[vcCache.viewMode] ? vcCache.viewMode : 'versions';
    const setSelectedProjectId = (v) => setVcCache({ selectedProjectId: v });
    const setViewMode = (v) => setVcCache({ viewMode: v });
    const setSearchQuery = (v) => setVcCache({ searchQuery: v });
    const { addLog } = useLogs();

    // A ?project= link wins over the project remembered for this tab.
    useEffect(() => {
        const fromUrl = searchParams.get('project');
        if (fromUrl && fromUrl !== vcCache.selectedProjectId) {
            latestProjectIdRef.current = fromUrl;
            setVcCache({ selectedProjectId: fromUrl });
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps -- mount only
    }, []);
    const normalizeId = useCallback((value) => String(value ?? '').trim(), []);

    const latestProjectIdRef = useRef(selectedProjectId);
    useEffect(() => {
        latestProjectIdRef.current = selectedProjectId;
    }, [selectedProjectId]);

    // Keep ?project=…&v=… in the URL so a version can be linked to.
    useEffect(() => {
        const next = new URLSearchParams(searchParams);
        if (selectedProjectId) next.set('project', selectedProjectId); else next.delete('project');
        if (selectedRunId) next.set('v', selectedRunId); else next.delete('v');
        if (next.toString() !== searchParams.toString()) setSearchParams(next, { replace: true });
    }, [selectedProjectId, selectedRunId, searchParams, setSearchParams]);

    const loadProjects = useCallback(async () => {
        setProjectsLoadError('');
        try {
            const data = await api.listProjects();
            const projectList = Array.isArray(data) ? data : [];
            setProjects(projectList);

            const currentId = normalizeId(latestProjectIdRef.current);
            const hasCurrent = currentId && projectList.some((p) => normalizeId(p?.id ?? p?.project_id) === currentId);
            if (projectList.length > 0 && !hasCurrent) {
                const nextId = normalizeId(projectList[0].id ?? projectList[0].project_id);
                latestProjectIdRef.current = nextId;
                setVcCache({ selectedProjectId: nextId });
            }
            setHasLoadedProjects(true);
        } catch (err) {
            setProjectsLoadError(err?.message || 'Failed to load projects');
            addLog('error', 'History', 'Failed to load projects: ' + err.message);
        }
    }, [addLog, normalizeId, setVcCache]);

    const loadSnapshotMeta = useCallback(async (projectId) => {
        try {
            const rows = await api.listProjectSnapshots(projectId, { limit: 500 });
            const list = Array.isArray(rows) ? rows : (rows?.snapshots || []);
            const meta = {};
            for (const s of list) {
                if (s?.snapshot_id) meta[s.snapshot_id] = { is_pinned: Boolean(s.is_pinned), tag: s.tag || null };
            }
            if (normalizeId(projectId) === normalizeId(latestProjectIdRef.current)) setSnapshotMeta(meta);
        } catch {
            /* pin/tag badges are optional decoration */
        }
    }, [normalizeId]);

    const loadVersions = useCallback(async (projId) => {
        const projectId = normalizeId(projId);
        if (!projectId) return;
        setIsLoading(true);
        setVersionsLoadError('');
        try {
            const runs = await api.getProjectRuns(projectId);
            if (projectId !== normalizeId(latestProjectIdRef.current)) return;
            const sorted = (Array.isArray(runs) ? runs : []).sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
            setVersions(sorted);
            setSelectedRunId((prev) => {
                if (!sorted.length) return null;
                return prev && sorted.some((r) => r.run_id === prev) ? prev : sorted[0].run_id;
            });
        } catch (err) {
            if (projectId !== normalizeId(latestProjectIdRef.current)) return;
            setVersionsLoadError(err?.message || 'Failed to load version history');
            addLog('error', 'History', 'Load failed: ' + err.message);
        } finally {
            if (projectId === normalizeId(latestProjectIdRef.current)) setIsLoading(false);
        }
        loadSnapshotMeta(projectId);
    }, [addLog, normalizeId, loadSnapshotMeta]);

    useEffect(() => {
        loadProjects();
    }, [loadProjects]);

    const isFirstProjectLoad = useRef(true);
    useEffect(() => {
        if (hasLoadedProjects && selectedProjectId) {
            if (!isFirstProjectLoad.current) setSelectedRunId(null);
            isFirstProjectLoad.current = false;
            setVersions([]);
            setDiffs({});
            setSnapshotMeta({});
            setBaseChoice(null);
            loadVersions(selectedProjectId);
        }
    }, [hasLoadedProjects, selectedProjectId, loadVersions]);

    // ── Derived data ────────────────────────────────────────────────────────
    const runNumbers = useMemo(() => {
        const map = {};
        versions.forEach((v, idx) => { map[v.run_id] = versions.length - idx; });
        return map;
    }, [versions]);
    const runNumber = useCallback((run) => runNumbers[run?.run_id] ?? '?', [runNumbers]);

    const pinInfo = useCallback((run) => {
        const meta = snapshotMeta[snapshotOf(run)] || {};
        return { pinned: Boolean(meta.is_pinned || run?.is_pinned), tag: meta.tag || run?.tags?.[0] || null };
    }, [snapshotMeta]);

    const filteredVersions = useMemo(() => {
        const q = searchQuery.trim().toLowerCase();
        if (!q) return versions;
        return versions.filter((v) => {
            const { tag } = pinInfo(v);
            return v.run_id?.toLowerCase().includes(q)
                || `run #${runNumbers[v.run_id]}`.includes(q)
                || String(runNumbers[v.run_id]) === q.replace('#', '')
                || (tag && tag.toLowerCase().includes(q))
                || (v.tags || []).some((t) => t.toLowerCase().includes(q));
        });
    }, [versions, searchQuery, pinInfo, runNumbers]);

    const selectedRun = versions.find((v) => v.run_id === selectedRunId) || null;

    /** The default "previous" pair for a run: previous run's result, else the state just before this run. */
    const defaultPair = useCallback((run) => {
        const idx = versions.findIndex((v) => v.run_id === run.run_id);
        const prev = idx >= 0 ? versions[idx + 1] : null;
        const to = snapshotOf(run);
        const prevSnap = prev ? snapshotOf(prev) : null;
        const from = prevSnap || beforeSnapshotOf(run);
        if (!from || !to || from === to) return null;
        return { from, to, label: prevSnap ? `Run #${runNumbers[prev.run_id]}` : 'before this run' };
    }, [versions, runNumbers]);

    const activePair = useMemo(() => {
        if (!selectedRun) return null;
        const to = snapshotOf(selectedRun);
        if (!baseChoice) return defaultPair(selectedRun);
        if (baseChoice.kind === 'before') {
            const from = beforeSnapshotOf(selectedRun);
            return from && to && from !== to ? { from, to, label: 'before this run' } : null;
        }
        const other = versions.find((v) => v.run_id === baseChoice.runId);
        const from = snapshotOf(other);
        return from && to ? { from, to, label: `Run #${runNumbers[other.run_id]}` } : null;
    }, [selectedRun, baseChoice, defaultPair, versions, runNumbers]);

    const diffsRef = useRef(diffs);
    useEffect(() => { diffsRef.current = diffs; }, [diffs]);

    const ensureDiff = useCallback(async (projectId, from, to) => {
        const key = `${from}|${to}`;
        if (diffsRef.current[key]) return;
        diffsRef.current = { ...diffsRef.current, [key]: { loading: true } };
        setDiffs((d) => ({ ...d, [key]: { loading: true } }));
        try {
            const data = await api.compareProjectSnapshots(projectId, from, to);
            if (normalizeId(projectId) !== normalizeId(latestProjectIdRef.current)) return;
            setDiffs((d) => ({ ...d, [key]: { models: Array.isArray(data?.models) ? data.models : [] } }));
        } catch (err) {
            if (normalizeId(projectId) !== normalizeId(latestProjectIdRef.current)) return;
            setDiffs((d) => ({ ...d, [key]: { error: `Could not compare these versions: ${err?.message || 'unknown error'}` } }));
        }
    }, [normalizeId]);

    // Diff for the selected version (re-uses the structural snapshot compare).
    useEffect(() => {
        if (activePair && selectedProjectId) ensureDiff(selectedProjectId, activePair.from, activePair.to);
    }, [activePair, selectedProjectId, ensureDiff]);

    // Short change summaries for the most recent versions in the list.
    useEffect(() => {
        if (!selectedProjectId || versions.length === 0) return undefined;
        let cancelled = false;
        (async () => {
            for (const run of versions.slice(0, 15)) {
                if (cancelled) return;
                const pair = defaultPair(run);
                if (pair) await ensureDiff(selectedProjectId, pair.from, pair.to);
            }
        })();
        return () => { cancelled = true; };
    }, [versions, selectedProjectId, defaultPair, ensureDiff]);

    const summaries = useMemo(() => {
        const out = {};
        for (const run of versions) {
            const pair = defaultPair(run);
            const d = pair ? diffs[`${pair.from}|${pair.to}`] : null;
            if (d?.models) out[run.run_id] = summarizeCounts(countDiff(d.models));
            else if (!pair && run === versions[versions.length - 1]) out[run.run_id] = 'First version';
        }
        return out;
    }, [versions, diffs, defaultPair]);

    const activeDiff = activePair ? (diffs[`${activePair.from}|${activePair.to}`] || { loading: true }) : null;

    // ── Actions ─────────────────────────────────────────────────────────────
    const handleSelectRun = (run) => {
        setSelectedRunId(run.run_id);
        setBaseChoice(null);
    };

    const handleTagSnapshot = async (run, tag) => {
        const snapshotId = snapshotOf(run);
        if (!snapshotId || !tag) return;
        try {
            await api.tagSnapshot(selectedProjectId, snapshotId, tag, 'Label added from History');
            addLog('success', 'History', `Labelled Run #${runNumber(run)} "${tag}"`);
            loadVersions(selectedProjectId);
        } catch (error) {
            addLog('error', 'History', 'Adding a label failed: ' + error.message);
        }
    };

    const handleTogglePin = async (run) => {
        const snapshotId = snapshotOf(run);
        if (!snapshotId) return;
        const { pinned } = pinInfo(run);
        try {
            await api.toggleSnapshotPin(selectedProjectId, snapshotId, !pinned);
            setSnapshotMeta((m) => ({ ...m, [snapshotId]: { ...(m[snapshotId] || {}), is_pinned: !pinned } }));
            addLog('success', 'History', `Run #${runNumber(run)} ${pinned ? 'unpinned' : 'pinned'}`);
        } catch (error) {
            addLog('error', 'History', 'Pinning failed: ' + error.message);
        }
    };

    // Downloads the Markdown run-summary report. Available for every run
    // regardless of outcome -- write_run_report is called unconditionally
    // from the backend's finally block.
    const handleDownloadReport = async (run) => {
        if (!run?.run_id) return;
        try {
            await api.downloadRunReport(selectedProjectId, run.run_id);
        } catch (error) {
            addLog('error', 'History', 'Report download failed: ' + error.message);
        }
    };

    // One file's own report from a multi-PBIX batch run (run.model_reports).
    const handleDownloadModelReport = async (run, modelLabel) => {
        if (!run?.run_id || !modelLabel) return;
        try {
            await api.downloadRunModelReport(selectedProjectId, run.run_id, modelLabel);
        } catch (error) {
            addLog('error', 'History', 'Report download failed: ' + error.message);
        }
    };

    const handleRestore = async (version) => {
        if (!version) return;
        setIsRestoring(true);
        try {
            await api.restoreProjectVersion(selectedProjectId, {
                restore_snapshot_id: snapshotOf(version),
                sync_mode: version.sync_mode || 'copy',
                comment: 'Restore to ' + version.run_id,
            });
            addLog('success', 'History', `Restore of Run #${runNumber(version)} started`);
            setNotice({ tone: 'success', text: `Restoring Run #${runNumber(version)}. It will appear here as a new version when it finishes.` });
            setRestoreOpen(false);
            loadVersions(selectedProjectId);
        } catch (err) {
            addLog('error', 'History', 'Restore failed: ' + err.message);
            setNotice({ tone: 'error', text: 'Restore failed: ' + err.message });
        } finally {
            setIsRestoring(false);
        }
    };

    const handleSaveNow = async () => {
        if (!selectedProjectId) return;
        setIsSaving(true);
        try {
            const res = await api.captureProjectSnapshot(selectedProjectId, { label: `Saved from History ${new Date().toISOString().slice(0, 16).replace('T', ' ')}` });
            addLog('success', 'History', 'Saved a version of the current model');
            const count = Array.isArray(res?.snapshot_ids) ? res.snapshot_ids.length : null;
            setNotice({ tone: 'success', text: `Version saved${count ? ` (${count} snapshot${count === 1 ? '' : 's'})` : ''}. Saved versions are listed under More › Saved snapshots.` });
            loadSnapshotMeta(selectedProjectId);
        } catch (err) {
            addLog('error', 'History', 'Save failed: ' + err.message);
            setNotice({ tone: 'error', text: 'Could not save a version: ' + err.message });
        } finally {
            setIsSaving(false);
        }
    };

    const selectedProject = projects.find(
        (p) => normalizeId(p?.id ?? p?.project_id) === normalizeId(selectedProjectId)
    );

    const compareItems = (() => {
        if (!selectedRun) return [];
        const def = defaultPair(selectedRun);
        const others = versions.filter((v) => v.run_id !== selectedRun.run_id && snapshotOf(v)).slice(0, 50);
        const items = [
            { key: 'prev', label: 'Previous version', hint: def ? `Default · ${def.label}` : 'Default', active: !baseChoice, onClick: () => setBaseChoice(null) },
        ];
        // Only offer "just before this run" when it differs from the default.
        if (beforeSnapshotOf(selectedRun) && def?.label !== 'before this run') {
            items.push({ key: 'before', label: 'Just before this run', active: baseChoice?.kind === 'before', onClick: () => setBaseChoice({ kind: 'before' }) });
        }
        if (others.length) {
            items.push({ key: 'div', divider: true }, { key: 'h', heading: 'Another version' });
            others.forEach((v) => items.push({
                key: v.run_id,
                label: `Run #${runNumbers[v.run_id]}${pinInfo(v).tag ? ` · ${pinInfo(v).tag}` : ''}`,
                hint: longTime(v.started_at),
                active: baseChoice?.kind === 'run' && baseChoice.runId === v.run_id,
                onClick: () => setBaseChoice({ kind: 'run', runId: v.run_id }),
            }));
        }
        return items;
    })();

    const tabStyle = (active) => ({
        padding: '8px 2px', marginRight: 20, border: 'none', background: 'none', cursor: 'pointer',
        fontSize: 14, fontWeight: active ? 600 : 500,
        color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
        borderBottom: `2px solid ${active ? 'var(--accent-blue)' : 'transparent'}`,
        marginBottom: -1,
    });

    return (
        <div style={{ padding: '28px 32px', maxWidth: 1400, margin: '0 auto', minHeight: '100%' }}>
            {/* Header: title · project picker · Save a version now */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 16 }}>
                <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text-primary)' }}>History</h1>
                <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <select
                        value={normalizeId(selectedProjectId)}
                        onChange={(e) => setSelectedProjectId(e.target.value)}
                        aria-label="Project"
                        style={{
                            padding: '6px 10px', borderRadius: 6, fontSize: 13, fontWeight: 500, maxWidth: 260,
                            background: 'var(--bg-surface)', color: 'var(--text-primary)', border: '1px solid var(--border-main)', cursor: 'pointer',
                        }}
                    >
                        {projects.length === 0 && <option value="">No projects</option>}
                        {projects.map(p => (
                            <option key={normalizeId(p?.id ?? p?.project_id)} value={normalizeId(p?.id ?? p?.project_id)}>{p.name || p.id}</option>
                        ))}
                    </select>
                    <Btn variant="primary" onClick={handleSaveNow} disabled={!selectedProjectId || isSaving}
                        title="Save the project's current model as a version you can come back to">
                        {isSaving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Save a version now
                    </Btn>
                </div>
            </div>

            {projectsLoadError && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8, marginBottom: 12, fontSize: 13, background: 'var(--color-error-bg)', color: 'var(--color-error)' }}>
                    <AlertTriangle size={14} /> Could not load projects: {projectsLoadError}
                    <Btn variant="ghost" onClick={loadProjects} style={{ marginLeft: 'auto' }}><RefreshCw size={13} /> Retry</Btn>
                </div>
            )}
            {notice && (
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8, marginBottom: 12, fontSize: 13,
                    background: notice.tone === 'error' ? 'var(--color-error-bg)' : 'var(--color-success-bg)',
                    color: notice.tone === 'error' ? 'var(--color-error)' : 'var(--color-success)',
                }}>
                    {notice.tone === 'error' ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
                    <span style={{ flex: 1 }}>{notice.text}</span>
                    <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss" style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'inherit' }}><X size={14} /></button>
                </div>
            )}

            {/* Tabs: Versions | Mappings, plus More */}
            <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid var(--border-main)', marginBottom: 16 }}>
                <button type="button" style={tabStyle(viewMode === 'versions')} onClick={() => setViewMode('versions')} aria-pressed={viewMode === 'versions'}>
                    Versions <span style={{ marginLeft: 4, fontSize: 12, color: 'var(--text-tertiary)' }}>{versions.length || ''}</span>
                </button>
                <button type="button" style={tabStyle(viewMode === 'mappings')} onClick={() => setViewMode('mappings')} aria-pressed={viewMode === 'mappings'}>
                    Mappings
                </button>
                {MORE_VIEWS[viewMode] && (
                    <span style={{ ...tabStyle(true), cursor: 'default' }}>{MORE_VIEWS[viewMode]}</span>
                )}
                <div style={{ marginLeft: 'auto', paddingBottom: 6 }}>
                    <MenuButton
                        label="More"
                        variant="ghost"
                        items={Object.entries(MORE_VIEWS).map(([id, label]) => ({
                            key: id,
                            label,
                            hint: id === 'repository' ? 'Every stored snapshot, including manual saves' : 'Runs and snapshots over time',
                            active: viewMode === id,
                            onClick: () => setViewMode(id),
                        }))}
                    />
                </div>
            </div>

            {viewMode === 'versions' && (
                <>
                    {versionsLoadError && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8, marginBottom: 12, fontSize: 13, background: 'var(--color-warning-bg)', color: 'var(--color-warning)' }}>
                            <AlertTriangle size={14} /> Could not load versions: {versionsLoadError}
                            <Btn variant="ghost" onClick={() => loadVersions(selectedProjectId)} style={{ marginLeft: 'auto' }}><RefreshCw size={13} /> Retry</Btn>
                        </div>
                    )}
                    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(240px, 300px) minmax(0, 1fr)', gap: 20, alignItems: 'start' }} className="history-grid">
                        <Card style={{ overflow: 'hidden' }}>
                            <VersionList
                                runs={filteredVersions}
                                runNumber={runNumber}
                                selectedId={selectedRunId}
                                onSelect={handleSelectRun}
                                summaries={summaries}
                                pinInfo={pinInfo}
                                query={searchQuery}
                                onQuery={setSearchQuery}
                                loading={isLoading}
                            />
                        </Card>
                        <div style={{ minWidth: 0 }}>
                            {selectedRun ? (
                                <VersionDetail
                                    key={selectedRun.run_id}
                                    run={selectedRun}
                                    runNumber={runNumber(selectedRun)}
                                    pinned={pinInfo(selectedRun).pinned}
                                    tag={pinInfo(selectedRun).tag}
                                    compareItems={compareItems}
                                    baseLabel={activePair?.label}
                                    diff={activeDiff}
                                    onModelClick={setSelectedModelHistory}
                                    onDownload={() => handleDownloadReport(selectedRun)}
                                    modelReports={selectedRun.model_reports}
                                    onDownloadModel={(model) => handleDownloadModelReport(selectedRun, model)}
                                    onTogglePin={() => handleTogglePin(selectedRun)}
                                    onAddTag={(tag) => handleTagSnapshot(selectedRun, tag)}
                                    onRestore={() => setRestoreOpen(true)}
                                    canRestore={Boolean(snapshotOf(selectedRun))}
                                />
                            ) : (
                                <Card style={{ padding: 24, fontSize: 13, color: 'var(--text-tertiary)' }}>
                                    {isLoading ? 'Loading…' : versions.length === 0 ? 'This project has no versions yet. Run it once, or use “Save a version now”.' : 'Pick a version on the left.'}
                                </Card>
                            )}
                        </div>
                    </div>
                    <StorageDisclosure
                        key={selectedProjectId}
                        projectId={selectedProjectId}
                        versionCount={versions.length}
                        oldestRun={versions[versions.length - 1]}
                    />
                </>
            )}

            {viewMode === 'mappings' && selectedProjectId && (
                <MappingsView key={selectedProjectId} projectId={selectedProjectId} onSelectModel={setSelectedModelHistory} />
            )}

            {viewMode === 'lineage' && selectedProjectId && (
                <LineageView key={selectedProjectId} projectId={selectedProjectId} />
            )}

            {viewMode === 'repository' && selectedProjectId && (
                <RepositoryBrowser projectId={selectedProjectId} />
            )}

            {/* --- Drawer & dialog --- */}
            {selectedModelHistory && (
                <>
                    <div onClick={() => setSelectedModelHistory(null)} style={{ position: 'fixed', inset: 0, zIndex: 250, background: 'var(--bg-backdrop, rgba(0,0,0,0.35))' }} />
                    <ModelHistoryDrawer
                        projectId={selectedProjectId}
                        modelName={selectedModelHistory}
                        onClose={() => setSelectedModelHistory(null)}
                    />
                </>
            )}

            {restoreOpen && selectedRun && snapshotOf(selectedRun) && (
                <RestoreDialog
                    projectId={selectedProjectId}
                    projectName={selectedProject?.name}
                    snapshotId={snapshotOf(selectedRun)}
                    versionLabel={`Run #${runNumber(selectedRun)}`}
                    busy={isRestoring}
                    onConfirm={() => handleRestore(selectedRun)}
                    onCancel={() => setRestoreOpen(false)}
                />
            )}
        </div>
    );
}
