/** Initial state of the wizard's Ontology review step (kept in CreateProjectPage). */
export const EMPTY_REVIEW = {
  signature: '',
  status: 'idle', // idle | loading | ready | unsupported | error
  analysis: null,
  message: '',
  merges: {},       // dupId -> { decision: 'merge' | 'separate', name }
  conflicts: {},    // conflictId -> { resolution: 'use_existing' | 'keep_new' | 'defer', new_name }
  descriptions: {}, // measure name -> text
  domainName: '',
  owner: '',
  saving: false,
  saved: null,      // apply response
  saveError: '',
};
