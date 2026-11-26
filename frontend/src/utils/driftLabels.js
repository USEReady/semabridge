// driftLabels.js - Centralized labels, mappings, and reason codes for Drift Monitoring

export const REASON_CODE_DETAILS = {
  LIVE_CAPTURE_BASELINE: {
    title: 'Live Baseline Captured',
    explanation: 'A baseline was captured from the live warehouse rather than a verified deploy. Redeploy to record an authoritative baseline.',
    actionLabel: 'Redeploy Baseline',
    actionType: 'deploy',
    badge: 'Live Baseline Captured',
    severity: 'warning',
  },
  NO_DEPLOY_BASELINE: {
    title: 'No Deploy Baseline',
    explanation: 'No deploy baseline is recorded for this view. Deploy this project to establish one.',
    actionLabel: 'Deploy Now',
    actionType: 'deploy',
    badge: 'Needs Deploy',
    severity: 'warning',
  },
  NO_DEPLOYED_VIEW: {
    title: 'No Deployed View',
    explanation: 'This project has not been deployed to the warehouse yet.',
    actionLabel: 'Deploy Project',
    actionType: 'deploy',
    badge: 'Not Deployed',
    severity: 'warning',
  },
  WAREHOUSE_UNREACHABLE: {
    title: 'Warehouse Unreachable',
    explanation: 'Could not connect to the warehouse. Check network connectivity and host settings.',
    actionLabel: 'Check Connection',
    actionType: 'settings',
    badge: 'Connection Error',
    severity: 'danger',
  },
  WAREHOUSE_AUTH_FAILED: {
    title: 'Authentication Failed',
    explanation: 'Warehouse credentials or permissions failed. Check warehouse credentials.',
    actionLabel: 'Update Credentials',
    actionType: 'settings',
    badge: 'Auth Failed',
    severity: 'danger',
  },
  PLATFORM_NOT_SUPPORTED: {
    title: 'Platform Not Supported',
    explanation: 'Drift monitoring is not yet available for this warehouse platform.',
    actionLabel: null,
    actionType: null,
    badge: 'Unsupported',
    severity: 'neutral',
  },
  VIEW_NOT_FOUND: {
    title: 'View Not Found',
    explanation: 'The semantic view does not exist in the warehouse schema.',
    actionLabel: 'Deploy View',
    actionType: 'deploy',
    badge: 'Missing View',
    severity: 'warning',
  },
  PARSE_ERROR: {
    title: 'Definition Parse Error',
    explanation: 'The warehouse view definition could not be read or parsed.',
    actionLabel: 'Retry Check',
    actionType: 'check_now',
    badge: 'Parse Error',
    severity: 'danger',
  },
  NO_COMPARABLE_OBJECTS: {
    title: 'No Comparable Metrics',
    explanation: 'There are no metrics or comparable semantic objects in this view.',
    actionLabel: 'Edit Model',
    actionType: 'edit_model',
    badge: 'Empty View',
    severity: 'neutral',
  },
  CHECK_IN_PROGRESS: {
    title: 'Check In Progress',
    explanation: 'A drift comparison is currently running for this view.',
    actionLabel: 'Refreshing...',
    actionType: 'refresh',
    badge: 'Checking...',
    severity: 'info',
  },
  NEVER_CHECKED: {
    title: 'No Check Yet',
    explanation: 'No drift comparison has been run yet for this project.',
    actionLabel: 'Check Drift Now',
    actionType: 'check_now',
    badge: 'No Check Yet',
    severity: 'neutral',
  },
  SOURCE_PATH_MISMATCH: {
    title: 'Definition Format Mismatch',
    explanation: 'The live definition was introspected through a different format than the baseline (e.g. YAML vs SQL DDL).',
    actionLabel: 'Redeploy Baseline',
    actionType: 'deploy',
    badge: 'Format Mismatch',
    severity: 'warning',
  },
  NO_PROJECT: {
    title: 'Select a Project',
    explanation: 'Choose a project from the left panel to inspect drift status.',
    actionLabel: 'Select Project',
    actionType: 'select_project',
    badge: 'No Project',
    severity: 'neutral',
  },
};

export function getReasonDetails(reasonCode, fallbackMessage = '') {
  if (!reasonCode) {
    if (!fallbackMessage) {
      return REASON_CODE_DETAILS.NEVER_CHECKED;
    }
  }
  const normalized = String(reasonCode || fallbackMessage)
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, '_');

  if (REASON_CODE_DETAILS[normalized]) {
    return REASON_CODE_DETAILS[normalized];
  }
  if (normalized.includes('NO_DEPLOY_BASELINE') || normalized.includes('REDEPLOY_TO_RECORD')) {
    return REASON_CODE_DETAILS.NO_DEPLOY_BASELINE;
  }
  if (normalized.includes('NO_DEPLOYED') || normalized.includes('NO_ONTOLOGY')) {
    return REASON_CODE_DETAILS.NO_DEPLOYED_VIEW;
  }
  if (normalized.includes('AUTH') || normalized.includes('250001') || normalized.includes('401') || normalized.includes('PERMISSION')) {
    return REASON_CODE_DETAILS.WAREHOUSE_AUTH_FAILED;
  }
  if (normalized.includes('NOT_SUPPORTED') || normalized.includes('UNSUPPORTED')) {
    return REASON_CODE_DETAILS.PLATFORM_NOT_SUPPORTED;
  }
  if (normalized.includes('NOT_FOUND') || normalized.includes('NOT_EXIST') || normalized.includes('002003')) {
    return REASON_CODE_DETAILS.VIEW_NOT_FOUND;
  }
  if (normalized.includes('PARSE') || normalized.includes('SYNTAX')) {
    return REASON_CODE_DETAILS.PARSE_ERROR;
  }
  if (normalized.includes('NO_COMPARABLE') || normalized.includes('NO_METRICS')) {
    return REASON_CODE_DETAILS.NO_COMPARABLE_OBJECTS;
  }
  if (normalized.includes('PROGRESS')) {
    return REASON_CODE_DETAILS.CHECK_IN_PROGRESS;
  }
  if (normalized.includes('UNAVAILABLE') || normalized.includes('CONNECTION') || normalized.includes('TIMEOUT') || normalized.includes('REFUSED')) {
    return REASON_CODE_DETAILS.WAREHOUSE_UNREACHABLE;
  }
  if (normalized.includes('NEVER') || normalized.includes('FRESH')) {
    return REASON_CODE_DETAILS.NEVER_CHECKED;
  }

  return {
    title: 'Needs Attention',
    explanation: fallbackMessage || String(reasonCode),
    actionLabel: 'Check Drift Now',
    actionType: 'check_now',
    badge: 'Needs Attention',
    severity: 'warning',
  };
}

export function getReasonLabel(reasonCode, fallbackMessage = '') {
  const details = getReasonDetails(reasonCode, fallbackMessage);
  return details.explanation;
}

export const CATEGORY_LABELS = {
  ADDITION: 'Added in warehouse',
  DELETION: 'Removed from warehouse',
  EXPRESSION_DRIFT: 'Formula changed',
  DESCRIPTION_DRIFT: 'Description changed',
};

export const CATEGORY_DETAILS = {
  ADDITION: { label: 'Addition', title: 'Added in Warehouse', color: 'var(--color-primary, #3b82f6)' },
  DELETION: { label: 'Deletion', title: 'Removed from Warehouse', color: 'var(--color-danger, #ef4444)' },
  EXPRESSION_DRIFT: { label: 'Expression Drift', title: 'Formula Changed', color: 'var(--accent-purple, #a855f7)' },
  DESCRIPTION_DRIFT: { label: 'Description Drift', title: 'Description Changed', color: 'var(--color-warning, #f59e0b)' },
};

export const LEGACY_TYPE_MAP = {
  UNMANAGED_METRIC_IN_SNOWFLAKE: 'ADDITION',
  KPI_ADD: 'ADDITION',
  METRIC_DROPPED_FROM_SNOWFLAKE: 'DELETION',
  KPI_DROP: 'DELETION',
  EXPRESSION_MISMATCH: 'EXPRESSION_DRIFT',
  KPI_EXPR: 'EXPRESSION_DRIFT',
  DESCRIPTION_CHANGED: 'DESCRIPTION_DRIFT',
};

export function getCanonicalCategory(categoryOrType) {
  if (!categoryOrType) return 'EXPRESSION_DRIFT';
  const upper = String(categoryOrType).toUpperCase();
  if (CATEGORY_LABELS[upper]) return upper;
  if (LEGACY_TYPE_MAP[upper]) return LEGACY_TYPE_MAP[upper];
  if (upper.includes('ADDITION')) return 'ADDITION';
  if (upper.includes('DELETION') || upper.includes('MISSING') || upper.includes('DROP')) return 'DELETION';
  if (upper.includes('DESCRIPTION')) return 'DESCRIPTION_DRIFT';
  return 'EXPRESSION_DRIFT';
}

export function getCategoryLabel(categoryOrType) {
  const canonical = getCanonicalCategory(categoryOrType);
  return CATEGORY_LABELS[canonical] || canonical;
}

export const STATUS_CARD_DETAILS = {
  IN_SYNC: {
    status: 'IN_SYNC',
    title: 'In Sync',
    badge: 'In Sync',
    severity: 'success',
    color: 'var(--color-success, #22c55e)',
    bg: 'rgba(34, 197, 94, 0.08)',
    border: 'rgba(34, 197, 94, 0.25)',
  },
  DRIFT_DETECTED: {
    status: 'DRIFT_DETECTED',
    title: 'Drift Detected',
    badge: 'Drift Detected',
    severity: 'danger',
    color: 'var(--color-danger, #ef4444)',
    bg: 'rgba(239, 68, 68, 0.08)',
    border: 'rgba(239, 68, 68, 0.25)',
  },
  CHECK_IN_PROGRESS: {
    status: 'CHECK_IN_PROGRESS',
    title: 'Check In Progress',
    badge: 'Checking...',
    severity: 'info',
    color: 'var(--color-info, #3b82f6)',
    bg: 'rgba(59, 130, 246, 0.08)',
    border: 'rgba(59, 130, 246, 0.25)',
  },
  NEVER_CHECKED: {
    status: 'NEVER_CHECKED',
    title: 'No Check Yet',
    badge: 'Not Checked',
    severity: 'neutral',
    color: 'var(--text-secondary, #94a3b8)',
    bg: 'rgba(148, 163, 184, 0.08)',
    border: 'rgba(148, 163, 184, 0.25)',
  },
  NOT_CHECKED: {
    status: 'NOT_CHECKED',
    title: 'Needs Attention',
    badge: 'Not Checked',
    severity: 'warning',
    color: 'var(--color-warning, #f59e0b)',
    bg: 'rgba(245, 158, 11, 0.08)',
    border: 'rgba(245, 158, 11, 0.25)',
  },
};

export const STATUS_LABELS = {
  IN_SYNC: 'In Sync',
  DRIFT_DETECTED: 'Drift Detected',
  NOT_CHECKED: 'Needs Attention',
  PARTIAL: 'Partial',
  CHECK_IN_PROGRESS: 'Check in progress',
  NEVER_CHECKED: 'No Check Yet',
};

export function getStatusDetails(status) {
  const upper = String(status || '').toUpperCase();
  if (upper === 'IN_SYNC') return STATUS_CARD_DETAILS.IN_SYNC;
  if (upper === 'DRIFT_DETECTED') return STATUS_CARD_DETAILS.DRIFT_DETECTED;
  if (upper === 'CHECK_IN_PROGRESS') return STATUS_CARD_DETAILS.CHECK_IN_PROGRESS;
  if (upper === 'NEVER_CHECKED') return STATUS_CARD_DETAILS.NEVER_CHECKED;
  return STATUS_CARD_DETAILS.NOT_CHECKED;
}

export function getStatusLabel(status) {
  if (!status) return 'No Check Yet';
  const upper = String(status).toUpperCase();
  return STATUS_LABELS[upper] || status;
}

/**
 * Unified drift state computation used identically by project lists and status cards.
 * Returns { status, reasonCode, reason, pendingCount, checkedAt, baselineSource, baselineAt, viewsChecked, platform }
 */
export function computeProjectDriftState(project, targetInfoMap = {}, eventsList = []) {
  if (!project) {
    return {
      status: 'NEVER_CHECKED',
      reasonCode: 'NEVER_CHECKED',
      pendingCount: 0,
      checkedAt: null,
      baselineSource: null,
      baselineAt: null,
      viewsChecked: [],
      platform: 'NOT_SET',
    };
  }

  const pid = project.id || project.project_id || project.name || '';
  const cleanId = String(pid).toLowerCase().replace(/^proj-/, '').trim();
  const pTarget = (cleanId && targetInfoMap[cleanId])
    || (pid && targetInfoMap[pid])
    || {};

  // Match events for this project
  const pEvents = (eventsList || []).filter((e) => {
    if (!e || !e.project_id) return false;
    const ePid = String(e.project_id).trim();
    const eClean = ePid.toLowerCase().replace(/^proj-/, '');
    return ePid === pid || eClean === cleanId;
  });

  const pendingEvents = pEvents.filter((e) => e.status === 'pending');
  const pendingCount = pendingEvents.length;

  if (pendingCount > 0) {
    return {
      status: 'DRIFT_DETECTED',
      reasonCode: null,
      pendingCount,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  // A project that has never been checked or has no target entry
  if (!pTarget.checked_at && !pTarget.status && !pTarget.reason_code) {
    return {
      status: 'NEVER_CHECKED',
      reasonCode: 'NEVER_CHECKED',
      pendingCount: 0,
      checkedAt: null,
      baselineSource: null,
      baselineAt: null,
      viewsChecked: [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  // A live-capture baseline or missing baseline must never be reported as IN_SYNC
  if (pTarget.baseline_source === 'live_capture') {
    return {
      status: 'NOT_CHECKED',
      reasonCode: 'LIVE_CAPTURE_BASELINE',
      reason: 'A baseline was captured from the live warehouse rather than a verified deploy. Redeploy to record an authoritative baseline.',
      pendingCount: 0,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }
  if ((!pTarget.baseline_source || pTarget.baseline_source === 'missing') && (pTarget.status === 'IN_SYNC' || !pTarget.status)) {
    return {
      status: 'NOT_CHECKED',
      reasonCode: 'NO_DEPLOY_BASELINE',
      reason: 'No deploy baseline is recorded for this view. Deploy this project to establish one.',
      pendingCount: 0,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  if (pTarget.status === 'IN_SYNC') {
    return {
      status: 'IN_SYNC',
      reasonCode: null,
      pendingCount: 0,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  if (pTarget.reason_code) {
    return {
      status: 'NOT_CHECKED',
      reasonCode: pTarget.reason_code,
      reason: pTarget.reason || pTarget.not_checked_reason,
      pendingCount: 0,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  if (pTarget.status && pTarget.status !== 'NOT_CHECKED' && pTarget.status !== 'NEVER_CHECKED') {
    return {
      status: pTarget.status,
      reasonCode: pTarget.reason_code || null,
      pendingCount: 0,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  if (pTarget.checked_at && (pTarget.reason || pTarget.not_checked_reason)) {
    const rawReason = pTarget.reason_code || pTarget.not_checked_reason || pTarget.reason;
    let resolvedCode = pTarget.reason_code;
    if (!resolvedCode && rawReason) {
      const details = getReasonDetails(rawReason);
      const matchedKey = Object.keys(REASON_CODE_DETAILS).find((k) => REASON_CODE_DETAILS[k] === details);
      resolvedCode = matchedKey || rawReason;
    }
    return {
      status: 'NOT_CHECKED',
      reasonCode: resolvedCode || 'WAREHOUSE_UNREACHABLE',
      reason: pTarget.reason || pTarget.not_checked_reason,
      pendingCount: 0,
      checkedAt: pTarget.checked_at,
      baselineSource: pTarget.baseline_source,
      baselineAt: pTarget.baseline_at,
      viewsChecked: pTarget.views_checked || [],
      platform: pTarget.platform || project.platform || 'NOT_SET',
    };
  }

  return {
    status: 'NEVER_CHECKED',
    reasonCode: 'NEVER_CHECKED',
    pendingCount: 0,
    checkedAt: null,
    baselineSource: null,
    baselineAt: null,
    viewsChecked: [],
    platform: pTarget.platform || project.platform || 'NOT_SET',
  };
}

/**
 * Sorting priority:
 * 0: Needs attention (reason code present, e.g. WAREHOUSE_UNREACHABLE, NO_DEPLOY_BASELINE)
 * 1: Drift detected (pendingCount > 0)
 * 2: In sync
 * 3: Never checked
 */
export function getDriftStateSortPriority(driftState) {
  if (!driftState) return 3;
  if (driftState.status === 'NOT_CHECKED' && driftState.reasonCode && driftState.reasonCode !== 'NEVER_CHECKED') {
    return 0; // Needs Attention
  }
  if (driftState.status === 'DRIFT_DETECTED' || driftState.pendingCount > 0) {
    return 1; // Drift Detected
  }
  if (driftState.status === 'IN_SYNC') {
    return 2; // In Sync
  }
  return 3; // Never Checked
}

/**
 * Strips [ADDED: ...] and [REMOVED: ...] diff markers from marked description text
 * so clean description text can be copied or processed.
 */
export function stripDiffMarkers(text) {
  if (!text) return '';
  return String(text).replace(/\[(?:ADDED|REMOVED):\s*([\s\S]*?)\]/g, '$1');
}

/**
 * Parses marked description text into segments of { type: 'text' | 'ADDED' | 'REMOVED', text: string }
 */
export function parseDiffSegments(text) {
  if (!text) return [];
  const str = String(text);
  const segments = [];
  const regex = /\[(ADDED|REMOVED):\s*([\s\S]*?)\]/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(str)) !== null) {
    if (match.index > lastIndex) {
      segments.push({
        type: 'text',
        text: str.slice(lastIndex, match.index),
      });
    }
    segments.push({
      type: match[1],
      text: match[2],
    });
    lastIndex = regex.lastIndex;
  }

  if (lastIndex < str.length) {
    segments.push({
      type: 'text',
      text: str.slice(lastIndex),
    });
  }

  return segments;
}
