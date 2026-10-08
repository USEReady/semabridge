import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { markInterruptedUploads } from '../utils/pbixFileEntries.js';

const DEFAULT_WIZARD_STATE = {
  // Step 1
  name: '',
  description: '',
  sourceConnector: '',
  targetConnectors: [],
  intermediateFormat: 'osi',
  tags: [],
  tagInput: '',

  // Step 2 (Connectors)
  fabricAccountId: '',
  selectedConnectionId: '',
  snowflakeAccountId: '',
  databricksAccountId: '',
  fabricWorkspaceId: '',
  snowflakeDatabase: '',
  snowflakeSchema: '',
  targetDatabase: '',
  targetSchema: '',
  targetAccount: '',
  targetWarehouse: '',
  modelQueryRegex: false,
  pbixSourceMode: 'TAG',
  selectedLocalFolderId: '',
  selectedPbixFilePath: '',
  // PBIX "Upload" mode: single-file upload path, the Single/Multiple toggle,
  // and the Multiple-files list ([{ id, name, size, path, status, error }],
  // see utils/pbixFileEntries.js). Kept here (persisted) rather than in
  // component state so the list survives leaving step 2 and page reloads.
  pbixUploadPath: '',
  pbixMultiFileMode: false,
  pbixFileEntries: [],

  // Step 3 (Sources)
  expandedWs: {},
  selectedModels: [],
  selectedModelNameByKey: {},
  selectedDatabricksTables: [],
  databricksQuery: '',

  // Step 4 (Mapping)
  autoRelationships: true,
  generateDescriptions: true,
  // When false (default), name collisions stay flagged for manual
  // review/Fix instead of being auto-renamed via the primary/base-table rule.
  // Sync strategy: 'copy' (default) or 'upsert'
  write_strategy: 'copy',

  // Global Progress
  currentStepIndex: 1,
};

export const useProjectWizardStore = create(
  persist(
    (set, get) => ({
      wizard: DEFAULT_WIZARD_STATE,
      hasRestoredDraft: false,

      setWizardState: (partial) => set((state) => ({
        wizard: {
          ...state.wizard,
          ...(partial && typeof partial === 'object' ? partial : {}),
        },
      })),

      // ── PBIX multi-file list actions ──────────────────────────────────────
      // Called from upload promise callbacks (never from inside a React
      // state updater), so an upload that finishes after step 2 unmounted
      // still lands in the list.
      addPbixFileEntries: (entries) => set((state) => ({
        wizard: {
          ...state.wizard,
          pbixFileEntries: [...(state.wizard.pbixFileEntries || []), ...(Array.isArray(entries) ? entries : [])],
        },
      })),
      updatePbixFileEntry: (id, patch) => set((state) => {
        const list = state.wizard.pbixFileEntries || [];
        if (!list.some((e) => e.id === id)) return state; // removed while uploading -- drop the result
        return {
          wizard: {
            ...state.wizard,
            pbixFileEntries: list.map((e) => (e.id === id ? { ...e, ...(patch || {}) } : e)),
          },
        };
      }),
      removePbixFileEntry: (id) => set((state) => ({
        wizard: {
          ...state.wizard,
          pbixFileEntries: (state.wizard.pbixFileEntries || []).filter((e) => e.id !== id),
        },
      })),

      setHasRestoredDraft: (val) => set({ hasRestoredDraft: val }),

      clearWizardState: () => set({
        wizard: DEFAULT_WIZARD_STATE,
        hasRestoredDraft: false
      }),

      // Helper to check if the store has meaningful data (not just defaults)
      hasMeaningfulData: () => {
        const { wizard } = get();
        return !!(wizard.name || wizard.sourceConnector || wizard.currentStepIndex > 1);
      }
    }),
    {
      name: 'semabridge-project-wizard-state',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({ wizard: state.wizard }),
      // Older drafts lack the newer keys; and an upload that was in flight
      // when the page reloaded can never complete -- mark it as failed.
      merge: (persisted, current) => {
        const persistedWizard = persisted && typeof persisted === 'object' ? persisted.wizard : null;
        if (!persistedWizard || typeof persistedWizard !== 'object') return current;
        return {
          ...current,
          wizard: {
            ...DEFAULT_WIZARD_STATE,
            ...persistedWizard,
            pbixFileEntries: markInterruptedUploads(persistedWizard.pbixFileEntries || []),
          },
        };
      },
    },
  ),
);

export { DEFAULT_WIZARD_STATE };
