import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { yaml } from '@codemirror/lang-yaml';
import { useTheme } from '../context/ThemeProvider';
import ErrorPanel from './ErrorPanel';
import {
    X,
    FileCode2,
    MoreHorizontal,
    Copy,
    Save,
    Play,
    Zap,
    CheckCircle2,
    AlertTriangle,
    Info,
    Clock,
    Activity,
    ChevronRight,
    Loader2,
} from 'lucide-react';

import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';

const DeploySummaryModal = ({ isOpen, onClose, summary, status, results }) => {
    if (!isOpen) return null;

    const isSuccess = status === 'success';
    const isPartial = status === 'partial';
    const multiModel = results && results.length > 1;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 backdrop-blur-sm animate-in fade-in duration-200" style={{ background: 'var(--bg-backdrop)' }}>
            <div className="bg-surface border border-main rounded-xl shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh] animate-in zoom-in-95 duration-200">
                {/* Header */}
                <div className={`px-6 py-4 flex items-center justify-between border-b border-main ${
                    isSuccess ? 'bg-emerald-500/10' : isPartial ? 'bg-amber-500/10' : 'bg-red-500/10'
                }`}>
                    <div className="flex items-center gap-3">
                        <div className={`p-2 rounded-lg ${
                            isSuccess ? 'bg-emerald-500/20 text-emerald-400'
                            : isPartial ? 'bg-amber-500/20 text-amber-400'
                            : 'bg-red-500/20 text-red-400'
                        }`}>
                            {isSuccess ? <CheckCircle2 size={24} /> : <AlertTriangle size={24} />}
                        </div>
                        <div>
                            <h2 className="text-lg font-bold text-primary">
                                {isSuccess ? 'Deployment Successful'
                                 : isPartial ? 'Partial Deployment'
                                 : 'Deployment Failed'}
                            </h2>
                            {multiModel ? (
                                <p className="text-xs text-tertiary">
                                    {results.filter(r => r.status === 'success').length} / {results.length} models synced
                                </p>
                            ) : (
                                <p className="text-xs text-tertiary">Run ID: {summary?.run_id || 'N/A'}</p>
                            )}
                        </div>
                    </div>
                    <button onClick={onClose} className="p-2 hover:bg-surface-hover rounded-full text-tertiary transition-colors">
                        <X size={20} />
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">

                    {/* Per-model results table (multi-model) */}
                    {multiModel && (
                        <div className="space-y-3">
                            <h3 className="text-sm font-bold text-secondary">Per-Model Results</h3>
                            <div className="border border-main rounded-xl overflow-hidden bg-surface-raised">
                                <table className="w-full text-xs">
                                    <thead className="border-b border-main">
                                        <tr>
                                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase text-tertiary">Model</th>
                                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase text-tertiary">Status</th>
                                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase text-tertiary">Duration</th>
                                            <th className="text-left px-3 py-2 text-[10px] font-bold uppercase text-tertiary">Steps</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {results.map((r, i) => (
                                            <tr key={i} className="border-b border-main last:border-0 hover:bg-surface-hover">
                                                <td className="px-3 py-2 font-mono font-bold text-primary">{r.model}</td>
                                                <td className="px-3 py-2">
                                                    <span className={`px-2 py-0.5 rounded-full text-[9px] font-bold uppercase ${
                                                        r.status === 'success'
                                                            ? 'bg-emerald-500/15 text-emerald-400'
                                                            : 'bg-red-500/15 text-red-400'
                                                    }`}>{r.status}</span>
                                                </td>
                                                <td className="px-3 py-2 text-secondary">{r.summary?.duration_ms ?? '—'}ms</td>
                                                <td className="px-3 py-2 text-secondary">
                                                    {r.summary?.last_successful_step ?? '—'} / {r.summary?.total_steps ?? 10}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            {/* Errors from failed models */}
                            {results.filter(r => r.status !== 'success' && r.summary?.errors?.length > 0).map((r, i) => (
                                <div key={i} className="border border-red-500/30 bg-red-500/5 rounded-xl p-4 space-y-1">
                                    <p className="text-xs font-bold text-red-400">{r.model} — Error</p>
                                    {r.summary.errors.map((err, j) => (
                                        <p key={j} className="text-[11px] font-mono text-red-200/80">{err.message}</p>
                                    ))}
                                </div>
                            ))}
                        </div>
                    )}

                    {/* Single-model stats (original layout) */}
                    {!multiModel && (
                    <>
                    <div className="grid grid-cols-3 gap-4">
                        <div className="bg-surface-raised border border-main p-4 rounded-xl">
                            <div className="flex items-center gap-2 text-tertiary mb-2">
                                <Activity size={14} />
                                <span className="text-[11px] font-bold uppercase tracking-wider">Status</span>
                            </div>
                            <div className={`text-xl font-bold ${isSuccess ? 'text-emerald-400' : 'text-red-400'}`}>
                                {summary?.status || 'UNKNOWN'}
                            </div>
                        </div>
                        <div className="bg-surface-raised border border-main p-4 rounded-xl">
                            <div className="flex items-center gap-2 text-tertiary mb-2">
                                <Clock size={14} />
                                <span className="text-[11px] font-bold uppercase tracking-wider">Duration</span>
                            </div>
                            <div className="text-xl font-bold text-primary">
                                {summary?.duration_ms || 0}ms
                            </div>
                        </div>
                        <div className="bg-surface-raised border border-main p-4 rounded-xl">
                            <div className="flex items-center gap-2 text-tertiary mb-2">
                                <Info size={14} />
                                <span className="text-[11px] font-bold uppercase tracking-wider">Steps</span>
                            </div>
                            <div className="text-xl font-bold text-primary">
                                {summary?.last_successful_step || 0} / {summary?.total_steps || 10}
                            </div>
                        </div>
                    </div>

                    {/* Steps List */}
                    <div className="space-y-3">
                        <h3 className="text-sm font-bold text-secondary flex items-center gap-2">
                            Execution Steps
                        </h3>
                        <div className="border border-main rounded-xl overflow-hidden bg-surface-raised shadow-inner">
                            {summary?.steps_completed?.map((step, idx) => (
                                <div
                                    key={idx}
                                    className={`flex items-center gap-4 px-4 py-3 border-b border-main last:border-0 hover:bg-surface-hover transition-colors`}
                                >
                                    <div className="flex flex-col items-center">
                                        <div className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold ${step.status === 'success' ? 'bg-emerald-500/20 text-emerald-400' :
                                            step.status === 'skipped' ? 'bg-slate-500/20 text-slate-400' : 'bg-red-500/20 text-red-400'
                                            }`}>
                                            {step.status === 'success' ? '✓' : step.status === 'skipped' ? '○' : '✗'}
                                        </div>
                                        {idx !== summary.steps_completed.length - 1 && <div className="w-px h-4 bg-main my-0.5" />}
                                    </div>
                                    <div className="flex-1">
                                        <div className="flex items-center justify-between">
                                            <span className="text-xs font-bold text-primary">Step {step.step_number}: {step.step_name}</span>
                                            <span className="text-[10px] font-medium text-tertiary font-mono">{step.duration_ms}ms</span>
                                        </div>
                                        {step.message && <p className="text-[11px] text-tertiary mt-0.5">{step.message}</p>}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Errors */}
                    {summary?.errors?.length > 0 && (
                        <div className="space-y-3 animate-in slide-in-from-bottom-2 duration-300">
                            <h3 className="text-sm font-bold text-red-400 flex items-center gap-2">
                                <AlertTriangle size={16} /> Error Details
                            </h3>
                            <div className="border border-red-500/30 bg-red-500/5 rounded-xl p-4 space-y-2">
                                {summary.errors.map((err, idx) => (
                                    <div key={idx} className="text-xs">
                                        <div className="font-bold text-red-300">Step {err.step_number} ({err.step_name}):</div>
                                        <div className="mt-1 font-mono text-red-200/80 bg-black/20 p-2 rounded border border-red-500/20 whitespace-pre-wrap">
                                            {err.message}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                    </>
                    )}
                </div>

                {/* Footer */}
                <div className="px-6 py-4 bg-surface-raised border-t border-main flex justify-end gap-3 sticky bottom-0">
                    <button
                        onClick={onClose}
                        className="px-6 py-2 bg-accent-blue text-white rounded-lg text-sm font-bold shadow-lg shadow-accent-blue/20 hover:bg-accent-blue-hover active:scale-95 transition-all"
                    >
                        Done
                    </button>
                </div>
            </div>
        </div>
    );
};

export default function YamlEditor({ selectedItems = [], activeModelId = null, sourceType = 'fabric', targetType = 'snowflake', pbixFolder = '', importedPbixModel = null }) {
    const { theme } = useTheme();
    const { addLog } = useLogs();
    const [tabs, setTabs] = useState([{ id: 'semabridge.yaml', name: 'semabridge.yaml', active: true, modified: false, modelId: null }]);
    const [editorContent, setEditorContent] = useState('');
    const [errors, setErrors] = useState([]);
    const [warnings, setWarnings] = useState([]);
    const [isErrorOpen, setIsErrorOpen] = useState(false);
    const [tableWarning, setTableWarning] = useState(null);
    const [isSaving, setIsSaving] = useState(false);
    const [lastSavedVersionId, setLastSavedVersionId] = useState(null);
    const validationTimerRef = useRef(null);

    // Load initial config from API
    useEffect(() => {
        const loadConfig = async () => {
            try {
                const data = await api.getConfig();
                if (data.content) {
                    setEditorContent(data.content);
                }
            } catch (err) {
                console.error('Failed to load config:', err);
            }
        };
        loadConfig();
    }, []);

    // Load model from repository when activeModelId changes
    useEffect(() => {
        if (!activeModelId || sourceType !== 'repository') return;

        const loadModel = async () => {
            try {
                const data = await api.getModel(activeModelId);
                if (data.content) {
                    setEditorContent(data.content);
                    setLastSavedVersionId(null);

                    // Add or switch to tab for this model
                    setTabs(prev => {
                        const existing = prev.find(t => t.modelId === activeModelId);
                        if (existing) {
                            return prev.map(t => ({ ...t, active: t.modelId === activeModelId }));
                        }
                        return [
                            ...prev.map(t => ({ ...t, active: false })),
                            {
                                id: data.filename,
                                name: data.filename,
                                active: true,
                                modified: false,
                                modelId: activeModelId,
                            }
                        ];
                    });

                    addLog('info', 'Editor', `Loaded model: ${data.filename}`);
                }
            } catch (err) {
                console.error('Failed to load model:', err);
                addLog('error', 'Editor', `Failed to load model ${activeModelId}: ${err.message}`);
            }
        };
        loadModel();
    }, [activeModelId, sourceType]);

    useEffect(() => {
        if (sourceType !== 'pbix' || !importedPbixModel?.content) return;

        setEditorContent(importedPbixModel.content);
        setLastSavedVersionId(null);

        setTabs(prev => {
            const existing = prev.find(t => t.id === importedPbixModel.tabId);
            if (existing) {
                return prev.map(t => ({ ...t, active: t.id === importedPbixModel.tabId, modified: t.id === importedPbixModel.tabId }));
            }

            return [
                ...prev.map(t => ({ ...t, active: false })),
                {
                    id: importedPbixModel.tabId,
                    name: importedPbixModel.tabName,
                    active: true,
                    modified: true,
                    modelId: null,
                },
            ];
        });

        addLog('info', 'Editor', `Loaded PBIX model: ${importedPbixModel.tabName}`);
    }, [sourceType, importedPbixModel]);

    // Debounced validation (300ms)
    const debouncedValidate = useCallback((val) => {
        if (validationTimerRef.current) clearTimeout(validationTimerRef.current);
        validationTimerRef.current = setTimeout(async () => {
            try {
                const res = await api.validateConfig(val);
                const errs = (res.errors || []).filter(e => e.severity === 'error' || !e.severity);
                const warns = (res.errors || []).filter(e => e.severity === 'warning');
                setErrors(errs);
                setWarnings(warns);
                setIsErrorOpen(errs.length > 0);
            } catch (err) {
                console.error('Validation failed:', err);
            }
        }, 300);
    }, []);

    const extensions = useMemo(() => [yaml()], []);

    // Save model to local repo + create DuckDB version
    const handleSave = useCallback(async () => {
        const activeTab = tabs.find(t => t.active);
        const modelId = activeTab?.modelId;

        if (!modelId) {
            addLog('warning', 'Save', 'No model file is active. Switch to a repository model tab first.');
            return;
        }

        setIsSaving(true);
        try {
            const result = await api.saveModel(modelId, editorContent, 'Saved from UI');
            setLastSavedVersionId(result.version_id);
            setTabs(prev => prev.map(t => t.active ? { ...t, modified: false } : t));
            addLog('info', 'Save', `✔ Saved ${result.filename} → version ${result.version_id.substring(0, 8)}…`);
        } catch (err) {
            console.error('Save failed:', err);
            addLog('error', 'Save', `Failed to save model: ${err.message}`);
        } finally {
            setIsSaving(false);
        }
    }, [addLog, editorContent, tabs]);

    const handleGenerate = useCallback(async () => {
        if (!selectedItems || selectedItems.length === 0) {
            console.log("No models selected");
            return;
        }

        try {
            const data = await api.generateConfig({
                models: selectedItems,
                sourceType: sourceType,
                targetType: targetType,
                pbixFolder: pbixFolder
            });

            if (data?.content) {
                setEditorContent(data.content);

                setTabs(prev =>
                    prev.map(t =>
                        t.active ? { ...t, modified: true } : t
                    )
                );
            }
        } catch (err) {
            console.error("Generation failed:", err);
        }
    }, [pbixFolder, selectedItems, sourceType, targetType]);
    const activeTab = tabs.find(t => t.active);

    const [isDeploying, setIsDeploying] = useState(false);
    const [syncResult, setSyncResult] = useState(null);
    const [showModal, setShowModal] = useState(false);

    const handleDeploy = useCallback(async () => {
        // Block deploy if there are errors
        if (errors.length > 0) {
            addLog('error', 'Deploy', 'Cannot deploy: validation errors exist');
            return;
        }
        setIsDeploying(true);
        setTableWarning(null);
        addLog('info', 'Deploy', 'Starting deployment...');
        try {
            const envExecutor = String(import.meta.env.VITE_SYNC_EXECUTOR || '').trim().toLowerCase();
            const envMaxParallelModels = Number.parseInt(String(import.meta.env.VITE_MAX_PARALLEL_MODELS || ''), 10);
            const envMaxFabricJobs = Number.parseInt(String(import.meta.env.VITE_MAX_PARALLEL_FABRIC_JOBS || ''), 10);
            const envProcessMaxWorkers = Number.parseInt(String(import.meta.env.VITE_PROCESS_MAX_WORKERS || ''), 10);

            const syncPayload = { content: editorContent };
            if (envExecutor === 'thread' || envExecutor === 'process') {
                syncPayload.executor = envExecutor;
            }
            if (Number.isInteger(envMaxParallelModels) && envMaxParallelModels > 0) {
                syncPayload.max_parallel_models = envMaxParallelModels;
            }
            if (Number.isInteger(envMaxFabricJobs) && envMaxFabricJobs > 0) {
                syncPayload.max_parallel_fabric_jobs = envMaxFabricJobs;
            }
            if (Number.isInteger(envProcessMaxWorkers) && envProcessMaxWorkers > 0) {
                syncPayload.process_max_workers = envProcessMaxWorkers;
            }

            const res = await api.sync(syncPayload);
            console.log("Sync result:", res);
            setSyncResult(res);

            const succeeded = res.models_synced ?? (res.status === 'success' ? 1 : 0);
            const total = res.total_models ?? 1;

            // Check for TABLE_NOT_FOUND warnings
            const step9 = res.summary?.steps_completed?.find(s => s.step_number === 9);
            if (step9?.message?.toLowerCase().includes("warning")) {
                const warningMessage = step9.message;
                const match = warningMessage.match(/Table '([^']+)'/);
                const tableName = match ? match[1] : "Unknown table";
                setTableWarning(tableName);
                addLog('warning', 'Deploy', warningMessage);
            }

            if (res.status === 'success') {
                addLog('info', 'Deploy', `Deployment succeeded: ${succeeded}/${total} model(s) synced`);
            } else if (res.status === 'partial') {
                addLog('warning', 'Deploy', `Partial deployment: ${succeeded}/${total} model(s) succeeded`);
            } else {
                addLog('error', 'Deploy', `Deployment failed: ${res.summary?.errors?.[0]?.message || 'Unknown error'}`);
            }
            setShowModal(true);
        } catch (err) {
            console.error("Sync failed:", err);
            addLog('error', 'Deploy', `Sync failed: ${err.message}`);
            setSyncResult({ status: 'failed', summary: { errors: [{ message: err.message, step_number: 0, step_name: 'Network' }] } });
            setShowModal(true);
        } finally {
            setIsDeploying(false);
        }
    }, [addLog, editorContent, errors]);

    useEffect(() => {
        const handleGlobalSave = () => {
            handleSave();
        };
        const handleGlobalSync = () => {
            handleDeploy();
        };

        document.addEventListener('semabridge:save', handleGlobalSave);
        document.addEventListener('semabridge:sync', handleGlobalSync);

        return () => {
            document.removeEventListener('semabridge:save', handleGlobalSave);
            document.removeEventListener('semabridge:sync', handleGlobalSync);
        };
    }, [handleDeploy, handleSave]);

    return (
        <div className="flex-1 flex flex-col overflow-hidden h-full">
            {/* Tab bar */}
            <div
                className="flex items-center shrink-0 border-b border-main bg-surface-raised h-10"
            >
                <div className="flex-1 flex items-center h-full overflow-x-auto no-scrollbar">
                    {tabs.map(tab => (
                        <div
                            key={tab.id}
                            className={`flex items-center gap-2 px-4 h-full cursor-pointer border-r border-main relative theme-transition ${tab.active ? 'bg-surface text-primary border-t-2 border-t-accent-blue' : 'text-tertiary hover:bg-surface-hover hover:text-secondary'
                                }`}
                            onClick={() => setTabs(tabs.map(t => ({ ...t, active: t.id === tab.id })))}
                        >
                            <FileCode2 size={13} className={tab.active ? 'text-accent-blue' : 'text-tertiary'} />
                            <span className="text-[12px] font-medium whitespace-nowrap">
                                {tab.name}
                            </span>
                            {tab.modified && (
                                <div className="w-1.5 h-1.5 rounded-full bg-accent-blue ml-1" title="Unsaved changes" />
                            )}
                            {tab.active && !tab.modified && (
                                <X size={12} className="ml-1 opacity-0 group-hover:opacity-100 hover:bg-surface-hover rounded" />
                            )}
                        </div>
                    ))}
                </div>

                <div className="flex items-center gap-1.5 px-3 h-full border-l border-main bg-surface-raised">
                    <button
                        onClick={handleGenerate}
                        disabled={selectedItems.length === 0}
                        className={`flex items-center gap-1.5 px-3 h-7 rounded-md text-[11px] font-bold uppercase tracking-tight theme-transition border ${selectedItems.length > 0
                            ? 'bg-accent-blue/10 text-accent-blue border-accent-blue/30 hover:bg-accent-blue/20'
                            : 'text-tertiary border-main cursor-not-allowed opacity-50'
                            }`}
                    >
                        <Zap size={12} fill="currentColor" />
                        Generate
                    </button>
                    <button
                        onClick={handleDeploy}
                        disabled={isDeploying || errors.length > 0}
                        className={`flex items-center gap-1.5 px-3 h-7 rounded-md text-[11px] font-bold uppercase tracking-tight theme-transition border ${isDeploying
                            ? 'bg-accent-blue/50 text-white border-accent-blue/50 cursor-wait'
                            : errors.length > 0
                                ? 'bg-red-500/20 text-red-400 border-red-500/30 cursor-not-allowed'
                                : 'bg-accent-blue text-white border-accent-blue hover:bg-accent-blue-hover shadow-sm active:translate-y-px'
                            }`}
                    >
                        {isDeploying ? (
                            <div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                        ) : (
                            <Play size={12} fill="currentColor" />
                        )}
                        {isDeploying ? 'Deploying...' : 'Deploy'}
                    </button>
                    <div className="w-px h-5 bg-border-main mx-1" />
                    <button
                        onClick={handleSave}
                        disabled={isSaving || !activeTab?.modelId}
                        title={activeTab?.modelId ? `Save ${activeTab.name} + create version` : 'No model file active'}
                        className={`flex items-center gap-1 p-1.5 rounded transition-all ${isSaving ? 'text-accent-blue animate-pulse'
                            : activeTab?.modelId ? 'text-secondary hover:bg-surface-hover hover:text-accent-blue'
                                : 'text-tertiary opacity-40 cursor-not-allowed'
                            }`}
                    >
                        <Save size={14} />
                        {lastSavedVersionId && (
                            <span className="text-[9px] text-emerald-400 font-mono">v{lastSavedVersionId.substring(0, 6)}</span>
                        )}
                    </button>
                    <button className="p-1.5 rounded hover:bg-surface-hover text-secondary"><MoreHorizontal size={14} /></button>
                </div>
            </div>

            {/* Breadcrumb / Editor Header */}
            <div className="flex items-center justify-between px-4 h-8 border-b border-main bg-surface text-[11px] font-medium text-tertiary">
                <div className="flex items-center gap-1">
                    <span className="hover:text-secondary cursor-pointer">semabridge</span>
                    <span className="opacity-40">›</span>
                    <span className="text-secondary">{activeTab?.name}</span>
                    {activeTab?.modified && <span className="ml-2 text-accent-blue font-bold italic opacity-80">(modified)</span>}
                </div>
                <div className="flex items-center gap-3">
                    {errors.length > 0 ? (
                        <div className="flex items-center gap-1 text-red-400">
                            <span className="w-2 h-2 rounded-full bg-red-400 shadow-[0_0_8px_rgba(248,81,73,0.5)]" />
                            🔴 {errors.length} Error{errors.length > 1 ? 's' : ''}
                        </div>
                    ) : warnings.length > 0 ? (
                        <div className="flex items-center gap-1 text-amber-400">
                            <span className="w-2 h-2 rounded-full bg-amber-400 shadow-[0_0_8px_rgba(245,158,11,0.5)]" />
                            🟡 {warnings.length} Warning{warnings.length > 1 ? 's' : ''}
                        </div>
                    ) : (
                        <div className="flex items-center gap-1 text-color-success">
                            <CheckCircle2 size={12} /> Live Validation: Clean
                        </div>
                    )}
                </div>
            </div>
            {/* TABLE_NOT_FOUND Warning Banner */}
            {tableWarning && (
                <div className="mx-4 mt-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-start gap-2">
                    <AlertTriangle size={14} className="text-amber-400 shrink-0 mt-0.5" />
                    <div className="flex-1">
                        <p className="text-[11px] font-bold text-amber-400">⚠️ Missing Table Warning</p>
                        <p className="text-[10px] text-amber-300/80 mt-0.5">
                            Table <strong>{tableWarning}</strong> does not exist in the selected source. The semantic model will be created, but queries may fail until the table is available.
                        </p>
                    </div>
                    <button onClick={() => setTableWarning(null)} className="text-amber-400 hover:text-amber-300 shrink-0">
                        <X size={12} />
                    </button>
                </div>
            )}

            {/* Editor */}
            <div className="flex-1 min-h-0 relative">
                <CodeMirror
                    value={editorContent}
                    height="100%"
                    theme={theme === 'dark' ? 'dark' : 'light'}
                    extensions={extensions}
                    onChange={(val) => {
                        setEditorContent(val);
                        debouncedValidate(val);
                    }}
                    basicSetup={{
                        lineNumbers: true,
                        foldGutter: true,
                        dropCursor: true,
                        allowMultipleSelections: true,
                        indentOnInput: true,
                        highlightActiveLine: true,
                        scrollPastEnd: true,
                    }}
                    className="h-full text-[13px] font-mono"
                />
            </div>

            {/* Error Panel */}
            <ErrorPanel
                errors={errors}
                isOpen={isErrorOpen}
                onToggle={() => setIsErrorOpen(!isErrorOpen)}
            />

            {/* Deployment Summary Modal */}
            <DeploySummaryModal
                isOpen={showModal}
                onClose={() => setShowModal(false)}
                summary={syncResult?.summary}
                status={syncResult?.status}
                results={syncResult?.results}
            />
        </div>
    );
}