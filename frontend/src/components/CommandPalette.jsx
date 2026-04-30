import { useState, useEffect, useRef, useMemo } from 'react';
import {
    Search,
    Regex,
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
import { matchesSmartQuery } from './common/SmartSearchBar';

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
    useRegexValue,
    onUseRegexChange,
}) {
    const [query, setQuery] = useState('');
    const [useRegex, setUseRegex] = useState(false);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef(null);

    const queryText = queryValue ?? query;
    const setQueryText = onQueryChange ?? setQuery;
    const regexEnabled = useRegexValue ?? useRegex;
    const setRegexEnabled = onUseRegexChange ?? setUseRegex;

    useEffect(() => {
        if (isOpen) {
            setSelectedIndex(0);
            if (!onQueryChange) setQuery('');
            if (!onUseRegexChange) setUseRegex(false);
            setTimeout(() => inputRef.current?.focus(), 50);
        }
    }, [isOpen, onQueryChange, onUseRegexChange]);

    const regexError = useMemo(() => {
        const q = String(queryText || '').trim();
        if (!regexEnabled || !q) return '';
        try {
            new RegExp(q);
            return '';
        } catch (err) {
            return err?.message || 'Invalid regex';
        }
    }, [queryText, regexEnabled]);

    // Global Ctrl+K and '/' shortcut
    useEffect(() => {
        const handler = (e) => {
            // Ctrl+K to toggle
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                if (isOpen) onClose();
                else onAction?.('openPalette');
            }
            // '/' to open
            if (e.key === '/' && !isOpen) {
                // Ignore if user is already typing in an input or textarea
                if (
                    e.target.tagName === 'INPUT' ||
                    e.target.tagName === 'TEXTAREA' ||
                    e.target.isContentEditable
                ) {
                    return;
                }
                e.preventDefault();
                onAction?.('openPalette');
            }
            // Escape to close
            if (e.key === 'Escape' && isOpen) {
                onClose();
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [isOpen, onClose, onAction]);

    const results = useMemo(() => {
        const q = String(queryText || '').trim();
        const qLower = q.toLowerCase();
        const items = [];

        const match = (text) => {
            if (!q) return true;
            if (regexEnabled) {
                if (regexError) return false;
                return matchesSmartQuery(text, q, true);
            }
            return String(text || '').toLowerCase().includes(qLower);
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
    }, [queryText, models, regexEnabled, regexError]);

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
                <div className="flex items-center gap-4 px-6 py-5 border-b border-main" style={{ background: 'rgba(255,255,255,0.02)' }}>
                    <Search size={22} className="shrink-0" style={{ color: 'var(--text-tertiary)' }} />
                    <div className="flex-1 relative flex items-center">
                        <input
                            ref={inputRef}
                            type="text"
                            value={queryText}
                            onChange={e => setQueryText(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="Search models, commands..."
                            className="w-full outline-none px-4 py-2.5 rounded-xl transition-all font-medium"
                            style={{
                                background: 'color-mix(in srgb, var(--bg-surface-raised) 70%, transparent)',
                                border: '1px solid var(--border-main)',
                                color: 'var(--text-primary)',
                                fontSize: 17,
                            }}
                        />
                    </div>
                    <button
                        type="button"
                        onClick={() => setRegexEnabled(!regexEnabled)}
                        title={regexEnabled ? 'Regex enabled' : 'Regex disabled'}
                        className="hover:opacity-80 transition-opacity flex items-center justify-center shrink-0"
                        style={{
                            width: 28,
                            height: 28,
                            borderRadius: 6,
                            border: '1px solid var(--border-main)',
                            background: regexEnabled ? 'var(--color-accent-faint)' : 'var(--bg-surface)',
                            color: regexEnabled ? 'var(--accent-blue)' : 'var(--text-tertiary)',
                            cursor: 'pointer',
                        }}
                    >
                        <Regex size={15} />
                    </button>
                    <button 
                        onClick={onClose}
                        className="px-2 py-1 rounded border border-main text-[10px] font-bold font-mono text-tertiary cursor-pointer hover:text-white transition-colors shrink-0"
                        style={{ background: 'var(--bg-surface-raised)' }}
                        title="Close (ESC)"
                    >
                        ESC
                    </button>
                </div>
                {regexError && (
                    <div className="px-6 py-1.5 text-[11px] font-medium" style={{ color: 'var(--color-error)', borderBottom: '1px solid var(--border-main)', background: 'var(--color-error-bg)' }}>
                        Regex error: {regexError}
                    </div>
                )}

                {/* Results */}
                <div className="max-h-[450px] overflow-y-auto custom-scrollbar py-2">
                    {results.length === 0 ? (
                        <div className="px-6 py-10 text-center text-sm text-tertiary">No results found</div>
                    ) : (
                        Object.entries(grouped).map(([category, items]) => (
                            <div key={category}>
                                <div className="px-6 pt-3 pb-2 text-[11px] font-bold uppercase tracking-wider text-tertiary">{category}</div>
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
                                            <Icon size={16} className="shrink-0" style={{ color: idx === selectedIndex ? 'var(--accent-blue)' : 'var(--text-tertiary)' }} />
                                            <span className="font-medium flex-1" style={{ transform: idx === selectedIndex ? 'translateX(4px)' : 'none', transition: 'transform 0.15s ease' }}>{item.label}</span>
                                            {item.category && <span className="text-[11px] text-tertiary">{item.category}</span>}
                                        </button>
                                    );
                                })}
                            </div>
                        ))
                    )}
                </div>


            </div>
        </div>
    );
}
