export function buildDryRunPayload({
  sourceConnector,
  targetConnectors,
  fabricAccountId,
  fabricWorkspaceId,
  snowflakeAccountId,
  snowflakeDatabase,
  snowflakeSchema,
  targetDatabase,
  targetSchema,
  selectedModelNames,
  pbixPath,
}) {
  const sourceConfig = { type: sourceConnector };

  if (sourceConnector === 'fabric') {
    if (fabricAccountId) sourceConfig.identity_id = fabricAccountId;
    if (fabricWorkspaceId) sourceConfig.workspace_id = fabricWorkspaceId;
  }

  if (sourceConnector === 'snowflake') {
    if (snowflakeDatabase) sourceConfig.database = snowflakeDatabase;
    if (snowflakeSchema) sourceConfig.schema = snowflakeSchema;
  }

  if (sourceConnector === 'pbix' && pbixPath) {
    sourceConfig.pbix_path = pbixPath;
  }

  if (Array.isArray(selectedModelNames) && selectedModelNames.length > 0) {
    sourceConfig.models = selectedModelNames;
  }

  const targetConfig = { type: Array.from(targetConnectors || [])[0] || '' };

  if (targetConfig.type === 'snowflake') {
    if (targetDatabase) targetConfig.database = targetDatabase;
    if (targetSchema) targetConfig.schema = targetSchema;
    // The dry run checks target tables live; it needs the chosen account.
    if (snowflakeAccountId) targetConfig.identity_id = snowflakeAccountId;
  }

  if (targetConfig.type === 'fabric') {
    if (fabricAccountId) targetConfig.identity_id = fabricAccountId;
    if (fabricWorkspaceId) targetConfig.workspace_id = fabricWorkspaceId;
  }

  return { sourceConfig, targetConfig };
}

// Max sources (PBIX files or Fabric models) a single multi-model dry run /
// project may carry -- matches the backend's MAX_PARALLEL_DRY_RUN_FILES /
// MAX_PBIX_FILES cap.
export const MAX_MULTI_SOURCES = 10;

// Source types whose multi-source (>1) dry runs go through the background
// dry-run-jobs endpoint. Everything else (and every single-source run) keeps
// using the synchronous /dry-run endpoint exactly as before.
const MULTI_JOB_SOURCE_TYPES = new Set(['pbix', 'fabric']);

function cleanList(values) {
  const out = [];
  const seen = new Set();
  (Array.isArray(values) ? values : []).forEach((value) => {
    const text = String(value ?? '').trim();
    if (text && !seen.has(text)) {
      seen.add(text);
      out.push(text);
    }
  });
  return out;
}

/**
 * resolveEffectiveSources — the ONE derived description of "what will be
 * dry-run / saved" for the current wizard state. Both handleDryRun() and
 * buildSourceConfig() (project save) read this, so the two can never
 * disagree about which file(s)/model(s) are in play.
 *
 * Returns:
 *   mode            : 'none' | 'single' | 'multi'
 *   sources         : string[]  -- PBIX paths (pbix) or model names (others)
 *   singlePbixPath  : string    -- the pbix_path for a single-PBIX run/save ('' otherwise)
 *   isMulti         : boolean   -- true only when >1 source AND the source type
 *                                  supports the jobs endpoint (pbix/fabric)
 *   useMultiFileList: boolean   -- true when the PBIX "Multiple files" list is the active source
 *
 * Source-mode-specific inputs are only consulted for the ACTIVE mode: a
 * leftover "Multiple files" list never overrides a tagged-folder choice, and
 * "Multiple files" with exactly one uploaded file behaves as a single file.
 */
export function resolveEffectiveSources({
  sourceConnector,
  pbixSourceMode = 'TAG',
  pbixMultiFileMode = false,
  pbixFilePaths = [],
  pbixUploadPath = '',
  selectedPbixFilePath = '',
  selectedModelNames = [],
} = {}) {
  const type = String(sourceConnector || '').toLowerCase();

  if (type === 'pbix') {
    if (pbixSourceMode === 'TAG') {
      const path = String(selectedPbixFilePath || '').trim();
      return {
        mode: path ? 'single' : 'none',
        sources: path ? [path] : [],
        singlePbixPath: path,
        isMulti: false,
        useMultiFileList: false,
      };
    }
    if (pbixMultiFileMode) {
      const paths = cleanList(pbixFilePaths);
      if (paths.length > 1) {
        return { mode: 'multi', sources: paths, singlePbixPath: '', isMulti: true, useMultiFileList: true };
      }
      if (paths.length === 1) {
        return { mode: 'single', sources: paths, singlePbixPath: paths[0], isMulti: false, useMultiFileList: true };
      }
      return { mode: 'none', sources: [], singlePbixPath: '', isMulti: false, useMultiFileList: true };
    }
    const path = String(pbixUploadPath || '').trim();
    return {
      mode: path ? 'single' : 'none',
      sources: path ? [path] : [],
      singlePbixPath: path,
      isMulti: false,
      useMultiFileList: false,
    };
  }

  const names = cleanList(selectedModelNames);
  const isMulti = names.length > 1 && MULTI_JOB_SOURCE_TYPES.has(type);
  return {
    mode: names.length === 0 ? 'none' : (isMulti ? 'multi' : 'single'),
    sources: names,
    singlePbixPath: '',
    isMulti,
    useMultiFileList: false,
  };
}

/**
 * buildMappingSignature — stable string identifying every input that affects
 * a dry run's result. When it changes, any previous dry run (single OR
 * multi-model job) is stale and must be discarded. Arrays are sorted so
 * re-ordering a selection doesn't count as a change.
 */
export function buildMappingSignature(input = {}) {
  const sorted = (value) => [...(Array.isArray(value) ? value : Array.from(value || []))].map(String).sort();
  return JSON.stringify({
    sourceConnector: input.sourceConnector || '',
    targetConnectors: sorted(input.targetConnectors),
    intermediateFormat: input.intermediateFormat || '',
    fabricAccountId: input.fabricAccountId || '',
    selectedConnectionId: input.selectedConnectionId || '',
    fabricWorkspaceId: input.fabricWorkspaceId || '',
    snowflakeAccountId: input.snowflakeAccountId || '',
    snowflakeDatabase: input.snowflakeDatabase || '',
    snowflakeSchema: input.snowflakeSchema || '',
    targetDatabase: input.targetDatabase || '',
    targetSchema: input.targetSchema || '',
    targetAccount: input.targetAccount || '',
    targetWarehouse: input.targetWarehouse || '',
    databricksAccountId: input.databricksAccountId || '',
    selectedModels: sorted(input.selectedModels),
    selectedModelNames: sorted(input.selectedModelNames),
    selectedDatabricksTables: sorted(input.selectedDatabricksTables),
    pbixPath: input.pbixPath || '',
    pbixSourceMode: input.pbixSourceMode || '',
    pbixMultiFileMode: Boolean(input.pbixMultiFileMode),
    // Order-sensitive on purpose: the job's file order follows this list.
    pbixFilePaths: Array.isArray(input.pbixFilePaths) ? input.pbixFilePaths.map(String) : [],
    effectiveSources: Array.isArray(input.effectiveSources) ? input.effectiveSources.map(String) : [],
  });
}
