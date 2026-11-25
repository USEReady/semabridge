/**
 * Pure step navigation & pre-Step-4 duplicate-check helpers for CreateProjectPage + WizardHeader.
 */

export function isStep1Valid({ name = '', sourceConnector = '', targetConnectors, intermediateFormat = '' } = {}) {
  const targetCount = targetConnectors instanceof Set
    ? targetConnectors.size
    : (Array.isArray(targetConnectors) ? targetConnectors.length : 0);
  return Boolean(String(name || '').trim()) && Boolean(sourceConnector) && targetCount > 0 && Boolean(intermediateFormat);
}

export function hasSelectedWizardSource({
  sourceConnector = '',
  effectiveSources,
  selectedModelNames = [],
  selectedDatabricksTables,
  pbixUploadPath = '',
  selectedPbixFilePath = '',
  snowflakeDatabase = '',
  snowflakeSchema = '',
} = {}) {
  const type = String(sourceConnector || '').toLowerCase();
  if (type === 'pbix') {
    const pbixList = Array.isArray(effectiveSources?.sources) ? effectiveSources.sources.filter(Boolean) : [];
    return pbixList.length > 0 || Boolean(String(effectiveSources?.singlePbixPath || pbixUploadPath || selectedPbixFilePath || '').trim());
  }
  if (type === 'tableau') {
    return (
      Boolean(String(effectiveSources?.singlePbixPath || pbixUploadPath || selectedPbixFilePath || '').trim())
      || (Array.isArray(selectedModelNames) && selectedModelNames.length > 0)
    );
  }
  if (type === 'databricks') {
    const count = selectedDatabricksTables instanceof Set
      ? selectedDatabricksTables.size
      : (Array.isArray(selectedDatabricksTables) ? selectedDatabricksTables.filter(Boolean).length : 0);
    return count > 0 || (Array.isArray(selectedModelNames) && selectedModelNames.length > 0);
  }
  if (type === 'snowflake') {
    return (
      (Array.isArray(selectedModelNames) && selectedModelNames.length > 0)
      || (Boolean(String(snowflakeDatabase || '').trim()) && Boolean(String(snowflakeSchema || '').trim()))
    );
  }
  return Array.isArray(selectedModelNames) && selectedModelNames.length > 0;
}

export function canJumpToWizardStep(targetStep, state = {}) {
  const current = Number(state.currentStep || 1);
  const target = Number(targetStep || 1);
  if (target < 1 || target > 6) return false;
  if (state.editMode || target <= current) return true;

  if (!isStep1Valid(state)) return false;
  if (target <= 3) return true;

  if (!hasSelectedWizardSource(state)) return false;
  if (target <= 5) return true;

  // Step 6 (Finish) in create mode requires Dry Run readiness from Step 5.
  return Boolean(state.mappingReadyToProceed);
}

export function resolveWizardStepJump(targetStep, state = {}) {
  const current = Number(state.currentStep || 1);
  const target = Math.max(1, Math.min(6, Number(targetStep || 1)));

  if (target <= current) {
    return { allowed: true, targetStep: target, requiresDuplicateCheck: false };
  }

  if (!isStep1Valid(state)) {
    return { allowed: false, fallbackStep: 1, reason: 'step1_invalid', requiresDuplicateCheck: false };
  }

  if (target >= 4 && !state.editMode && !hasSelectedWizardSource(state)) {
    return { allowed: false, fallbackStep: 3, reason: 'no_source_selected', requiresDuplicateCheck: false };
  }

  if (target === 6 && !state.editMode && !state.mappingReadyToProceed) {
    return { allowed: false, fallbackStep: 5, reason: 'dry_run_incomplete', requiresDuplicateCheck: false };
  }

  const crossingIntoPostSource = current <= 3 && target >= 4;
  const sig = String(state.currentDuplicateSignature || '');
  const ack = String(state.acknowledgedDuplicateSignature || '');
  const requiresDuplicateCheck = crossingIntoPostSource && (!sig || sig !== ack);

  return {
    allowed: true,
    targetStep: target,
    requiresDuplicateCheck,
  };
}

export function buildDuplicateCheckSignature({
  sourceConnector = '',
  sources = [],
  targetType = '',
  targetDatabase = '',
  targetSchema = '',
} = {}) {
  const cleanSources = [...(Array.isArray(sources) ? sources : Array.from(sources || []))]
    .map((s) => String(s || '').trim())
    .filter(Boolean)
    .sort();
  return JSON.stringify({
    sourceConnector: String(sourceConnector || '').toLowerCase(),
    sources: cleanSources,
    targetType: String(targetType || '').toLowerCase(),
    targetDatabase: String(targetDatabase || '').trim().toUpperCase(),
    targetSchema: String(targetSchema || '').trim().toUpperCase(),
  });
}

