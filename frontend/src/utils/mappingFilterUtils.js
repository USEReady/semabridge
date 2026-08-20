/**
 * mappingFilterUtils — connector-agnostic helpers for the Mapping Options
 * two-tier filter (field type -> status). Field-type and status vocabularies
 * are read from whatever values are actually present in the mapping rows;
 * nothing here assumes a fixed label set beyond a couple of known synonyms.
 */

// Known synonyms for the same conceptual field type across connectors/normalizers.
const FIELD_TYPE_ALIASES = { metric: 'measure' };

// Statuses currently known to represent a "no action needed" outcome.
// Anything not in this set — including a status no connector has produced
// yet — is treated as actionable by rowNeedsAttention().
const CLEAN_STATUSES = new Set(['auto', 'auto_resolved']);

export function normalizeFieldTypeKey(row) {
  const raw = String(row?.field_type || row?.entity_kind || 'unknown').toLowerCase().trim() || 'unknown';
  return FIELD_TYPE_ALIASES[raw] || raw;
}

function titleCase(word) {
  return word.replace(/\b\w/g, (c) => c.toUpperCase());
}

// Naive pluralization for a dynamic key ('measure' -> 'Measures', 'status' -> 'Statuses' isn't
// handled specially — good enough for short field-type/status words, not a general inflector.
export function humanizeGroupLabel(key) {
  const word = String(key || '').replace(/[_-]+/g, ' ').trim();
  if (!word) return 'Unknown';
  const titled = titleCase(word);
  return /s$/i.test(titled) ? titled : `${titled}s`;
}

/**
 * Groups rows by normalized field type, in first-seen order, with an
 * always-first synthetic 'all' group holding every row.
 */
export function groupRowsByFieldType(rows) {
  const order = [];
  const buckets = new Map();
  rows.forEach((row) => {
    const key = normalizeFieldTypeKey(row);
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key).push(row);
  });
  const groups = order.map((key) => ({ key, label: humanizeGroupLabel(key), rows: buckets.get(key) }));
  return [{ key: 'all', label: 'All', rows }, ...groups];
}

/**
 * Whether a row has an expression that actually went through translation
 * (DAX, SQL, or otherwise) — as opposed to sync_enabled/sync_failure_reason
 * merely sitting at their defaults because there was nothing to translate.
 */
function rowHasExpression(row) {
  return Boolean(String(row?.measure_expression || '').trim() || String(row?.target_expression || '').trim());
}

/** null when the row has no expression to convert; otherwise the outcome. */
export function rowConversionOutcome(row) {
  if (!rowHasExpression(row)) return null;
  const failed = row?.sync_enabled === false || Boolean(String(row?.sync_failure_reason || '').trim());
  return failed ? 'conversion_failed' : 'conversion_success';
}

export function hasConversionData(rows) {
  return rows.some(rowHasExpression);
}

export const CONVERSION_FACET_LABELS = {
  conversion_success: 'Expression Converted',
  conversion_failed: 'Expression Not Converted',
};

export function rowIsCollision(row) {
  return String(row?.status || '').toLowerCase().trim() === 'collision';
}

/**
 * A row needs attention for some reason OTHER than the two that already get
 * their own dedicated chip (Collisions, Expression Not Converted) — e.g. an
 * unmapped field, a manual-pending edit, a predicted deploy-time failure, or
 * any other non-clean status a connector emits. Deliberately excludes
 * collision and conversion-failed rows so this chip's set never duplicates
 * theirs — see buildFilterOptions below.
 */
export function rowNeedsAttention(row) {
  if (rowIsCollision(row)) return false;
  if (rowConversionOutcome(row) === 'conversion_failed') return false;
  const status = String(row?.status || '').toLowerCase().trim();
  return !CLEAN_STATUSES.has(status);
}

/**
 * The Tier 2 status filter row, reduced to a small, fixed, non-overlapping
 * set: Collisions, Expression Converted, Expression Not Converted, and
 * Needs Attention (everything else non-clean). Earlier this row grew one
 * chip per distinct raw status value plus a separately-computed "Needs
 * Review" toggle and a DAX-complexity-tier breakdown — several of those
 * chips ended up covering the exact same rows (e.g. for a columns-only
 * view with no other statuses, "Needs Review" and "Collision" were
 * literally identical sets), which is confusing rather than informative.
 * A chip is only included when its count is non-zero.
 */
export function buildFilterOptions(rows) {
  const options = [];

  const collisionCount = rows.filter(rowIsCollision).length;
  if (collisionCount > 0) {
    options.push({ key: 'collision', label: 'Collisions', count: collisionCount, kind: 'status' });
  }

  if (hasConversionData(rows)) {
    Object.entries(CONVERSION_FACET_LABELS).forEach(([key, label]) => {
      const count = rows.filter((r) => rowConversionOutcome(r) === key).length;
      if (count > 0) options.push({ key, label, count, kind: 'conversion' });
    });
  }

  const attentionCount = rows.filter(rowNeedsAttention).length;
  if (attentionCount > 0) {
    options.push({ key: 'needs_attention', label: 'Needs Attention', count: attentionCount, kind: 'attention' });
  }

  return options;
}
