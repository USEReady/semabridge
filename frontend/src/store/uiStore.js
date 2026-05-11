import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

const DEFAULT_FILTER_OPTIONS = {
  selectedFolder: null,
  sourceFilter: 'all',
  targetFilters: [],
  targetFilter: 'all',
  tagFilters: [],
  viewMode: 'folder',
  useRegexSearch: false,
};

function normalizeViewMode(raw) {
  const value = String(raw || '').toLowerCase().trim();
  if (value === 'system' || value === 'adapter') return 'adapter';
  return 'folder';
}

const DEFAULT_UI_STATE = {
  activeProjectId: null,
  searchQuery: '',
  filterOptions: DEFAULT_FILTER_OPTIONS,
  lastNavigatedPath: '/projects',
  sidebarCollapsed: false,
  projectListScrollTop: 0,
  createProjectDraft: null,
  projectConfigDrafts: {},
};

function sanitizeFilterOptions(raw) {
  const next = (raw && typeof raw === 'object') ? raw : {};
  const tagFilters = Array.isArray(next.tagFilters)
    ? next.tagFilters.map(String).filter(Boolean)
    : [];
  const targetFilters = Array.isArray(next.targetFilters)
    ? [...new Set(next.targetFilters.map(String).filter((value) => value && value !== 'all'))]
    : [];
  const targetFilter = (typeof next.targetFilter === 'string' && next.targetFilter.trim() && next.targetFilter !== 'all')
    ? next.targetFilter
    : '';
  const normalizedTargetFilters = targetFilters.length > 0
    ? targetFilters
    : (targetFilter ? [targetFilter] : []);

  return {
    selectedFolder: next.selectedFolder ?? null,
    sourceFilter: typeof next.sourceFilter === 'string' && next.sourceFilter.trim() ? next.sourceFilter : 'all',
    targetFilters: normalizedTargetFilters,
    targetFilter: normalizedTargetFilters[0] || 'all',
    tagFilters,
    viewMode: normalizeViewMode(next.viewMode),
    useRegexSearch: Boolean(next.useRegexSearch),
  };
}

function areShallowArraysEqual(a, b) {
  if (a === b) return true;
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function areFilterOptionsEqual(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;

  return (
    a.selectedFolder === b.selectedFolder
    && a.sourceFilter === b.sourceFilter
    && areShallowArraysEqual(a.targetFilters, b.targetFilters)
    && a.targetFilter === b.targetFilter
    && a.viewMode === b.viewMode
    && a.useRegexSearch === b.useRegexSearch
    && areShallowArraysEqual(a.tagFilters, b.tagFilters)
  );
}

export const useUIStore = create(
  persist(
    (set) => ({
      ...DEFAULT_UI_STATE,
      hasHydrated: false,
      setHasHydrated: (hasHydrated) => set((state) => (
        state.hasHydrated === hasHydrated ? state : { hasHydrated }
      )),

      setActiveProjectId: (activeProjectId) => set((state) => (
        state.activeProjectId === activeProjectId ? state : { activeProjectId }
      )),
      setSearchQuery: (searchQuery) => set((state) => (
        state.searchQuery === searchQuery ? state : { searchQuery }
      )),
      setFilterOptions: (partial) =>
        set((state) => {
          const nextFilterOptions = sanitizeFilterOptions({
            ...state.filterOptions,
            ...(partial && typeof partial === 'object' ? partial : {}),
          });

          if (areFilterOptionsEqual(state.filterOptions, nextFilterOptions)) {
            return state;
          }

          return {
            filterOptions: nextFilterOptions,
          };
        }),
      setLastNavigatedPath: (lastNavigatedPath) => set((state) => (
        state.lastNavigatedPath === lastNavigatedPath ? state : { lastNavigatedPath }
      )),
      setSidebarCollapsed: (sidebarCollapsed) => set((state) => (
        state.sidebarCollapsed === sidebarCollapsed ? state : { sidebarCollapsed }
      )),
      setProjectListScrollTop: (projectListScrollTop) => set((state) => (
        state.projectListScrollTop === projectListScrollTop ? state : { projectListScrollTop }
      )),

      setCreateProjectDraft: (createProjectDraft) =>
        set((state) => {
          const prevKey = JSON.stringify(state.createProjectDraft ?? null);
          const nextKey = JSON.stringify(createProjectDraft ?? null);
          if (prevKey === nextKey) return state;
          return { createProjectDraft };
        }),
      clearCreateProjectDraft: () =>
        set((state) => (state.createProjectDraft === null ? state : { createProjectDraft: null })),

      setProjectConfigDraft: (projectId, draft) =>
        set((state) => {
          const key = String(projectId);
          const prevDraft = state.projectConfigDrafts?.[key];
          const prevKey = JSON.stringify(prevDraft ?? null);
          const nextKey = JSON.stringify(draft ?? null);

          if (prevKey === nextKey) return state;

          return {
            projectConfigDrafts: {
              ...state.projectConfigDrafts,
              [key]: draft,
            },
          };
        }),
      clearProjectConfigDraft: (projectId) =>
        set((state) => {
          if (!Object.prototype.hasOwnProperty.call(state.projectConfigDrafts, String(projectId))) {
            return state;
          }
          const next = { ...state.projectConfigDrafts };
          delete next[String(projectId)];
          return { projectConfigDrafts: next };
        }),
      resetUIState: () => set((state) => {
        const sameState = (
          state.activeProjectId === DEFAULT_UI_STATE.activeProjectId
          && state.searchQuery === DEFAULT_UI_STATE.searchQuery
          && state.lastNavigatedPath === DEFAULT_UI_STATE.lastNavigatedPath
          && state.sidebarCollapsed === DEFAULT_UI_STATE.sidebarCollapsed
          && state.projectListScrollTop === DEFAULT_UI_STATE.projectListScrollTop
          && state.createProjectDraft === DEFAULT_UI_STATE.createProjectDraft
          && Object.keys(state.projectConfigDrafts || {}).length === 0
          && areFilterOptionsEqual(state.filterOptions, DEFAULT_UI_STATE.filterOptions)
        );

        if (sameState) return state;
        return { ...DEFAULT_UI_STATE, hasHydrated: true };
      }),
    }),
    {
      name: 'semabridge-ui-state',
      storage: createJSONStorage(() => sessionStorage),
      // Only persist non-ephemeral UI state — omit hasHydrated itself.
      partialize: (state) => ({
        activeProjectId: state.activeProjectId,
        searchQuery: state.searchQuery,
        filterOptions: state.filterOptions,
        lastNavigatedPath: state.lastNavigatedPath,
        sidebarCollapsed: state.sidebarCollapsed,
        projectListScrollTop: state.projectListScrollTop,
        createProjectDraft: state.createProjectDraft,
        projectConfigDrafts: state.projectConfigDrafts,
      }),
      onRehydrateStorage: () => (state) => {
        // Mark hydration complete after sessionStorage has been read.
        if (state) state.setHasHydrated(true);
      },
    },
  ),
);

export function clearUIStoreStorage() {
  // Clear persisted sessionStorage entry and reset in-memory state.
  useUIStore.persist?.clearStorage();
  useUIStore.setState({
    ...DEFAULT_UI_STATE,
    hasHydrated: true,
  });
}

export { DEFAULT_FILTER_OPTIONS, DEFAULT_UI_STATE };
