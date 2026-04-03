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

let apiLock = false;

    // Fetch workspaces from the DB-driven Fabric endpoint on every mount.
    // This keeps the workspace list aligned with the current Fabric account selection.
    useEffect(() => {
        const load = async () => {
            if (apiLock) return;
            apiLock = true;
            setIsLoading(true);
            try {
                // Use only the DB-driven /connections/fabric/workspaces endpoint.
                // The old /api/workspaces (settings-based) is intentionally NOT called here.
                const fabricResponse = await api.fabricListWorkspaces().catch(() => ({ workspaces: [] }));
                console.log('WorkspaceContext: raw fabricListWorkspaces response:', fabricResponse);

                const raw = (fabricResponse?.workspaces) || [];
                const merged = [];
                const seen = new Set();

                raw.forEach(ws => {
                    if (!ws) return;
                    const id = String(ws.id ?? ws.workspace_id ?? '').trim();
                    if (!id || seen.has(id)) return;
                    seen.add(id);
                    merged.push({
                        ...ws,
                        id,
                        name: ws.name ?? ws.displayName ?? ws.display_name ?? id,
                    });
                });

                setWorkspaces(merged);

                // Purge stale localStorage if the stored ID is not in the live list
                const storedId = localStorage.getItem(STORAGE_KEY) || '';
                const isStoredValid = storedId && merged.some(ws => ws.id === storedId);

                if (!isStoredValid && merged.length > 0) {
                    // Prefer 'My workspace' as fallback, otherwise first item
                    const myWs = merged.find(w => w.name === 'My workspace');
                    const defaultId = myWs ? myWs.id : merged[0].id;
                    setActiveWorkspaceId(defaultId);
                    localStorage.setItem(STORAGE_KEY, defaultId);
                    if (storedId) {
                        console.warn('WorkspaceContext: purged stale workspace ID from localStorage:', storedId);
                    }
                } else if (!isStoredValid && merged.length === 0) {
                    setActiveWorkspaceId('');
                    localStorage.removeItem(STORAGE_KEY);
                }
                // else: stored ID is valid — keep it
            } catch (err) {
                console.warn('WorkspaceContext: failed to load workspaces:', err.message);
                setWorkspaces([]);
            } finally {
                setIsLoading(false);
            }
        };
        load();
        
        return () => {
            apiLock = false; // reset lock if entire app unmounts
        };
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
