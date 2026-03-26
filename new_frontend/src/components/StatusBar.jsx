import { useState, useEffect, useRef } from 'react';
import {
    Clock,
    Radio,
    RefreshCw,
} from 'lucide-react';
import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';
import useProjectSync from '../hooks/useProjectSync';
import { useSyncStatusStore } from '../context/SyncStatusContext';

function extractRunError(run) {
    const direct = [run?.error_message, run?.error].filter(Boolean).join(' | ');
    if (direct) return direct;

    const summaryErrors = run?.summary?.errors;
    if (Array.isArray(summaryErrors) && summaryErrors.length > 0) {
        return String(summaryErrors[0]?.message || summaryErrors[0]?.error_type || '').trim();
    }

    const nestedErrors = run?.results?.[0]?.summary?.errors;
    if (Array.isArray(nestedErrors) && nestedErrors.length > 0) {
        return String(nestedErrors[0]?.message || nestedErrors[0]?.error_type || '').trim();
    }

    return '';
}

export default function StatusBar() {
    const [connected, setConnected] = useState(false);
    const { wsConnected, addLog } = useLogs();
    const seenStatusRef = useRef(new Map());
    const { runs, currentSyncId } = useSyncStatusStore();
    const {
        status: currentSyncStatus,
        progress,
        indeterminate,
        warning,
    } = useProjectSync();

    // Replicating PyQt ProgressBar behavior + Health Checking
    useEffect(() => {
        const checkHealth = async () => {
            try {
                const res = await api.getHealth();
                setConnected(res.status === 'ok');
            } catch (err) {
                setConnected(false);
            }
        };

        checkHealth();
        const interval = setInterval(checkHealth, 5000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        runs.forEach((run) => {
            const runId = String(run?.run_id || run?.id || '');
            if (!runId) return;
            const status = String(run?.status || '').toLowerCase();
            const previous = seenStatusRef.current.get(runId);
            if (previous === 'running' && status && status !== 'running') {
                const projectName = run?.project_name || run?.project_id || 'project';
                if (status === 'success') {
                    addLog('success', 'Sync', `${projectName} sync completed successfully.`);
                } else if (status === 'warning' || status === 'partial') {
                    addLog('warning', 'Sync', `${projectName} sync completed with warnings.`);
                } else {
                    const reason = extractRunError(run);
                    addLog('error', 'Sync', `${projectName} sync failed${reason ? `: ${reason}` : '.'}`);
                }
            }
            seenStatusRef.current.set(runId, status);
        });
    }, [runs, addLog]);

    const isSyncing = currentSyncStatus === 'running' && currentSyncId;

    const latestCompletedRun = runs.find((run) => {
        const status = String(run?.status || '').toLowerCase();
        return status && status !== 'running';
    });

    const lastSyncTimestamp = latestCompletedRun?.completed_at || latestCompletedRun?.started_at;
    const lastSyncLabel = lastSyncTimestamp
        ? new Date(lastSyncTimestamp).toLocaleString()
        : 'Never';

    // TASK 2 & 3: Guard progress and disable transitions for starting state
    const safeProgress = progress ?? 0;
    const isStarting = safeProgress < 5;
    const transitionStyle = isStarting ? 'none' : 'width 1s linear';

    return (
        <footer
            className="theme-transition flex items-center justify-between px-4 h-8 shrink-0 text-[11px] select-none"
            style={{
                background: 'var(--bg-surface)',
                borderTop: '1px solid var(--border-main)',
                color: 'var(--text-secondary)',
                zIndex: 50,
            }}
        >
            {/* Left section */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5">
                    <div className="relative flex items-center justify-center w-4 h-4">
                        <span
                            className={`absolute w-2 h-2 rounded-full ${connected ? 'status-pulse status-pulse-success' : 'status-pulse status-pulse-danger'}`}
                        />
                        <span
                            className="relative w-2 h-2 rounded-full"
                            style={{ background: connected ? 'var(--color-success)' : 'var(--color-danger)' }}
                        />
                    </div>
                    <span className="flex items-center gap-1 font-medium" style={{ color: connected ? 'var(--color-success)' : 'var(--color-danger)' }}>
                        {connected ? 'API Connected' : 'API Unreachable'}
                    </span>
                </div>

                <div className="w-px h-3.5 bg-white/5" />

                <div className="flex items-center gap-1">
                    <Radio size={11} className={wsConnected ? 'text-emerald-400' : 'text-slate-500'} />
                    <span>Alerts:</span>
                    <span className={`font-bold uppercase text-[9px] ${wsConnected ? 'text-emerald-500' : 'text-amber-500'}`}>
                        {wsConnected ? 'LIVE' : 'OFF'}
                    </span>
                </div>

                {isSyncing && (
                    <div className="flex items-center gap-3 ml-4">
                        <span className="text-indigo-400 font-bold animate-pulse inline-flex items-center gap-1">
                            <RefreshCw size={11} className="animate-spin" />
                            Sync in progress
                        </span>
                        <div className="w-32 h-1.5 bg-indigo-500/10 rounded-full overflow-hidden border border-white/5">
                            <div
                                className={indeterminate ? 'h-full sync-progress-zebra' : 'h-full bg-indigo-500'}
                                data-is-starting={isStarting}
                                style={{
                                    width: `${safeProgress}%`,
                                    transition: transitionStyle,
                                }}
                            />
                        </div>
                        <span className="text-indigo-400 font-mono w-8">{safeProgress}%</span>
                        {warning && <span className="text-amber-400">{warning}</span>}
                    </div>
                )}
            </div>

            {/* Right section */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-1">
                    <Clock size={11} className="text-slate-500" />
                    <span>Last sync: {lastSyncLabel}</span>
                </div>
            </div>
        </footer>
    );
}
