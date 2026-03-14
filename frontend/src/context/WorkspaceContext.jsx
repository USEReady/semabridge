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
                const data = await api.getWorkspaces();
                setWorkspaces(data);
                // If none selected yet, pick the first one
                if (!activeWorkspaceId && data.length > 0) {
                    setActiveWorkspaceId(data[0].id);
                    localStorage.setItem(STORAGE_KEY, data[0].id);
                }
            } catch (err) {
                console.warn('Failed to load workspaces, using defaults:', err.message);
                // Fallback: use the env workspace as the single option
                const fallbackId = activeWorkspaceId || 'default';
                setWorkspaces([{ id: fallbackId, name: 'Default Workspace' }]);
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
