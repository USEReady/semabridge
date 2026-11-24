export function shortDeterministicHash(value) {
  let hash = 2166136261;
  const text = String(value || '');
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

// ── Snowflake identifier rules ─────────────────────────────────────────────
// Exact mirror of the backend's IdentifierSanitizer.to_alias
// (src/semabridge/utils/identifiers.py) -- the function the Snowflake emitter,
// the mapping engine and config overrides all apply. A name picked here is
// therefore deployed verbatim (no second sanitization renames it).
// collisionResolution.test.js pins this against backend outputs.
// Keywords Snowflake actually reserves for identifiers (backend
// SNOWFLAKE_ALIAS_RESERVED_WORDS, from docs.snowflake.com/en/sql-reference/
// reserved-keywords). Type keywords and words like DATE, KEY, COUNT, USER are
// not reserved, so 'Date' deploys as DATE, not COL_DATE.
export const SNOWFLAKE_RESERVED_WORDS = new Set([
  'all', 'alter', 'and', 'any', 'as', 'between', 'by', 'check', 'column', 'connect',
  'create', 'current', 'delete', 'distinct', 'drop', 'else', 'exists', 'following',
  'for', 'from', 'grant', 'group', 'having', 'ilike', 'in', 'increment', 'insert',
  'intersect', 'into', 'is', 'like', 'minus', 'not', 'null', 'of', 'on', 'or',
  'order', 'qualify', 'regexp', 'revoke', 'rlike', 'row', 'rows', 'sample', 'select',
  'set', 'some', 'start', 'table', 'tablesample', 'then', 'to', 'trigger', 'union',
  'unique', 'update', 'values', 'whenever', 'where', 'window', 'with',
  'current_date', 'current_time', 'current_timestamp', 'current_user',
  'localtime', 'localtimestamp',
  // Reserved by Snowflake in some contexts (joins, FROM/WHERE, CAST ...).
  'account', 'case', 'cast', 'connection', 'constraint', 'cross', 'database',
  'false', 'full', 'gscluster', 'inner', 'issue', 'join', 'lateral', 'left',
  'natural', 'organization', 'right', 'schema', 'true', 'try_cast', 'using',
  'view', 'when',
]);

// The backend's broader list (SNOWFLAKE_RESERVED_WORDS): still used for
// METRIC names and physical columns, whose names and SQL references must
// not change. Column (dimension/fact) names use SNOWFLAKE_RESERVED_WORDS.
export const SNOWFLAKE_LEGACY_RESERVED_WORDS = new Set([
  'account', 'all', 'alter', 'and', 'any', 'as', 'avg', 'between', 'boolean', 'by', 'case', 'cast',
  'check', 'column', 'connect', 'connection', 'constraint', 'count', 'create', 'cross', 'current',
  'current_date', 'current_time', 'current_timestamp', 'current_user', 'database', 'date', 'default',
  'delete', 'distinct', 'drop', 'else', 'end', 'except', 'exists', 'false', 'float', 'following', 'for',
  'foreign', 'from', 'full', 'grant', 'group', 'gscluster', 'having', 'ilike', 'in', 'increment', 'index',
  'inner', 'insert', 'integer', 'intersect', 'into', 'is', 'issue', 'join', 'key', 'lateral', 'left',
  'like', 'limit', 'localtime', 'localtimestamp', 'max', 'min', 'minus', 'natural', 'not', 'null',
  'number', 'of', 'offset', 'on', 'or', 'order', 'organization', 'over', 'partition', 'pipe', 'primary',
  'qualify', 'range', 'references', 'regexp', 'revoke', 'right', 'rlike', 'role', 'row', 'rows', 'sample',
  'schema', 'select', 'sequence', 'session', 'set', 'some', 'stage', 'start', 'stream', 'string', 'sum',
  'table', 'tablesample', 'task', 'then', 'timestamp', 'to', 'trigger', 'true', 'try_cast', 'union',
  'unique', 'update', 'user', 'using', 'value', 'values', 'varchar', 'view', 'warehouse', 'when',
  'whenever', 'where', 'with',
]);


// Same order as the backend's replacement table.
const SNOWFLAKE_CHAR_REPLACEMENTS = [
  ['%', 'PCT'], ['$', 'DOL'], ['@', 'AT'], ['#', 'NUM'], ['&', 'AND'], ['*', 'STAR'], ['+', 'PLUS'],
  ['=', 'EQ'], ['?', 'Q'], ['!', 'EXCL'], ['<', 'LT'], ['>', 'GT'], ['{', '_'], ['}', '_'], ['|', '_'],
  [';', '_'], [':', '_'], [',', '_'], ['.', '_'], ['/', '_'], ['\\', '_'], ['(', '_'], [')', '_'],
  ['[', '_'], [']', '_'], [' ', '_'], ['-', '_'], ["'", ''], ['"', ''],
];

const isMetricKind = (kind) => ['metric', 'measure'].includes(String(kind || '').toLowerCase());

/**
 * The backend's Snowflake name for a field, character for character:
 * columns -> IdentifierSanitizer.sanitize_semantic_alias (only real reserved
 * words get COL_, 'Date' -> DATE); metrics -> to_alias (broader list).
 */
export function toSnowflakeAlias(value, kind = 'column') {
  let name = String(value ?? '');
  if (!name) return '';
  SNOWFLAKE_CHAR_REPLACEMENTS.forEach(([from, to]) => { name = name.split(from).join(to); });
  let clean = name
    .replace(/[^A-Za-z0-9_$]/gu, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!clean) return 'COLUMN_UNKNOWN';
  if (/^[0-9]/.test(clean)) clean = `_${clean}`;
  let result = clean.toUpperCase();
  const reserved = isMetricKind(kind) ? SNOWFLAKE_LEGACY_RESERVED_WORDS : SNOWFLAKE_RESERVED_WORDS;
  if (reserved.has(result.toLowerCase())) result = `COL_${result}`;
  return result;
}

/**
 * Physical column name in the Snowflake table (backend
 * IdentifierSanitizer.sanitize_column, broad legacy list): 'Date' -> COL_DATE,
 * 'Sales %' -> SALES_PCT. Differs from the semantic (dimension) name for
 * type words such as DATE, VALUE, KEY.
 */
export function toPhysicalColumnName(value) {
  return toSnowflakeAlias(value, 'metric');
}

/** Generic identifier rules (non-Snowflake targets): backend sanitize_identifier. */
export function toGenericIdentifier(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const cleaned = raw
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!cleaned) return '';
  return /^\d/.test(cleaned) ? `N_${cleaned}` : cleaned;
}

/**
 * Target-name sanitizer used across the mapping UI. Defaults to the
 * Snowflake emitter's rules (to_alias): "% Margin" -> PCT_MARGIN,
 * "Order" -> COL_ORDER, "2024 Sales" -> _2024_SALES.
 */
export function sanitizeMappingName(value, targetType = 'snowflake', kind = 'column') {
  if (!String(value ?? '').trim()) return '';
  return usesSnowflakeNaming(targetType || 'snowflake')
    ? toSnowflakeAlias(value, kind)
    : toGenericIdentifier(value);
}

/**
 * Targets whose names follow Snowflake's naming rule. Databricks reuses it
 * (backend: target_rules/databricks/naming.py), so its target names match
 * Snowflake's and "Margin" / "Margin %" never collide.
 */
export function usesSnowflakeNaming(targetType) {
  const t = String(targetType || '').toLowerCase();
  return t === 'snowflake' || t === 'databricks';
}

/**
 * Keystroke-friendly normalisation for a name being typed: upper-cases and
 * turns illegal characters into "_" but keeps a trailing "_" (so SALES_AMT
 * can be typed) and does not apply reserved-word prefixes yet. The final
 * name is sanitizeMappingName(typed) -- see finalizeTypedName.
 */
export function normalizeTypedName(value) {
  return String(value ?? '').toUpperCase().replace(/[^A-Z0-9_]/g, '_');
}

export function finalizeTypedName(value, targetType = 'snowflake', kind = 'column') {
  return sanitizeMappingName(value, targetType, kind);
}

/** The namespace a row's target name must be unique in: its logical table
 * for columns (Snowflake semantic views qualify names by table), the model
 * for measures. Uses the backend's mapping_scope when present. */
export function rowNamespace(row) {
  const scope = String(row?.mapping_scope || '').trim();
  if (scope) return scope;
  const kind = String(row?.field_type || row?.entity_kind || '').toLowerCase();
  if (kind === 'measure' || kind === 'metric') return 'metric';
  return `column::${String(resolveColumnSourceTable(row) || row?.parent_source_path || '').toLowerCase()}`;
}

export function parseDatasetFromPath(pathValue) {
  const path = String(pathValue || '').trim();
  if (!path) return '';
  const direct = /^datasets\.([^.]+)$/i.exec(path);
  if (direct?.[1]) return String(direct[1]).trim();
  const nested = /^datasets\.([^.]+)\./i.exec(path);
  if (nested?.[1]) return String(nested[1]).trim();
  return '';
}

export function resolveColumnSourceTable(row, parentTable = '') {
  const direct = String(row?.source_table || '').trim();
  if (direct) return direct;

  const fromPath = parseDatasetFromPath(row?.source_path);
  if (fromPath) return fromPath;

  const fromParentPath = parseDatasetFromPath(row?.parent_source_path);
  if (fromParentPath) return fromParentPath;

  return String(parentTable || row?.parent_table || row?.source_table_name || '').trim();
}

export function buildSourceFingerprint(mapping, column) {
  return [
    mapping?.source_schema,
    mapping?.schema,
    mapping?.source,
    column?.source_table_name,
    column?.parent_table,
    column?.source_path,
    column?.source,
  ].map(part => String(part || '').trim()).filter(Boolean).join('.');
}

export function normalizeTargetStatus(item) {
  if (item?.collision_detected) return 'collision';
  const explicit = String(item?.status || '').toLowerCase();
  if (!String(item?.target || item?.target_name || item?.target_field || '').trim()) return 'unmapped';
  if (explicit === 'manual') return 'manual';
  return 'auto';
}

export function applyHashDeduplication(mappings) {
  return (mappings || []).map((mapping) => {
    const columns = Array.isArray(mapping?.columns) ? mapping.columns : [];
    if (columns.length === 0) return mapping;

    const grouped = new Map();
    columns.forEach((column) => {
      const key = sanitizeMappingName(column?.target || column?.source || '');
      if (!key) return;
      grouped.set(key, [...(grouped.get(key) || []), column]);
    });

    const nextColumns = columns.map((column) => {
      const baseTarget = sanitizeMappingName(column?.target || column?.source || '');
      const duplicates = grouped.get(baseTarget) || [];
      if (duplicates.length < 2) {
        return { ...column, status: normalizeTargetStatus(column), collision_detected: false };
      }
      const tbl = sanitizeMappingName(
        resolveColumnSourceTable(column, mapping?.source || '') ||
        column?.source_table_name || column?.parent_table || mapping?.source || ''
      );
      const resolved = (tbl && tbl !== baseTarget)
        ? `${tbl}_${baseTarget}`
        : `${baseTarget}_${shortDeterministicHash(buildSourceFingerprint(mapping, column)).slice(0, 6)}`;
      return {
        ...column,
        target: resolved,
        suggested_target_name: resolved,
        original_target_name: baseTarget,
        status: 'manual',
        auto_resolved: true,
        collision_detected: false,
      };
    });

    return {
      ...mapping,
      columns: nextColumns,
      status: String(mapping?.status || 'auto').toLowerCase(),
      collision_detected: false,
    };
  });
}

/**
 * Given a collision row and the full flat list of rows, return:
 *  - peers: the other rows of the SAME namespace that share its target name
 *  - suggestions: ordered list of rename options, best-first. The backend's
 *    resolution_suggestions (computed with the emitter's own rules, first =
 *    recommended, unique in the namespace) come first; local options are
 *    only a fallback when the backend sent none.
 */
export function suggestCollisionResolutions(row, allRows) {
  const baseTarget = sanitizeMappingName(row?.target_field || row?.target_name || row?.source_field || '');
  const namespace = rowNamespace(row);
  const peers = (allRows || []).filter(
    r => r.id !== row.id &&
         rowNamespace(r) === namespace &&
         sanitizeMappingName(r.target_field || r.target_name || r.source_field || '') === baseTarget
  );

  const suggestions = [];
  const backend = Array.isArray(row?.resolution_suggestions) ? row.resolution_suggestions : [];
  backend.forEach((item, idx) => {
    const label = sanitizeMappingName(item);
    if (label && !suggestions.some(s => s.label === label)) {
      suggestions.push({
        id: idx === 0 ? 'recommended' : `backend_${idx}`,
        label,
        description: idx === 0 ? 'Recommended' : 'Alternative',
      });
    }
  });
  if (suggestions.length > 0) return { suggestions, peers };

  const tbl = resolveColumnSourceTable(row) || row?.source_table_name || '';
  const field = row?.source_field || row?.target_field || '';
  if (tbl && sanitizeMappingName(tbl) !== baseTarget) {
    suggestions.push({
      id: 'table_prefix',
      label: sanitizeMappingName(`${tbl}_${field}`),
      description: `Prefix with source table "${tbl}"`,
    });
  }
  const kind = String(row?.entity_kind || row?.field_type || 'col').toUpperCase();
  const kindPrefix = kind === 'MEASURE' || kind === 'METRIC' ? 'MSR' : 'COL';
  const kindLabel = sanitizeMappingName(`${kindPrefix}_${baseTarget}`);
  if (!suggestions.some(s => s.label === kindLabel)) {
    suggestions.push({ id: 'kind_prefix', label: kindLabel, description: `Prefix with entity type (${kindPrefix})` });
  }
  const hash = shortDeterministicHash(buildSourceFingerprint({}, row)).slice(0, 6);
  suggestions.push({
    id: 'hash',
    label: sanitizeMappingName(`${baseTarget}_${hash}`),
    description: 'Append short unique hash (last resort)',
  });
  return { suggestions, peers };
}

/**
 * Resolves all collisions in a rows list by generating unique, collision-free
 * names (unique within each row's namespace). Uses each row's backend
 * suggestions first. Returns a mapping of row.id -> candidateName.
 */
export function resolveCollisionsForRows(rows) {
  const mappings = Array.isArray(rows) ? rows : [];
  const collisionRows = mappings.filter(
    r => String(r?.status || '').toLowerCase() === 'collision'
  );
  if (!collisionRows.length) return {};

  const assigned = new Map(); // namespace -> Set(upper-case names)
  const taken = (row) => {
    const ns = rowNamespace(row);
    if (!assigned.has(ns)) assigned.set(ns, new Set());
    return assigned.get(ns);
  };
  mappings
    .filter(r => String(r?.status || '').toLowerCase() !== 'collision')
    .forEach((r) => {
      const name = String(r?.target_field || r?.target_name || '').trim().toUpperCase();
      if (name) taken(r).add(name);
    });

  const resolvedMap = {};
  collisionRows.forEach(row => {
    const used = taken(row);
    const { suggestions } = suggestCollisionResolutions(row, mappings);
    let candidate = suggestions.map(s => s.label).find(label => label && !used.has(label.toUpperCase()));
    if (!candidate) {
      const base = suggestions[0]?.label || sanitizeMappingName(row.target_field || row.source_field || '') || 'FIELD';
      const hash = shortDeterministicHash(buildSourceFingerprint({}, row)).slice(0, 6).toUpperCase();
      candidate = sanitizeMappingName(`${base}_${hash}`);
      let counter = 2;
      while (used.has(candidate.toUpperCase())) {
        candidate = sanitizeMappingName(`${base}_${hash}_${counter}`);
        counter += 1;
      }
    }
    resolvedMap[row.id] = candidate;
    used.add(candidate.toUpperCase());
  });

  return resolvedMap;
}
