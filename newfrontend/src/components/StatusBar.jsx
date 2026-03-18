import { useState, useEffect } from 'react';
import {
    Wifi,
    WifiOff,
    Database,
    HardDrive,
    Clock,
    GitBranch,
    AlertTriangle,
    CheckCircle2,
    Radio,
} from 'lucide-react';
import { api } from '../utils/api';
import { useLogs } from '../context/LogsContext';

export default function StatusBar() {
    const [connected, setConnected] = useState(false);
    const [isSyncing, setIsSyncing] = useState(false);
    const [progress, setProgress] = useState(0);
    const { wsConnected } = useLogs();

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
        // Simulated sync for UI demonstration (PyQt parity)
        if (connected) {
            const timeout = setTimeout(() => {
                setIsSyncing(true);
                const progressInterval = setInterval(() => {
                    setProgress(p => {
                        if (p >= 100) {
                            clearInterval(progressInterval);
                            setTimeout(() => setIsSyncing(false), 1000);
                            return 100;
                        }
                        return p + 2;
                    });
                }, 50);
            }, 5000);
            return () => clearTimeout(timeout);
        }
    }, [connected]);

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
                        {connected ? 'Fabric Connected' : 'API Unreachable'}
                    </span>
                </div>

                <div className="w-px h-3.5 bg-white/5" />

                <div className="flex items-center gap-1">
                    <Database size={11} className="opacity-50" />
                    <span>Snowflake:</span>
                    <span className="text-emerald-500 font-bold uppercase text-[9px]">OK</span>
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
                        <span className="text-indigo-400 font-bold animate-pulse">Syncing Models...</span>
                        <div className="w-32 h-1.5 bg-indigo-500/10 rounded-full overflow-hidden border border-white/5">
                            <div
                                className="h-full bg-indigo-500 transition-all duration-300 ease-out"
                                style={{ width: `${progress}%` }}
                            />
                        </div>
                        <span className="text-indigo-400 font-mono w-8">{progress}%</span>
                    </div>
                )}
            </div>

            {/* Right section */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-1">
                    <GitBranch size={11} className="text-slate-500" />
                    <span className="font-mono">main</span>
                </div>

                <div className="flex items-center gap-1">
                    <CheckCircle2 size={11} className="text-emerald-500" />
                    <span className="text-slate-400">22 Models Valid</span>
                </div>

                <div className="w-px h-3.5 bg-white/5" />

                <div className="flex items-center gap-1">
                    <Clock size={11} className="text-slate-500" />
                    <span>Last sync: 2 min ago</span>
                </div>
            </div>
        </footer>
    );
}
