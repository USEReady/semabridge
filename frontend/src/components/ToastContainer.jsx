import { X, AlertTriangle, Info, AlertCircle, CheckCircle2, ChevronRight } from 'lucide-react';
import { useLogs } from '../context/LogsContext';

export default function ToastContainer({ onOpenLogs }) {
    const { toasts, dismissToast } = useLogs();

    if (toasts.length === 0) return null;

    return (
        <div className="fixed top-16 right-4 z-[100] flex flex-col gap-2 max-w-sm pointer-events-none">
            {toasts.map(toast => (
                <div
                    key={toast.id}
                    className="pointer-events-auto flex items-start gap-3 px-4 py-3 rounded-xl border shadow-2xl backdrop-blur-lg animate-in slide-in-from-right-5 duration-300"
                    style={{
                        background: toast.severity === 'error'
                            ? 'rgba(239,68,68,0.1)'
                            : toast.severity === 'success'
                                ? 'rgba(16,185,129,0.1)'
                            : toast.severity === 'warning'
                                ? 'rgba(245,158,11,0.1)'
                                : 'rgba(59,130,246,0.1)',
                        borderColor: toast.severity === 'error'
                            ? 'rgba(239,68,68,0.3)'
                            : toast.severity === 'success'
                                ? 'rgba(16,185,129,0.3)'
                            : toast.severity === 'warning'
                                ? 'rgba(245,158,11,0.3)'
                                : 'rgba(59,130,246,0.3)',
                    }}
                >
                    <div className="shrink-0 mt-0.5">
                        {toast.severity === 'error' && <AlertCircle size={18} className="text-red-400" />}
                        {toast.severity === 'success' && <CheckCircle2 size={18} className="text-emerald-400" />}
                        {toast.severity === 'warning' && <AlertTriangle size={18} className="text-amber-400" />}
                        {toast.severity === 'info' && <Info size={18} className="text-blue-400" />}
                    </div>
                    <div className="flex-1 min-w-0">
                        <p className="text-xs font-bold text-primary capitalize">{toast.severity}</p>
                        <p
                            className="text-[11px] text-secondary mt-0.5"
                            style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 220, overflowY: 'auto' }}
                        >
                            {toast.message}
                        </p>
                        <button
                            onClick={() => { dismissToast(toast.id); if (onOpenLogs) onOpenLogs(); }}
                            className="text-[10px] text-accent-blue font-bold mt-1 flex items-center gap-0.5 hover:underline"
                        >
                            View Details <ChevronRight size={10} />
                        </button>
                    </div>
                    <button
                        onClick={() => dismissToast(toast.id)}
                        className="shrink-0 p-1 rounded hover:bg-surface-hover text-tertiary"
                    >
                        <X size={14} />
                    </button>
                </div>
            ))}
        </div>
    );
}
