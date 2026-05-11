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
import { getSmartQueryError, matchesSmartQuery } from './common/smartSearchQuery.js';

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

export default function CommandPalette({
    isOpen,
    onClose,
    onAction,
    models = [],
    queryValue,
    onQueryChange,
}) {
    const [query, setQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef(null);

    const queryText = queryValue ?? query;
    const setQueryText = onQueryChange ?? setQuery;

    useEffect(() => {
        if (isOpen) {
            setSelectedIndex(0);
            if (!onQueryChange) setQuery('');
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [isOpen, onQueryChange]);

    const regexError = useMemo(() => {
        return getSmartQueryError(queryText);
    }, [queryText]);

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
        const q = String(queryText || '').trim();
        const items = [];

        const match = (text) => {
            if (!q) return true;
            if (regexError) return false;
            return matchesSmartQuery(text, q);
        };

        // Search models
        if (q) {
            const matchedModels = models
                .filter(m => match(`${m.name || ''} ${m.id || ''}`))
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
            !q || match(`${c.label || ''} ${c.category || ''}`)
        );
        items.push(...matchedCommands);

        return items;
    }, [queryText, models, regexError]);

    useEffect(() => {
        setSelectedIndex(0);
    }, [queryText]);

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
            style={{ background: 'var(--bg-backdrop)', backdropFilter: 'blur(8px)' }}
            onClick={onClose}
        >
            <div
                className="w-full max-w-2xl rounded-2xl overflow-hidden shadow-2xl border border-main"
                style={{
                    background: 'color-mix(in srgb, var(--bg-surface) 92%, transparent)',
                    boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.5), 0 0 0 1px var(--border-main)',
                    backdropFilter: 'blur(16px)',
                }}
                onClick={e => e.stopPropagation()}
            >
                {/* Search Input */}
                <div className="flex items-center gap-4 px-6 py-6 border-b border-main" style={{ background: 'rgba(255,255,255,0.04)' }}>
                    <Search size={22} className="shrink-0" style={{ color: 'var(--accent-blue)' }} />
                    <div className="flex-1 relative flex items-center">
                        <input
                            ref={inputRef}
                            type="text"
                            value={queryText}
                            onChange={e => setQueryText(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="Search models, commands..."
                            className="w-full outline-none px-5 py-3 rounded-xl transition-all font-medium"
                            style={{
                                background: 'color-mix(in srgb, var(--bg-surface-raised) 70%, transparent)',
                                border: '1px solid var(--border-main)',
                                color: 'var(--text-primary)',
                                fontSize: 18,
                            }}
                        />
                    </div>
                    <kbd className="px-1.5 py-0.5 rounded bg-surface-raised border border-main text-[9px] font-mono text-tertiary">ESC</kbd>
                </div>
                {regexError && (
                    <div className="px-4 py-1 text-[10px]" style={{ color: 'var(--color-error)', borderBottom: '1px solid var(--border-main)' }}>
                        Regex error: {regexError}
                    </div>
                )}

                {/* Results */}
                <div className="max-h-[450px] overflow-y-auto custom-scrollbar py-2">
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
                                            className={`w-full flex items-center gap-4 px-6 py-3 text-sm text-left transition-all outline-none border-none cursor-pointer ${
                                                idx === selectedIndex ? 'text-primary' : 'text-secondary'
                                            }`}
                                            style={idx === selectedIndex
                                                ? { background: 'var(--color-accent-faint)', borderLeft: '3px solid var(--accent-blue)' }
                                                : { background: 'transparent', borderLeft: '3px solid transparent' }}
                                            onMouseEnter={() => setSelectedIndex(idx)}
                                            onClick={() => handleSelect(item)}
                                        >
                                            <Icon size={16} className="shrink-0" style={{ opacity: idx === selectedIndex ? 1 : 0.6, color: idx === selectedIndex ? 'var(--accent-blue)' : undefined }} />
                                            <span className="font-medium flex-1" style={{ transform: idx === selectedIndex ? 'translateX(4px)' : 'none', transition: 'transform 0.15s ease' }}>{item.label}</span>
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
