import { useState, useEffect, useRef, useCallback } from 'react';
import {
    X,
    GitCommit,
    GitCompare,
    RotateCcw,
    Clock,
    AlertTriangle,
    Filter,
    Loader2,
    Trash2,
} from 'lucide-react';
import { api } from '../utils/api';
import { useUIStore } from '../store/uiStore';
import { useLogs } from '../context/LogsContext';

// Diff table component for Project Runs
function DiffTable({ diffs }) {
    if (!diffs || (!diffs.metadata_diff && (!diffs.models || diffs.models.length === 0))) {
        return (
            <div className="p-8 text-center text-tertiary text-sm">
                <GitCompare size={32} className="mx-auto mb-2 opacity-30" />
                No differences found
            </div>
        );
    }

    const models = diffs.models || [];
    const meta = diffs.metadata_diff || {};

    return (
        <div className="flex flex-col gap-6">
            {/* Metadata Section */}
            {meta.snapshot_a && meta.snapshot_b && (
                <div className="border border-main rounded-lg bg-surface-raised p-4">
                    <h4 className="text-xs font-bold text-slate-400 mb-3 uppercase tracking-wider">Metadata Diff</h4>
                    <div className="grid grid-cols-2 gap-4 text-xs">
                        <div>
                            <div className="text-[10px] text-tertiary mb-1">Old Snapshot</div>
                            <div className="text-secondary">Models: {meta.snapshot_a.model_count}</div>
                            <div className="text-secondary">Format: {meta.snapshot_a.format}</div>
                        </div>
                        <div>
                            <div className="text-[10px] text-tertiary mb-1">New Snapshot</div>
                            <div className="text-secondary">Models: {meta.snapshot_b.model_count}</div>
                            <div className="text-secondary">Format: {meta.snapshot_b.format}</div>
                        </div>
                    </div>
                </div>
            )}

            {/* Models Section */}
            <div className="overflow-x-auto border border-main rounded-lg bg-surface-raised">
                <table className="w-full text-xs">
                    <thead className="sticky top-0 backdrop-blur-md" style={{ background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)' }}>
                        <tr>
                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Model Name</th>
                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Status</th>
                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Details</th>
                        </tr>
                    </thead>
                    <tbody>
                        {models.length === 0 && (
                            <tr>
                                <td colSpan="3" className="px-3 py-4 text-center text-tertiary">No model changes</td>
                            </tr>
                        )}
                        {models.map((m, idx) => (
                            <tr key={idx} className="transition-colors border-b border-light hover:bg-surface-hover">
                                <td className="px-3 py-2 font-medium" style={{ color: 'var(--text-primary)' }}>{m.name}</td>
                                <td className="px-3 py-2">
                                    <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase" style={{
                                        background: m.status === 'ADDED' ? 'var(--color-success-bg)' : m.status === 'REMOVED' ? 'var(--color-error-bg)' : m.status === 'MODIFIED' ? 'var(--color-warning-bg)' : 'var(--bg-surface)',
                                        color: m.status === 'ADDED' ? 'var(--color-success)' : m.status === 'REMOVED' ? 'var(--color-error)' : m.status === 'MODIFIED' ? 'var(--color-warning)' : 'var(--text-tertiary)'
                                    }}>{m.status}</span>
                                </td>
                                <td className="px-3 py-2 font-mono text-[10px] text-secondary">
                                    {JSON.stringify(m.details)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default function VersionControlPanel({ isOpen, onClose }) {
    const activeProjectId = useUIStore(state => state.activeProjectId);
    const [versions, setVersions] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [selectedForCompare, setSelectedForCompare] = useState([]);
    const [diffs, setDiffs] = useState(null);
    const [isComparing, setIsComparing] = useState(false);
    const [rollbackTarget, setRollbackTarget] = useState(null);
    const [isRollingBack, setIsRollingBack] = useState(false);
    const [rollbackError, setRollbackError] = useState('');
    const [activeVersionId, setActiveVersionId] = useState(null);
    const [isSnapshotSwitching, setIsSnapshotSwitching] = useState(false);
    const [historyTilt, setHistoryTilt] = useState({ x: 0, y: 0 });
    const [viewMode, setViewMode] = useState('history');
    const switchTimerRef = useRef(null);
    const contentScrollRef = useRef(null);
    const diffSectionRef = useRef(null);
    const { addLog } = useLogs();

    const loadVersions = useCallback(async () => {
        setIsLoading(true);
        setDiffs(null);
        setSelectedForCompare([]);
        try {
            if (!activeProjectId) {
                setVersions([]);
                setActiveVersionId(null);
                setIsLoading(false);
                return;
            }
            const data = await api.getProjectRuns(activeProjectId);
            const sortedData = (data || []).sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
            setVersions(sortedData);
            if (sortedData.length > 0) {
                setActiveVersionId(sortedData[0].run_id);
            } else {
                setActiveVersionId(null);
            }
            addLog('info', 'Version Control', `Loaded ${sortedData.length} runs for project ${activeProjectId}`);
        } catch (err) {
            console.error('Failed to load runs:', err);
            setVersions([]);
            setActiveVersionId(null);
            addLog('error', 'Version Control', `Failed to load runs: ${err.message}`);
        } finally {
            setIsLoading(false);
        }
    }, [activeProjectId, addLog]);

    // Reload versions when panel opens or active project changes
    useEffect(() => {
        if (isOpen) loadVersions();
    }, [isOpen, activeProjectId, loadVersions]);

    useEffect(() => {
        return () => {
            if (switchTimerRef.current) clearTimeout(switchTimerRef.current);
        };
    }, []);

    const handleCompare = async () => {
        if (selectedForCompare.length !== 2) return;
        const run1 = versions.find(v => v.run_id === selectedForCompare[0]);
        const run2 = versions.find(v => v.run_id === selectedForCompare[1]);
        
        // Use after_tgt_snapshots if available, else before_tgt_snapshots, else fallback
        const snap1 = run1?.after_tgt_snapshots?.[0] || run1?.before_tgt_snapshots?.[0];
        const snap2 = run2?.after_tgt_snapshots?.[0] || run2?.before_tgt_snapshots?.[0];

        if (!snap1 || !snap2) {
            addLog('warning', 'Version Control', 'Selected runs do not have valid snapshots to compare.');
            return;
        }

        setIsComparing(true);
        try {
            const data = await api.compareProjectSnapshots(activeProjectId, snap1, snap2);
            setDiffs(data || { metadata_diff: {}, models: [] });
            addLog('info', 'Version Control', `Comparison complete.`);
        } catch (err) {
            console.error('Compare failed:', err);
            addLog('error', 'Version Control', `Comparison failed: ${err.message}`);
            setDiffs(null);
        } finally {
            setIsComparing(false);
        }
    };

    const handleRollback = async (runId) => {
        setIsRollingBack(true);
        setRollbackError('');
        try {
            const v = versions.find(ver => ver.run_id === runId);
            if (!v || !v.after_tgt_snapshots || !v.after_tgt_snapshots[0]) {
                addLog('warning', 'Version Control', 'Cannot rollback: no target snapshot found for this run.');
                setRollbackTarget(null);
                return;
            }
            const payload = { snapshot_id: v.after_tgt_snapshots[0] };
            const result = await api.restoreProjectVersion(activeProjectId, payload);
            setRollbackTarget(null);
            addLog('info', 'Version Control',
                `Rollback initiated: new restore run ${result.run_id?.substring(0, 8)} created.`
            );
            loadVersions();
        } catch (err) {
            console.error('Rollback failed:', err);
            setRollbackError(err.message || 'Rollback failed');
            addLog('error', 'Version Control', `Rollback failed: ${err.message}`);
        } finally {
            setIsRollingBack(false);
        }
    };

    const toggleCompareSelection = (runId) => {
        setSelectedForCompare(prev => {
            if (prev.includes(runId)) return prev.filter(v => v !== runId);
            if (prev.length >= 2) return [prev[1], runId];
            return [...prev, runId];
        });
    };

    const handleVersionSelect = (version) => {
        if (!version?.run_id || version.run_id === activeVersionId) return;
        setActiveVersionId(version.run_id);
        setIsSnapshotSwitching(true);
        if (switchTimerRef.current) clearTimeout(switchTimerRef.current);
        switchTimerRef.current = setTimeout(() => setIsSnapshotSwitching(false), 180);
    };

    const handleHistoryMouseMove = (e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const normalizedX = (e.clientX - rect.left) / rect.width - 0.5;
        const normalizedY = (e.clientY - rect.top) / rect.height - 0.5;
        setHistoryTilt({ x: -(normalizedY * 1.2), y: normalizedX * 1.2 });
    };

    const handleHistoryMouseLeave = () => {
        setHistoryTilt({ x: 0, y: 0 });
    };

    const activeVersion = versions.length === 0
        ? null
        : (versions.find(v => v.run_id === activeVersionId) || versions[0]);
    const activeSnapshotLabel = activeVersion
        ? `Run ${activeVersion.run_id?.substring(0, 8)}`
        : 'None';
    const timelineEntries = versions;

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex justify-end">
            <div className="absolute inset-0 backdrop-blur-sm bg-[#020617]/65" onClick={onClose} />
            <div className="relative w-[100vw] sm:w-[90vw] lg:w-[1000px] h-full bg-[#020617] border-l border-slate-800/70 flex flex-col shadow-2xl animate-in slide-in-from-right duration-300 text-slate-200">
                {/* Header */}
                <div className="h-16 px-6 flex items-center justify-between border-b border-slate-800/60 bg-[#020617]/80 backdrop-blur-md">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-lg bg-[#6467f2]/20 text-[#6467f2] border border-[#6467f2]/35 shadow-lg shadow-[#6467f2]/15">
                            <GitCommit size={20} />
                        </div>
                        <div>
                            <h2 className="text-lg font-bold text-slate-100">Project <span className="text-[#6467f2]">History</span></h2>
                            <p className="text-[10px] text-slate-500 font-semibold uppercase tracking-widest">
                                {activeProjectId
                                    ? `${versions.length} run(s) for project`
                                    : `No project selected`}
                            </p>
                            <p className="text-[10px] mt-1 uppercase tracking-widest text-slate-500 font-semibold">
                                Active Run:
                                <span className="ml-2 text-xs font-mono text-[#6467f2] bg-[#6467f2]/10 px-2 py-0.5 rounded-full border border-[#6467f2]/20 normal-case">{activeSnapshotLabel}</span>
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-4">
                        <div className="flex items-center gap-2">
                            <button
                                onClick={() => setViewMode('history')}
                                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 border ${viewMode === 'history'
                                    ? 'bg-[#6467f2]/20 text-[#6467f2] border-[#6467f2]/50 shadow-lg shadow-[#6467f2]/15'
                                    : 'text-slate-400 border-slate-700/50 hover:border-[#6467f2]/50 hover:text-[#6467f2] hover:bg-slate-800/40'
                                    }`}
                            >
                                History
                            </button>
                            <button
                                onClick={() => {
                                    setViewMode('diff');
                                    setTimeout(() => {
                                        if (diffSectionRef.current && contentScrollRef.current) {
                                            diffSectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
                                        }
                                    }, 0);
                                }}
                                className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 border ${viewMode === 'diff'
                                    ? 'bg-[#6467f2]/20 text-[#6467f2] border-[#6467f2]/50 shadow-lg shadow-[#6467f2]/15'
                                    : 'text-slate-400 border-slate-700/50 hover:border-[#6467f2]/50 hover:text-[#6467f2] hover:bg-slate-800/40'
                                    }`}
                            >
                                Diff Mode
                            </button>
                        </div>
                        <button onClick={onClose} className="p-2 hover:bg-slate-800/70 rounded-full text-slate-400 transition-colors">
                            <X size={20} />
                        </button>
                    </div>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto custom-scrollbar" ref={contentScrollRef}>
                    {/* Snapshot workspace */}
                    <div className="p-6 pb-4">
                        <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-4 min-h-[420px]">
                            <section className="relative rounded-lg border border-slate-800/70 overflow-hidden bg-[radial-gradient(circle_at_20%_35%,_#0f172a_0%,_#020617_100%)]">
                                <svg className="absolute inset-0 w-full h-full pointer-events-none opacity-40" viewBox="0 0 1200 700" preserveAspectRatio="none">
                                    <path d="M260,260 C440,260 440,480 620,480" stroke="#6467f2" strokeWidth="2" fill="none" opacity="0.3" />
                                    <path d="M260,300 C440,300 440,180 620,180" stroke="#6467f2" strokeWidth="2" fill="none" opacity="0.3" />
                                </svg>

                                <div className="relative h-full min-h-[420px] p-6">
                                    <div className="absolute inset-x-6 top-6 bottom-6 [perspective:1200px]">
                                        <div className="absolute inset-[16%_9%_16%_9%] rounded-lg border-2 border-slate-800/40 bg-slate-900/20 blur-sm" />
                                        <div className="absolute inset-[13%_7%_13%_7%] rounded-lg border-2 border-slate-800/40 bg-slate-900/20 blur-[2px]" />
                                        <div className="absolute inset-[10%_5%_10%_5%] rounded-lg border border-[#6467f2]/30 bg-slate-900/85 shadow-2xl p-6 backdrop-blur-xl">
                                            <div className={`grid grid-cols-1 md:grid-cols-2 gap-5 transition-opacity duration-150 ${isSnapshotSwitching ? 'opacity-70' : 'opacity-100'}`}>
                                                <article className="rounded-lg border border-[#6467f2]/30 bg-slate-900/95 overflow-hidden shadow-xl ring-1 ring-white/5">
                                                    <div className="p-4 border-b border-[#6467f2]/20 bg-[#6467f2]/10">
                                                        <h3 className="text-sm font-bold text-[#6467f2]">{activeVersion?.run_id?.substring(0, 8) || 'Orders_Fact'}</h3>
                                                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mt-1 font-bold">Primary Transactional Model</p>
                                                    </div>
                                                    <div className="p-4 space-y-2 text-xs">
                                                        <div className="flex justify-between"><span className="text-slate-400">Version:</span><span className="text-[#6467f2] font-mono">None</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Author:</span><span className="text-slate-200">system</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Timestamp:</span><span className="text-slate-200">—</span></div>
                                                        <div className="border-t border-slate-800/60 pt-2 text-slate-300">Snapshot selected from timeline.</div>
                                                    </div>
                                                </article>

                                                <article className="rounded-lg border border-[#6467f2]/30 bg-slate-900/95 overflow-hidden shadow-xl ring-1 ring-white/5">
                                                    <div className="p-4 border-b border-[#6467f2]/20 bg-[#6467f2]/10">
                                                        <h3 className="text-sm font-bold text-[#6467f2]">Customers_Dim</h3>
                                                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mt-1 font-bold">Core Dimension</p>
                                                    </div>
                                                    <div className="p-4 space-y-2 text-xs">
                                                        <div className="flex justify-between"><span className="text-slate-400">Diff Selected:</span><span className="text-slate-200">{selectedForCompare.length}/2</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Changes Loaded:</span><span className="text-emerald-400">0</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Rollback Ready:</span><span className="text-amber-400">Yes</span></div>
                                                        <div className="border-t border-slate-800/60 pt-2 text-slate-300">Click a timeline entry to switch active snapshot instantly.</div>
                                                    </div>
                                                </article>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </section>

                            <aside className="rounded-lg border border-slate-800/80 bg-slate-900/90 backdrop-blur-2xl flex flex-col min-h-[420px]">
                                <div className="p-4 border-b border-slate-800/60 flex items-center justify-between bg-slate-900/40">
                                    <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">Timeline</h3>
                                    <span className="text-[10px] uppercase tracking-widest text-slate-500">2026</span>
                                </div>
                                <div className="flex-1 overflow-y-auto max-h-[56vh] p-4 space-y-6 custom-scrollbar">
                                    {timelineEntries.length === 0 && (
                                        <p className="text-xs text-slate-500">No snapshots available yet.</p>
                                    )}
                                    {timelineEntries.map((v, idx) => {
                                        const isActive = v.run_id === activeVersion?.run_id;
                                        return (
                                            <button
                                                key={v.run_id}
                                                onClick={() => handleVersionSelect(v)}
                                                className={`w-full text-left relative pl-5 pr-2 py-1 border-l-2 rounded-r-lg transition-all duration-150 ${isActive ? 'border-[#6467f2] bg-[#6467f2]/8' : 'border-slate-800 hover:border-[#6467f2]/40 hover:bg-slate-800/35 hover:translate-x-0.5'} group`}
                                            >
                                                <span
                                                    className="absolute -left-[7px] top-1.5 w-3 h-3 rounded-full border-2 border-slate-900"
                                                    style={{ backgroundColor: isActive ? '#6467f2' : '#1f2937' }}
                                                />
                                                <div className={`pb-4 ${idx === timelineEntries.length - 1 ? 'pb-0' : ''} ${isActive ? 'opacity-100' : 'opacity-60 group-hover:opacity-100'} transition-opacity`}>
                                                    <p className={`text-[10px] uppercase tracking-wider font-bold ${isActive ? 'text-[#6467f2]' : 'text-slate-500'}`}>
                                                        {v.started_at ? new Date(v.started_at).toLocaleDateString() : 'Now'}
                                                    </p>
                                                    <p className={`text-sm font-semibold mt-1 ${isActive ? 'text-slate-100' : 'text-slate-300'}`}>
                                                        {v.run_type || 'Sync'}
                                                    </p>
                                                    <p className="text-xs text-slate-500 truncate">{v.status}</p>
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>

                                <div className="p-5 border-t border-slate-800/80 bg-slate-950/80">
                                    <h4 className="text-[10px] uppercase tracking-[0.15em] text-slate-500 font-bold mb-4">Metadata Inspector</h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-slate-400">Total Models:</span><span className="text-slate-200">0</span></div>
                                        <div className="flex justify-between"><span className="text-slate-400">Snapshots:</span><span className="text-slate-200">0</span></div>
                                        <div className="flex justify-between"><span className="text-slate-400">Current:</span><span className="px-2 py-0.5 rounded-full bg-[#6467f2]/15 border border-[#6467f2]/30 text-[#6467f2] font-mono font-bold">None</span></div>
                                    </div>
                                </div>
                            </aside>
                        </div>
                    </div>

                    {viewMode === 'history' && (
                        <div className="px-6 pb-6 space-y-6">
                            <div className="h-12 border border-slate-800/80 bg-[#020617] rounded-lg px-4 flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-wide">
                                <div className="flex items-center gap-4">
                                    <span className="inline-flex items-center gap-2">
                                        <span className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(74,222,128,0.6)] animate-pulse" />
                                        Live Sync Connected
                                    </span>
                                </div>
                                <div className="flex items-center gap-4 normal-case">
                                    <span>Space: Run History</span>
                                </div>
                            </div>
                            
                            {/* Run History List */}
                            <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <h3 className="text-sm font-bold text-primary">Run History • {activeSnapshotLabel}</h3>
                                    <div className="flex items-center gap-2">
                                        <button
                                            onClick={handleCompare}
                                            disabled={selectedForCompare.length !== 2 || isComparing}
                                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all border ${selectedForCompare.length === 2
                                                ? 'bg-accent-blue/10 text-accent-blue border-accent-blue/30 hover:bg-accent-blue/20'
                                                : 'text-tertiary border-main cursor-not-allowed opacity-50'
                                                }`}
                                        >
                                            {isComparing ? <Loader2 size={12} className="animate-spin" /> : <GitCompare size={12} />}
                                            Compare Selected ({selectedForCompare.length}/2)
                                        </button>
                                    </div>
                                </div>

                                {isLoading ? (
                                    <div className="flex items-center justify-center py-8">
                                        <Loader2 size={24} className="animate-spin text-[#6467f2]" />
                                    </div>
                                ) : (
                                    <div
                                        className={`border border-slate-800/70 rounded-lg overflow-hidden bg-slate-900/70 transition-opacity duration-150 ${isSnapshotSwitching ? 'opacity-70' : 'opacity-100'}`}
                                        onMouseMove={handleHistoryMouseMove}
                                        onMouseLeave={handleHistoryMouseLeave}
                                        style={{
                                            transform: `perspective(1200px) rotateX(${historyTilt.x}deg) rotateY(${historyTilt.y}deg)`,
                                            transition: 'transform 180ms ease-out, opacity 150ms ease-out',
                                            willChange: 'transform, opacity',
                                        }}
                                    >
                                        <table className="w-full text-xs">
                                            <thead className="border-b border-slate-800/70 bg-slate-900/80">
                                                <tr>
                                                    <th className="w-10 px-3 py-2.5"></th>
                                                    <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Run ID</th>
                                                    <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Type</th>
                                                    <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Status</th>
                                                    <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Started At</th>
                                                    <th className="text-right px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Actions</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {versions.length === 0 && (
                                                    <tr>
                                                        <td colSpan={6} className="px-3 py-8 text-center text-tertiary text-sm">
                                                            No run history found.
                                                        </td>
                                                    </tr>
                                                )}
                                                {versions.map(v => {
                                                    const isChecked = selectedForCompare.includes(v.run_id);
                                                    return (
                                                        <tr
                                                            key={v.run_id}
                                                            onClick={() => handleVersionSelect(v)}
                                                            className={`border-b border-slate-800/70 last:border-0 hover:bg-slate-800/40 transition-colors cursor-pointer ${activeVersionId === v.run_id ? 'bg-[#6467f2]/10' : ''}`}
                                                        >
                                                            <td className="px-3 py-2.5">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={isChecked}
                                                                    onChange={() => toggleCompareSelection(v.run_id)}
                                                                    onClick={(e) => e.stopPropagation()}
                                                                    className="rounded border-slate-700 accent-[#6467f2]"
                                                                />
                                                            </td>
                                                            <td className="px-3 py-2.5 font-mono text-[#6467f2] font-bold">{v.run_id?.substring(0, 8)}</td>
                                                            <td className="px-3 py-2.5">
                                                                <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-indigo-500/15 text-indigo-400 border border-indigo-500/30">
                                                                    {v.run_type || 'Sync'}
                                                                </span>
                                                            </td>
                                                            <td className="px-3 py-2.5">
                                                                <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-slate-500/15 text-slate-400 border border-slate-500/30">
                                                                    {v.status || 'unknown'}
                                                                </span>
                                                            </td>
                                                            <td className="px-3 py-2.5 text-slate-300">
                                                                <div className="flex items-center gap-1">
                                                                    <Clock size={10} className="text-slate-500" />
                                                                    {v.started_at ? new Date(v.started_at).toLocaleString() : '—'}
                                                                </div>
                                                            </td>
                                                            <td className="px-3 py-2.5 text-right">
                                                                <button
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        setRollbackTarget(v);
                                                                    }}
                                                                    className="px-2 py-1 rounded text-[10px] font-bold bg-amber-500/10 text-amber-500 border border-amber-500/20 hover:bg-amber-500/20 transition-colors"
                                                                >
                                                                    Restore
                                                                </button>
                                                            </td>
                                                        </tr>
                                                    );
                                                })}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {viewMode === 'diff' && <div ref={diffSectionRef} className="px-6 pb-6 space-y-6">
                        {/* Diff Table */}
                        {diffs ? (
                            <div className="space-y-3">
                                <h3 className="text-sm font-bold text-primary">Comparison Results</h3>
                                <DiffTable diffs={diffs} />
                            </div>
                        ) : (
                            <div className="p-8 text-center text-tertiary text-sm border border-main rounded-xl bg-surface-raised">
                                <GitCompare size={32} className="mx-auto mb-2 opacity-30" />
                                Select two runs in History mode and click Compare to see differences.
                            </div>
                        )}
                    </div>}
                </div>

                {/* Rollback Confirmation Dialog */}
                {rollbackTarget && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center backdrop-blur-sm rounded-xl" style={{ background: 'var(--bg-backdrop)' }}>
                        <div className="bg-surface border border-main rounded-xl p-6 max-w-md shadow-2xl">
                            <div className="flex items-center gap-3 mb-4">
                                <div className="p-2 rounded-lg bg-amber-500/15 text-amber-400">
                                    <AlertTriangle size={20} />
                                </div>
                                <h3 className="text-base font-bold text-primary">Confirm Rollback</h3>
                            </div>
                            <p className="text-xs text-secondary mb-2">
                                This will revert the project configuration and models to the state of Run{' '}
                                <strong className="text-accent-blue font-mono">
                                    {rollbackTarget.run_id?.substring(0, 8)}
                                </strong>
                                .
                            </p>
                            <p className="text-[11px] text-tertiary mb-4">
                                {rollbackTarget.run_type || 'Sync'} Run created on {rollbackTarget.started_at ? new Date(rollbackTarget.started_at).toLocaleString() : 'Now'}
                            </p>
                            <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-300/80 mb-6">
                                <p>• A <strong>new version record</strong> will be created representing this restore.</p>
                                <p>• Target models will be overwritten with the restored content upon applying.</p>
                                <p>• All existing version history will be <strong>preserved</strong>.</p>
                            </div>
                            {rollbackError && (
                                <div className="mb-4 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-[11px] text-red-300">
                                    {rollbackError}
                                </div>
                            )}
                            <div className="flex justify-end gap-3">
                                <button
                                    onClick={() => {
                                        setRollbackTarget(null);
                                        setRollbackError('');
                                    }}
                                    disabled={isRollingBack}
                                    className="px-4 py-2 rounded-lg text-xs font-bold text-secondary border border-main hover:bg-surface-hover"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={() => handleRollback(rollbackTarget.run_id)}
                                    disabled={isRollingBack}
                                    className="px-4 py-2 rounded-lg text-xs font-bold text-white bg-amber-500 hover:bg-amber-600 shadow-lg disabled:opacity-60 disabled:cursor-not-allowed inline-flex items-center gap-2"
                                >
                                    {isRollingBack ? <Loader2 size={12} className="animate-spin" /> : null}
                                    {isRollingBack ? 'Restoring...' : 'Restore to this run'}
                                </button>
                            </div>
                        </div>
                    </div>
                )}

                {/* Footer */}
                <div className="h-12 px-6 bg-[#020617] border-t border-slate-800/80 flex justify-between items-center">
                    <span className="text-[10px] uppercase tracking-wider text-slate-500">© 2026 DataStore Labs</span>
                    <button onClick={onClose} className="px-5 py-2 bg-[#6467f2] text-white rounded-lg text-sm font-bold hover:bg-[#4f46e5] active:scale-95 transition-all">
                        Done
                    </button>
                </div>
            </div>
        </div>
    );
}
