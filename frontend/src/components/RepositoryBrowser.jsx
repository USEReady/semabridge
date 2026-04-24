import React, { useState, useEffect, useMemo } from 'react';
import { 
    Search, 
    Database, 
    Clock, 
    Calendar, 
    Eye, 
    Send, 
    Terminal, 
    Archive,
    Loader2,
    ShieldCheck,
    Pin,
    ChevronRight,
    ArrowRight,
    Filter,
    X,
    Maximize2,
    Download
} from 'lucide-react';
import { api } from '../utils/api';
import { GlassCard, ActionButton, Badge } from "../pages/VersionControlPage"; 

// --- Helper: Local Modal for Raw Content ---
function SnapshotContentModal({ isOpen, onClose, snapshotId, projectId }) {
    const [content, setContent] = useState(null);
    const [isLoading, setIsLoading] = useState(false);

    useEffect(() => {
        if (isOpen && snapshotId) {
            setIsLoading(true);
            api.getSnapshotContent(projectId, snapshotId)
                .then(data => setContent(data))
                .catch(err => console.error("Failed to load snapshot content:", err))
                .finally(() => setIsLoading(false));
        }
    }, [isOpen, snapshotId, projectId]);

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-[300] flex items-center justify-center p-6">
            <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-md animate-in fade-in" onClick={onClose} />
            <GlassCard className="relative p-0 max-w-5xl w-full h-[80vh] flex flex-col overflow-hidden animate-in zoom-in-95 shadow-2xl">
                <div className="p-6 border-b border-[var(--border-light)] flex items-center justify-between bg-slate-900/20">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-blue-500/10 rounded-lg text-blue-500">
                            <Terminal size={18} />
                        </div>
                        <div>
                            <h3 className="text-lg font-black text-[var(--text-primary)]">Snapshot Inspector</h3>
                            <p className="text-xs text-[var(--text-tertiary)] font-mono">{snapshotId}</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <button className="p-2 hover:bg-slate-500/10 rounded-lg text-[var(--text-tertiary)] transition-colors">
                            <Download size={18} />
                        </button>
                        <button onClick={onClose} className="p-2 hover:bg-rose-500/10 rounded-lg text-rose-500 transition-colors">
                            <X size={18} />
                        </button>
                    </div>
                </div>

                <div className="flex-1 overflow-auto p-8 bg-[var(--bg-main)]/30">
                    {isLoading ? (
                        <div className="h-full flex flex-center justify-center items-center">
                            <Loader2 className="animate-spin text-blue-500" size={48} />
                        </div>
                    ) : content ? (
                        <div className="space-y-6">
                             <div className="grid grid-cols-3 gap-4 mb-8">
                                <div className="p-4 rounded-xl bg-slate-500/5 border border-[var(--border-light)]">
                                    <span className="text-[10px] font-black text-[var(--text-tertiary)] uppercase block mb-1">Role</span>
                                    <span className="text-sm font-bold text-[var(--text-primary)] capitalize">{content.role}</span>
                                </div>
                                <div className="p-4 rounded-xl bg-slate-500/5 border border-[var(--border-light)]">
                                    <span className="text-[10px] font-black text-[var(--text-tertiary)] uppercase block mb-1">Captured At</span>
                                    <span className="text-sm font-bold text-[var(--text-primary)]">{new Date(content.captured_at).toLocaleString()}</span>
                                </div>
                                <div className="p-4 rounded-xl bg-slate-500/5 border border-[var(--border-light)]">
                                    <span className="text-[10px] font-black text-[var(--text-tertiary)] uppercase block mb-1">Model Count</span>
                                    <span className="text-sm font-bold text-[var(--text-primary)]">{content.content?.models?.length || 0}</span>
                                </div>
                             </div>
                             
                             <div className="rounded-2xl border border-[var(--border-main)] overflow-hidden bg-slate-950 font-mono text-sm leading-relaxed p-6 shadow-inner">
                                <pre className="text-emerald-400 whitespace-pre-wrap">
                                    {JSON.stringify(content.content, null, 2)}
                                </pre>
                             </div>
                        </div>
                    ) : (
                        <p className="text-center py-20 text-[var(--text-tertiary)]">No data available for this snapshot.</p>
                    )}
                </div>
            </GlassCard>
        </div>
    );
}

export default function RepositoryBrowser({ projectId }) {
    const [snapshots, setSnapshots] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [roleFilter, setRoleFilter] = useState('all');
    const [originFilter, setOriginFilter] = useState('all');
    const [pinnedOnly, setPinnedOnly] = useState(false);
    const [viewContentId, setViewContentId] = useState(null);
    const [isDeploying, setIsDeploying] = useState(null);

    const loadSnapshots = async () => {
        setIsLoading(true);
        try {
            const data = await api.listProjectSnapshots(projectId);
            setSnapshots(data?.snapshots || data || []);
        } catch (error) {
            console.error("Failed to fetch snapshots:", error);
        } finally {
            setIsLoading(false);
        }
    };

    useEffect(() => {
        if (projectId) loadSnapshots();
    }, [projectId]);

    const filteredSnapshots = useMemo(() => {
        return snapshots.filter(s => {
            const matchesRole = roleFilter === 'all' || s.role === roleFilter;
            const matchesOrigin = originFilter === 'all' || (s.snapshot_origin || '').toLowerCase() === originFilter.toLowerCase();
            const matchesPinned = !pinnedOnly || s.is_pinned;
            
            const q = searchQuery.toLowerCase();
            const matchesSearch = !searchQuery || 
                s.snapshot_id.toLowerCase().includes(q) || 
                (s.tags && s.tags.some(t => t.toLowerCase().includes(q))) ||
                (s.comment && s.comment.toLowerCase().includes(q));
                
            return matchesRole && matchesOrigin && matchesPinned && matchesSearch;
        }).sort((a, b) => new Date(b.captured_at) - new Date(a.captured_at));
    }, [snapshots, searchQuery, roleFilter, originFilter, pinnedOnly]);

    const handleManualDeploy = async (snapId) => {
        if (!window.confirm("Manually deploy this snapshot to all target connectors?")) return;
        setIsDeploying(snapId);
        try {
            await api.manualDeploy(projectId, snapId, `Manual deploy via Repository Browser`);
            alert("Deployment initiated successfully!");
        } catch (error) {
            console.error("Deploy failed:", error);
            alert("Deployment failed: " + error.message);
        } finally {
            setIsDeploying(null);
        }
    };

    return (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* --- Controls --- */}
            <div className="flex flex-col md:flex-row gap-4 items-center justify-between">
                <div className="relative flex-1 w-full md:w-auto">
                    <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" size={18} />
                    <input 
                        type="text"
                        placeholder="Search repository by ID or tag..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full bg-[var(--bg-surface)]/60 border border-[var(--border-main)] rounded-2xl pl-12 pr-4 py-4 text-sm font-bold focus:border-blue-500/50 outline-none transition-all shadow-inner"
                    />
                </div>

                <div className="flex items-center gap-2 p-1 bg-[var(--bg-surface)]/60 border border-[var(--border-main)] rounded-2xl shadow-inner overflow-x-auto no-scrollbar">
                    {['all', 'source', 'target'].map(role => (
                        <button
                            key={role}
                            onClick={() => setRoleFilter(role)}
                            className={`px-4 py-2 rounded-xl text-[9px] font-black uppercase tracking-widest whitespace-nowrap transition-all ${
                                roleFilter === role 
                                ? 'bg-blue-600 text-white shadow-lg' 
                                : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                            }`}
                        >
                            {role}
                        </button>
                    ))}
                    <div className="w-px h-4 bg-[var(--border-light)] mx-1" />
                    {['all', 'manual', 'run'].map(orig => (
                        <button
                            key={orig}
                            onClick={() => setOriginFilter(orig)}
                            className={`px-4 py-2 rounded-xl text-[9px] font-black uppercase tracking-widest whitespace-nowrap transition-all ${
                                originFilter === orig 
                                ? 'bg-emerald-600 text-white shadow-lg' 
                                : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                            }`}
                        >
                            {orig}
                        </button>
                    ))}
                    <div className="w-px h-4 bg-[var(--border-light)] mx-1" />
                    <button
                        onClick={() => setPinnedOnly(!pinnedOnly)}
                        className={`px-4 py-2 rounded-xl text-[9px] font-black uppercase tracking-widest whitespace-nowrap transition-all flex items-center gap-2 ${
                            pinnedOnly 
                            ? 'bg-amber-500 text-white shadow-lg' 
                            : 'text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
                        }`}
                    >
                        <Pin size={12} fill={pinnedOnly ? "currentColor" : "none"} />
                        Pinned
                    </button>
                </div>
            </div>

            {/* --- List --- */}
            <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-6">
                {isLoading ? (
                    Array(6).fill(0).map((_, i) => (
                        <div key={i} className="h-64 rounded-2xl bg-[var(--bg-surface)]/40 border border-[var(--border-main)] animate-pulse" />
                    ))
                ) : filteredSnapshots.length === 0 ? (
                    <div className="col-span-full py-40 text-center">
                        <Archive size={48} className="mx-auto text-[var(--text-tertiary)] opacity-10 mb-4" />
                        <p className="text-[var(--text-tertiary)] font-black uppercase tracking-widest text-sm">Repository is empty</p>
                    </div>
                ) : filteredSnapshots.map((snap) => (
                    <GlassCard 
                        key={snap.snapshot_id}
                        className="p-6 group relative overflow-hidden flex flex-col gap-6 hover:border-blue-500/30 transition-all"
                    >
                        {/* Background Decoration */}
                        <div className="absolute top-0 right-0 w-32 h-32 blur-[60px] opacity-[0.05] -mr-16 -mt-16 bg-blue-500" />

                        <div className="flex justify-between items-start z-10">
                            <div className="flex items-start gap-3">
                                <div className={`p-3 rounded-2xl shadow-lg ${
                                    snap.role === 'source' 
                                    ? 'bg-amber-500/10 text-amber-500 shadow-amber-500/10' 
                                    : 'bg-blue-500/10 text-blue-500 shadow-blue-500/10'
                                }`}>
                                    <Database size={20} />
                                </div>
                                <div>
                                    <div className="flex items-center gap-2 mb-1">
                                        <Badge variant={snap.role === 'source' ? 'warning' : 'info'}>{snap.role}</Badge>
                                        {snap.is_pinned && <Pin size={12} className="text-amber-500 fill-amber-500" />}
                                    </div>
                                    <h4 className="text-base font-black font-mono text-[var(--text-primary)] tracking-tight">
                                        {snap.snapshot_id.substring(0, 12)}...
                                    </h4>
                                </div>
                            </div>
                        </div>

                        <div className="space-y-3 z-10">
                            <div className="flex items-center gap-2 text-[var(--text-secondary)]">
                                <Calendar size={14} className="opacity-50" />
                                <span className="text-xs font-bold">{new Date(snap.captured_at).toLocaleDateString()}</span>
                                <Clock size={14} className="ml-2 opacity-50" />
                                <span className="text-xs font-bold">{new Date(snap.captured_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                            </div>
                            {snap.tags && snap.tags.length > 0 && (
                                <div className="flex flex-wrap gap-1">
                                    {snap.tags.map(t => (
                                        <span key={t} className="px-2 py-0.5 rounded-md bg-slate-500/10 text-[9px] font-black text-[var(--text-tertiary)] uppercase tracking-tighter border border-[var(--border-light)]">
                                            {t}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>

                        <div className="mt-auto flex gap-2 z-10 pt-4 border-t border-[var(--border-light)]">
                            <button 
                                onClick={() => setViewContentId(snap.snapshot_id)}
                                className="flex-1 flex items-center justify-center gap-2 py-3 rounded-xl bg-slate-500/5 hover:bg-slate-500/10 text-[11px] font-black uppercase tracking-widest text-[var(--text-primary)] transition-all"
                            >
                                <Eye size={16} /> View
                            </button>
                            <button 
                                onClick={() => handleManualDeploy(snap.snapshot_id)}
                                disabled={isDeploying === snap.snapshot_id || snap.role === 'source'}
                                className={`flex-1 flex items-center justify-center gap-2 py-3 rounded-xl text-[11px] font-black uppercase tracking-widest transition-all ${
                                    snap.role === 'source'
                                    ? 'bg-slate-500/10 text-[var(--text-tertiary)] cursor-not-allowed opacity-50'
                                    : 'bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/20'
                                }`}
                            >
                                {isDeploying === snap.snapshot_id ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                                Deploy
                            </button>
                        </div>
                    </GlassCard>
                ))}
            </div>

            {/* --- Modal --- */}
            <SnapshotContentModal 
                isOpen={!!viewContentId} 
                onClose={() => setViewContentId(null)} 
                snapshotId={viewContentId}
                projectId={projectId}
            />
        </div>
    );
}
