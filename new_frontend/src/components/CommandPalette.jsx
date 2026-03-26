import { useState, useEffect, useRef, useMemo } from 'react';
import {
    Search,
    Map,
    GitCommit,
    Bell,
    Settings,
    RefreshCw,
    Save,
    PanelBottom,
    FileCode2,
    Layers,
    Command,
} from 'lucide-react';

const COMMANDS = [
    { id: 'repo-map', label: 'Open Repository Map', icon: Map, action: 'toggleRepoMap', category: 'View' },
    { id: 'version', label: 'Version Control', icon: GitCommit, action: 'toggleVersionControl', category: 'View' },
    { id: 'logs', label: 'Logs & Activity', icon: Bell, action: 'toggleLogs', category: 'View' },
    { id: 'terminal', label: 'Toggle Terminal', icon: PanelBottom, action: 'toggleTerminal', category: 'View' },
    { id: 'settings', label: 'Open Settings', icon: Settings, action: 'toggleConnections', category: 'Settings' },
    { id: 'sync', label: 'Sync Models', icon: RefreshCw, action: 'sync', category: 'Project' },
    { id: 'save', label: 'Save Configuration', icon: Save, action: 'save', category: 'File' },
    { id: 'export', label: 'Export SML', icon: FileCode2, action: 'export', category: 'File' },
];

export default function CommandPalette({ isOpen, onClose, onAction, models = [] }) {
    const [query, setQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef(null);

    useEffect(() => {
        if (isOpen) {
            setQuery('');
            setSelectedIndex(0);
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [isOpen]);

    // Global Ctrl+K shortcut
    useEffect(() => {
        const handler = (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                if (isOpen) onClose();
                else onAction?.('openPalette');
            }
            if (e.key === 'Escape' && isOpen) {
                onClose();
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [isOpen, onClose, onAction]);

    const results = useMemo(() => {
        const q = query.toLowerCase().trim();
        const items = [];

        // Search models
        if (q) {
            const matchedModels = models
                .filter(m => m.name?.toLowerCase().includes(q) || m.id?.toLowerCase().includes(q))
                .slice(0, 5)
                .map(m => ({
                    id: `model-${m.id}`,
                    label: m.name || m.id,
                    icon: Layers,
                    action: 'openModel',
                    payload: m.id,
                    category: 'Models',
                }));
            items.push(...matchedModels);
        }

        // Search commands
        const matchedCommands = COMMANDS.filter(c =>
            !q || c.label.toLowerCase().includes(q) || c.category.toLowerCase().includes(q)
        );
        items.push(...matchedCommands);

        return items;
    }, [query, models]);

    useEffect(() => {
        setSelectedIndex(0);
    }, [query]);

    const handleSelect = (item) => {
        onClose();
        if (item.action === 'openModel') {
            onAction?.('openModel', item.payload);
        } else {
            onAction?.(item.action);
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setSelectedIndex(i => Math.min(i + 1, results.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setSelectedIndex(i => Math.max(i - 1, 0));
        } else if (e.key === 'Enter' && results[selectedIndex]) {
            e.preventDefault();
            handleSelect(results[selectedIndex]);
        }
    };

    if (!isOpen) return null;

    // Group results by category
    const grouped = {};
    results.forEach(r => {
        if (!grouped[r.category]) grouped[r.category] = [];
        grouped[r.category].push(r);
    });

    let flatIndex = 0;

    return (
        <div
            className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh]"
            style={{ background: 'var(--bg-backdrop)' }}
            onClick={onClose}
        >
            <div
                className="w-full max-w-lg rounded-xl overflow-hidden shadow-2xl border border-main"
                style={{ background: 'var(--bg-surface)', boxShadow: 'var(--shadow-lg)' }}
                onClick={e => e.stopPropagation()}
            >
                {/* Search Input */}
                <div className="flex items-center gap-3 px-4 py-3 border-b border-main">
                    <Search size={16} className="text-tertiary shrink-0" />
                    <input
                        ref={inputRef}
                        type="text"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Search models, commands..."
                        className="flex-1 bg-transparent border-none outline-none text-sm text-primary placeholder:text-tertiary"
                    />
                    <kbd className="px-1.5 py-0.5 rounded bg-surface-raised border border-main text-[9px] font-mono text-tertiary">ESC</kbd>
                </div>

                {/* Results */}
                <div className="max-h-[300px] overflow-y-auto custom-scrollbar py-1">
                    {results.length === 0 ? (
                        <div className="px-4 py-6 text-center text-sm text-tertiary">No results found</div>
                    ) : (
                        Object.entries(grouped).map(([category, items]) => (
                            <div key={category}>
                                <div className="px-4 pt-2 pb-1 text-[10px] font-bold uppercase tracking-wider text-tertiary">{category}</div>
                                {items.map((item) => {
                                    const idx = flatIndex++;
                                    const Icon = item.icon;
                                    return (
                                        <button
                                            key={item.id}
                                            className={`w-full flex items-center gap-3 px-4 py-2 text-xs text-left transition-colors outline-none border-none cursor-pointer ${
                                                idx === selectedIndex ? 'bg-surface-raised text-primary' : 'text-secondary'
                                            }`}
                                            style={idx === selectedIndex ? { background: 'var(--bg-surface-hover)' } : { background: 'transparent' }}
                                            onMouseEnter={() => setSelectedIndex(idx)}
                                            onClick={() => handleSelect(item)}
                                        >
                                            <Icon size={14} className="opacity-60 shrink-0" />
                                            <span className="font-medium flex-1">{item.label}</span>
                                            {item.category && <span className="text-[10px] text-tertiary">{item.category}</span>}
                                        </button>
                                    );
                                })}
                            </div>
                        ))
                    )}
                </div>

                {/* Footer Hint */}
                <div className="flex items-center gap-4 px-4 py-2 border-t border-main text-[10px] text-tertiary">
                    <span>↑↓ Navigate</span>
                    <span>↵ Select</span>
                    <span>ESC Close</span>
                </div>
            </div>
        </div>
    );
}
