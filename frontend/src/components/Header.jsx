import {
    Search,
    Command,
    Bell,
    Settings,
    ChevronDown,
    Hexagon,
    Plus,
    FolderOpen,
    Save,
    FileCode2,
    RefreshCw,
    CheckCircle2,
    Maximize,
    GitCommit,
    Map,
    Terminal,
    Eye,
    PanelBottom,
    LogOut,
    FileCode2,
    SlidersHorizontal,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import ThemeToggle from './ThemeToggle';
import DropdownMenu from './DropdownMenu';
import { useLogs } from '../context/LogsContext';
import { useAuth } from '../context/AuthContext';

export default function Header({ onToggleLogs, onToggleVersionControl, onToggleRepoMap, onToggleConnections, onToggleTerminal, onOpenCommandPalette, showRepoMap, onToggleYamlView, isYamlView }) {
    const { logs } = useLogs();
    const { user, logout } = useAuth();
    const [showUserMenu, setShowUserMenu] = useState(false);
    const menuRef = useRef(null);

    const unreadCount = logs.filter(l => l.severity === 'warning' || l.severity === 'error').length;
    const initial = user?.username?.[0]?.toUpperCase() || '?';

    // Close user menu on outside click
    useEffect(() => {
        const handler = (e) => {
            if (menuRef.current && !menuRef.current.contains(e.target)) setShowUserMenu(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    return (
        <header
            className="theme-transition flex items-center justify-between px-4 h-12 shrink-0 select-none bg-surface"
            style={{
                borderBottom: '1px solid var(--border-main)',
                zIndex: 50,
            }}
        >
            {/* Left: App Name */}
            <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                    <Hexagon size={18} className="text-accent-blue" strokeWidth={2.5} />
                    <h1 className="text-sm font-bold tracking-tight text-primary">
                        SemaBridge
                    </h1>
                </div>
            </div>

            {/* Center: Global Search (Command Palette) */}
            <div className="flex-1 max-w-xl px-12">
                <div
                    className="group theme-transition flex items-center gap-2 px-3 h-8 rounded-md bg-app border border-main hover:border-slate-500 cursor-pointer"
                    onClick={onOpenCommandPalette}
                >
                    <Search size={14} className="text-tertiary group-hover:text-secondary transition-colors" />
                    <span className="flex-1 text-[12px] text-tertiary">Search models, commands, configs...</span>
                    <div className="flex items-center gap-1">
                        <kbd className="px-1.5 py-0.5 rounded bg-surface-raised border border-main text-[9px] font-mono text-tertiary">Ctrl</kbd>
                        <kbd className="px-1.5 py-0.5 rounded bg-surface-raised border border-main text-[9px] font-mono text-tertiary">K</kbd>
                    </div>
                </div>
            </div>

            {/* Right: Actions */}
            <div className="flex items-center gap-1">
                <nav className="flex items-center gap-1 mr-4">
                    <DropdownMenu
                        label="File"
                        items={[
                            { label: 'New Config', icon: Plus, shortcut: 'Ctrl+N', onClick: () => {} },
                            { label: 'Open repository...', icon: FolderOpen, shortcut: 'Ctrl+O', onClick: onToggleRepoMap },
                            { label: 'Save configuration', icon: Save, shortcut: 'Ctrl+S', onClick: () => {
                                document.dispatchEvent(new CustomEvent('semabridge:save'));
                            }},
                            { label: 'Export SML', icon: FileCode2, shortcut: 'Ctrl+E', onClick: () => {} },
                        ]}
                    />
                    <DropdownMenu
                        label="Project"
                        items={[
                            { label: 'Sync Models', icon: RefreshCw, shortcut: 'Ctrl+R', onClick: () => {
                                document.dispatchEvent(new CustomEvent('semabridge:sync'));
                            }},
                            { label: 'Validate Metadata', icon: CheckCircle2, shortcut: 'Ctrl+L', onClick: () => {} },
                            { label: 'Configure Pipeline', icon: Settings, onClick: onToggleConnections },
                        ]}
                    />
                    <DropdownMenu
                        label="View"
                        items={[
                            { label: 'Repository Map', icon: Map, onClick: onToggleRepoMap },
                            { label: 'Terminal', icon: PanelBottom, onClick: onToggleTerminal, shortcut: 'Ctrl+`' },
                            { label: 'Logs & Activity', icon: Bell, onClick: onToggleLogs },
                            { label: 'Version Control', icon: GitCommit, onClick: onToggleVersionControl },
                        ]}
                    />
                </nav>

                <div className="flex items-center gap-1 border-l border-main pl-3 ml-2">
                    <button
                        onClick={onToggleRepoMap}
                        className={`p-2 rounded-md hover:bg-surface-hover relative ${showRepoMap ? 'text-accent-blue' : 'text-secondary'}`}
                        title="Repository Map"
                        style={showRepoMap ? { background: 'rgba(99,102,241,.15)' } : {}}
                    >
                        <Map size={16} />
                    </button>

                    {/* Version Control Button */}
                    <button
                        onClick={onToggleVersionControl}
                        className="p-2 rounded-md hover:bg-surface-hover text-secondary relative"
                        title="Version Control"
                    >
                        <GitCommit size={16} />
                    </button>

                    {/* Logs/Bell Button */}
                    <button
                        onClick={onToggleLogs}
                        className="p-2 rounded-md hover:bg-surface-hover text-secondary relative"
                        title="Logs & Activity"
                    >
                        <Bell size={16} />
                        {unreadCount > 0 && (
                            <span className="absolute top-1.5 right-1.5 min-w-[14px] h-[14px] flex items-center justify-center rounded-full bg-red-500 text-white text-[8px] font-bold leading-none px-1">
                                {unreadCount > 9 ? '9+' : unreadCount}
                            </span>
                        )}
                    </button>

                    <div className="flex flex-col items-center gap-1">
                        <ThemeToggle />
                        {onToggleYamlView && (
                            <button
                                type="button"
                                onClick={onToggleYamlView}
                                aria-label={isYamlView ? 'Switch to form view' : 'Switch to YAML view'}
                                title={isYamlView ? 'Switch to form view' : 'Switch to YAML view'}
                                className={`w-10 h-10 rounded-xl flex items-center justify-center cursor-pointer border-none outline-none transition-none shadow-none ${isYamlView ? 'text-accent-blue' : 'text-secondary'}`}
                                style={isYamlView ? { background: 'rgba(99,102,241,.15)' } : { background: 'var(--bg-surface-hover)' }}
                            >
                                {isYamlView ? <SlidersHorizontal size={17} /> : <FileCode2 size={17} />}
                            </button>
                        )}
                    </div>
                    <button onClick={onToggleConnections} className="p-2 rounded-md hover:bg-surface-hover text-secondary" title="Connections & Settings">
                        <Settings size={16} />
                    </button>

                    <div className="relative" ref={menuRef}>
                        <div
                            className="flex items-center gap-2 ml-2 pl-2 border-l border-main h-6 cursor-pointer"
                            onClick={() => setShowUserMenu(v => !v)}
                        >
                            <div className="w-6 h-6 rounded bg-accent-blue/20 border border-accent-blue/30 flex items-center justify-center text-[10px] font-bold text-accent-blue">
                                {initial}
                            </div>
                            <ChevronDown size={12} className="text-tertiary" />
                        </div>

                        {showUserMenu && (
                            <div
                                className="absolute right-0 top-9 w-48 rounded-lg bg-surface border border-main py-1 z-[100]"
                                style={{ boxShadow: 'var(--shadow-lg)' }}
                            >
                                <div className="px-3 py-2 border-b border-main">
                                    <p className="text-xs font-semibold text-primary truncate">{user?.username}</p>
                                    <p className="text-[10px] text-tertiary truncate">{user?.email}</p>
                                    <span className="inline-block mt-1 px-1.5 py-0.5 rounded-full text-[9px] font-medium"
                                        style={{ background: 'var(--color-accent-faint)', color: 'var(--accent-blue)' }}>
                                        {user?.role}
                                    </span>
                                </div>
                                <button
                                    onClick={() => { setShowUserMenu(false); onToggleConnections(); }}
                                    className="w-full flex items-center gap-2 px-3 py-2 text-xs text-secondary hover:bg-surface-hover transition"
                                >
                                    <Settings size={12} /> Settings
                                </button>
                                <button
                                    onClick={() => { setShowUserMenu(false); logout(); }}
                                    className="w-full flex items-center gap-2 px-3 py-2 text-xs transition"
                                    style={{ color: 'var(--color-danger)' }}
                                >
                                    <LogOut size={12} /> Sign out
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </header>
    );
}
