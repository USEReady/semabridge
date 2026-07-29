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
// yet — is treated as actionable by rowNeedsReview().
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

export function humanizeStatusLabel(key) {
  const word = String(key || '').replace(/[_-]+/g, ' ').trim();
  return word ? titleCase(word) : 'Unknown';
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

/** Distinct status values present in `rows`, in first-seen order, with counts. */
export function computeStatusFacets(rows) {
  const order = [];
  const counts = new Map();
  rows.forEach((row) => {
    const key = String(row?.status || '').toLowerCase().trim() || 'unknown';
    counts.set(key, (counts.get(key) || 0) + 1);
    if (!order.includes(key)) order.push(key);
  });
  return order.map((key) => ({ key, label: humanizeStatusLabel(key), count: counts.get(key) }));
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

/**
 * null when the row isn't a measure with a known DAX complexity tier;
 * otherwise buckets tiers 1-4 (rule-based translation) vs tier 5 (LLM
 * fallback) so the UI can flag AI-assisted conversions for manual review.
 */
export function rowComplexityTier(row) {
  if (normalizeFieldTypeKey(row) !== 'measure') return null;
  // Only a metric that was actually, successfully converted gets a trust
  // tier — a by-design-excluded or genuinely-failed metric must never show
  // up as "Tier 1, trustworthy" just because complexity_tier defaulted to a
  // low number. Those surface only in the conversion-outcome facet instead.
  if (rowConversionOutcome(row) !== 'conversion_success') return null;
  const tier = Number(row?.complexity_tier);
  if (!Number.isFinite(tier) || tier <= 0) return null;
  return tier >= 5 ? 'tier_ai_assisted' : 'tier_rule_based';
}

export function hasComplexityTierData(rows) {
  return rows.some((row) => rowComplexityTier(row) !== null);
}

export const COMPLEXITY_TIER_FACET_LABELS = {
  tier_rule_based: 'Converted (rule-based)',
  tier_ai_assisted: 'Converted (AI-assisted, review recommended)',
};

/**
 * A row needs review when its status isn't in the known-clean set, or when
 * it has an expression that failed to convert (even if its status is clean).
 */
export function rowNeedsReview(row) {
  const status = String(row?.status || '').toLowerCase().trim();
  if (!CLEAN_STATUSES.has(status)) return true;
  return rowConversionOutcome(row) === 'conversion_failed';
}
