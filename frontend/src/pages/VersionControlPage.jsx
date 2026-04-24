import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    History as HistoryIcon,
    GitCompare,
    RotateCcw,
    Trash2,
    Database,
    Clock,
    ShieldCheck,
    Zap,
    Search,
    Filter,
    ChevronRight,
    RefreshCw,
    Loader2,
    AlertTriangle,
    CheckCircle2,
    ArrowRight,
    ArrowLeft,
    Layers,
    FileText,
    Activity,
    Info,
    LayoutGrid,
    Calendar
} from 'lucide-react';
import { api } from '../utils/api';
import { useUIStore } from '../store/uiStore';
import { useLogs } from '../context/LogsContext';
import PageHeader from '../components/common/PageHeader';

// --- STYLED COMPONENTS ---

const GlassCard = ({ children, className = "", style = {} }) => (
    <div 
        className={`bg-[var(--bg-surface)]/40 backdrop-blur-md border border-[var(--border-main)] rounded-2xl shadow-sm hover:shadow-md transition-all duration-300 ${className}`}
        style={style}
    >
        {children}
    </div>
);

const Badge = ({ children, variant = "default" }) => {
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

const ActionButton = ({ onClick, children, variant = "default", disabled = false, className = "", style = {} }) => {
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

// --- DIFF VIEW COMPONENT ---

function DiffView({ diffData, baseRun, targetRun, onBack }) {
    if (!diffData) return null;
    const { metadata_diff, models } = diffData;
    
    return (
        <div className="flex flex-col gap-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="flex items-center justify-between mb-2">
                <button 
                    onClick={onBack}
                    className="flex items-center gap-2 text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors text-sm font-bold"
                >
                    <ArrowLeft size={16} /> Back to History
                </button>
                <div className="flex items-center gap-3">
                    <Badge variant="info">Structural Diff</Badge>
                    <span className="text-xs text-[var(--text-tertiary)] font-mono">
                        {metadata_diff.snapshot_a.id?.substring(0, 8)} → {metadata_diff.snapshot_b.id?.substring(0, 8)}
                    </span>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {[
                    { key: 'snapshot_a', run: baseRun, label: 'BASE VERSION', accent: 'var(--accent-blue)' },
                    { key: 'snapshot_b', run: targetRun, label: 'TARGET VERSION', accent: 'var(--color-success)' }
                ].map(({ key, run, label, accent }) => (
                    <GlassCard key={key} className="p-6 relative group overflow-hidden">
                        <div className="absolute top-0 right-0 w-32 h-32 blur-3xl opacity-5 -mr-16 -mt-16 bg-[var(--accent-blue)]" />
                        <div className="flex justify-between items-center mb-4 relative z-10">
                            <h4 className="text-[10px] font-black text-[var(--text-tertiary)] uppercase tracking-[0.2em]">{label}</h4>
                            <div className="p-2 rounded-lg bg-[var(--bg-main)]/50 border border-[var(--border-light)]">
                                <Database size={16} style={{ color: accent }} />
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-y-4 text-[13px] relative z-10">
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Run ID</span> 
                                <span className="text-[var(--text-primary)] font-mono font-bold mt-0.5">{run?.run_id?.substring(0, 12) || 'N/A'}...</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Format</span> 
                                <span className="text-[var(--text-primary)] uppercase font-bold mt-0.5">{metadata_diff?.[key]?.format || 'N/A'}</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Entities</span> 
                                <span className="text-[var(--text-primary)] font-bold mt-0.5">{metadata_diff?.[key]?.model_count || 0} Models</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Captured</span> 
                                <span className="text-[var(--text-primary)] mt-0.5">
                                    {metadata_diff?.[key]?.taken_at ? new Date(metadata_diff[key].taken_at).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '—'}
                                </span>
                            </div>
                        </div>
                    </GlassCard>
                ))}
            </div>

            <GlassCard className="flex-1 flex flex-col overflow-hidden bg-[var(--bg-surface)]/20 min-h-[400px]">
                <div className="px-6 py-4 border-b border-[var(--border-light)] flex justify-between items-center bg-[var(--bg-surface)]/50">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center border border-amber-500/20">
                            <Zap size={16} className="text-amber-500" />
                        </div>
                        <h3 className="text-xs font-black text-[var(--text-primary)] uppercase tracking-widest">Mutation Log</h3>
                    </div>
                    <div className="flex gap-3">
                        <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20">
                            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                            <span className="text-[10px] font-bold text-emerald-500">+{ (models || []).filter(m => m.status === 'ADDED').length } Added</span>
                        </div>
                        <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-rose-500/10 border border-rose-500/20">
                            <div className="w-1.5 h-1.5 rounded-full bg-rose-500" />
                            <span className="text-[10px] font-bold text-rose-500">-{ (models || []).filter(m => m.status === 'REMOVED').length } Removed</span>
                        </div>
                    </div>
                </div>
                <div className="overflow-auto no-scrollbar">
                    <table className="w-full text-left">
                        <thead className="sticky top-0 bg-[var(--bg-surface)]/80 backdrop-blur-md z-10">
                            <tr className="border-b border-[var(--border-light)] text-[var(--text-tertiary)]">
                                <th className="px-8 py-4 font-bold uppercase tracking-widest text-[9px]">Entity Name</th>
                                <th className="px-8 py-4 font-bold uppercase tracking-widest text-[9px]">Status</th>
                                <th className="px-8 py-4 font-bold uppercase tracking-widest text-[9px]">Insight / Deviation</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border-light)]/30">
                            {(!models || models.length === 0) ? (
                                <tr>
                                    <td colSpan="3" className="px-8 py-20 text-center text-[var(--text-tertiary)]">
                                        <div className="flex flex-col items-center gap-3">
                                            <ShieldCheck size={32} className="opacity-20" />
                                            <p className="text-sm font-medium">No structural changes detected between these checkpoints.</p>
                                        </div>
                                    </td>
                                </tr>
                            ) : models.map((m, idx) => (
                                <tr key={idx} className="hover:bg-[var(--color-accent-faint)] transition-colors group">
                                    <td className="px-8 py-4">
                                        <div className="flex items-center gap-3">
                                            <div className={`w-2 h-2 rounded-full ${
                                                m.status === 'ADDED' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]' : 
                                                m.status === 'REMOVED' ? 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)]' : 
                                                m.status === 'MODIFIED' ? 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]' : 'bg-slate-400'
                                            }`} />
                                            <span className="font-bold text-[var(--text-primary)] text-sm">{m.name}</span>
                                        </div>
                                    </td>
                                    <td className="px-8 py-4">
                                        <Badge variant={
                                            m.status === 'ADDED' ? 'success' : 
                                            m.status === 'REMOVED' ? 'error' : 
                                            m.status === 'MODIFIED' ? 'warning' : 'default'
                                        }>{m.status}</Badge>
                                    </td>
                                    <td className="px-8 py-4 text-[12px] text-[var(--text-secondary)] italic leading-relaxed">
                                        {m.details || 'Schema evolution within standard parameters.'}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </GlassCard>
        </div>
    );
}

// --- MAIN PAGE ---

export default function VersionControlPage() {
    const activeProjectId = useUIStore(state => state.activeProjectId);
    const [projects, setProjects] = useState([]);
    const [selectedProjectId, setSelectedProjectId] = useState(activeProjectId || '');
    const [versions, setVersions] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [isProjectsLoading, setIsProjectsLoading] = useState(false);
    const [selectedRun, setSelectedRun] = useState(null);
    const [diffSelection, setDiffSelection] = useState([]);
    const [viewMode, setViewMode] = useState('history'); 
    const [diffData, setDiffData] = useState(null);
    const [isComparing, setIsComparing] = useState(false);
    const [rollbackTarget, setRollbackTarget] = useState(null);
    const [isRollingBack, setIsRollingBack] = useState(false);
    const [isDeleting, setIsDeleting] = useState(null);
    const [stats, setStats] = useState(null);
    const { addLog } = useLogs();

    const loadProjects = useCallback(async () => {
        setIsProjectsLoading(true);
        try {
            const data = await api.listProjects();
            setProjects(data || []);
            if (!selectedProjectId && data.length > 0) {
                setSelectedProjectId(data[0].id);
            }
        } catch (err) {
            addLog('error', 'VC', 'Failed to load projects: ' + err.message);
        } finally {
            setIsProjectsLoading(false);
        }
    }, [selectedProjectId, addLog]);

    const loadVersions = useCallback(async (projId) => {
        if (!projId) return;
        setIsLoading(true);
        try {
            const [runs, vcStats] = await Promise.all([
                api.getProjectRuns(projId),
                api.apiFetch ? api.apiFetch(`/api/version-control/stats?project_id=${projId}`) : Promise.resolve(null)
            ]);
            
            const sorted = (Array.isArray(runs) ? runs : []).sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
            setVersions(sorted);
            const runIds = new Set(sorted.map((r) => r.run_id));
            setDiffSelection((prev) => prev.filter((id) => runIds.has(id)));
            
            // Stats might be raw from fetch
            if (vcStats && vcStats.json) {
                const s = await vcStats.json();
                setStats(s);
            }

            setSelectedRun((prev) => {
                if (!sorted.length) return null;
                if (!prev || !runIds.has(prev.run_id)) return sorted[0];
                return prev;
            });
        } catch (err) {
            addLog('error', 'VC', 'Load failed: ' + err.message);
        } finally {
            setIsLoading(false);
        }
    }, [addLog]);

    useEffect(() => {
        loadProjects();
    }, [loadProjects]);

    useEffect(() => {
        if (selectedProjectId) {
            // Reset state when switching projects
            setDiffSelection([]);
            setViewMode('history');
            setDiffData(null);
            setSelectedRun(null);
            loadVersions(selectedProjectId);
        }
    }, [selectedProjectId, loadVersions]);

    const handleCompare = async () => {
        if (diffSelection.length !== 2) return;
        setIsComparing(true);
        try {
            const r1 = versions.find(v => v.run_id === diffSelection[0]);
            const r2 = versions.find(v => v.run_id === diffSelection[1]);
            
            // Logic to determine which is base and which is target (chronological)
            const d1 = new Date(r1.started_at).getTime();
            const d2 = new Date(r2.started_at).getTime();
            
            const [base, target] = d1 < d2 ? [r1, r2] : [r2, r1];
            
            const s1 = base?.after_tgt_snapshots?.[0] || base?.before_tgt_snapshots?.[0];
            const s2 = target?.after_tgt_snapshots?.[0] || target?.before_tgt_snapshots?.[0];
            
            if (!s1 || !s2) throw new Error('Selected versions do not contain valid snapshots.');
            
            const data = await api.compareProjectSnapshots(selectedProjectId, s1, s2);
            setDiffData(data);
            setViewMode('diff');
        } catch (err) {
            addLog('error', 'VC', 'Comparison Error: ' + err.message);
        } finally {
            setIsComparing(false);
        }
    };

    const handleRestore = async (run) => {
        setIsRollingBack(true);
        try {
            const snapId = run?.after_tgt_snapshots?.[0] || run?.before_tgt_snapshots?.[0];
            if (!snapId) throw new Error('No snapshot found for this run.');
            
            const result = await api.restoreProjectVersion(selectedProjectId, { snapshot_id: snapId });
            addLog('success', 'VC', 'Rollback initiated: ' + (result.run_id || result.id));
            setRollbackTarget(null);
            loadVersions(selectedProjectId);
        } catch (err) {
            addLog('error', 'VC', 'Rollback failed: ' + err.message);
        } finally {
            setIsRollingBack(false);
        }
    };

    const handleDeleteSnapshot = async (snapshotId) => {
        if (!snapshotId) return;
        setIsDeleting(snapshotId);
        try {
            const result = await api.deleteModelVersions(selectedProjectId, [snapshotId]);
            if (result.blocked_ids?.length > 0) {
                addLog('warning', 'VC', 'Snapshot is referenced and cannot be deleted.');
            } else {
                addLog('success', 'VC', `Successfully deleted version(s).`);
                loadVersions(selectedProjectId);
            }
        } catch (err) {
            addLog('error', 'VC', 'Delete failed: ' + err.message);
        } finally {
            setIsDeleting(null);
        }
    };

    const selectedProject = projects.find(p => p.id === selectedProjectId);

    return (
        <div className="p-8 max-w-[1600px] mx-auto min-h-full">
            <PageHeader 
                title="Version Control" 
                description="Manage semantic snapshots, structural evolution, and system rollbacks across all projects."
            />

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-8 mt-8">
                
                {/* --- Left Sidebar: Project Selection & Stats --- */}
                <aside className="xl:col-span-3 flex flex-col gap-6">
                    <GlassCard className="p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center border border-blue-500/20">
                                <Layers size={16} className="text-blue-500" />
                            </div>
                            <h3 className="text-xs font-black text-[var(--text-primary)] uppercase tracking-widest">Workspace Context</h3>
                        </div>

                        <div className="space-y-4">
                            <div>
                                <label className="text-[10px] font-bold text-[var(--text-tertiary)] uppercase tracking-[0.1em] block mb-2 px-1">Active Project</label>
                                <div className="relative group">
                                    <select 
                                        value={selectedProjectId}
                                        onChange={(e) => setSelectedProjectId(e.target.value)}
                                        className="w-full bg-[var(--bg-surface-raised)] border border-[var(--border-main)] rounded-xl px-4 py-3 text-[13px] font-bold text-[var(--text-primary)] appearance-none cursor-pointer outline-none focus:border-[var(--accent-blue)] transition-all group-hover:bg-[var(--bg-surface)]"
                                    >
                                        {projects.map(p => (
                                            <option key={p.id} value={p.id}>{p.name}</option>
                                        ))}
                                    </select>
                                    <ChevronRight size={14} className="absolute right-4 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)] rotate-90 pointer-events-none" />
                                </div>
                            </div>

                            {selectedProject && (
                                <div className="p-4 rounded-xl bg-blue-500/5 border border-blue-500/10 mt-2">
                                    <div className="flex items-center gap-2 mb-2">
                                        <Badge variant="info">{selectedProject.adapter || 'SML'}</Badge>
                                        <span className="text-[10px] font-bold text-[var(--text-secondary)] truncate">{selectedProject.id}</span>
                                    </div>
                                    <p className="text-[11px] text-[var(--text-tertiary)] leading-normal line-clamp-2">
                                        {selectedProject.description || 'Enterprise semantic model with versioned schema history.'}
                                    </p>
                                </div>
                            )}
                        </div>
                    </GlassCard>

                    <GlassCard className="p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center border border-purple-500/20">
                                <Activity size={16} className="text-purple-500" />
                            </div>
                            <h3 className="text-xs font-black text-[var(--text-primary)] uppercase tracking-widest">Pruning Intelligence</h3>
                        </div>

                        <div className="space-y-5">
                            <div className="flex justify-between items-center">
                                <span className="text-xs text-[var(--text-secondary)] font-medium">History Depth</span>
                                <span className="text-sm font-black text-[var(--text-primary)]">{versions.length} versions</span>
                            </div>
                            <div className="flex justify-between items-center">
                                <span className="text-xs text-[var(--text-secondary)] font-medium">Storage Impact</span>
                                <span className="text-sm font-black text-[var(--text-primary)]">{stats?.storage_size || '12.4 MB'}</span>
                            </div>
                            <div className="flex justify-between items-center">
                                <span className="text-xs text-[var(--text-secondary)] font-medium">Oldest Snapshot</span>
                                <span className="text-sm font-black text-[var(--text-primary)]">
                                    {versions.length > 0 ? new Date(versions[versions.length-1].started_at).toLocaleDateString() : '—'}
                                </span>
                            </div>
                            
                            <div className="pt-4 border-t border-[var(--border-light)]">
                                <ActionButton variant="ghost" className="w-full justify-center opacity-60 hover:opacity-100">
                                    <ShieldCheck size={14} /> View Retention Policy
                                </ActionButton>
                            </div>
                        </div>
                    </GlassCard>
                </aside>

                {/* --- Main Area: History Timeline or Diff View --- */}
                <main className="xl:col-span-9 flex flex-col gap-6">
                    
                    {viewMode === 'history' ? (
                        <>
                            {/* --- Selected Version Card --- */}
                            <GlassCard className="p-8 relative overflow-hidden group">
                                <div className="absolute top-0 right-0 w-64 h-64 blur-[100px] opacity-[0.03] -mr-32 -mt-32 bg-blue-500 transition-all group-hover:opacity-[0.06]" />
                                
                                {selectedRun ? (
                                    <div className="animate-in fade-in slide-in-from-top-4 duration-500">
                                        <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-6 mb-10">
                                            <div className="flex items-center gap-4">
                                                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-600 to-blue-400 flex items-center justify-center shadow-lg shadow-blue-500/20">
                                                    <RotateCcw className="text-white" size={28} />
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2 mb-1">
                                                        <span className="text-[10px] font-black text-blue-500 uppercase tracking-widest">Active Checkpoint</span>
                                                        <Badge variant={selectedRun.status === 'success' ? 'success' : 'error'}>{selectedRun.status}</Badge>
                                                    </div>
                                                    <h3 className="text-2xl font-black text-[var(--text-primary)] tracking-tight font-mono">{selectedRun.run_id}</h3>
                                                </div>
                                            </div>

                                            <div className="flex gap-3">
                                                <ActionButton 
                                                    variant="primary" 
                                                    onClick={() => setRollbackTarget(selectedRun)}
                                                    className="px-8 py-3.5 shadow-xl"
                                                >
                                                    <RotateCcw size={18} /> Rollback System
                                                </ActionButton>
                                                <ActionButton 
                                                    variant="ghost" 
                                                    disabled={diffSelection.length !== 2 || isComparing}
                                                    onClick={handleCompare}
                                                    className={`px-6 py-3.5 ${diffSelection.length === 2 ? 'border-blue-500 text-blue-500 bg-blue-500/5' : ''}`}
                                                >
                                                    {isComparing ? <Loader2 size={18} className="animate-spin" /> : <GitCompare size={18} />}
                                                    {diffSelection.length === 2 ? 'Analyse Comparison' : 'Select 2 to Diff'}
                                                </ActionButton>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                            {[
                                                { label: 'Origin Method', val: selectedRun.run_type || 'Manual Sync', icon: Zap, color: 'text-amber-500' },
                                                { label: 'Execution Time', val: new Date(selectedRun.started_at).toLocaleTimeString(), icon: Clock, color: 'text-slate-500' },
                                                { label: 'Integrity Check', val: 'Passed', icon: ShieldCheck, color: 'text-emerald-500' },
                                                { label: 'Snapshot ID', val: (selectedRun.after_tgt_snapshots?.[0] || 'N/A').substring(0, 10) + '...', icon: Database, color: 'text-blue-500' }
                                            ].map((stat, i) => (
                                                <div key={i} className="p-5 rounded-2xl bg-[var(--bg-surface-raised)]/50 border border-[var(--border-light)] hover:border-[var(--accent-blue)]/20 transition-colors">
                                                    <div className="flex items-center gap-2 mb-2">
                                                        <stat.icon size={14} className={stat.color} />
                                                        <span className="text-[9px] font-black text-[var(--text-tertiary)] uppercase tracking-widest">{stat.label}</span>
                                                    </div>
                                                    <p className="text-[var(--text-primary)] font-bold text-[15px]">{stat.val}</p>
                                                </div>
                                            ))}
                                        </div>
                                        
                                        {selectedRun.error_message && (
                                            <div className="mt-8 p-5 rounded-2xl bg-rose-500/5 border border-rose-500/10 flex gap-4">
                                                <AlertTriangle className="text-rose-500 flex-shrink-0" size={20} />
                                                <div>
                                                    <h4 className="text-xs font-bold text-rose-500 uppercase tracking-widest mb-1">Deployment Error Detected</h4>
                                                    <p className="text-sm text-rose-500/80 leading-relaxed font-medium">{selectedRun.error_message}</p>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                ) : (
                                    <div className="py-20 flex flex-col items-center justify-center text-center">
                                        <div className="w-20 h-20 rounded-full bg-slate-500/5 flex items-center justify-center mb-6">
                                            <HistoryIcon size={40} className="text-slate-500/20" />
                                        </div>
                                        <h3 className="text-xl font-bold text-[var(--text-primary)] mb-2">Select a version from the timeline</h3>
                                        <p className="text-[var(--text-tertiary)] max-w-sm">
                                            Choose a checkpoint from the history log on the right to view its details or initiate a rollback.
                                        </p>
                                    </div>
                                )}
                            </GlassCard>

                            {/* --- History List (Grid) --- */}
                            <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-6">
                                {isLoading ? (
                                    Array(6).fill(0).map((_, i) => (
                                        <div key={i} className="h-44 rounded-2xl bg-[var(--bg-surface)]/40 border border-[var(--border-main)] animate-pulse" />
                                    ))
                                ) : versions.length === 0 ? (
                                    <div className="col-span-full py-20 text-center">
                                        <Info size={40} className="mx-auto text-[var(--text-tertiary)] opacity-20 mb-4" />
                                        <p className="text-[var(--text-tertiary)] font-bold uppercase tracking-widest">No version history available for this project.</p>
                                    </div>
                                ) : versions.map((v, idx) => (
                                    <div 
                                        key={v.run_id}
                                        onClick={() => setSelectedRun(v)}
                                        className={`group relative p-6 rounded-2xl border transition-all cursor-pointer overflow-hidden ${
                                            selectedRun?.run_id === v.run_id 
                                            ? 'bg-gradient-to-br from-blue-600 to-blue-500 border-transparent shadow-xl shadow-blue-500/10' 
                                            : 'bg-[var(--bg-surface)]/60 border-[var(--border-main)] hover:border-blue-500/30 hover:bg-[var(--bg-surface)]'
                                        }`}
                                    >
                                        <div className="flex justify-between items-start mb-6 relative z-10">
                                            <div>
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className={`text-[9px] font-black uppercase tracking-widest ${selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-tertiary)]'}`}>
                                                        Checkpoint #{versions.length - idx}
                                                    </span>
                                                    {v.status === 'error' && (
                                                        <div className={`w-2 h-2 rounded-full bg-rose-400 animate-pulse`} />
                                                    )}
                                                </div>
                                                <h4 className={`text-base font-bold font-mono ${selectedRun?.run_id === v.run_id ? 'text-white' : 'text-[var(--text-primary)]'}`}>
                                                    {v.run_id?.substring(0, 12)}
                                                </h4>
                                            </div>
                                            <div className="flex gap-1.5">
                                                <div 
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        const id = v.run_id;
                                                        setDiffSelection(p => p.includes(id) ? p.filter(x => x !== id) : p.length < 2 ? [...p, id] : [p[1], id]);
                                                    }}
                                                    className={`p-2 rounded-lg transition-all border ${
                                                        diffSelection.includes(v.run_id)
                                                        ? 'bg-white text-blue-600 border-white'
                                                        : selectedRun?.run_id === v.run_id 
                                                            ? 'bg-blue-400/20 text-white border-blue-400/30 hover:bg-white hover:text-blue-600'
                                                            : 'bg-[var(--bg-main)] text-[var(--text-tertiary)] border-[var(--border-light)] hover:text-blue-500 hover:border-blue-500/30'
                                                    }`}
                                                >
                                                    <GitCompare size={14} />
                                                </div>
                                                <div 
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        handleDeleteSnapshot(v.after_tgt_snapshots?.[0] || v.before_tgt_snapshots?.[0]);
                                                    }}
                                                    className={`p-2 rounded-lg transition-all border ${
                                                        selectedRun?.run_id === v.run_id 
                                                            ? 'bg-blue-400/20 text-white border-blue-400/30 hover:bg-rose-500 hover:border-rose-500'
                                                            : 'bg-[var(--bg-main)] text-[var(--text-tertiary)] border-[var(--border-light)] hover:text-rose-500 hover:border-rose-500/30'
                                                    }`}
                                                >
                                                    {isDeleting === (v.after_tgt_snapshots?.[0] || v.before_tgt_snapshots?.[0]) ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                                                </div>
                                            </div>
                                        </div>

                                        <div className="flex items-end justify-between relative z-10">
                                            <div className="flex flex-col gap-1">
                                                <div className="flex items-center gap-2">
                                                    <Calendar size={12} className={selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-tertiary)]'} />
                                                    <span className={`text-[11px] font-bold ${selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-secondary)]'}`}>
                                                        {new Date(v.started_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                                                    </span>
                                                </div>
                                                <div className="flex items-center gap-2">
                                                    <Clock size={12} className={selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-tertiary)]'} />
                                                    <span className={`text-[11px] font-bold ${selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-secondary)]'}`}>
                                                        {new Date(v.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                                    </span>
                                                </div>
                                            </div>
                                            <div className={`p-2 rounded-full ${selectedRun?.run_id === v.run_id ? 'bg-white/20' : 'bg-[var(--bg-surface-raised)] opacity-0 group-hover:opacity-100'} transition-all`}>
                                                <ArrowRight size={14} className={selectedRun?.run_id === v.run_id ? 'text-white' : 'text-[var(--text-tertiary)]'} />
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </>
                    ) : (
                        <DiffView 
                            diffData={diffData} 
                            baseRun={versions.find(v => v.run_id === diffSelection[0])}
                            targetRun={versions.find(v => v.run_id === diffSelection[1])}
                            onBack={() => setViewMode('history')} 
                        />
                    )}
                </main>
            </div>

            {/* --- Rollback Confirmation Dialog --- */}
            {rollbackTarget && (
                <div className="fixed inset-0 z-[150] flex items-center justify-center p-6">
                    <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-md animate-in fade-in duration-300" onClick={() => setRollbackTarget(null)} />
                    <GlassCard className="relative p-10 max-w-md w-full border-amber-500/30 animate-in zoom-in-95 shadow-2xl bg-[var(--bg-surface)]">
                        <div className="w-16 h-16 rounded-2xl bg-amber-500/10 flex items-center justify-center text-amber-500 border border-amber-500/20 mb-8 mx-auto shadow-inner">
                            <RotateCcw size={32} />
                        </div>
                        <div className="text-center mb-10">
                            <h3 className="text-2xl font-black text-[var(--text-primary)] mb-3 tracking-tight">Destructive Rollback</h3>
                            <p className="text-sm text-[var(--text-secondary)] leading-relaxed px-4">
                                You are about to restore project <span className="font-bold text-[var(--text-primary)]">{selectedProject?.name}</span> to checkpoint <span className="font-mono font-bold text-blue-500">{rollbackTarget.run_id?.substring(0, 10)}</span>. 
                                <br/><br/>
                                This will overwrite current semantic definitions and may affect downstream datasets.
                            </p>
                        </div>
                        <div className="flex gap-4">
                            <ActionButton onClick={() => setRollbackTarget(null)} className="flex-1 py-3.5 font-black uppercase tracking-widest text-[11px] hover:bg-slate-500/5">Aborted</ActionButton>
                            <ActionButton 
                                variant="primary"
                                onClick={() => handleRestore(rollbackTarget)} 
                                disabled={isRollingBack} 
                                className="flex-1 py-3.5 font-black uppercase tracking-widest text-[11px]"
                            >
                                {isRollingBack ? <Loader2 size={16} className="animate-spin" /> : 'Confirm Mutation'}
                            </ActionButton>
                        </div>
                    </GlassCard>
                </div>
            )}
        </div>
    );
}
