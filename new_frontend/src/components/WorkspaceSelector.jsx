import { useState, useRef, useEffect } from 'react';
import { ChevronDown, Building2, Check, Loader2 } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';


    const { workspaces, activeWorkspaceId, activeWorkspace, selectWorkspace, isLoading } = useWorkspace();
    const [isOpen, setIsOpen] = useState(false);
    const [error, setError] = useState(null);
    const ref = useRef(null);

    // Close on outside click
    useEffect(() => {
        const handler = (e) => {
            if (ref.current && !ref.current.contains(e.target)) setIsOpen(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    // Watch for empty or error state
    useEffect(() => {
        if (!isLoading && workspaces.length === 0) {
            setError('No Fabric workspaces found for the selected account.\nCheck your account permissions or try re-authenticating.');
        } else {
            setError(null);
        }
    }, [isLoading, workspaces]);

    return (
        <div ref={ref} className="relative">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-2 px-2 py-0.5 rounded-md bg-white/5 border border-white/5 hover:border-accent-blue/30 transition-all"
            >
                {isLoading ? (
                    <Loader2 size={10} className="animate-spin text-tertiary" />
                ) : (
                    <Building2 size={10} className="text-accent-blue" />
                )}
                <span className="text-[10px] font-bold text-secondary uppercase tracking-wider">Workspace:</span>
                <span className="text-[10px] font-mono text-primary max-w-[120px] truncate">
                    {activeWorkspace?.name || activeWorkspaceId?.substring(0, 12) || 'Select...'}
                </span>
                <ChevronDown size={10} className={`text-tertiary transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>

            {/* Error or empty state banner */}
            {error && !isLoading && (
                <div className="absolute left-0 mt-2 w-72 bg-red-50 border border-red-300 text-red-700 rounded-xl shadow-lg p-3 z-50 text-xs">
                    <strong>Workspace Error:</strong>
                    <div className="mt-1 whitespace-pre-line">{error}</div>
                </div>
            )}

            {isOpen && !error && (
                <div className="absolute top-full left-0 mt-1 w-72 bg-surface border border-main rounded-xl shadow-2xl z-50 overflow-hidden animate-in slide-in-from-top-1 duration-150">
                    <div className="px-3 py-2 border-b border-main">
                        <p className="text-[10px] font-bold uppercase tracking-wider text-tertiary">Select Workspace</p>
                    </div>
                    <div className="max-h-48 overflow-y-auto custom-scrollbar">
                        {workspaces.map(ws => (
                            <button
                                key={ws.id}
                                onClick={() => { selectWorkspace(ws.id); setIsOpen(false); }}
                                className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors ${ws.id === activeWorkspaceId
                                        ? 'bg-accent-blue/10 text-accent-blue'
                                        : 'text-primary hover:bg-surface-hover'
                                    }`}
                            >
                                <Building2 size={14} className={ws.id === activeWorkspaceId ? 'text-accent-blue' : 'text-tertiary'} />
                                <div className="flex-1 min-w-0">
                                    <p className="text-xs font-bold truncate">{ws.name}</p>
                                    <p className="text-[10px] text-tertiary font-mono truncate">{ws.id}</p>
                                </div>
                                {ws.id === activeWorkspaceId && <Check size={14} className="text-accent-blue shrink-0" />}
                            </button>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}
