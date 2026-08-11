/**
 * Pure presentation logic for the dry-run page's two SEPARATE, never-
 * blended signals:
 *
 *   1. The static risk tier ("No known risk signals" / "Known risky
 *      pattern detected" / "Failed a static check — predicted failure")
 *      — computed backend-side from static validators only
 *      (project_mapping_engine.py's _compute_static_risk_tier). Metric-
 *      only; null for tables/columns.
 *   2. The Tier-5 LLM's own self-reported estimate — metric-only, and
 *      only ever present for a Tier-5 (LLM-translated) metric. Always
 *      null for a Tier 1-4 (deterministic) metric, since there's nothing
 *      for a deterministic translation to self-report on.
 *
 * Kept as plain, framework-free functions (no React) so they're testable
 * with Node's built-in test runner without adding a new frontend test
 * framework — see Tests/frontend/riskLabels.test.js.
 */

// Mirrors project_mapping_engine.py's STATIC_RISK_LABELS exactly — the
// backend is the source of truth for which tier a metric got; this map is
// presentation-only (which StatusBadge status/color each tier renders as).
export const STATIC_RISK_BADGE_CONFIG = {
  no_known_risk: { status: 'success', label: 'No known risk signals' },
  known_risky_pattern: { status: 'warning', label: 'Known risky pattern detected' },
  predicted_failure: { status: 'error', label: 'Failed a static check — predicted failure' },
};

/**
 * Returns { status, label } for the static-risk badge, or null when this
 * row has no static risk tier at all (non-metric rows, or a metric row
 * from a backend response that hasn't been updated yet). Gates on the
 * field itself being a recognized value, NOT on entity_kind — the dry-run
 * API response normalises entity_kind "metric" -> "measure" upstream
 * (mappings_controller.py), so entity_kind is not a reliable gate here.
 */
export function getStaticRiskBadge(row) {
  const tier = row?.static_risk_tier;
  if (!tier) return null;
  return STATIC_RISK_BADGE_CONFIG[tier] ?? null;
}

/**
 * Returns the exact display string for the AI self-reported estimate, or
 * null when there is nothing to show (this is what makes a Tier 1-4
 * metric render nothing at all: llm_self_reported_confidence is always
 * null/undefined for those, by construction on the backend).
 *
 * The "(not independently verified)" qualifier is part of the string
 * itself, not a separately-toggleable suffix — every caller that shows
 * this value MUST go through this function, so the qualifier can never be
 * accidentally dropped by a call site rendering the number directly.
 */
export function getSelfReportedEstimateText(row) {
  const value = row?.llm_self_reported_confidence;
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  const pct = Math.round(numeric * 100);
  return `AI self-reported estimate: ${pct}% (not independently verified)`;
}

// Mirrors converter/time_intelligence_shapes.py's
// ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE exactly.
const ENRICHMENT_COLUMN_UNVERIFIABLE_CATEGORY = 'enrichment_column_unverifiable';

/**
 * Real incident: a flag-column metric (e.g. Total Units YTD, referencing
 * an enriched-view column like IS_YTD that only exists at real-deploy
 * time) was translating correctly but getting hard-rejected by dry-run's
 * DDL-emission schema check, which has no live connection to see that
 * column. Fixed backend-side (metrics_clause_builder.py) by NOT dropping
 * the metric and instead tagging it with this advisory category --
 * this function surfaces that as a THIRD, separate caveat line, distinct
 * from both the static-risk badge and the self-reported estimate: it is
 * neither "this is risky" nor "the LLM's own guess" -- it is "this
 * specific check cannot run offline, and the SQL is otherwise fine."
 * Returns null when the category isn't present (the vast majority of
 * rows), so nothing renders for them.
 */
export function getEnrichmentUnverifiableCaveat(row) {
  const categories = row?.advisory_categories;
  if (!Array.isArray(categories) || !categories.includes(ENRICHMENT_COLUMN_UNVERIFIABLE_CATEGORY)) {
    return null;
  }
  return 'Cannot verify in dry-run — resolved by live enrichment at deploy time';
}
