import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { api } from '../utils/api';

const WorkspaceContext = createContext(null);

const STORAGE_KEY = 'semabridge_workspace_id';

export function WorkspaceProvider({ children }) {
    const [workspaces, setWorkspaces] = useState([]);
    const [activeWorkspaceId, setActiveWorkspaceId] = useState(
        () => localStorage.getItem(STORAGE_KEY) || ''
    );
    const [isLoading, setIsLoading] = useState(false);

    // Fetch workspaces from API
    useEffect(() => {
        const load = async () => {
            setIsLoading(true);
            try {
                const [baseWorkspaces, fabricWorkspaces] = await Promise.all([
                    api.getWorkspaces().catch(() => []),
                    api.fabricListWorkspaces().catch(() => ({ workspaces: [] })),
                ]);

                const merged = [];
                const seen = new Set();
                const pushUnique = (ws) => {
                    if (!ws) return;
                    const id = String(ws.id ?? ws.workspace_id ?? '').trim();
                    if (!id || seen.has(id)) return;
                    seen.add(id);
                    merged.push({
                        ...ws,
                        id,
                        name: ws.name ?? ws.displayName ?? ws.display_name ?? id,
                    });
                };

                // Keep a stable local workspace option visible in the selector.
                pushUnique({ id: 'semabridge-local', name: 'SemaBridge Workspace' });

                (baseWorkspaces || []).forEach(pushUnique);
                ((fabricWorkspaces && fabricWorkspaces.workspaces) || []).forEach(pushUnique);

                setWorkspaces(merged);

                // Keep selected workspace if valid; else choose first available.
                const hasActive = merged.some(ws => ws.id === activeWorkspaceId);
                if (!hasActive && merged.length > 0) {
                    setActiveWorkspaceId(merged[0].id);
                    localStorage.setItem(STORAGE_KEY, merged[0].id);
                }
            } catch (err) {
                console.warn('Failed to load workspaces, using defaults:', err.message);
                // Fallback: use the env workspace as the single option
                const fallbackId = activeWorkspaceId || 'semabridge-local';
                setWorkspaces([{ id: fallbackId, name: 'SemaBridge Workspace' }]);
            } finally {
                setIsLoading(false);
            }
        };
        load();
    }, []);

    const selectWorkspace = useCallback((id) => {
        setActiveWorkspaceId(id);
        localStorage.setItem(STORAGE_KEY, id);
    }, []);

    const activeWorkspace = workspaces.find(w => w.id === activeWorkspaceId) || workspaces[0];

    return (
        <WorkspaceContext.Provider value={{
            workspaces,
            activeWorkspaceId,
            activeWorkspace,
            selectWorkspace,
            isLoading,
        }}>
            {children}
        </WorkspaceContext.Provider>
    );
}

export function useWorkspace() {
    const ctx = useContext(WorkspaceContext);
    if (!ctx) throw new Error('useWorkspace must be used within WorkspaceProvider');
    return ctx;
}
