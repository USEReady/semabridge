/**
 * Tests for utils/riskLabels.js — the pure presentation logic behind the
 * dry-run page's two separate badges (static risk tier, AI self-reported
 * estimate). Uses Node's built-in test runner (no new frontend test
 * framework dependency needed: node --test, Node 18+, ESM).
 *
 * Run: node --test src/utils/riskLabels.test.js   (from frontend/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  getStaticRiskBadge,
  getSelfReportedEstimateText,
  getEnrichmentUnverifiableCaveat,
  STATIC_RISK_BADGE_CONFIG,
} from './riskLabels.js';

// ---------------------------------------------------------------------------
// getStaticRiskBadge
// ---------------------------------------------------------------------------

test('getStaticRiskBadge returns null for a row with no static_risk_tier', () => {
  assert.equal(getStaticRiskBadge({ static_risk_tier: null }), null);
  assert.equal(getStaticRiskBadge({}), null);
  assert.equal(getStaticRiskBadge(undefined), null);
});

test('getStaticRiskBadge returns the exact three approved labels', () => {
  assert.deepEqual(getStaticRiskBadge({ static_risk_tier: 'no_known_risk' }), {
    status: 'success',
    label: 'No known risk signals',
  });
  assert.deepEqual(getStaticRiskBadge({ static_risk_tier: 'known_risky_pattern' }), {
    status: 'warning',
    label: 'Known risky pattern detected',
  });
  assert.deepEqual(getStaticRiskBadge({ static_risk_tier: 'predicted_failure' }), {
    status: 'error',
    label: 'Failed a static check — predicted failure',
  });
});

test('getStaticRiskBadge returns null for an unrecognized tier value', () => {
  assert.equal(getStaticRiskBadge({ static_risk_tier: 'some_future_unknown_tier' }), null);
});

test('STATIC_RISK_BADGE_CONFIG has exactly the three approved tiers, no more', () => {
  assert.deepEqual(Object.keys(STATIC_RISK_BADGE_CONFIG).sort(), [
    'known_risky_pattern',
    'no_known_risk',
    'predicted_failure',
  ]);
});

// ---------------------------------------------------------------------------
// getSelfReportedEstimateText
// ---------------------------------------------------------------------------

test('getSelfReportedEstimateText always includes the "not independently verified" qualifier when shown', () => {
  const cases = [0, 0.01, 0.55, 0.999, 1];
  for (const value of cases) {
    const text = getSelfReportedEstimateText({ llm_self_reported_confidence: value });
    assert.notEqual(text, null, `expected a string for value ${value}`);
    assert.match(text, /\(not independently verified\)/, `missing qualifier for value ${value}`);
  }
});

test('getSelfReportedEstimateText renders the exact expected format for a known value', () => {
  const text = getSelfReportedEstimateText({ llm_self_reported_confidence: 0.55 });
  assert.equal(text, 'AI self-reported estimate: 55% (not independently verified)');
});

test('getSelfReportedEstimateText never renders a bare percentage or the words "confidence score" alone', () => {
  const text = getSelfReportedEstimateText({ llm_self_reported_confidence: 0.55 });
  assert.doesNotMatch(text, /^55%$/);
  assert.doesNotMatch(text, /confidence score/i);
});

// ---------------------------------------------------------------------------
// Explicit requirement: Tier 1-4 metrics (llm_self_reported_confidence is
// null, by construction on the backend) must render NOTHING -- not an
// implication of the != null check, an explicit assertion of it.
// ---------------------------------------------------------------------------

test('getSelfReportedEstimateText renders nothing for a Tier 1-4 metric (field is null)', () => {
  const tier1Row = { entity_kind: 'measure', complexity_tier: 1, llm_self_reported_confidence: null };
  const tier4Row = { entity_kind: 'measure', complexity_tier: 4, llm_self_reported_confidence: null };
  assert.equal(getSelfReportedEstimateText(tier1Row), null);
  assert.equal(getSelfReportedEstimateText(tier4Row), null);
});

test('getSelfReportedEstimateText renders nothing when the field is undefined (never sent by backend)', () => {
  const rowWithoutField = { entity_kind: 'measure', complexity_tier: 2 };
  assert.equal(getSelfReportedEstimateText(rowWithoutField), null);
});

test('getSelfReportedEstimateText renders nothing for a non-numeric value (defensive, should never happen)', () => {
  assert.equal(getSelfReportedEstimateText({ llm_self_reported_confidence: 'not a number' }), null);
  assert.equal(getSelfReportedEstimateText({ llm_self_reported_confidence: NaN }), null);
});

test('getSelfReportedEstimateText DOES render for a Tier 5 metric with a real value', () => {
  const tier5Row = { entity_kind: 'measure', complexity_tier: 5, llm_self_reported_confidence: 0.82 };
  const text = getSelfReportedEstimateText(tier5Row);
  assert.equal(text, 'AI self-reported estimate: 82% (not independently verified)');
});

// ---------------------------------------------------------------------------
// getEnrichmentUnverifiableCaveat -- real incident: a flag-column metric
// (e.g. Total Units YTD) that translated correctly but references a column
// (IS_YTD) only created by live enrichment at real-deploy time. Must be a
// THIRD, distinct signal -- never blended with the static risk badge or
// the self-reported estimate.
// ---------------------------------------------------------------------------

test('getEnrichmentUnverifiableCaveat returns null when the category is absent', () => {
  assert.equal(getEnrichmentUnverifiableCaveat({ advisory_categories: [] }), null);
  assert.equal(getEnrichmentUnverifiableCaveat({ advisory_categories: ['some_other_category'] }), null);
  assert.equal(getEnrichmentUnverifiableCaveat({}), null);
  assert.equal(getEnrichmentUnverifiableCaveat(undefined), null);
});

test('getEnrichmentUnverifiableCaveat returns the exact honest caveat text when the category is present', () => {
  const row = { advisory_categories: ['enrichment_column_unverifiable'] };
  assert.equal(
    getEnrichmentUnverifiableCaveat(row),
    'Cannot verify in dry-run — resolved by live enrichment at deploy time'
  );
});

test('getEnrichmentUnverifiableCaveat is independent of the static risk badge and self-reported estimate', () => {
  // A metric can show all three at once (Tier-5 + flag-column reference),
  // or any subset -- none of the three functions should read the others'
  // fields or short-circuit based on them.
  const row = {
    static_risk_tier: 'no_known_risk',
    llm_self_reported_confidence: 0.9,
    advisory_categories: ['enrichment_column_unverifiable'],
  };
  assert.deepEqual(getStaticRiskBadge(row), { status: 'success', label: 'No known risk signals' });
  assert.equal(getSelfReportedEstimateText(row), 'AI self-reported estimate: 90% (not independently verified)');
  assert.equal(
    getEnrichmentUnverifiableCaveat(row),
    'Cannot verify in dry-run — resolved by live enrichment at deploy time'
  );
});
