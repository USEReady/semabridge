import { AlertTriangle, X, ChevronDown, ChevronUp } from 'lucide-react';

export default function ErrorPanel({ errors, isOpen, onToggle }) {
    if (errors.length === 0) return null;

    return (
        <div
            className="theme-transition shrink-0 overflow-hidden"
            style={{
                background: 'var(--bg-surface-raised)',
                borderTop: '2px solid var(--color-danger)',
                height: isOpen ? '160px' : '32px',
                transition: 'height var(--transition-normal)'
            }}
        >
            {/* Header */}
            <div
                className="flex items-center justify-between px-3 h-8 cursor-pointer select-none"
                onClick={onToggle}
            >
                <div className="flex items-center gap-2">
                    <AlertTriangle size={14} className="text-red-500" />
                    <span className="text-[11px] font-bold text-red-500 uppercase tracking-wider">
                        Validation Errors ({errors.length})
                    </span>
                </div>
                <div className="flex items-center gap-2">
                    {isOpen ? <ChevronDown size={14} className="text-slate-500" /> : <ChevronUp size={14} className="text-slate-500" />}
                </div>
            </div>

            {/* Content */}
            {isOpen && (
                <div className="px-3 pb-3 overflow-y-auto h-[128px] custom-scrollbar">
                    <div className="space-y-1 mt-1">
                        {errors.map((err, i) => (
                            <div
                                key={i}
                                className="flex items-start gap-2 p-2 rounded bg-red-500/5 border border-red-500/10"
                            >
                                <span className="text-[10px] font-mono font-bold text-red-400 bg-red-400/10 px-1 rounded">
                                    Line {err.line}
                                </span>
                                <p className="text-[11px] text-secondary leading-normal">
                                    {err.message}
                                </p>
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
