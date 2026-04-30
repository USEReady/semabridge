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
    // Remove showSyncBar, always render the bar
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

    // No-op: always show the bar, only sync status is conditional

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


    const latestSuccessfulRun = runs.find((run) => {
        const status = String(run?.status || '').toLowerCase();
        return status === 'success';
    });

    let lastSyncLabel = 'Never';
    if (latestSuccessfulRun) {
        const ts = latestSuccessfulRun.completed_at || latestSuccessfulRun.started_at;
        if (ts) {
            const dateStr = ts.includes('Z') || ts.includes('+') ? ts : `${ts}Z`;
            const d = new Date(dateStr);
            if (!isNaN(d.getTime())) {
                lastSyncLabel = d.toLocaleString('en-IN', {
                    timeZone: 'Asia/Kolkata',
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: true
                });
            }
        }
    }


    const safeProgress = progress ?? 0;
    const isSyncing = (currentSyncStatus === 'running' || currentSyncStatus === 'starting' || currentSyncStatus === 'pending') && currentSyncId;
    const isStarting = safeProgress < 5;
    const transitionStyle = isStarting ? 'none' : 'width 1s linear';

    // The bar is always visible; only the sync progress section is conditional
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
                {isSyncing && (
                    <div className="flex items-center gap-3">
                        <span className="text-indigo-400 font-bold animate-pulse inline-flex items-center gap-1">
                            <RefreshCw size={11} className="animate-spin" />
                            Sync in progress
                        </span>
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
