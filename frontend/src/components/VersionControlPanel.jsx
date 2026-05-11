import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    X,
    GitCommit,
    GitCompare,
    Clock,
    History as HistoryIcon,
    Activity,
    ShieldCheck,
    Loader2,
    AlertTriangle,
    Database,
    CheckCircle2,
    ArrowRight,
    Search,
    Filter,
    ChevronRight,
    Zap,
    Download,
    Trash2,
    RefreshCw,
    RotateCcw
} from 'lucide-react';
import { api } from '../utils/api';
import { useUIStore } from '../store/uiStore';
import { useLogs } from '../context/LogsContext';

// --- THEME-AWARE STYLED COMPONENTS ---

const GlassCard = ({ children, className = "" }) => (
    <div className={`bg-[var(--bg-surface)]/80 backdrop-blur-xl border border-[var(--border-main)] rounded-xl shadow-[var(--shadow-sm)] ${className}`}>
        {children}
    </div>
);

const Badge = ({ children, variant = "default" }) => {
    const variants = {
        success: "bg-[var(--color-success-bg)] text-[var(--color-success)] border-[var(--color-success)]/30",
        error: "bg-[var(--color-error-bg)] text-[var(--color-error)] border-[var(--color-error)]/30",
        warning: "bg-[var(--color-warning-bg)] text-[var(--color-warning)] border-[var(--color-warning)]/30",
        info: "bg-[var(--color-primary-muted)] text-[var(--accent-blue)] border-[var(--accent-blue)]/30",
        default: "bg-[var(--bg-button)] text-[var(--text-secondary)] border-[var(--border-light)]"
    };
    return (
        <span className={`px-2 py-0.5 rounded-md text-[9px] font-bold uppercase tracking-wider border ${variants[variant] || variants.default}`}>
            {children}
        </span>
    );
};

const ActionButton = ({ onClick, children, variant = "default", disabled = false, className = "" }) => {
    const variants = {
        primary: "bg-[var(--accent-blue)] hover:bg-[var(--accent-blue-hover)] text-white border-none shadow-md shadow-[var(--accent-blue)]/10",
        danger: "bg-[var(--color-error)] hover:opacity-90 text-white border-none shadow-md shadow-[var(--color-error)]/10",
        ghost: "bg-transparent hover:bg-[var(--bg-surface-hover)] text-[var(--text-secondary)] border-[var(--border-main)]",
        default: "bg-[var(--bg-button)] hover:bg-[var(--bg-button-hover)] text-[var(--text-primary)] border-[var(--border-main)]"
    };
    return (
        <button 
            onClick={onClick} 
            disabled={disabled}
            className={`px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all flex items-center justify-center gap-2 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
        >
            {children}
        </button>
    );
};

// --- SUB-COMPONENTS ---

function DiffView({ diffData }) {
    if (!diffData) return null;
    const { metadata_diff, models } = diffData;
    
    return (
        <div className="flex-1 flex flex-col gap-6 overflow-hidden animate-in fade-in zoom-in-95 duration-500">
            <div className="grid grid-cols-2 gap-6">
                {['snapshot_a', 'snapshot_b'].map((key) => (
                    <GlassCard key={key} className="p-4 relative group overflow-hidden">
                        <div className={`absolute top-0 right-0 w-24 h-24 blur-3xl opacity-5 -mr-12 -mt-12 transition-all duration-1000 group-hover:opacity-10 ${key === 'snapshot_a' ? 'bg-[var(--accent-blue)]' : 'bg-[var(--color-success)]'}`} />
                        <div className="flex justify-between items-center mb-3 relative z-10">
                            <h4 className="text-[9px] font-black text-[var(--text-tertiary)] uppercase tracking-[0.2em]">
                                {key === 'snapshot_a' ? 'BASE VERSION' : 'TARGET VERSION'}
                            </h4>
                            <Database size={14} className={key === 'snapshot_a' ? 'text-[var(--accent-blue)]' : 'text-[var(--color-success)]'} />
                        </div>
                        <div className="grid grid-cols-2 gap-y-3 text-[11px] relative z-10">
                            <div className="flex flex-col"><span className="text-[var(--text-quaternary)] text-[8px] uppercase font-bold">ID</span> <span className="text-[var(--text-primary)] font-mono">{metadata_diff?.[key]?.id?.substring(0, 8) || 'N/A'}</span></div>
                            <div className="flex flex-col"><span className="text-[var(--text-quaternary)] text-[8px] uppercase font-bold">Format</span> <span className="text-[var(--text-primary)] uppercase font-bold">{metadata_diff?.[key]?.format || 'N/A'}</span></div>
                            <div className="flex flex-col"><span className="text-[var(--text-quaternary)] text-[8px] uppercase font-bold">Models</span> <span className="text-[var(--text-primary)] font-bold">{metadata_diff?.[key]?.model_count || 0}</span></div>
                            <div className="flex flex-col"><span className="text-[var(--text-quaternary)] text-[8px] uppercase font-bold">Captured</span> <span className="text-[var(--text-primary)]">{metadata_diff?.[key]?.taken_at ? new Date(metadata_diff[key].taken_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '—'}</span></div>
                        </div>
                    </GlassCard>
                ))}
            </div>

            <GlassCard className="flex-1 flex flex-col overflow-hidden bg-[var(--bg-main)]/20">
                <div className="px-6 py-3 border-b border-[var(--border-light)] flex justify-between items-center bg-[var(--bg-surface)]/50">
                    <div className="flex items-center gap-2">
                        <Zap size={14} className="text-[var(--color-warning)]" />
                        <h3 className="text-[10px] font-black text-[var(--text-primary)] uppercase tracking-widest">Structural Evolution Log</h3>
                    </div>
                    <div className="flex gap-2">
                        <Badge variant="success">+{ (models || []).filter(m => m.status === 'ADDED').length }</Badge>
                        <Badge variant="error">-{ (models || []).filter(m => m.status === 'REMOVED').length }</Badge>
                    </div>
                </div>
                <div className="flex-1 overflow-auto custom-scrollbar">
                    <table className="w-full text-left">
                        <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
                            <tr className="border-b border-[var(--border-light)] text-[var(--text-tertiary)]">
                                <th className="px-6 py-3 font-bold uppercase tracking-widest text-[8px]">Entity Name</th>
                                <th className="px-6 py-3 font-bold uppercase tracking-widest text-[8px]">Mutation</th>
                                <th className="px-6 py-3 font-bold uppercase tracking-widest text-[8px]">Insight</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-[var(--border-light)]/30">
                            {(models || []).map((m, idx) => (
                                <tr key={idx} className="hover:bg-[var(--color-accent-faint)] transition-all group">
                                    <td className="px-6 py-3">
                                        <div className="flex items-center gap-3">
                                            <div className={`w-1.5 h-1.5 rounded-full ${
                                                m.status === 'ADDED' ? 'bg-[var(--color-success)]' : 
                                                m.status === 'REMOVED' ? 'bg-[var(--color-error)]' : 
                                                m.status === 'MODIFIED' ? 'bg-[var(--color-warning)]' : 'bg-[var(--color-grey)]'
                                            }`} />
                                            <span className="font-bold text-[var(--text-primary)] text-xs">{m.name}</span>
                                        </div>
                                    </td>
                                    <td className="px-6 py-3">
                                        <Badge variant={
                                            m.status === 'ADDED' ? 'success' : 
                                            m.status === 'REMOVED' ? 'error' : 
                                            m.status === 'MODIFIED' ? 'warning' : 'default'
                                        }>{m.status}</Badge>
                                    </td>
                                    <td className="px-6 py-3 text-[10px] text-[var(--text-muted)] italic">
                                        {m.details}
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

export default function VersionControlPanel({ isOpen, onClose }) {
    const activeProjectId = useUIStore(state => state.activeProjectId);
    const [versions, setVersions] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [selectedRun, setSelectedRun] = useState(null);
    const [diffSelection, setDiffSelection] = useState([]);
    const [viewMode, setViewMode] = useState('history'); 
    const [diffData, setDiffData] = useState(null);
    const [isComparing, setIsComparing] = useState(false);
    const [rollbackTarget, setRollbackTarget] = useState(null);
    const [isRollingBack, setIsRollingBack] = useState(false);
    const [isDeleting, setIsDeleting] = useState(null); 
    const { addLog } = useLogs();

    const loadVersions = useCallback(async () => {
        if (!activeProjectId) return;
        setIsLoading(true);
        try {
            const data = await api.getProjectRuns(activeProjectId);
            const sorted = (Array.isArray(data) ? data : []).sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
            setVersions(sorted);
            if (sorted.length > 0 && !selectedRun) {
                setSelectedRun(sorted[0]);
            }
        } catch (err) {
            addLog('error', 'VC', 'Load failed: ' + err.message);
        } finally {
            setIsLoading(false);
        }
    }, [activeProjectId, addLog]);

    useEffect(() => {
        if (isOpen) loadVersions();
    }, [isOpen, loadVersions]);

    const handleCompare = async () => {
        if (diffSelection.length !== 2) return;
        setIsComparing(true);
        try {
            const r1 = versions.find(v => v.run_id === diffSelection[0]);
            const r2 = versions.find(v => v.run_id === diffSelection[1]);
            const s1 = r1?.after_tgt_snapshots?.[0] || r1?.before_tgt_snapshots?.[0];
            const s2 = r2?.after_tgt_snapshots?.[0] || r2?.before_tgt_snapshots?.[0];
            const data = await api.compareProjectSnapshots(activeProjectId, s1, s2);
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
            const result = await api.restoreProjectVersion(activeProjectId, {
                snapshot_id: snapId,
                restore_snapshot_id: snapId,
                sync_mode: run?.sync_mode || 'copy',
            });
            addLog('success', 'VC', 'Rollback initiated: ' + (result.run_id || result.id));
            setRollbackTarget(null);
            loadVersions();
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
            const result = await api.deleteModelVersions(activeProjectId, [snapshotId]);
            if (result.blocked_ids?.length > 0) {
                addLog('warning', 'VC', 'Snapshot is referenced and cannot be deleted.');
            } else {
                addLog('success', 'VC', `Successfully deleted version(s).`);
                loadVersions();
            }
        } catch (err) {
            addLog('error', 'VC', 'Delete failed: ' + err.message);
        } finally {
            setIsDeleting(null);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 animate-in fade-in duration-300">
            <div className="absolute inset-0 bg-[var(--bg-backdrop)] backdrop-blur-sm" onClick={onClose} />
            
            <div className="relative w-full max-w-[1100px] h-[85vh] flex flex-col bg-[var(--bg-app)] border border-[var(--border-main)] rounded-2xl shadow-2xl overflow-hidden">
                
                <header className="px-6 py-4 border-b border-[var(--border-light)] bg-[var(--bg-surface)] flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-[var(--accent-blue-dark)] to-[var(--accent-blue)] flex items-center justify-center shadow-lg">
                            <RotateCcw className="text-white" size={20} />
                        </div>
                        <div>
                            <h2 className="text-lg font-black text-[var(--text-primary)] tracking-tight leading-tight">Version Control</h2>
                            <div className="flex items-center gap-2 mt-0.5">
                                <Badge variant="info">{versions.length} History Points</Badge>
                                <span className="text-[9px] font-bold text-[var(--text-tertiary)] uppercase tracking-wider">
                                    Active: {activeProjectId?.substring(0, 10)}...
                                </span>
                            </div>
                        </div>
                    </div>
                    
                    <div className="flex items-center gap-4">
                        <div className="flex bg-[var(--bg-main)] p-1 rounded-xl border border-[var(--border-main)] shadow-inner">
                            <button 
                                onClick={() => setViewMode('history')}
                                className={`px-4 py-2 rounded-lg text-[10px] font-black uppercase transition-all flex items-center gap-2 ${viewMode === 'history' ? 'bg-[var(--bg-surface-raised)] text-[var(--text-primary)] shadow-sm' : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'}`}
                            >
                                <HistoryIcon size={14} /> History
                            </button>
                            <button 
                                onClick={() => viewMode === 'diff' ? setViewMode('history') : handleCompare()}
                                disabled={viewMode === 'history' && diffSelection.length !== 2 && !isComparing}
                                className={`px-4 py-2 rounded-lg text-[10px] font-black uppercase transition-all flex items-center gap-2 ${
                                    viewMode === 'diff' ? 'bg-[var(--accent-blue-dark)] text-white' : 
                                    (diffSelection.length === 2 ? 'bg-[var(--accent-blue)] text-white' : 'text-[var(--text-tertiary)] opacity-40')
                                }`}
                            >
                                {isComparing ? <Loader2 size={14} className="animate-spin" /> : <GitCompare size={14} />}
                                {viewMode === 'diff' ? 'Close Diff' : 'Analyze Diff'}
                            </button>
                        </div>
                        <button onClick={onClose} className="w-9 h-9 flex items-center justify-center hover:bg-[var(--color-error-bg)] hover:text-[var(--color-error)] rounded-full text-[var(--text-tertiary)] transition-all">
                            <X size={20} />
                        </button>
                    </div>
                </header>

                <main className="flex-1 flex overflow-hidden">
                    <section className="flex-1 flex flex-col p-8 gap-8 overflow-y-auto custom-scrollbar bg-gradient-to-br from-[var(--bg-app)] to-[var(--bg-main)]/30">
                        {viewMode === 'history' ? (
                            <div className="animate-in fade-in slide-in-from-left-5 duration-500">
                                <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
                                    <div className="lg:col-span-8">
                                        <GlassCard className="p-8 bg-[var(--bg-surface)]">
                                            <div className="flex justify-between items-start mb-8">
                                                <div className="flex items-center gap-3">
                                                    <div className="w-1 h-8 bg-[var(--accent-blue)] rounded-full" />
                                                    <div>
                                                        <span className="text-[9px] font-black text-[var(--accent-blue)] uppercase tracking-widest">Selected Checkpoint</span>
                                                        <h3 className="text-xl font-bold text-[var(--text-primary)] mt-1 font-mono">{selectedRun?.run_id || '----'}</h3>
                                                    </div>
                                                </div>
                                                <Badge variant={selectedRun?.status === 'success' ? 'success' : 'error'}>
                                                    {selectedRun?.status || 'Unknown'}
                                                </Badge>
                                            </div>

                                            <div className="grid grid-cols-2 gap-4 mb-8">
                                                {[
                                                    { label: 'Run Type', val: selectedRun?.run_type || 'SYNC', icon: Zap, color: 'text-[var(--color-warning)]' },
                                                    { label: 'Time', val: selectedRun?.started_at ? new Date(selectedRun.started_at).toLocaleTimeString() : '—', icon: Clock, color: 'text-[var(--text-tertiary)]' },
                                                    { label: 'Execution ID', val: selectedRun?.run_id?.substring(0, 14) + '...', icon: ShieldCheck, color: 'text-[var(--accent-blue)]' },
                                                    { label: 'Snapshot Count', val: `${selectedRun?.after_tgt_snapshots?.length || 0} Models`, icon: Database, color: 'text-[var(--color-success)]' }
                                                ].map((stat, i) => (
                                                    <div key={i} className="p-4 rounded-xl bg-[var(--bg-main)]/40 border border-[var(--border-light)]">
                                                        <div className="flex items-center gap-2 mb-1">
                                                            <stat.icon size={12} className={stat.color} />
                                                            <span className="text-[8px] font-black text-[var(--text-quaternary)] uppercase tracking-widest">{stat.label}</span>
                                                        </div>
                                                        <p className="text-[var(--text-primary)] font-bold text-sm">{stat.val}</p>
                                                    </div>
                                                ))}
                                            </div>

                                            <div className="flex gap-4">
                                                <ActionButton 
                                                    variant="primary" 
                                                    onClick={() => setRollbackTarget(selectedRun)}
                                                    className="flex-1 py-4 text-[11px] tracking-widest uppercase rounded-xl shadow-lg"
                                                >
                                                    <RotateCcw size={20} /> Rollback to this Version
                                                </ActionButton>
                                            </div>
                                        </GlassCard>
                                    </div>

                                    <div className="lg:col-span-4 flex flex-col gap-6">
                                        <GlassCard className="p-6 flex-1 flex flex-col items-center justify-center text-center border-dashed border-2 border-[var(--border-main)]/50">
                                            <GitCompare className="text-[var(--text-tertiary)] mb-4" size={28} />
                                            <h4 className="text-[10px] font-black text-[var(--text-secondary)] uppercase tracking-widest mb-2">Diff Comparison</h4>
                                            <p className="text-[10px] text-[var(--text-muted)] leading-relaxed">
                                                Select exactly two versions from the list to compare.
                                            </p>
                                        </GlassCard>
                                        
                                        {selectedRun?.error_message && (
                                            <GlassCard className="p-4 bg-[var(--color-error-bg)] border-[var(--color-error)]/30">
                                                <div className="flex items-center gap-2 mb-2">
                                                    <AlertTriangle className="text-[var(--color-error)]" size={14} />
                                                    <span className="text-[9px] font-black text-[var(--color-error)] uppercase">Critical Error</span>
                                                </div>
                                                <p className="text-[10px] text-[var(--color-error)]/90 leading-normal">{selectedRun.error_message}</p>
                                            </GlassCard>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ) : (
                            <DiffView diffData={diffData} />
                        )}
                    </section>

                    <aside className="w-[320px] border-l border-[var(--border-light)] bg-[var(--bg-surface)]/50 flex flex-col">
                        <div className="p-6 border-b border-[var(--border-light)] flex items-center justify-between">
                            <span className="text-[10px] font-black text-[var(--text-primary)] uppercase tracking-widest">History Log</span>
                            <ActionButton variant="ghost" onClick={loadVersions} className="w-8 h-8 p-0 rounded-full">
                                <RefreshCw size={14} />
                            </ActionButton>
                        </div>
                        
                        <div className="flex-1 overflow-y-auto p-4 space-y-3 custom-scrollbar">
                            {versions.length === 0 ? (
                                <div className="flex flex-col items-center justify-center h-40 text-center opacity-50">
                                    <Clock size={32} className="mb-2" />
                                    <p className="text-xs font-bold uppercase tracking-widest">No History Found</p>
                                </div>
                            ) : versions.map((v, idx) => (
                                <div 
                                    key={v.run_id} 
                                    onClick={() => setSelectedRun(v)}
                                    className={`group p-4 rounded-xl border transition-all cursor-pointer relative ${
                                        selectedRun?.run_id === v.run_id 
                                        ? 'bg-[var(--accent-blue-dark)] text-white border-transparent shadow-md' 
                                        : 'bg-[var(--bg-surface)] border-[var(--border-light)] hover:border-[var(--accent-blue)]/30'
                                    }`}
                                >
                                    <div className="flex justify-between items-start mb-2 relative z-10">
                                        <div className="flex flex-col">
                                            <span className={`text-[8px] font-black uppercase mb-0.5 ${selectedRun?.run_id === v.run_id ? 'text-white/60' : 'text-[var(--text-tertiary)]'}`}>
                                                Point #{versions.length - idx}
                                            </span>
                                            <span className={`text-[11px] font-bold font-mono ${selectedRun?.run_id === v.run_id ? 'text-white' : 'text-[var(--text-primary)]'}`}>
                                                {v.run_id?.substring(0, 10)}...
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-1.5">
                                            <button 
                                                title="Quick Rollback"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setRollbackTarget(v);
                                                }}
                                                className={`p-1.5 rounded-lg transition-all ${selectedRun?.run_id === v.run_id ? 'hover:bg-white/10 text-white/50 hover:text-white' : 'hover:bg-[var(--color-primary-muted)] text-[var(--accent-blue)] hover:text-[var(--accent-blue-hover)]'}`}
                                            >
                                                <RotateCcw size={12} />
                                            </button>
                                            <button 
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDeleteSnapshot(v.after_tgt_snapshots?.[0] || v.before_tgt_snapshots?.[0]);
                                                }}
                                                className={`p-1.5 rounded-lg transition-all ${selectedRun?.run_id === v.run_id ? 'hover:bg-white/10 text-white/50 hover:text-white' : 'hover:bg-[var(--color-error-bg)] text-[var(--text-quaternary)] hover:text-[var(--color-error)]'}`}
                                            >
                                                {isDeleting === (v.after_tgt_snapshots?.[0] || v.before_tgt_snapshots?.[0]) ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                            </button>
                                            <input 
                                                type="checkbox" 
                                                checked={diffSelection.includes(v.run_id)}
                                                onChange={(e) => {
                                                    e.stopPropagation();
                                                    const id = v.run_id;
                                                    setDiffSelection(p => p.includes(id) ? p.filter(x => x !== id) : p.length < 2 ? [...p, id] : [p[1], id]);
                                                }}
                                                className="w-4 h-4 rounded border-[var(--border-main)] bg-[var(--bg-main)] text-[var(--accent-blue)] focus:ring-0 cursor-pointer"
                                            />
                                        </div>
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <div className="flex items-center gap-2">
                                            <div className={`w-1.5 h-1.5 rounded-full ${v.status === 'success' ? 'bg-[var(--color-success)]' : 'bg-[var(--color-error)]'}`} />
                                            <span className={`text-[9px] font-bold ${selectedRun?.run_id === v.run_id ? 'text-white/80' : 'text-[var(--text-secondary)]'}`}>
                                                {new Date(v.started_at).toLocaleDateString()}
                                            </span>
                                        </div>
                                        <ChevronRight size={12} className={selectedRun?.run_id === v.run_id ? 'text-white' : 'text-[var(--text-tertiary)]'} />
                                    </div>
                                </div>
                            ))}
                        </div>
                    </aside>
                </main>

                <footer className="px-6 py-3 bg-[var(--bg-main)] border-t border-[var(--border-light)] flex justify-between items-center text-[9px] font-black text-[var(--text-quaternary)] uppercase tracking-widest">
                    <div className="flex items-center gap-6">
                        <span className="flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-[var(--color-success)]" /> Status: <span className="text-[var(--text-secondary)]">Operational</span></span>
                        <span>v1.5.1</span>
                    </div>
                    <span>© 2026 SEMABRIDGE</span>
                </footer>
            </div>

            {rollbackTarget && (
                <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
                    <div className="absolute inset-0 bg-black/40 backdrop-blur-md" onClick={() => setRollbackTarget(null)} />
                    <GlassCard className="relative p-8 max-w-sm w-full border-[var(--color-warning)]/30 animate-in zoom-in-95">
                        <div className="w-12 h-12 rounded-2xl bg-[var(--color-warning-bg)] flex items-center justify-center text-[var(--color-warning)] border border-[var(--color-warning)]/20 mb-6">
                            <RotateCcw size={24} />
                        </div>
                        <h3 className="text-xl font-bold text-[var(--text-primary)] mb-2 tracking-tight">System Rollback</h3>
                        <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed mb-8">
                            Confirming rollback to execution checkpoint <span className="font-mono text-[var(--accent-blue)]">{rollbackTarget.run_id?.substring(0, 10)}</span>.
                        </p>
                        <div className="flex gap-3">
                            <ActionButton onClick={() => setRollbackTarget(null)} className="flex-1 py-2.5 font-bold uppercase">Cancel</ActionButton>
                            <ActionButton 
                                variant="primary"
                                onClick={() => handleRestore(rollbackTarget)} 
                                disabled={isRollingBack} 
                                className="flex-1 py-2.5 font-bold uppercase"
                            >
                                {isRollingBack ? <Loader2 size={14} className="animate-spin" /> : 'Confirm Rollback'}
                            </ActionButton>
                        </div>
                    </GlassCard>
                </div>
            )}
        </div>
    );
}
