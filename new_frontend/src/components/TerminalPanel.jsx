import { Terminal, X, ChevronUp, ChevronDown, Filter, Trash2 } from 'lucide-react';
import { useState } from 'react';

export default function TerminalPanel() {
    const [isOpen, setIsOpen] = useState(true);
    const [messages, setMessages] = useState([
        { type: 'info', text: 'Vite v7.3.1 ready in 5695 ms', time: '18:15:03' },
        { type: 'info', text: '➜ Local: http://localhost:5173/', time: '18:15:03' },
        { type: 'success', text: 'FastAPI backend connected at http://localhost:8000', time: '18:16:12' },
        { type: 'warning', text: 'Snowflake connection established with read-only permissions.', time: '18:20:45' },
    ]);

    if (!isOpen) {
        return (
            <div
                className="h-8 flex items-center px-4 bg-surface border-t border-main cursor-pointer hover:bg-surface-hover transition-colors"
                style={{ background: 'var(--bg-surface)', borderTop: '1px solid var(--border-main)' }}
                onClick={() => setIsOpen(true)}
            >
                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-slate-500">
                    <Terminal size={12} />
                    Terminal
                </div>
                <div className="flex-1" />
                <ChevronUp size={14} className="text-slate-500" />
            </div>
        );
    }

    return (
        <div
            className="flex flex-col border-t border-main"
            style={{
                height: 180,
                background: 'var(--bg-surface)',
                borderTop: '1px solid var(--border-main)'
            }}
        >
            {/* Header */}
            <div className="flex items-center justify-between px-3 h-9 border-b border-white/5 bg-black/10">
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-indigo-400">
                        <Terminal size={12} />
                        Terminal
                    </div>
                    <div className="flex items-center gap-3">
                        <button className="text-[11px] font-medium text-secondary hover:text-primary transition-colors">Output</button>
                        <button className="text-[11px] font-medium text-tertiary hover:text-primary transition-colors">Problems</button>
                        <button className="text-[11px] font-medium text-tertiary hover:text-primary transition-colors">Debug Console</button>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button className="p-1 hover:bg-surface-hover rounded text-tertiary"><Filter size={13} /></button>
                    <button className="p-1 hover:bg-surface-hover rounded text-tertiary" onClick={() => setMessages([])}><Trash2 size={13} /></button>
                    <button className="p-1 hover:bg-surface-hover rounded text-tertiary" onClick={() => setIsOpen(false)}><X size={13} /></button>
                </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-3 font-mono text-[12px] custom-scrollbar">
                {messages.length === 0 ? (
                    <div className="text-tertiary italic">No output...</div>
                ) : messages.map((m, i) => (
                    <div key={i} className="flex gap-3 mb-1 group">
                        <span className="text-tertiary shrink-0">[{m.time}]</span>
                        <span className={`
                            ${m.type === 'error' ? 'text-red-400' : ''}
                            ${m.type === 'warning' ? 'text-yellow-400' : ''}
                            ${m.type === 'success' ? 'text-emerald-400' : ''}
                            ${m.type === 'info' ? 'text-blue-400' : ''}
                        `}>
                            {m.text}
                        </span>
                    </div>
                ))}
            </div>
        </div>
    );
}
