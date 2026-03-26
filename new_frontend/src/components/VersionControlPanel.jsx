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
import { useLogs } from '../context/LogsContext';

// Diff table component
function DiffTable({ diffs, objectTypeFilter, changeTypeFilter }) {
    const filtered = diffs.filter(d =>
        (objectTypeFilter === 'All' || d.object_type === objectTypeFilter) &&
        (changeTypeFilter === 'All' || d.change_type === changeTypeFilter)
    );

    if (filtered.length === 0) {
        return (
            <div className="p-8 text-center text-tertiary text-sm">
                <GitCompare size={32} className="mx-auto mb-2 opacity-30" />
                No differences found for selected filters
            </div>
        );
    }

    return (
        <div className="overflow-x-auto">
            <table className="w-full text-xs">
                <thead className="sticky top-0 backdrop-blur-md" style={{ background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)' }}>
                    <tr>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Object Name</th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Type</th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Property</th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                            Old Value {filtered[0]?.old_version_tag ? `(${filtered[0].old_version_tag})` : ''}
                        </th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>
                            New Value {filtered[0]?.new_version_tag ? `(${filtered[0].new_version_tag})` : ''}
                        </th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--text-tertiary)' }}>Change</th>
                    </tr>
                </thead>
                <tbody>
                    {filtered.map((d, idx) => (
                        <tr key={idx} className="transition-colors" style={{ borderBottom: '1px solid var(--border-light)', }} onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-surface-hover)'} onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                            <td className="px-3 py-2 font-medium" style={{ color: 'var(--text-primary)' }}>{d.object_name}</td>
                            <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{d.object_type}</td>
                            <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-secondary)' }}>{d.property}</td>
                            <td className="px-3 py-2">
                                {d.old_value && <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono" style={{ background: 'var(--color-error-bg)', color: 'var(--color-error)' }}>{d.old_value}</span>}
                            </td>
                            <td className="px-3 py-2">
                                {d.new_value && <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono" style={{ background: 'var(--color-success-bg)', color: 'var(--color-success)' }}>{d.new_value}</span>}
                            </td>
                            <td className="px-3 py-2">
                                <span className="px-2 py-0.5 rounded-full text-[9px] font-bold uppercase" style={{
                                    background: d.change_type === 'Added' ? 'var(--color-success-bg)' : d.change_type === 'Removed' ? 'var(--color-error-bg)' : 'var(--color-warning-bg)',
                                    color: d.change_type === 'Added' ? 'var(--color-success)' : d.change_type === 'Removed' ? 'var(--color-error)' : 'var(--color-warning)'
                                }}>{d.change_type}</span>
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function VersionControlPanel({ isOpen, onClose, activeModelId = null }) {
    const [versions, setVersions] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [selectedForCompare, setSelectedForCompare] = useState([]);
    const [diffs, setDiffs] = useState(null);
    const [isComparing, setIsComparing] = useState(false);
    const [rollbackTarget, setRollbackTarget] = useState(null);
    const [deleteConfirm, setDeleteConfirm] = useState(false);
    const [isDeleting, setIsDeleting] = useState(false);
    const [objectTypeFilter, setObjectTypeFilter] = useState('All');
    const [changeTypeFilter, setChangeTypeFilter] = useState('All');
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
            // When no model is selected, fetch versions for all models.
            const data = await api.getModelVersions(activeModelId || '');
            setVersions(data || []);
            if ((data || []).length > 0) {
                setActiveVersionId(data[0].version_id);
            } else {
                setActiveVersionId(null);
            }
            const label = activeModelId ? `for ${activeModelId}` : 'across all models';
            addLog('info', 'Version Control', `Loaded ${(data || []).length} versions ${label}`);
        } catch (err) {
            console.error('Failed to load versions:', err);
            setVersions([]);
            setActiveVersionId(null);
            addLog('error', 'Version Control', `Failed to load versions: ${err.message}`);
        } finally {
            setIsLoading(false);
        }
    }, [activeModelId, addLog]);

    // Reload versions when panel opens or active model changes
    useEffect(() => {
        if (isOpen) loadVersions();
    }, [isOpen, loadVersions]);

    useEffect(() => {
        return () => {
            if (switchTimerRef.current) clearTimeout(switchTimerRef.current);
        };
    }, []);

    const handleCompare = async () => {
        if (selectedForCompare.length !== 2) return;
        const selectedRows = versions.filter(v => selectedForCompare.includes(v.version_id));
        const unsupportedCompare = selectedRows.some(v => v.can_compare === false);
        if (unsupportedCompare) {
            addLog('warning', 'Version Control', 'Selected versions cannot be compared (read-only config history entries).');
            return;
        }
        setIsComparing(true);
        try {
            const data = await api.compareVersions(selectedForCompare[0], selectedForCompare[1]);
            setDiffs(data.changes || []);
            const changeCount = (data.changes || []).length;
            addLog('info', 'Version Control', `Comparison complete: ${changeCount} difference(s) found`);
        } catch (err) {
            console.error('Compare failed:', err);
            addLog('error', 'Version Control', `Comparison failed: ${err.message}`);
            setDiffs([]);
        } finally {
            setIsComparing(false);
        }
    };

    const handleRollback = async (versionId) => {
        try {
            const v = versions.find(ver => ver.version_id === versionId);
            if (v?.can_rollback === false) {
                addLog('warning', 'Version Control', 'Rollback is not supported for this version source.');
                setRollbackTarget(null);
                return;
            }
            const modelId = v?.model_id || activeModelId || 'default';
            const wsId = v?.workspace_id || 'default';
            const result = await api.rollbackVersion(versionId, modelId, wsId);
            setRollbackTarget(null);
            addLog('info', 'Version Control',
                `Rollback successful: ${modelId} restored to ${v?.version_tag || versionId.substring(0, 8)}. New version: ${result.new_version_id?.substring(0, 8)}`
            );
            loadVersions();
        } catch (err) {
            console.error('Rollback failed:', err);
            addLog('error', 'Version Control', `Rollback failed: ${err.message}`);
            alert(`Rollback failed: ${err.message}`);
        }
    };

    const handleDeleteAll = async () => {
        if (!activeModelId) return;
        setIsDeleting(true);
        try {
            const result = await api.deleteAllVersions(activeModelId);
            addLog('info', 'Version Control', `Deleted ${result.deleted} version(s) for ${activeModelId}`);
            setVersions([]);
            setDiffs(null);
            setSelectedForCompare([]);
            setDeleteConfirm(false);
        } catch (err) {
            console.error('Delete failed:', err);
            addLog('error', 'Version Control', `Delete failed: ${err.message}`);
        } finally {
            setIsDeleting(false);
        }
    };

    const toggleCompareSelection = (versionId) => {
        setSelectedForCompare(prev => {
            if (prev.includes(versionId)) return prev.filter(v => v !== versionId);
            if (prev.length >= 2) return [prev[1], versionId];
            return [...prev, versionId];
        });
    };

    const handleVersionSelect = (version) => {
        if (!version?.version_id || version.version_id === activeVersionId) return;
        setActiveVersionId(version.version_id);
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

    const objectTypes = ['All', ...new Set((diffs || []).map(d => d.object_type))];
    const changeTypes = ['All', ...new Set((diffs || []).map(d => d.change_type))];
    const activeVersion = versions.length === 0
        ? null
        : (versions.find(v => v.version_id === activeVersionId) || versions[0]);
    const activeSnapshotLabel = activeVersion
        ? (activeVersion.version_tag || activeVersion.version_id?.substring(0, 8))
        : 'None';
    const timelineEntries = versions;

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex justify-end">
            <div className="absolute inset-0 backdrop-blur-sm bg-[#020617]/65" onClick={onClose} />
            <div className="relative w-full h-full bg-[#020617] border-l border-slate-800/70 flex flex-col shadow-2xl animate-in slide-in-from-right duration-300 text-slate-200">
                {/* Header */}
                <div className="h-16 px-6 flex items-center justify-between border-b border-slate-800/60 bg-[#020617]/80 backdrop-blur-md">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-lg bg-[#6467f2]/20 text-[#6467f2] border border-[#6467f2]/35 shadow-lg shadow-[#6467f2]/15">
                            <GitCommit size={20} />
                        </div>
                        <div>
                            <h2 className="text-lg font-bold text-slate-100">Version <span className="text-[#6467f2]">History</span></h2>
                            <p className="text-[10px] text-slate-500 font-semibold uppercase tracking-widest">
                                {activeModelId
                                    ? `${versions.length} version(s) for ${activeModelId}`
                                    : `${versions.length} version(s) across all models`}
                            </p>
                            <p className="text-[10px] mt-1 uppercase tracking-widest text-slate-500 font-semibold">
                                Active Snapshot:
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
                                                        <h3 className="text-sm font-bold text-[#6467f2]">{activeModelId || activeVersion?.model_id || 'Orders_Fact'}</h3>
                                                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mt-1 font-bold">Primary Transactional Model</p>
                                                    </div>
                                                    <div className="p-4 space-y-2 text-xs">
                                                        <div className="flex justify-between"><span className="text-slate-400">Version:</span><span className="text-[#6467f2] font-mono">{activeSnapshotLabel}</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Author:</span><span className="text-slate-200">{activeVersion?.author || 'system'}</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Timestamp:</span><span className="text-slate-200">{activeVersion?.timestamp ? new Date(activeVersion.timestamp).toLocaleString() : '—'}</span></div>
                                                        <div className="border-t border-slate-800/60 pt-2 text-slate-300">{activeVersion?.description || 'Snapshot selected from timeline.'}</div>
                                                    </div>
                                                </article>

                                                <article className="rounded-lg border border-[#6467f2]/30 bg-slate-900/95 overflow-hidden shadow-xl ring-1 ring-white/5">
                                                    <div className="p-4 border-b border-[#6467f2]/20 bg-[#6467f2]/10">
                                                        <h3 className="text-sm font-bold text-[#6467f2]">Customers_Dim</h3>
                                                        <p className="text-[10px] uppercase tracking-wider text-slate-500 mt-1 font-bold">Core Dimension</p>
                                                    </div>
                                                    <div className="p-4 space-y-2 text-xs">
                                                        <div className="flex justify-between"><span className="text-slate-400">Diff Selected:</span><span className="text-slate-200">{selectedForCompare.length}/2</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Changes Loaded:</span><span className="text-emerald-400">{(diffs || []).length}</span></div>
                                                        <div className="flex justify-between"><span className="text-slate-400">Rollback Ready:</span><span className="text-amber-400">{activeVersion?.can_rollback === false ? 'No' : 'Yes'}</span></div>
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
                                        const isActive = v.version_id === activeVersion?.version_id;
                                        return (
                                            <button
                                                key={v.version_id}
                                                onClick={() => handleVersionSelect(v)}
                                                className={`w-full text-left relative pl-5 pr-2 py-1 border-l-2 rounded-r-lg transition-all duration-150 ${isActive ? 'border-[#6467f2] bg-[#6467f2]/8' : 'border-slate-800 hover:border-[#6467f2]/40 hover:bg-slate-800/35 hover:translate-x-0.5'} group`}
                                            >
                                                <span
                                                    className="absolute -left-[7px] top-1.5 w-3 h-3 rounded-full border-2 border-slate-900"
                                                    style={{ backgroundColor: isActive ? '#6467f2' : '#1f2937' }}
                                                />
                                                <div className={`pb-4 ${idx === timelineEntries.length - 1 ? 'pb-0' : ''} ${isActive ? 'opacity-100' : 'opacity-60 group-hover:opacity-100'} transition-opacity`}>
                                                    <p className={`text-[10px] uppercase tracking-wider font-bold ${isActive ? 'text-[#6467f2]' : 'text-slate-500'}`}>
                                                        {v.timestamp ? new Date(v.timestamp).toLocaleDateString() : 'Now'}
                                                    </p>
                                                    <p className={`text-sm font-semibold mt-1 ${isActive ? 'text-slate-100' : 'text-slate-300'}`}>
                                                        {v.version_tag || `v${v.version_id.substring(0, 6)}`}
                                                    </p>
                                                    <p className="text-xs text-slate-500 truncate">{v.description || 'Config snapshot'}</p>
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>

                                <div className="p-5 border-t border-slate-800/80 bg-slate-950/80">
                                    <h4 className="text-[10px] uppercase tracking-[0.15em] text-slate-500 font-bold mb-4">Metadata Inspector</h4>
                                    <div className="space-y-2 text-xs">
                                        <div className="flex justify-between"><span className="text-slate-400">Total Models:</span><span className="text-slate-200">{new Set(versions.map(v => v.model_id).filter(Boolean)).size || (activeModelId ? 1 : 0)}</span></div>
                                        <div className="flex justify-between"><span className="text-slate-400">Snapshots:</span><span className="text-slate-200">{versions.length}</span></div>
                                        <div className="flex justify-between"><span className="text-slate-400">Current:</span><span className="px-2 py-0.5 rounded-full bg-[#6467f2]/15 border border-[#6467f2]/30 text-[#6467f2] font-mono font-bold">{activeSnapshotLabel}</span></div>
                                    </div>
                                </div>
                            </aside>
                        </div>
                    </div>

                    {viewMode === 'history' && (
                        <div className="px-6 pb-6">
                            <div className="h-12 border border-slate-800/80 bg-[#020617] rounded-lg px-4 flex items-center justify-between text-[10px] font-bold text-slate-500 uppercase tracking-wide">
                                <div className="flex items-center gap-4">
                                    <span className="inline-flex items-center gap-2">
                                        <span className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_rgba(74,222,128,0.6)] animate-pulse" />
                                        Live Sync Connected
                                    </span>
                                    <span className="h-4 w-px bg-slate-800/80" />
                                    <span className="normal-case font-medium">Query Engine: BigQuery</span>
                                </div>
                                <div className="flex items-center gap-4 normal-case">
                                    <span>Space: Play History</span>
                                    <span>S: Split Screen</span>
                                </div>
                            </div>
                        </div>
                    )}

                    {viewMode === 'diff' && <div ref={diffSectionRef} className="px-6 pb-6 space-y-6">
                    {/* Version History List */}
                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-bold text-primary">Version History • {activeSnapshotLabel}</h3>
                            <div className="flex items-center gap-2">
                            <button
                                onClick={() => versions.length > 0 && setDeleteConfirm(true)}
                                disabled={!activeModelId || versions.length === 0}
                                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all border border-red-500/30 text-red-400 hover:bg-red-500/10 disabled:opacity-40 disabled:cursor-not-allowed"
                                title="Delete all versions for this model"
                            >
                                <Trash2 size={12} /> Delete All
                            </button>
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
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Version ID</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Tag</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Model</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Timestamp</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Author</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Summary</th>
                                            <th className="text-right px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {versions.length === 0 && (
                                            <tr>
                                                <td colSpan={8} className="px-3 py-8 text-center text-tertiary text-sm">
                                                    {activeModelId
                                                        ? `No versions recorded for ${activeModelId} yet.`
                                                        : 'No version history found. Versions appear here after a model sync or manual commit.'}
                                                </td>
                                            </tr>
                                        )}
                                        {versions.map(v => {
                                            const isChecked = selectedForCompare.includes(v.version_id);
                                            const canCompare = v.can_compare !== false;
                                            const canRollback = v.can_rollback !== false;
                                            return (
                                                <tr
                                                    key={v.version_id}
                                                    onClick={() => handleVersionSelect(v)}
                                                    className={`border-b border-slate-800/70 last:border-0 hover:bg-slate-800/40 transition-colors cursor-pointer ${activeVersionId === v.version_id ? 'bg-[#6467f2]/10' : ''}`}
                                                >
                                                    <td className="px-3 py-2.5">
                                                        <input
                                                            type="checkbox"
                                                            checked={isChecked}
                                                            onChange={() => toggleCompareSelection(v.version_id)}
                                                            onClick={(e) => e.stopPropagation()}
                                                            disabled={!canCompare}
                                                            className="rounded border-slate-700 accent-[#6467f2]"
                                                        />
                                                    </td>
                                                    <td className="px-3 py-2.5 font-mono text-[#6467f2] font-bold">{v.version_id?.substring(0, 8)}</td>
                                                    <td className="px-3 py-2.5">
                                                        {v.version_tag ? (
                                                            <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-purple-500/15 text-purple-400 border border-purple-500/30">
                                                                {v.version_tag}
                                                            </span>
                                                        ) : (
                                                            <span className="text-tertiary">—</span>
                                                        )}
                                                    </td>
                                                    <td className="px-3 py-2.5">
                                                        <span className="px-2 py-0.5 rounded-full text-[9px] font-bold bg-indigo-500/15 text-indigo-400 border border-indigo-500/30">
                                                            {v.model_id || '—'}
                                                        </span>
                                                    </td>
                                                    <td className="px-3 py-2.5 text-slate-300">
                                                        <div className="flex items-center gap-1">
                                                            <Clock size={10} className="text-slate-500" />
                                                            {new Date(v.timestamp).toLocaleString()}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5 text-slate-300 capitalize">{v.author || 'system'}</td>
                                                    <td className="px-3 py-2.5 text-slate-100">
                                                        <div className="flex items-center gap-1.5">
                                                            {v.is_rollback && (
                                                                <span className="px-1.5 py-0.5 rounded-full text-[8px] font-bold bg-amber-500/15 text-amber-400 border border-amber-500/30">
                                                                    ↩ ROLLBACK
                                                                </span>
                                                            )}
                                                            {v.description}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right">
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                canRollback && setRollbackTarget(v);
                                                            }}
                                                            disabled={!canRollback}
                                                            className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[10px] font-bold transition-colors ${
                                                                canRollback
                                                                    ? 'text-amber-400 hover:bg-amber-500/10'
                                                                    : 'text-tertiary opacity-50 cursor-not-allowed'
                                                            }`}
                                                        >
                                                            <RotateCcw size={10} /> Rollback
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

                    {/* Diff Table */}
                    {diffs && (
                        <div className="space-y-3">
                            <h3 className="text-sm font-bold text-primary">Comparison Results</h3>
                            <div className="flex items-center gap-3">
                                <div className="flex items-center gap-2">
                                    <Filter size={12} className="text-tertiary" />
                                    <select
                                        value={objectTypeFilter}
                                        onChange={e => setObjectTypeFilter(e.target.value)}
                                        className="bg-surface border border-main rounded-md px-2 py-1 text-[11px] text-primary"
                                    >
                                        {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
                                    </select>
                                    <select
                                        value={changeTypeFilter}
                                        onChange={e => setChangeTypeFilter(e.target.value)}
                                        className="bg-surface border border-main rounded-md px-2 py-1 text-[11px] text-primary"
                                    >
                                        {changeTypes.map(t => <option key={t} value={t}>{t}</option>)}
                                    </select>
                                </div>
                            </div>
                            <div className="border border-main rounded-xl overflow-hidden bg-surface-raised">
                                <DiffTable diffs={diffs} objectTypeFilter={objectTypeFilter} changeTypeFilter={changeTypeFilter} />
                            </div>
                        </div>
                    )}
                    </div>}
                </div>

                {/* Delete All Confirmation Dialog */}
                {deleteConfirm && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center backdrop-blur-sm rounded-xl" style={{ background: 'var(--bg-backdrop)' }}>
                        <div className="bg-surface border border-main rounded-xl p-6 max-w-md shadow-2xl">
                            <div className="flex items-center gap-3 mb-4">
                                <div className="p-2 rounded-lg bg-red-500/15 text-red-400">
                                    <Trash2 size={20} />
                                </div>
                                <h3 className="text-base font-bold text-primary">Delete All Versions?</h3>
                            </div>
                            <p className="text-xs text-secondary mb-4">
                                This will permanently delete <strong>all {versions.length} version(s)</strong> for{' '}
                                <strong className="text-accent-blue">{activeModelId}</strong>. This action cannot be undone.
                            </p>
                            <div className="flex justify-end gap-3">
                                <button
                                    onClick={() => setDeleteConfirm(false)}
                                    disabled={isDeleting}
                                    className="px-4 py-2 rounded-lg text-xs font-bold text-secondary border border-main hover:bg-surface-hover"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleDeleteAll}
                                    disabled={isDeleting}
                                    className="px-4 py-2 rounded-lg text-xs font-bold text-white bg-red-500 hover:bg-red-600 shadow-lg flex items-center gap-2"
                                >
                                    {isDeleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                                    Delete All
                                </button>
                            </div>
                        </div>
                    </div>
                )}

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
                                This will revert the configuration to version{' '}
                                <strong className="text-accent-blue font-mono">
                                    {rollbackTarget.version_tag || rollbackTarget.version_id?.substring(0, 8)}
                                </strong>
                                {rollbackTarget.version_tag && (
                                    <span className="text-tertiary"> ({rollbackTarget.version_id?.substring(0, 8)})</span>
                                )}
                                .
                            </p>
                            <p className="text-[11px] text-tertiary mb-4">
                                {rollbackTarget.description || 'No description available'}
                            </p>
                            <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-300/80 mb-6">
                                <p>• A <strong>new version record</strong> will be created from this snapshot.</p>
                                <p>• The local model file will be overwritten with the restored content.</p>
                                <p>• All existing version history will be <strong>preserved</strong>.</p>
                            </div>
                            <div className="flex justify-end gap-3">
                                <button
                                    onClick={() => setRollbackTarget(null)}
                                    className="px-4 py-2 rounded-lg text-xs font-bold text-secondary border border-main hover:bg-surface-hover"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={() => handleRollback(rollbackTarget.version_id)}
                                    className="px-4 py-2 rounded-lg text-xs font-bold text-white bg-amber-500 hover:bg-amber-600 shadow-lg"
                                >
                                    Rollback to this version
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

