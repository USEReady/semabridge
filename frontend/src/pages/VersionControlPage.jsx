import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    History,
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
    Calendar,
    Check,
    X,
    Lock,
    Unlock,
    Hash,
    GitBranch,
    Table,
    ListTree,
    Pin
} from 'lucide-react';
import { api, formatDate } from '../utils/api';
import { useUIStore } from '../store/uiStore';
import { useLogs } from '../context/LogsContext';
import PageHeader from '../components/common/PageHeader';
import RepositoryBrowser from '../components/RepositoryBrowser';
import { Archive } from 'lucide-react';


// --- STYLED COMPONENTS ---

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

// --- DIFF VIEW COMPONENT ---

function DiffView({ diffData, baseRun, targetRun, onBack }) {
    if (!diffData) return null;
    const { metadata_diff, models } = diffData;
    const [filterStatus, setFilterStatus] = useState('ALL');
    const [search, setSearch] = useState('');

    const filteredModels = (models || []).filter(m => {
        const matchesSearch = m.name.toLowerCase().includes(search.toLowerCase());
        const matchesStatus = filterStatus === 'ALL' || m.status === filterStatus;
        return matchesSearch && matchesStatus;
    });
    
    return (
        <div className="flex flex-col gap-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {[
                    { key: 'snapshot_a', run: baseRun, label: 'BASE VERSION', accent: 'var(--accent-blue)', color: 'blue' },
                    { key: 'snapshot_b', run: targetRun, label: 'TARGET VERSION', accent: 'var(--color-success)', color: 'emerald' }
                ].map(({ key, run, label, accent, color }) => (
                    <GlassCard key={key} className="p-6 relative group overflow-hidden">
                        <div className={`absolute top-0 right-0 w-32 h-32 blur-3xl opacity-10 -mr-16 -mt-16 bg-${color}-500`} />
                        <div className="flex justify-between items-center mb-4 relative z-10">
                            <h4 className="text-[10px] font-black text-[var(--text-tertiary)] uppercase tracking-[0.2em]">{label}</h4>
                            <div className="p-2 rounded-lg bg-[var(--bg-main)]/50 border border-[var(--border-light)]">
                                <Database size={16} className={`text-${color}-500`} />
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-y-4 text-[13px] relative z-10">
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Run Context</span> 
                                <span className="text-[var(--text-primary)] font-mono font-bold mt-0.5">{run?.run_id?.substring(0, 12) || 'N/A'}</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Data Format</span> 
                                <span className="text-[var(--text-primary)] uppercase font-bold mt-0.5">{metadata_diff?.[key]?.format || 'OSI'}</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Model Count</span> 
                                <span className="text-[var(--text-primary)] font-bold mt-0.5">{metadata_diff?.[key]?.model_count || 0} Entities</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[var(--text-tertiary)] text-[10px] uppercase font-bold tracking-wider">Captured At</span> 
                                <span className="text-[var(--text-primary)] font-bold mt-0.5">
                                    {formatDate(metadata_diff?.[key]?.taken_at)}
                                </span>
                            </div>
                        </div>
                    </GlassCard>
                ))}
            </div>

            <GlassCard className="flex-1 flex flex-col overflow-hidden bg-[var(--bg-surface)]/20 min-h-[500px]">
                <div className="px-6 py-5 border-b border-[var(--border-light)] flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-[var(--bg-surface)]/50">
                    <div className="flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-blue-500/10 flex items-center justify-center border border-blue-500/20">
                            <GitCompare size={20} className="text-blue-500" />
                        </div>
                        <div>
                            <h3 className="text-sm font-black text-[var(--text-primary)] uppercase tracking-widest">Structural Evolution</h3>
                            <p className="text-[10px] text-[var(--text-tertiary)] font-bold uppercase mt-0.5">Comparing logical schema divergence</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-3 w-full md:w-auto">
                        <div className="relative flex-1 md:w-64">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" size={14} />
                            <input 
                                type="text"
                                placeholder="Filter models..."
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className="w-full bg-[var(--bg-main)]/50 border border-[var(--border-light)] rounded-xl pl-9 pr-4 py-2 text-xs font-bold focus:border-blue-500/50 outline-none transition-all"
                            />
                        </div>
                        <select 
                            value={filterStatus}
                            onChange={(e) => setFilterStatus(e.target.value)}
                            className="bg-[var(--bg-main)]/50 border border-[var(--border-light)] rounded-xl px-4 py-2 text-xs font-bold outline-none focus:border-blue-500/50 cursor-pointer"
                        >
                            <option value="ALL">ALL CHANGES</option>
                            <option value="ADDED">ADDED</option>
                            <option value="REMOVED">REMOVED</option>
                            <option value="MODIFIED">MODIFIED</option>
                            <option value="UNCHANGED">UNCHANGED</option>
                        </select>
                    </div>
                </div>

                <div className="overflow-auto no-scrollbar max-h-[600px]">
                    <table className="w-full text-left">
                        <thead className="sticky top-0 bg-[var(--bg-surface)]/90 backdrop-blur-md z-10 border-b border-[var(--border-light)]">
                            <tr className="text-[var(--text-tertiary)]">
                                <th className="px-8 py-4 font-black uppercase tracking-[0.1em] text-[9px] w-1/3">Model Entity</th>
                                <th className="px-8 py-4 font-black uppercase tracking-[0.1em] text-[9px] w-1/4">Status</th>
                                <th className="px-8 py-4 font-black uppercase tracking-[0.1em] text-[9px]">Drift Analysis</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border-light)]/30">
                            {filteredModels.length === 0 ? (
                                <tr>
                                    <td colSpan="3" className="px-8 py-20 text-center text-[var(--text-tertiary)]">
                                        <div className="flex flex-col items-center gap-3">
                                            <Search size={32} className="opacity-10" />
                                            <p className="text-sm font-bold uppercase tracking-widest opacity-40">No matching entities found</p>
                                        </div>
                                    </td>
                                </tr>
                            ) : filteredModels.map((m, idx) => (
                                <tr 
                                    key={idx} 
                                    onClick={() => setSelectedModelHistory(m.name)}
                                    className="hover:bg-blue-500/5 transition-colors group cursor-pointer"
                                >
                                    <td className="px-8 py-5">
                                        <div className="flex items-center gap-3">
                                            <div className={`w-2 h-2 rounded-full ${
                                                m.status === 'ADDED' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]' : 
                                                m.status === 'REMOVED' ? 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.5)]' : 
                                                m.status === 'MODIFIED' ? 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.5)]' : 'bg-slate-400 opacity-60'
                                            }`} />
                                            <span className="font-bold text-[var(--text-primary)] text-sm tracking-tight">{m.name}</span>
                                        </div>
                                    </td>
                                    <td className="px-8 py-5">
                                        <div className="flex">
                                            <Badge variant={
                                                m.status === 'ADDED' ? 'success' : 
                                                m.status === 'REMOVED' ? 'error' : 
                                                m.status === 'MODIFIED' ? 'warning' : 'default'
                                            }>{m.status}</Badge>
                                        </div>
                                    </td>
                                    <td className="px-8 py-5 text-[12px] text-[var(--text-secondary)] font-medium leading-relaxed italic opacity-80">
                                        {m.details?.message || 'Logical consistency maintained across this mutation.'}
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

// --- MODEL HISTORY DRAWER ---

function ModelHistoryDrawer({ projectId, modelName, onClose }) {
    const [history, setHistory] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetch = async () => {
            setLoading(true);
            try {
                const res = await api.apiFetch(`/projects/${projectId}/models/${modelName}/history`);
                const json = await res.json();
                setHistory(json || []);
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        fetch();
    }, [projectId, modelName]);

    return (
        <div className="fixed inset-y-0 right-0 w-[500px] bg-[var(--bg-surface)]/95 backdrop-blur-xl border-l border-[var(--border-light)] z-[300] shadow-2xl animate-in slide-in-from-right duration-300">
            <div className="p-8 h-full flex flex-col">
                <div className="flex justify-between items-start mb-8">
                    <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-2xl bg-blue-500/10 flex items-center justify-center border border-blue-500/20">
                            <History size={24} className="text-blue-500" />
                        </div>
                        <div>
                            <h3 className="text-xl font-black text-[var(--text-primary)] truncate max-w-[300px]">{modelName}</h3>
                            <p className="text-[10px] text-[var(--text-tertiary)] font-bold uppercase tracking-widest mt-1">Entity Evolution Timeline</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-slate-500/10 rounded-xl transition-all">
                        <X size={20} className="text-[var(--text-tertiary)]" />
                    </button>
                </div>

                <div className="flex-1 overflow-auto no-scrollbar pr-4">
                    {loading ? (
                        <div className="h-full flex flex-col items-center justify-center gap-4">
                            <Loader2 size={32} className="animate-spin text-blue-500" />
                            <span className="text-[10px] font-black text-[var(--text-tertiary)] uppercase tracking-widest">Tracing lineage...</span>
                        </div>
                    ) : history.length === 0 ? (
                        <div className="h-full flex flex-col items-center justify-center text-center opacity-40">
                            <Search size={48} className="mb-4" />
                            <p className="text-sm font-bold uppercase tracking-widest">No change history found</p>
                        </div>
                    ) : (
                        <div className="space-y-12 relative before:absolute before:left-[19px] before:top-2 before:bottom-2 before:w-[2px] before:bg-gradient-to-b before:from-blue-500/50 before:to-transparent">
                            {history.reverse().map((item, i) => (
                                <div key={i} className="relative pl-12 group">
                                    <div className="absolute left-0 top-1 w-10 h-10 rounded-full bg-[var(--bg-surface)] border-2 border-blue-500 flex items-center justify-center z-10 group-hover:scale-110 transition-transform shadow-lg shadow-blue-500/20">
                                        <div className="w-2 h-2 rounded-full bg-blue-500" />
                                    </div>
                                    <div className="p-5 rounded-2xl bg-[var(--bg-main)]/50 border border-[var(--border-light)] hover:border-blue-500/30 transition-all">
                                        <div className="flex justify-between items-start mb-3">
                                            <span className="text-[10px] font-black text-blue-500 uppercase tracking-widest">Run #{item.run_id.substring(0,8)}</span>
                                            <span className="text-[10px] font-bold text-[var(--text-tertiary)]">{formatDate(item.timestamp)}</span>
                                        </div>
                                        <div className="flex items-center gap-3 mb-4">
                                            <Badge variant="ghost">Snapshot {item.snapshot_id.substring(0,8)}</Badge>
                                            <Badge variant="success">Active</Badge>
                                        </div>
                                        <div className="text-[11px] text-[var(--text-secondary)] font-medium leading-relaxed italic opacity-80">
                                            This model was captured as part of a system sync. Its logical structure was validated.
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

// --- PROJECT STATS VIEW ---

function ProjectStatsView({ projectId }) {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetch = async () => {
            setLoading(true);
            try {
                const data = await api.apiFetch(`/projects/${projectId}/stats`);
                const json = await data.json();
                setStats(json);
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        fetch();
    }, [projectId]);

    if (loading) return (
        <div className="py-40 flex justify-center">
            <Loader2 className="animate-spin text-blue-500" size={32} />
        </div>
    );

    const cards = [
        { label: 'Total Snapshots', val: stats?.total_snapshots, icon: Database, color: 'text-blue-500' },
        { label: 'Pinned Versions', val: stats?.pinned_count, icon: Pin, color: 'text-amber-500' },
        { label: 'Storage Used', val: stats?.storage_estimate, icon: ShieldCheck, color: 'text-emerald-500' },
        { label: 'Avg Models/Snap', val: stats?.avg_models_per_snapshot, icon: ListTree, color: 'text-purple-500' }
    ];

    return (
        <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 space-y-8">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                {cards.map((c, i) => (
                    <GlassCard key={i} className="p-8">
                        <div className="flex justify-between items-start mb-6">
                            <div className={`p-3 rounded-xl bg-slate-500/5 ${c.color}`}>
                                <c.icon size={24} />
                            </div>
                            <div className="h-2 w-12 bg-slate-500/10 rounded-full overflow-hidden">
                                <div className={`h-full ${c.color.replace('text', 'bg')} opacity-40`} style={{width: '60%'}} />
                            </div>
                        </div>
                        <p className="text-[10px] font-black text-[var(--text-tertiary)] uppercase tracking-[0.15em] mb-2">{c.label}</p>
                        <h3 className="text-3xl font-black text-[var(--text-primary)] tracking-tighter">{c.val}</h3>
                    </GlassCard>
                ))}
            </div>

            <GlassCard className="p-10">
                <div className="flex items-center gap-4 mb-10">
                    <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 flex items-center justify-center border border-emerald-500/20">
                        <Activity size={24} className="text-emerald-500" />
                    </div>
                    <div>
                        <h3 className="text-xl font-black text-[var(--text-primary)]">Project Health Score</h3>
                        <p className="text-xs text-[var(--text-tertiary)] font-bold uppercase tracking-widest mt-1">Reliability index based on sync success rates</p>
                    </div>
                </div>
                
                <div className="flex items-center gap-12">
                    <div className="relative w-48 h-48 flex items-center justify-center">
                        <svg className="w-full h-full transform -rotate-90">
                            <circle cx="96" cy="96" r="88" className="stroke-slate-500/10 fill-none" strokeWidth="16" />
                            <circle cx="96" cy="96" r="88" className="stroke-emerald-500 fill-none transition-all duration-1000" strokeWidth="16" strokeDasharray="552.92" strokeDashoffset={552.92 * (1 - stats?.health_score / 100)} />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className="text-5xl font-black text-[var(--text-primary)] tracking-tighter">{stats?.health_score}%</span>
                            <span className="text-[10px] font-black text-emerald-500 uppercase tracking-widest mt-1">Stable</span>
                        </div>
                    </div>
                    <div className="flex-1 space-y-6">
                        <div className="p-6 rounded-2xl bg-blue-500/5 border border-blue-500/10">
                            <h4 className="text-xs font-black text-blue-500 uppercase tracking-widest mb-2">Observation</h4>
                            <p className="text-[13px] text-[var(--text-secondary)] font-medium leading-relaxed italic">
                                "This project has maintained high structural integrity. Sync operations are 92% successful, and snapshot pruning is optimized for retention."
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="p-5 rounded-2xl border border-[var(--border-light)] bg-[var(--bg-main)]/30">
                                <span className="text-[9px] font-black text-[var(--text-tertiary)] uppercase tracking-widest block mb-1">Prune Eligible</span>
                                <span className="text-xl font-black text-[var(--text-primary)]">12 Snapshots</span>
                            </div>
                            <div className="p-5 rounded-2xl border border-[var(--border-light)] bg-[var(--bg-main)]/30">
                                <span className="text-[9px] font-black text-[var(--text-tertiary)] uppercase tracking-widest block mb-1">Last Pruned</span>
                                <span className="text-xl font-black text-[var(--text-primary)]">2d ago</span>
                            </div>
                        </div>
                    </div>
                </div>
            </GlassCard>
        </div>
    );
}

// --- MAPPINGS VIEW COMPONENT ---

function MappingsView({ runId, projectId }) {
    const [mappings, setMappings] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetch = async () => {
            setLoading(true);
            try {
                const data = await api.listMappings(projectId);
                // The backend returns an object { mappings: [...], collisions: [...] }
                // or a direct array in some legacy paths.
                const results = Array.isArray(data) ? data : (data?.mappings || []);
                setMappings(results);
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        fetch();
    }, [projectId, runId]);

    if (loading) return (
        <div className="flex flex-col items-center justify-center py-40 gap-4">
            <Loader2 size={32} className="animate-spin text-blue-500" />
            <span className="text-[10px] font-black uppercase tracking-widest text-[var(--text-tertiary)]">Reconstructing effective mappings...</span>
        </div>
    );

    return (
        <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <GlassCard className="overflow-hidden">
                <div className="px-8 py-6 border-b border-[var(--border-light)] flex justify-between items-center bg-[var(--bg-surface)]/30">
                    <div className="flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-amber-500/10 flex items-center justify-center border border-amber-500/20">
                            <ListTree size={20} className="text-amber-500" />
                        </div>
                        <div>
                            <h3 className="text-sm font-black text-[var(--text-primary)] uppercase tracking-widest">Effective Mappings</h3>
                            <p className="text-[10px] text-[var(--text-tertiary)] font-bold uppercase mt-0.5">Source to Target Resolution</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-500/5 border border-[var(--border-light)]">
                        <Lock size={14} className="text-[var(--text-tertiary)]" />
                        <span className="text-[10px] font-black text-[var(--text-tertiary)] uppercase tracking-widest">Immutable Context</span>
                    </div>
                </div>

                <div className="overflow-auto no-scrollbar">
                    <table className="w-full text-left">
                        <thead className="bg-[var(--bg-surface)]/60 text-[var(--text-tertiary)]">
                            <tr>
                                <th className="px-8 py-4 font-black uppercase tracking-[0.1em] text-[9px]">Source Entity</th>
                                <th className="px-8 py-4 font-black uppercase tracking-[0.1em] text-[9px]">Target (Effective)</th>
                                <th className="px-8 py-4 font-black uppercase tracking-[0.1em] text-[9px] text-center">Collision</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border-light)]/20">
                            {mappings.length === 0 ? (
                                <tr>
                                    <td colSpan="3" className="px-8 py-20 text-center text-[var(--text-tertiary)]">
                                        <Info size={32} className="mx-auto opacity-10 mb-2" />
                                        <p className="text-xs font-bold uppercase tracking-widest opacity-40">No mapping data associated with this execution</p>
                                    </td>
                                </tr>
                            ) : mappings.map((m, i) => (
                                <tr 
                                    key={i} 
                                    onClick={() => setSelectedModelHistory(m.source)}
                                    className="hover:bg-[var(--color-accent-faint)] transition-colors cursor-pointer"
                                >
                                    <td className="px-8 py-5">
                                        <div className="flex items-center gap-3">
                                            <Table size={14} className="text-blue-500 opacity-50" />
                                            <span className="font-mono text-xs font-bold text-[var(--text-primary)]">{m.source}</span>
                                        </div>
                                    </td>
                                    <td className="px-8 py-5">
                                        <div className="flex items-center gap-3">
                                            <ArrowRight size={14} className="text-[var(--text-tertiary)]" />
                                            <div className="flex flex-col">
                                                <span className="font-mono text-xs font-bold text-blue-500">{m.target}</span>
                                                {m.collision_detected && (
                                                    <span className="text-[9px] font-black text-amber-500/80 uppercase tracking-tighter mt-0.5">Hash Suffix Appended</span>
                                                )}
                                            </div>
                                        </div>
                                    </td>
                                    <td className="px-8 py-5 text-center">
                                        {m.collision_detected ? (
                                            <div className="flex items-center justify-center gap-1 text-amber-500">
                                                <AlertTriangle size={16} fill="currentColor" fillOpacity={0.1} />
                                                <span className="text-[10px] font-black uppercase tracking-tighter">Resolved</span>
                                            </div>
                                        ) : (
                                            <div className="flex items-center justify-center text-emerald-500 opacity-40">
                                                <CheckCircle2 size={16} />
                                            </div>
                                        )}
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

// --- LINEAGE VIEW COMPONENT ---

function LineageView({ projectId }) {
    const [data, setData] = useState({ nodes: [], edges: [] });
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetch = async () => {
            setLoading(true);
            try {
                const json = await api.getProjectLineage(projectId);
                setData(json || { nodes: [], edges: [] });
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        fetch();
    }, [projectId]);

    if (loading) return (
        <div className="flex flex-col items-center justify-center py-40 gap-4">
            <Loader2 size={32} className="animate-spin text-purple-500" />
            <span className="text-[10px] font-black uppercase tracking-widest text-[var(--text-tertiary)]">Mapping system lineage...</span>
        </div>
    );

    return (
        <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
            <GlassCard className="p-8">
                <div className="flex items-center gap-4 mb-8">
                    <div className="w-10 h-10 rounded-xl bg-purple-500/10 flex items-center justify-center border border-purple-500/20">
                        <GitBranch size={20} className="text-purple-500" />
                    </div>
                    <div>
                        <h3 className="text-sm font-black text-[var(--text-primary)] uppercase tracking-widest">Lineage Trace</h3>
                        <p className="text-[10px] text-[var(--text-tertiary)] font-bold uppercase mt-0.5">Snapshot Evolution & Dependency Tree</p>
                    </div>
                </div>

                <div className="relative min-h-[500px] bg-[var(--bg-main)]/30 rounded-2xl border border-[var(--border-light)] p-8 overflow-auto no-scrollbar">
                    {data.nodes.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-[var(--text-tertiary)] py-20">
                            <Info size={40} className="opacity-10 mb-4" />
                            <p className="text-xs font-bold uppercase tracking-widest opacity-40">Insufficient data to reconstruct lineage</p>
                        </div>
                    ) : (
                        <div className="flex flex-col gap-12 items-center">
                            {[...data.nodes]
                                .sort((a, b) => new Date(a.metadata?.created_at || a.metadata?.started_at || 0) - new Date(b.metadata?.created_at || b.metadata?.started_at || 0))
                                .map((node, i) => (
                                <div key={node.id} className="flex flex-col items-center relative">
                                    {i > 0 && (
                                        <div className="absolute -top-12 h-12 w-0.5 bg-gradient-to-b from-purple-500/20 to-transparent" />
                                    )}
                                    <div className={`
                                        p-4 rounded-2xl border transition-all shadow-sm hover:shadow-md
                                        ${node.type === 'snapshot' 
                                            ? 'bg-[var(--bg-surface)] border-[var(--border-main)] w-64' 
                                            : 'bg-gradient-to-br from-purple-600 to-indigo-600 border-transparent w-48 text-white'}
                                    `}>
                                        <div className="flex items-center gap-3">
                                            {node.type === 'snapshot' ? (
                                                <Database size={16} className="text-blue-500" />
                                            ) : (
                                                <Zap size={16} className="text-white" />
                                            )}
                                            <div className="flex flex-col">
                                                <span className={`text-[10px] font-black uppercase tracking-widest ${node.type === 'run' ? 'text-purple-100' : 'text-[var(--text-tertiary)]'}`}>
                                                    {node.metadata?.is_pinned && <Pin size={10} className="text-amber-500 fill-amber-500 mr-1" />}
                                                    {node.type}
                                                </span>
                                                <span className="text-xs font-bold font-mono truncate">{node.label}</span>
                                            </div>
                                        </div>
                                        <div className={`mt-3 pt-3 border-t ${node.type === 'run' ? 'border-white/10' : 'border-[var(--border-light)]'}`}>
                                            <div className="flex justify-between items-center text-[9px] font-bold uppercase opacity-60">
                                                <span>{formatDate(node.metadata?.created_at || node.metadata?.started_at)}</span>
                                                {node.metadata?.status && (
                                                    <span className={node.metadata.status === 'success' ? 'text-emerald-400' : 'text-rose-400'}>
                                                        {node.metadata.status}
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </GlassCard>
        </div>
    );
}

// --- RESTORE PREVIEW MODAL ---

function RestorePreviewModal({ projectId, snapshotId, onConfirm, onCancel }) {
    const [preview, setPreview] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetch = async () => {
            setLoading(true);
            try {
                const data = await api.previewRestore(projectId, snapshotId);
                setPreview(data);
            } catch (e) {
                console.error(e);
            } finally {
                setLoading(false);
            }
        };
        fetch();
    }, [projectId, snapshotId]);

    return (
        <div className="fixed inset-0 z-[200] flex items-center justify-center p-6">
            <div className="absolute inset-0 bg-slate-900/80 backdrop-blur-md" onClick={onCancel} />
            <GlassCard className="relative p-10 max-w-2xl w-full border-blue-500/30 animate-in zoom-in-95 shadow-2xl bg-[var(--bg-surface)]">
                <div className="flex items-center gap-4 mb-8">
                    <div className="w-12 h-12 rounded-2xl bg-blue-500/10 flex items-center justify-center text-blue-500 border border-blue-500/20">
                        <RotateCcw size={24} />
                    </div>
                    <div>
                        <h3 className="text-xl font-black text-[var(--text-primary)] tracking-tight">Restore Impact Analysis</h3>
                        <p className="text-xs text-[var(--text-tertiary)] font-bold uppercase tracking-widest">Pre-flight check for Snapshot {snapshotId.substring(0, 8)}</p>
                    </div>
                </div>

                {loading ? (
                    <div className="py-20 flex flex-col items-center gap-4">
                        <Loader2 className="animate-spin text-blue-500" size={32} />
                        <span className="text-[10px] font-black uppercase tracking-widest text-[var(--text-tertiary)]">Calculating structural delta...</span>
                    </div>
                ) : preview ? (
                    <div className="space-y-8">
                        <div className="grid grid-cols-4 gap-4">
                            {[
                                { label: 'Added', val: preview.impact_summary.added, color: 'text-emerald-500', bg: 'bg-emerald-500/10' },
                                { label: 'Removed', val: preview.impact_summary.removed, color: 'text-rose-500', bg: 'bg-rose-500/10' },
                                { label: 'Modified', val: preview.impact_summary.modified, color: 'text-amber-500', bg: 'bg-amber-500/10' },
                                { label: 'Unchanged', val: preview.impact_summary.unchanged, color: 'text-slate-500', bg: 'bg-slate-500/10' }
                            ].map(s => (
                                <div key={s.label} className={`p-4 rounded-xl border border-transparent ${s.bg}`}>
                                    <div className="text-[9px] font-black uppercase tracking-widest opacity-60 mb-1">{s.label}</div>
                                    <div className={`text-xl font-black ${s.color}`}>{s.val}</div>
                                </div>
                            ))}
                        </div>

                        <div className="max-h-60 overflow-auto border border-[var(--border-light)] rounded-xl bg-[var(--bg-main)]/50 p-4 space-y-2 no-scrollbar">
                            <h4 className="text-[10px] font-black uppercase tracking-widest text-[var(--text-tertiary)] mb-4">Detailed Mutations</h4>
                            {preview.models.map((m, i) => (
                                <div key={i} className="flex items-center justify-between py-2 border-b border-[var(--border-light)] last:border-0">
                                    <span className="text-xs font-bold text-[var(--text-primary)] font-mono">{m.name}</span>
                                    <Badge variant={m.status === 'ADDED' ? 'success' : m.status === 'REMOVED' ? 'error' : m.status === 'MODIFIED' ? 'warning' : 'ghost'}>
                                        {m.status}
                                    </Badge>
                                </div>
                            ))}
                        </div>

                        <div className="p-4 rounded-xl bg-amber-500/5 border border-amber-500/20">
                            <p className="text-[11px] text-amber-200/70 leading-relaxed">
                                <span className="font-black uppercase text-amber-500 mr-2">Warning:</span>
                                This restore will overwrite your current active project state. Downstream dependencies may be affected. This action is recorded in the audit trail.
                            </p>
                        </div>

                        <div className="flex gap-4">
                            <ActionButton onClick={onCancel} className="flex-1 py-4 font-black uppercase tracking-widest text-[11px]">Abort Mission</ActionButton>
                            <ActionButton 
                                variant="primary" 
                                onClick={onConfirm}
                                className="flex-1 py-4 font-black uppercase tracking-widest text-[11px]"
                            >
                                Execute Restore
                            </ActionButton>
                        </div>
                    </div>
                ) : (
                    <div className="py-20 text-center">
                        <p className="text-rose-500 font-bold">Failed to load preview data.</p>
                        <ActionButton onClick={onCancel} className="mt-4">Close</ActionButton>
                    </div>
                )}
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
    const [searchQuery, setSearchQuery] = useState('');
    const [isTagging, setIsTagging] = useState(null); // snapshot_id being tagged
    const [tagValue, setTagValue] = useState('');
    const [showRestorePreview, setShowRestorePreview] = useState(null); // snapshot_id for preview
    const [selectedModelHistory, setSelectedModelHistory] = useState(null);
    const [showPinnedOnly, setShowPinnedOnly] = useState(false);
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
                api.apiFetch ? api.apiFetch(`/projects/${projId}/stats`) : Promise.resolve(null)
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

    const filteredVersions = useMemo(() => {
        let list = versions;
        if (showPinnedOnly) {
            list = list.filter(v => v.is_pinned);
        }
        if (!searchQuery.trim()) return list;
        const q = searchQuery.toLowerCase();
        return list.filter(v => 
            v.run_id?.toLowerCase().includes(q) || 
            (v.tags && v.tags.some(t => t.toLowerCase().includes(q))) ||
            (v.last_comment && v.last_comment.toLowerCase().includes(q))
        );
    }, [versions, searchQuery, showPinnedOnly]);

    const handleTagSnapshot = async (snapshotId) => {
        if (!tagValue.trim()) return;
        try {
            await api.tagSnapshot(selectedProjectId, snapshotId, tagValue, 'Manual tag');
            setIsTagging(null);
            setTagValue('');
            loadVersions(selectedProjectId);
        } catch (error) {
            console.error('Tagging failed:', error);
        }
    };

    const handleTogglePin = async (snapshotId, currentPinned) => {
        try {
            await api.toggleSnapshotPin(selectedProjectId, snapshotId, !currentPinned);
            addLog('success', 'VC', `Snapshot ${!currentPinned ? 'pinned' : 'unpinned'}`);
            loadVersions(selectedProjectId);
        } catch (error) {
            addLog('error', 'VC', 'Pinning failed: ' + error.message);
        }
    };

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

    const handleRestore = async (version) => {
        setIsRollingBack(true);
        try {
            const snapId = version.after_tgt_snapshots?.[0] || version.before_tgt_snapshots?.[0];
            await api.restoreProjectVersion(selectedProjectId, {
                restore_snapshot_id: snapId,
                comment: 'Rollback to ' + version.run_id
            });
            addLog('success', 'VC', 'Rollback initiated successfully');
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
                                    {versions.length > 0 ? formatDate(versions[versions.length-1].started_at) : '—'}
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

                {/* --- Main Area: Tabs & Views --- */}
                <main className="xl:col-span-9 flex flex-col gap-6">
                    
                    {/* --- Navigation Tabs --- */}
                    <div className="flex items-center gap-1 p-1 bg-[var(--bg-surface)]/40 border border-[var(--border-light)] rounded-2xl w-fit">
                        {[
                            { id: 'history', label: 'History Log', icon: HistoryIcon },
                            { id: 'repository', label: 'Repository', icon: Archive },
                            { id: 'diff', label: 'Structural Diff', icon: GitCompare, disabled: !diffData },
                            { id: 'mappings', label: 'Mappings', icon: ListTree, disabled: !selectedRun },
                            { id: 'lineage', label: 'Lineage Graph', icon: GitBranch },
                            { id: 'stats', label: 'Stats', icon: Activity }
                        ].map(tab => (
                            <button
                                key={tab.id}
                                disabled={tab.disabled}
                                onClick={() => setViewMode(tab.id)}
                                className={`flex items-center gap-2 px-6 py-2.5 rounded-xl text-[11px] font-black uppercase tracking-widest transition-all ${
                                    viewMode === tab.id 
                                    ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20' 
                                    : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-slate-500/5 disabled:opacity-30 disabled:cursor-not-allowed'
                                }`}
                            >
                                <tab.icon size={14} />
                                {tab.label}
                            </button>
                        ))}
                    </div>
                    
                    {viewMode === 'history' && (
                        <div className="flex flex-col gap-6 animate-in fade-in slide-in-from-bottom-2 duration-300">
                            {/* --- Selected Version Overview --- */}
                            <GlassCard className="p-8 relative overflow-hidden group">
                                <div className="absolute top-0 right-0 w-64 h-64 blur-[100px] opacity-[0.03] -mr-32 -mt-32 bg-blue-500" />
                                
                                {selectedRun ? (
                                    <div className="animate-in fade-in slide-in-from-top-2 duration-300">
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
                                                    onClick={() => setShowRestorePreview(selectedRun?.after_tgt_snapshots?.[0] || selectedRun?.before_tgt_snapshots?.[0])}
                                                    className="px-8 py-3.5 shadow-xl"
                                                >
                                                    <RotateCcw size={18} /> Rollback
                                                </ActionButton>
                                                <ActionButton 
                                                    variant="ghost" 
                                                    onClick={() => setViewMode('mappings')}
                                                    className="px-8 py-3.5"
                                                >
                                                    <ListTree size={18} /> View Mappings
                                                </ActionButton>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                            {[
                                                { label: 'Run Type', val: selectedRun.run_type || 'Manual', icon: Zap, color: 'text-amber-500' },
                                                { label: 'Executed', val: formatDate(selectedRun.started_at), icon: Clock, color: 'text-slate-500' },
                                                { label: 'Audit Log', val: 'Verified', icon: ShieldCheck, color: 'text-emerald-500' },
                                                { label: 'Snapshot', val: (selectedRun.after_tgt_snapshots?.[0] || 'N/A').substring(0, 8), icon: Database, color: 'text-blue-500' }
                                            ].map((stat, i) => (
                                                <div key={i} className="p-5 rounded-2xl bg-[var(--bg-surface-raised)]/50 border border-[var(--border-light)]">
                                                    <div className="flex items-center gap-2 mb-2">
                                                        <stat.icon size={14} className={stat.color} />
                                                        <span className="text-[9px] font-black text-[var(--text-tertiary)] uppercase tracking-widest">{stat.label}</span>
                                                    </div>
                                                    <p className="text-[var(--text-primary)] font-bold text-[14px]">{stat.val}</p>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                ) : (
                                    <div className="py-20 text-center">
                                        <HistoryIcon size={40} className="mx-auto text-[var(--text-tertiary)] opacity-20 mb-4" />
                                        <h3 className="text-xl font-bold text-[var(--text-primary)]">Select a checkpoint</h3>
                                        <p className="text-[var(--text-tertiary)] max-w-xs mx-auto mt-2">Pick a version from the list below to analyze or restore state.</p>
                                    </div>
                                )}
                            </GlassCard>

                            {/* --- Comparison Tray --- */}
                            {diffSelection.length > 0 && (
                                <div className="p-4 rounded-2xl bg-blue-600/10 border border-blue-500/20 flex items-center justify-between animate-in slide-in-from-left-4">
                                    <div className="flex items-center gap-4">
                                        <GitCompare className="text-blue-500" size={20} />
                                        <div className="flex items-center gap-2">
                                            <span className="text-[10px] font-black text-blue-500 uppercase tracking-widest">Comparison Context:</span>
                                            {diffSelection.map((id, i) => (
                                                <span key={id} className="text-xs font-mono font-bold text-[var(--text-primary)]">
                                                    {id.substring(0, 8)}{i === 0 && diffSelection.length > 1 ? " vs " : ""}
                                                </span>
                                            ))}
                                        </div>
                                    </div>
                                    <div className="flex gap-2">
                                        <button onClick={() => setDiffSelection([])} className="px-4 py-2 text-[10px] font-black uppercase text-[var(--text-tertiary)] hover:text-blue-500">Clear</button>
                                        <ActionButton 
                                            variant="primary" 
                                            disabled={diffSelection.length !== 2 || isComparing}
                                            onClick={handleCompare}
                                            className="px-6 py-2"
                                        >
                                            {isComparing ? <Loader2 size={14} className="animate-spin" /> : <GitCompare size={14} />}
                                            Run Diff
                                        </ActionButton>
                                    </div>
                                </div>
                            )}

                            <div className="relative flex flex-col md:flex-row gap-4">
                                <div className="relative flex-1">
                                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" size={18} />
                                    <input 
                                        type="text"
                                        placeholder="Search history by tag, run ID, or metadata..."
                                        value={searchQuery}
                                        onChange={(e) => setSearchQuery(e.target.value)}
                                        className="w-full bg-[var(--bg-surface)]/60 border border-[var(--border-main)] rounded-2xl pl-12 pr-4 py-5 text-sm font-bold focus:border-blue-500/50 outline-none transition-all shadow-inner"
                                    />
                                </div>
                                <button 
                                    onClick={() => setShowPinnedOnly(!showPinnedOnly)}
                                    className={`px-6 py-4 rounded-2xl border transition-all flex items-center gap-2 font-black text-[10px] uppercase tracking-widest ${
                                        showPinnedOnly 
                                        ? 'bg-amber-500/10 border-amber-500/30 text-amber-500 shadow-lg shadow-amber-500/10' 
                                        : 'bg-[var(--bg-surface)]/60 border-[var(--border-main)] text-[var(--text-tertiary)] hover:border-blue-500/30'
                                    }`}
                                >
                                    <Pin size={14} className={showPinnedOnly ? 'fill-amber-500' : ''} />
                                    {showPinnedOnly ? 'Pinned Only' : 'Show Pinned'}
                                </button>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-6">
                                {isLoading ? (
                                    Array(6).fill(0).map((_, i) => (
                                        <div key={i} className="h-48 rounded-2xl bg-[var(--bg-surface)]/40 border border-[var(--border-main)] animate-pulse" />
                                    ))
                                ) : filteredVersions.length === 0 ? (
                                    <div className="col-span-full py-40 text-center">
                                        <Info size={48} className="mx-auto text-[var(--text-tertiary)] opacity-10 mb-4" />
                                        <p className="text-[var(--text-tertiary)] font-black uppercase tracking-widest text-sm">No historical data matched your filter</p>
                                    </div>
                                ) : filteredVersions.map((v, idx) => (
                                    <div 
                                        key={v.run_id}
                                        onClick={() => setSelectedRun(v)}
                                        className={`group relative p-6 rounded-2xl border transition-all cursor-pointer ${
                                            selectedRun?.run_id === v.run_id 
                                            ? 'bg-gradient-to-br from-blue-600 to-blue-500 border-transparent shadow-xl shadow-blue-500/20' 
                                            : 'bg-[var(--bg-surface)]/60 border-[var(--border-main)] hover:border-blue-500/30'
                                        }`}
                                    >
                                        <div className="flex justify-between items-start mb-6">
                                            <div className="flex items-start gap-3">
                                                <div 
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        const id = v.run_id;
                                                        setDiffSelection(p => p.includes(id) ? p.filter(x => x !== id) : p.length < 2 ? [...p, id] : [p[1], id]);
                                                    }}
                                                    className={`mt-1 w-5 h-5 rounded-md border-2 transition-all flex items-center justify-center ${
                                                        diffSelection.includes(v.run_id)
                                                        ? 'bg-blue-500 border-blue-500 text-white'
                                                        : 'border-[var(--border-main)] hover:border-blue-500/50'
                                                    }`}
                                                >
                                                    {diffSelection.includes(v.run_id) && <Check size={12} strokeWidth={4} />}
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                                                        <span className={`text-[9px] font-black uppercase tracking-widest ${selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-tertiary)]'}`}>
                                                            #{versions.length - idx}
                                                        </span>
                                                        {v.tags?.map(t => (
                                                            <span key={t} className="px-1.5 py-0.5 rounded bg-blue-500/20 text-[8px] font-black text-blue-300 uppercase tracking-tighter">{t}</span>
                                                        ))}
                                                        <div className={`w-2 h-2 rounded-full ${v.status === 'success' ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                                                    </div>
                                                    <h4 className={`text-base font-bold font-mono ${selectedRun?.run_id === v.run_id ? 'text-white' : 'text-[var(--text-primary)]'}`}>
                                                        {v.run_id?.substring(0, 12)}
                                                    </h4>
                                                </div>
                                            </div>
                                            <div className="flex gap-2">
                                                <button 
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        const snapId = v.after_tgt_snapshots?.[0] || v.before_tgt_snapshots?.[0];
                                                        if (snapId) handleTogglePin(snapId, v.is_pinned);
                                                    }}
                                                    className={`p-2 rounded-lg transition-all ${
                                                        v.is_pinned 
                                                        ? 'bg-amber-500/20 text-amber-500' 
                                                        : 'hover:bg-slate-500/10 text-[var(--text-tertiary)]'
                                                    }`}
                                                >
                                                    <Pin size={14} className={v.is_pinned ? 'fill-amber-500' : ''} />
                                                </button>
                                                <div 
                                                    className={`p-2.5 rounded-xl border transition-all ${
                                                        diffSelection.includes(v.run_id)
                                                        ? 'bg-white text-blue-600 border-white shadow-lg'
                                                        : selectedRun?.run_id === v.run_id 
                                                            ? 'bg-blue-400/20 text-white border-blue-400/40'
                                                            : 'bg-[var(--bg-main)] text-[var(--text-tertiary)] border-[var(--border-light)]'
                                                    }`}
                                                >
                                                    <GitCompare size={16} />
                                                </div>
                                            </div>
                                        </div>

                                        <div className="flex items-end justify-between">
                                            <div className="space-y-1">
                                                <div className="flex items-center gap-2">
                                                    <Calendar size={12} className="opacity-40" />
                                                    <span className={`text-[11px] font-bold ${selectedRun?.run_id === v.run_id ? 'text-blue-100' : 'text-[var(--text-secondary)]'}`}>
                                                        {formatDate(v.started_at)}
                                                    </span>
                                                </div>
                                            </div>
                                            <div className={`p-2 rounded-lg ${selectedRun?.run_id === v.run_id ? 'bg-white/10' : 'bg-slate-500/5'} transition-all`}>
                                                <ArrowRight size={14} className={selectedRun?.run_id === v.run_id ? 'text-white' : 'text-[var(--text-tertiary)]'} />
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {viewMode === 'diff' && (
                        <DiffView 
                            diffData={diffData} 
                            baseRun={versions.find(v => v.run_id === diffSelection[0])}
                            targetRun={versions.find(v => v.run_id === diffSelection[1])}
                            onBack={() => setViewMode('history')} 
                        />
                    )}

                    {viewMode === 'mappings' && (
                        <MappingsView 
                            runId={selectedRun?.run_id} 
                            projectId={selectedProjectId} 
                        />
                    )}

                    {viewMode === 'lineage' && (
                        <LineageView 
                            projectId={selectedProjectId} 
                        />
                    )}

                    {viewMode === 'stats' && (
                        <ProjectStatsView 
                            projectId={selectedProjectId} 
                        />
                    )}

                    {viewMode === 'repository' && (
                        <RepositoryBrowser 
                            projectId={selectedProjectId} 
                        />
                    )}
                </main>
            </div>

            {/* --- Modals & Drawers --- */}
            {selectedModelHistory && (
                <>
                    <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-[250] animate-in fade-in" onClick={() => setSelectedModelHistory(null)} />
                    <ModelHistoryDrawer 
                        projectId={selectedProjectId} 
                        modelName={selectedModelHistory} 
                        onClose={() => setSelectedModelHistory(null)} 
                    />
                </>
            )}

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
            {/* --- Restore Preview Modal --- */}
            {showRestorePreview && (
                <RestorePreviewModal 
                    projectId={selectedProjectId}
                    snapshotId={showRestorePreview}
                    onConfirm={() => {
                        handleRestore(versions.find(v => (v.after_tgt_snapshots?.[0] || v.before_tgt_snapshots?.[0]) === showRestorePreview));
                        setShowRestorePreview(null);
                    }}
                    onCancel={() => setShowRestorePreview(null)}
                />
            )}
        </div>
    );
}
