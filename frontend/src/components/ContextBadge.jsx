import { useState, useRef, useEffect, useMemo } from 'react';
import { 
    Hexagon, 
    Settings, 
    LogOut, 
    Search, 
    Layers, 
    Target,
    ChevronUp,
    Zap,
    Database,
    Activity,
    Info
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import { useAuth } from '../context/AuthContext';
import { useConfiguration } from '../context/ConfigurationContext';
import SearchableSelect from './common/SearchableSelect';
import { getSmartQueryMode } from './common/smartSearchQuery.js';

import { createPortal } from 'react-dom';

export default function ContextBadge({ searchQuery = '', onOpenSettings, variant = 'footer', isCollapsed = false }) {
    const { workspaces, activeWorkspace, selectWorkspace, isLoading: workspacesLoading } = useWorkspace();
    const { user, logout } = useAuth();
    const { config } = useConfiguration();
    const [isOpen, setIsOpen] = useState(false);
    const panelRef = useRef(null);
    const triggerRef = useRef(null);
    const timerRef = useRef(null);

    const searchMode = useMemo(() => {
        if (!searchQuery) return { mode: 'Standard', icon: Search };
        const modeInfo = getSmartQueryMode(searchQuery);
        switch (modeInfo.mode) {
            case 'regex': return { mode: 'Regex', icon: Target, color: 'var(--accent-blue)' };
            case 'prefix': return { mode: 'Prefix', icon: Zap, color: 'var(--color-warning)' };
            default: return { mode: 'Standard', icon: Search };
        }
    }, [searchQuery]);

    const activeName = activeWorkspace?.name || config?.name || 'SemaBridge';
    
    // Generate Initials
    const projectInitials = useMemo(() => {
        const words = activeName.split(/[\s_-]+/);
        if (words.length >= 2) {
            return (words[0][0] + words[1][0]).toUpperCase();
        }
        return activeName.substring(0, 2).toUpperCase();
    }, [activeName]);

    // Handle Toast Auto-dismiss
    useEffect(() => {
        if (isOpen) {
            if (timerRef.current) clearTimeout(timerRef.current);
            timerRef.current = setTimeout(() => {
                setIsOpen(false);
            }, 5000);
        }
        return () => {
            if (timerRef.current) clearTimeout(timerRef.current);
        };
    }, [isOpen]);

    useEffect(() => {
        const handleClickOutside = (event) => {
            if (panelRef.current && !panelRef.current.contains(event.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const isFooter = variant === 'footer';

    // Calculate position for the Portal
    const portalPosition = useMemo(() => {
        if (!triggerRef.current) return { left: 0, bottom: 0 };
        const rect = triggerRef.current.getBoundingClientRect();
        return {
            left: rect.right + 12,
            bottom: window.innerHeight - rect.bottom
        };
    }, [isOpen]);

    return (
        <div className={`relative ${isFooter ? 'w-full px-2 mb-2' : ''}`}>
            {/* Branded Identity Badge (The Trigger) */}
            <button
                ref={triggerRef}
                onClick={() => setIsOpen(!isOpen)}
                className={`
                    flex items-center gap-3 w-full transition-all duration-300 group
                    ${isFooter 
                        ? isCollapsed 
                            ? 'p-1.5 justify-center bg-transparent border-transparent' 
                            : 'p-2.5 rounded-xl border border-main/30 hover:border-accent-blue/50 bg-surface-raised/40 shadow-lg hover:bg-surface-hover' 
                        : 'px-3 py-1.5 rounded-lg bg-surface-raised/50 hover:bg-surface-hover border border-main shadow-sm'
                    }
                    ${isOpen && !isCollapsed ? 'ring-2 ring-accent-blue/40 border-accent-blue/60 bg-surface-hover scale-[1.02]' : ''}
                    ${isOpen && isCollapsed ? 'scale-110' : ''}
                `}
            >
                {/* The "Name Logo" Square */}
                <div className={`
                    rounded-lg bg-primary text-secondary flex items-center justify-center font-black border border-white/10 shrink-0 shadow-inner group-hover:bg-accent-blue transition-colors duration-300
                    ${isCollapsed ? 'w-8 h-8 text-[11px]' : 'w-9 h-9 text-sm'}
                `}>
                    {projectInitials}
                </div>

                {!isCollapsed && (
                    <div className="flex flex-col items-start overflow-hidden">
                        <span className="text-[11px] font-black text-primary tracking-tight truncate w-full text-left uppercase leading-tight">
                            {activeName}
                        </span>
                        <div className="flex items-center gap-1.5 mt-0.5">
                            <div className="w-1.5 h-1.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]" />
                            <span className="text-[9px] text-tertiary font-bold tracking-wide uppercase">
                                Session Meta
                            </span>
                        </div>
                    </div>
                )}
                
                {!isCollapsed && (
                    <ChevronUp 
                        size={12} 
                        className={`absolute right-3 top-1/2 -translate-y-1/2 text-tertiary/50 transition-transform duration-300 ${isOpen ? 'rotate-180 text-accent-blue' : ''}`} 
                    />
                )}
            </button>

            {/* The Pulse Toast (Glassmorphic Pop-up via Portal) */}
            {isOpen && createPortal(
                <div 
                    ref={panelRef}
                    className="fixed z-[9999] w-80 rounded-2xl border border-white/10 shadow-[0_20px_50px_rgba(0,0,0,0.5)] animate-in fade-in slide-in-from-bottom-4 duration-300 origin-bottom-left"
                    style={{ 
                        left: portalPosition.left,
                        bottom: portalPosition.bottom,
                        backdropFilter: 'blur(24px)', 
                        background: 'rgba(15, 17, 20, 0.85)',
                        border: '1px solid rgba(255, 255, 255, 0.08)'
                    }}
                >
                    {/* Glassmorphic Content Area */}
                    <div className="p-4">
                        <div className="flex items-center gap-4 mb-5">
                            <div className="w-12 h-12 rounded-xl bg-accent-blue/20 flex items-center justify-center text-accent-blue border border-accent-blue/30 shadow-[0_0_20px_rgba(0,149,255,0.2)]">
                                <Activity size={24} />
                            </div>
                            <div className="flex-1 min-w-0">
                                <h3 className="text-xs font-black text-primary uppercase tracking-widest leading-none mb-1">Context Pulse</h3>
                                <div className="flex items-center gap-2">
                                    <span className="text-[10px] text-tertiary font-medium">{activeName}</span>
                                    <div className="w-1 h-1 rounded-full bg-tertiary/30" />
                                    <span className="text-[10px] text-accent-blue font-bold uppercase tracking-tighter">Live</span>
                                </div>
                            </div>
                        </div>

                        {/* High-Density technical stats */}
                        <div className="space-y-3">
                            <div className="flex items-center justify-between p-2.5 rounded-xl bg-white/5 border border-white/5">
                                <div className="flex items-center gap-2">
                                    <Info size={14} className="text-tertiary" />
                                    <span className="text-[10px] font-bold text-secondary uppercase">Index Status</span>
                                </div>
                                <span className="text-[10px] font-mono text-primary bg-white/10 px-2 py-0.5 rounded">10,240 items</span>
                            </div>

                            <div className="flex items-center justify-between p-2.5 rounded-xl bg-white/5 border border-white/5">
                                <div className="flex items-center gap-2">
                                    <searchMode.icon size={14} style={{ color: searchMode.color || 'var(--text-tertiary)' }} />
                                    <span className="text-[10px] font-bold text-secondary uppercase">Active Mode</span>
                                </div>
                                <span className="text-[10px] font-mono text-primary">{searchMode.mode}</span>
                            </div>

                            <div className="flex items-center justify-between p-2.5 rounded-xl bg-white/5 border border-white/5">
                                <div className="flex items-center gap-2">
                                    <Database size={14} className="text-tertiary" />
                                    <span className="text-[10px] font-bold text-secondary uppercase">Backend</span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                                    <span className="text-[10px] font-mono text-primary">PostgreSQL</span>
                                </div>
                            </div>
                        </div>

                        {/* Interactive footer actions */}
                        <div className="mt-5 pt-4 border-t border-white/5 flex gap-2">
                            <button
                                onClick={(e) => { e.stopPropagation(); setIsOpen(false); onOpenSettings?.(); }}
                                className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-[10px] font-black uppercase tracking-widest text-secondary hover:bg-white/10 hover:text-primary transition-all border border-white/5"
                            >
                                <Settings size={12} />
                                Config
                            </button>
                            <button
                                onClick={(e) => { e.stopPropagation(); setIsOpen(false); logout(); }}
                                className="flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-[10px] font-black uppercase tracking-widest text-danger hover:bg-danger/10 transition-all border border-danger/10"
                            >
                                <LogOut size={12} />
                                Exit
                            </button>
                        </div>
                    </div>

                    {/* Auto-dismiss progress bar */}
                    <div className="absolute bottom-0 left-0 h-1 bg-accent-blue/30 w-full overflow-hidden rounded-b-2xl">
                        <div 
                            className="h-full bg-accent-blue animate-shrink" 
                            style={{ animationDuration: '5000ms' }}
                        />
                    </div>
                </div>,
                document.body
            )}
        </div>
    );
}
