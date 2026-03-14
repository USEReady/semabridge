import { useState } from 'react';
import {
    X,
    AlertTriangle,
    AlertCircle,
    Info,
    Trash2,
    Filter,
    List,
} from 'lucide-react';
import { useLogs } from '../context/LogsContext';

const SEVERITY_CONFIG = {
    info: { icon: Info, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20' },
    warning: { icon: AlertTriangle, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20' },
    error: { icon: AlertCircle, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20' },
};

export default function LogsPanel({ isOpen, onClose }) {
    const { logs, clearLogs } = useLogs();
    const [filters, setFilters] = useState({ info: true, warning: true, error: true });

    if (!isOpen) return null;

    const toggleFilter = (key) => setFilters(prev => ({ ...prev, [key]: !prev[key] }));

    const filtered = logs.filter(l => filters[l.severity]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            {/* Backdrop */}
            <div className="absolute inset-0 backdrop-blur-sm" style={{ background: 'var(--bg-backdrop)' }} onClick={onClose} />

            {/* Popup Modal */}
            <div className="relative w-full max-w-4xl max-h-[85vh] bg-surface border border-main rounded-xl flex flex-col shadow-2xl overflow-hidden">
                {/* Header */}
                <div className="flex items-center justify-between px-5 h-14 border-b border-main bg-surface-raised">
                    <div className="flex items-center gap-2">
                        <List size={16} className="text-accent-blue" />
                        <h2 className="text-sm font-bold text-primary">Logs & Activity</h2>
                        <span className="ml-2 px-2 py-0.5 rounded-full bg-accent-blue/10 text-accent-blue text-[10px] font-bold">
                            {filtered.length}
                        </span>
                    </div>
                    <div className="flex items-center gap-2">
                        <button onClick={clearLogs} className="p-1.5 rounded hover:bg-surface-hover text-tertiary" title="Clear logs">
                            <Trash2 size={14} />
                        </button>
                        <button onClick={onClose} className="p-1.5 rounded hover:bg-surface-hover text-tertiary">
                            <X size={16} />
                        </button>
                    </div>
                </div>

                {/* Filter Bar */}
                <div className="flex items-center gap-2 px-5 py-2 border-b border-main bg-surface-raised">
                    <Filter size={12} className="text-tertiary" />
                    {Object.entries(SEVERITY_CONFIG).map(([key, cfg]) => (
                        <button
                            key={key}
                            onClick={() => toggleFilter(key)}
                            className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider transition-all border ${filters[key]
                                    ? `${cfg.bg} ${cfg.color} ${cfg.border}`
                                    : 'border-transparent text-tertiary opacity-50'
                                }`}
                        >
                            <cfg.icon size={11} />
                            {key}
                        </button>
                    ))}
                </div>

                {/* Log Entries */}
                <div className="flex-1 overflow-y-auto custom-scrollbar">
                    {filtered.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-full text-tertiary">
                            <List size={40} className="opacity-20 mb-3" />
                            <p className="text-sm font-medium">No log entries</p>
                            <p className="text-xs mt-1">Activity will appear here</p>
                        </div>
                    ) : (
                        <table className="w-full text-xs">
                            <thead className="sticky top-0 bg-surface-raised border-b border-main">
                                <tr>
                                    <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary w-24">Time</th>
                                    <th className="text-left px-2 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary w-20">Severity</th>
                                    <th className="text-left px-2 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary w-28">Source</th>
                                    <th className="text-left px-2 py-2.5 text-[10px] font-bold uppercase tracking-wider text-tertiary">Message</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filtered.map(log => {
                                    const cfg = SEVERITY_CONFIG[log.severity] || SEVERITY_CONFIG.info;
                                    const Icon = cfg.icon;
                                    return (
                                        <tr key={log.id} className="border-b border-main hover:bg-surface-hover transition-colors">
                                            <td className="px-4 py-2.5 text-tertiary font-mono whitespace-nowrap">{log.timestamp}</td>
                                            <td className="px-2 py-2.5">
                                                <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${cfg.bg} ${cfg.color} text-[10px] font-bold uppercase`}>
                                                    <Icon size={10} />
                                                    {log.severity}
                                                </span>
                                            </td>
                                            <td className="px-2 py-2.5 text-secondary font-medium">{log.source}</td>
                                            <td className="px-2 py-2.5 text-primary max-w-xs truncate">{log.message}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>
            </div>
        </div>
    );
}
