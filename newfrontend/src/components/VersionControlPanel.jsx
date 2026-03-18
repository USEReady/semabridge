import { useState, useEffect } from 'react';
import {
    X,
    GitCommit,
    GitCompare,
    RotateCcw,
    ChevronDown,
    Clock,
    AlertTriangle,
    CheckCircle2,
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
                <thead className="sticky top-0 bg-surface-raised border-b border-main">
                    <tr>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-tertiary">Object Name</th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-tertiary">Type</th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-tertiary">Property</th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-tertiary">
                            Old Value {filtered[0]?.old_version_tag ? `(${filtered[0].old_version_tag})` : ''}
                        </th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-tertiary">
                            New Value {filtered[0]?.new_version_tag ? `(${filtered[0].new_version_tag})` : ''}
                        </th>
                        <th className="text-left px-3 py-2 text-[10px] font-bold uppercase tracking-wider text-tertiary">Change</th>
                    </tr>
                </thead>
                <tbody>
                    {filtered.map((d, idx) => (
                        <tr key={idx} className="border-b border-main hover:bg-surface-hover transition-colors">
                            <td className="px-3 py-2 font-medium text-primary">{d.object_name}</td>
                            <td className="px-3 py-2 text-secondary">{d.object_type}</td>
                            <td className="px-3 py-2 text-secondary font-mono">{d.property}</td>
                            <td className="px-3 py-2">
                                {d.old_value && <span className="bg-red-500/10 text-red-400 px-1.5 py-0.5 rounded text-[10px] font-mono">{d.old_value}</span>}
                            </td>
                            <td className="px-3 py-2">
                                {d.new_value && <span className="bg-emerald-500/10 text-emerald-400 px-1.5 py-0.5 rounded text-[10px] font-mono">{d.new_value}</span>}
                            </td>
                            <td className="px-3 py-2">
                                <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase ${d.change_type === 'Added' ? 'bg-emerald-500/15 text-emerald-400' :
                                    d.change_type === 'Removed' ? 'bg-red-500/15 text-red-400' :
                                        'bg-amber-500/15 text-amber-400'
                                    }`}>{d.change_type}</span>
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
    const { addLog } = useLogs();

    // Reload versions when panel opens or active model changes
    useEffect(() => {
        if (isOpen) loadVersions();
    }, [isOpen, activeModelId]);

    const loadVersions = async () => {
        setIsLoading(true);
        setDiffs(null);
        setSelectedForCompare([]);
        try {
            // When no model is selected, fetch versions for all models.
            const data = await api.getModelVersions(activeModelId || '');
            setVersions(data || []);
            const label = activeModelId ? `for ${activeModelId}` : 'across all models';
            addLog('info', 'Version Control', `Loaded ${(data || []).length} versions ${label}`);
        } catch (err) {
            console.error('Failed to load versions:', err);
            setVersions([]);
            addLog('error', 'Version Control', `Failed to load versions: ${err.message}`);
        } finally {
            setIsLoading(false);
        }
    };

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

    if (!isOpen) return null;

    const objectTypes = ['All', ...new Set((diffs || []).map(d => d.object_type))];
    const changeTypes = ['All', ...new Set((diffs || []).map(d => d.change_type))];

    return (
        <div className="fixed inset-0 z-50 flex justify-end">
            <div className="absolute inset-0 backdrop-blur-sm" style={{ background: 'var(--bg-backdrop)' }} onClick={onClose} />
            <div className="relative w-full max-w-5xl h-full bg-surface border-l border-main flex flex-col shadow-2xl animate-in slide-in-from-right duration-300">
                {/* Header */}
                <div className="px-6 py-4 flex items-center justify-between border-b border-main bg-surface-raised">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-lg bg-accent-blue/15 text-accent-blue">
                            <GitCommit size={20} />
                        </div>
                        <div>
                            <h2 className="text-lg font-bold text-primary">Version Control</h2>
                            <p className="text-xs text-tertiary">
                                {activeModelId
                                    ? `${versions.length} version(s) for ${activeModelId}`
                                    : `${versions.length} version(s) across all models`}
                            </p>
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-surface-hover rounded-full text-tertiary">
                        <X size={20} />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">
                    {/* Version History List */}
                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-bold text-secondary">Version History</h3>
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
                                <Loader2 size={24} className="animate-spin text-accent-blue" />
                            </div>
                        ) : (
                            <div className="border border-main rounded-xl overflow-hidden bg-surface-raised">
                                <table className="w-full text-xs">
                                    <thead className="border-b border-main">
                                        <tr>
                                            <th className="w-10 px-3 py-2.5"></th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Version ID</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Tag</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Model</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Timestamp</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Author</th>
                                            <th className="text-left px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Summary</th>
                                            <th className="text-right px-3 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Actions</th>
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
                                                <tr key={v.version_id} className="border-b border-main last:border-0 hover:bg-surface-hover transition-colors">
                                                    <td className="px-3 py-2.5">
                                                        <input
                                                            type="checkbox"
                                                            checked={isChecked}
                                                            onChange={() => toggleCompareSelection(v.version_id)}
                                                            disabled={!canCompare}
                                                            className="rounded border-main accent-[var(--accent-blue)]"
                                                        />
                                                    </td>
                                                    <td className="px-3 py-2.5 font-mono text-accent-blue font-bold">{v.version_id?.substring(0, 8)}</td>
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
                                                    <td className="px-3 py-2.5 text-secondary">
                                                        <div className="flex items-center gap-1">
                                                            <Clock size={10} className="text-tertiary" />
                                                            {new Date(v.timestamp).toLocaleString()}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5 text-secondary capitalize">{v.author || 'system'}</td>
                                                    <td className="px-3 py-2.5 text-primary">
                                                        <div className="flex items-center gap-1.5">
                                                            {v.is_rollback && (
                                                                <span className="px-1.5 py-0.5 rounded text-[8px] font-bold bg-amber-500/15 text-amber-400 border border-amber-500/30">
                                                                    ↩ ROLLBACK
                                                                </span>
                                                            )}
                                                            {v.description}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2.5 text-right">
                                                        <button
                                                            onClick={() => canRollback && setRollbackTarget(v)}
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
                            <h3 className="text-sm font-bold text-secondary">Comparison Results</h3>
                            <div className="flex items-center gap-3">
                                <div className="flex items-center gap-2">
                                    <Filter size={12} className="text-tertiary" />
                                    <select
                                        value={objectTypeFilter}
                                        onChange={e => setObjectTypeFilter(e.target.value)}
                                        className="bg-app border border-main rounded-md px-2 py-1 text-[11px] text-primary"
                                    >
                                        {objectTypes.map(t => <option key={t} value={t}>{t}</option>)}
                                    </select>
                                    <select
                                        value={changeTypeFilter}
                                        onChange={e => setChangeTypeFilter(e.target.value)}
                                        className="bg-app border border-main rounded-md px-2 py-1 text-[11px] text-primary"
                                    >
                                        {changeTypes.map(t => <option key={t} value={t}>{t}</option>)}
                                    </select>
                                </div>
                            </div>
                            <div className="border border-main rounded-xl overflow-hidden">
                                <DiffTable diffs={diffs} objectTypeFilter={objectTypeFilter} changeTypeFilter={changeTypeFilter} />
                            </div>
                        </div>
                    )}
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
                <div className="px-6 py-4 bg-surface-raised border-t border-main flex justify-end">
                    <button onClick={onClose} className="px-5 py-2 bg-accent-blue text-white rounded-lg text-sm font-bold hover:bg-accent-blue-hover active:scale-95 transition-all">
                        Done
                    </button>
                </div>
            </div>
        </div>
    );
}
