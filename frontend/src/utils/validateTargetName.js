/**
 * validateTargetName — validates a target field name against platform-specific rules.
 *
 * Shared by CreateProjectPage, FieldMappingEditor and the wizard. For
 * Snowflake it applies the same rule the deploy uses (collisionResolution.js
 * toSnowflakeAlias, pinned to the backend's snowflake_target_name): columns
 * use Snowflake's real reserved keywords (DATE is fine), metrics the broader
 * legacy list (DATE -> COL_DATE).
 */

import {
  SNOWFLAKE_LEGACY_RESERVED_WORDS,
  SNOWFLAKE_RESERVED_WORDS,
  toSnowflakeAlias,
} from './collisionResolution.js';

function sanitizeIdentifier(value) {
  const raw = String(value || '').trim();
  if (!raw) return 'UNNAMED';
  const cleaned = raw
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!cleaned) return 'UNNAMED';
  return /^\d/.test(cleaned) ? `N_${cleaned}` : cleaned;
}

/** 'metric' for measures/metrics, 'column' otherwise (row.entity_kind / row.field_type). */
export function targetNameKind(rowOrKind) {
  const values = typeof rowOrKind === 'string'
    ? [rowOrKind]
    : [rowOrKind?.entity_kind, rowOrKind?.field_type, rowOrKind?.kind];
  return values.some((v) => ['metric', 'measure'].includes(String(v || '').toLowerCase())) ? 'metric' : 'column';
}

/**
 * Validates a target field name against platform-specific rules.
 *
 * @param {string} value          - The target name to validate
 * @param {string} targetPlatform - e.g. "snowflake", "fabric", "databricks"
 * @param {string} sourceType     - Source data type (e.g. "boolean")
 * @param {string} targetType     - Target data type (e.g. "date")
 * @param {string} kind           - 'column' (default) or 'metric'/'measure'
 * @returns {{ isValid: boolean, code: string, message: string, suggestion: string }}
 */
export function validateTargetName(value, targetPlatform, sourceType, targetType, kind = 'column') {
  const next = String(value || '').trim();
  if (!next) {
    return { isValid: false, code: 'EMPTY_TARGET', message: 'Target field cannot be empty.', suggestion: 'UNNAMED' };
  }

  const platform = String(targetPlatform || '').toLowerCase();
  const isSnowflake = platform.includes('snowflake');
  const rowKind = targetNameKind(kind);
  const upper = next.toUpperCase();
  const reserved = rowKind === 'metric' ? SNOWFLAKE_LEGACY_RESERVED_WORDS : SNOWFLAKE_RESERVED_WORDS;

  if (isSnowflake && reserved.has(upper.toLowerCase())) {
    return {
      isValid: false,
      code: 'RESERVED_KEYWORD',
      message: 'Target field is a Snowflake reserved keyword.',
      suggestion: `COL_${upper}`,
    };
  }

  const sanitized = isSnowflake ? toSnowflakeAlias(next, rowKind) : sanitizeIdentifier(next);
  if (isSnowflake && sanitized !== upper) {
    return {
      isValid: false,
      code: 'UNSUPPORTED_CHARACTERS',
      message: 'Target field has unsupported characters for Snowflake.',
      suggestion: sanitized,
    };
  }

  if (String(sourceType || '').toLowerCase() === 'boolean' && String(targetType || '').toLowerCase() === 'date') {
    return {
      isValid: false,
      code: 'INCOMPATIBLE_TYPE',
      message: 'Source and target data types are incompatible.',
      suggestion: sanitized,
    };
  }

  return { isValid: true, code: 'OK', message: '', suggestion: sanitized };
}
