import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

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
  domainHint: '',
  modelQueryRegex: false,
  pbixSourceMode: 'TAG',
  selectedLocalFolderId: '',
  selectedPbixFilePath: '',

  // Step 3 (Sources)
  expandedWs: {},
  selectedModels: [],
  selectedModelNameByKey: {},
  selectedDatabricksTables: [],
  databricksQuery: '',

  // Step 4 (Mapping)
  autoRelationships: true,
  generateDescriptions: true,

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
    },
  ),
);

export { DEFAULT_WIZARD_STATE };
