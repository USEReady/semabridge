/**
 * validateTargetName — validates a target field name against platform-specific rules.
 *
 * Extracted from CreateProjectPage.jsx so it can be shared with FieldMappingEditor
 * and other components without circular imports.
 */

const SNOWFLAKE_RESERVED = new Set([
  'SELECT', 'GROUP', 'ORDER', 'TABLE', 'COLUMN', 'DATE', 'FROM', 'WHERE',
  'BY', 'JOIN', 'VIEW', 'UNION', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP',
]);

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

/**
 * Validates a target field name against platform-specific rules.
 *
 * @param {string} value          - The target name to validate
 * @param {string} targetPlatform - e.g. "snowflake", "fabric", "databricks"
 * @param {string} sourceType     - Source data type (e.g. "boolean")
 * @param {string} targetType     - Target data type (e.g. "date")
 * @returns {{ isValid: boolean, code: string, message: string, suggestion: string }}
 */
export function validateTargetName(value, targetPlatform, sourceType, targetType) {
  const next = String(value || '').trim();
  if (!next) {
    return { isValid: false, code: 'EMPTY_TARGET', message: 'Target field cannot be empty.', suggestion: 'UNNAMED' };
  }

  const platform = String(targetPlatform || '').toLowerCase();
  const upper = next.toUpperCase();

  if (platform.includes('snowflake') && SNOWFLAKE_RESERVED.has(upper)) {
    return {
      isValid: false,
      code: 'RESERVED_KEYWORD',
      message: 'Target field is a Snowflake reserved keyword.',
      suggestion: `COL_${upper}`,
    };
  }

  const sanitized = sanitizeIdentifier(next);
  if (platform.includes('snowflake') && sanitized !== upper) {
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
