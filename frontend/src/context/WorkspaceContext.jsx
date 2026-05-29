import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../utils/api';
import { useAuth } from './AuthContext';

const WorkspaceContext = createContext(null);

const STORAGE_KEY = 'semabridge_workspace_id';

export function WorkspaceProvider({ children }) {
    const [workspaces, setWorkspaces] = useState([]);
    const [activeWorkspaceId, setActiveWorkspaceId] = useState(
        () => localStorage.getItem(STORAGE_KEY) || ''
    );
    const [isLoading, setIsLoading] = useState(false);
    const { isAuthenticated, token } = useAuth();
    const apiLockRef = useRef(false);

    // Fetch workspaces from the DB-driven Fabric endpoint on every mount.
    // This keeps the workspace list aligned with the current Fabric account selection.
    useEffect(() => {
        if (!isAuthenticated && !token) {
            setWorkspaces([]);
            setActiveWorkspaceId('');
            // Do not delete STORAGE_KEY on unauthenticated logout to preserve for next login
            return;
        }

        const load = async () => {
            if (apiLockRef.current) return;
            apiLockRef.current = true;
            setIsLoading(true);
            try {
                // Use only the DB-driven /connections/fabric/workspaces endpoint.
                // The old /api/workspaces (settings-based) is intentionally NOT called here.
                const fabricResponse = await api.fabricListWorkspaces();

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
                    // Fall back to the first workspace in the list (no hardcoded name matching)
                    const defaultId = merged[0].id;
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
                // Keep existing workspaces on network/transient failures rather than purging
            } finally {
                setIsLoading(false);
                apiLockRef.current = false;
            }
        };

        load();
    }, [isAuthenticated, token]);


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
