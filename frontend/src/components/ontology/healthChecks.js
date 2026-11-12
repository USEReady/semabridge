// Pure helpers that turn ontology data and check results into the Health list,
// metric status pills and per-domain issue counts.
import { plural } from './styles';

export const SEVERITY_ORDER = { breaking: 0, warning: 1, pending: 2, passing: 3 };

export const CATEGORY_LABEL = {
  impact: 'Impact',
  parity: 'Parity',
  quality: 'Quality',
  access: 'Access',
};

const itemName = (x) => (x && typeof x === 'object' ? x.name || x.message || JSON.stringify(x) : String(x));

/** Collisions whose domains overlap the visible (project-scoped) domains. */
export function scopeCollisions(collisions, domainNames) {
  const names = new Set(domainNames);
  return (collisions || []).filter((c) => {
    const doms = c.conflicting_domains || Object.keys(c.expressions || {});
    return doms.length === 0 || doms.some((d) => names.has(d));
  });
}

/** Status pill for a metric row: Conflict / Stale / Drift / Draft / Governed. */
export function metricStatus(metric, { collisionNames, driftMetric }) {
  if (driftMetric && driftMetric.toLowerCase() === (metric.name || '').toLowerCase()) {
    return { label: 'Drift', tone: 'bad' };
  }
  if (collisionNames.has(metric.name)) return { label: 'Conflict', tone: 'warn' };
  if (metric.stale) return { label: 'Stale', tone: 'warn' };
  if (!metric.sql_expression && !(metric.derives_from || []).length) return { label: 'Draft', tone: 'n' };
  return { label: 'Governed', tone: 'ok' };
}

/** "Used in" text for a metric: impact results when known, else bound BI measures. */
export function usedInText(metric, impactReport) {
  if (impactReport) {
    const r = impactReport.affected_bi_reports?.length || 0;
    const a = impactReport.affected_ai_tools?.length || 0;
    const k = impactReport.affected_metrics?.length || 0;
    const parts = [];
    if (r) parts.push(plural(r, 'report'));
    if (a) parts.push(plural(a, 'agent'));
    if (k) parts.push(plural(k, 'metric'));
    if (parts.length) return parts.join(' · ');
  }
  const bound = (metric.maps_to || []).length;
  return bound ? plural(bound, 'BI measure') : '—';
}

/** Guardrail text: the metric's own WHERE filter, or a domain policy that applies to it. */
export function guardrailText(metric, policies) {
  if (metric.filter_expression) return metric.filter_expression;
  const p = (policies || []).find((pol) => {
    const to = pol.applies_to || [];
    return to.length === 0 || to.includes(metric.name);
  });
  return p ? p.name : '—';
}

const LINT_TITLES = {
  RULE_MISSING_STEWARD: (n) => `${plural(n, 'item')} ${n === 1 ? 'has' : 'have'} no owner`,
  RULE_MISSING_DESCRIPTION: (n) => `${plural(n, 'item')} ${n === 1 ? 'has' : 'have'} no description`,
  RULE_NAMING_CONVENTION: (n) => `${plural(n, 'name')} ${n === 1 ? "doesn't" : "don't"} follow snake_case`,
  RULE_EMPTY_EXPRESSION: (n) => `${plural(n, 'metric')} ${n === 1 ? 'has' : 'have'} no formula`,
  RULE_UNRESOLVED_REFERENCE: (n) => `${plural(n, 'reference')} ${n === 1 ? 'points' : 'point'} to nothing`,
  RULE_CIRCULAR_JOIN: (n) => `${plural(n, 'circular join')} found`,
};

/** Lint issues that belong to a given domain (collisions are reported separately). */
export function lintIssuesFor(lintReport, { domain, objectName } = {}) {
  return (lintReport?.issues || []).filter((iss) => {
    if (iss.rule_id === 'RULE_METRIC_COLLISION') return false;
    const name = iss.object_name || iss.target_name;
    if (objectName && name !== objectName) return false;
    if (domain && !(iss.domain || '').split(',').map((s) => s.trim()).includes(domain)) return false;
    return true;
  });
}

/** Issue badge for a domain in the rail. */
export function domainIssueCount(domain, { collisions, lintReport }) {
  const col = (collisions || []).filter((c) => (c.conflicting_domains || []).includes(domain.domain)).length;
  const lint = lintIssuesFor(lintReport, { domain: domain.domain }).filter((i) => i.severity === 'ERROR').length;
  return col + (domain.stale_count || 0) + lint;
}

/**
 * Build the Health results list (one row per finding or passing check),
 * ordered by severity.
 */
export function buildHealthResults({
  collisions,
  unresolved,
  loadErrors,
  lintReport,
  impactReports,
  impactRan,
  recon,
  policyCount,
  rls,
}) {
  const rows = [];

  (loadErrors || []).forEach((e, i) => {
    rows.push({
      id: `load-${i}`,
      severity: 'breaking',
      category: 'quality',
      title: `A domain file could not be loaded`,
      detail: itemName(e),
      action: 'Details',
      kind: 'loadError',
      payload: e,
    });
  });

  if ((unresolved || []).length) {
    rows.push({
      id: 'unresolved',
      severity: 'breaking',
      category: 'quality',
      title: `${plural(unresolved.length, 'reference')} ${unresolved.length === 1 ? 'points' : 'point'} to something that doesn't exist`,
      detail: unresolved.slice(0, 3).map(itemName).join(' · ') + (unresolved.length > 3 ? ` +${unresolved.length - 3}` : ''),
      action: 'Details',
      kind: 'unresolved',
      payload: unresolved,
    });
  }

  (collisions || []).forEach((c, i) => {
    const doms = c.conflicting_domains || Object.keys(c.expressions || {});
    rows.push({
      id: `collision-${i}`,
      severity: 'warning',
      category: 'quality',
      title: `“${c.item_name}” defined in ${doms.join(' and ')}`,
      detail: 'Two different definitions. Pick an owner, or rename one.',
      action: 'Resolve',
      kind: 'collision',
      payload: c,
    });
  });

  // Quality (linter), grouped by rule
  if (lintReport) {
    const issues = lintIssuesFor(lintReport);
    const byRule = {};
    issues.forEach((iss) => {
      (byRule[iss.rule_id] = byRule[iss.rule_id] || []).push(iss);
    });
    Object.entries(byRule).forEach(([rule, list]) => {
      const names = [...new Set(list.map((x) => x.object_name || x.target_name))];
      const title = LINT_TITLES[rule] ? LINT_TITLES[rule](list.length) : list[0].message;
      rows.push({
        id: `lint-${rule}`,
        severity: 'warning',
        category: 'quality',
        title,
        detail: names.slice(0, 4).join(', ') + (names.length > 4 ? ` +${names.length - 4}` : ''),
        action: 'Details',
        kind: 'lint',
        payload: list,
      });
    });
    if (!Object.keys(byRule).length) {
      rows.push({
        id: 'lint-pass',
        severity: 'passing',
        category: 'quality',
        title: 'Owners, descriptions and names look good',
        detail: 'Every metric and concept has an owner, a description and a standard name.',
        action: 'View',
        kind: 'lint',
        payload: [],
      });
    }
  } else {
    rows.push({
      id: 'lint-pending',
      severity: 'pending',
      category: 'quality',
      title: 'Quality not checked yet',
      detail: 'Checks owners, descriptions and naming for every metric and concept.',
      action: 'Check',
      kind: 'lintRun',
    });
  }

  // Impact (blast radius per metric)
  const reports = Object.values(impactReports || {});
  const impacted = reports.filter((r) => (r.total_impacted_items || 0) > 0);
  impacted
    .sort((a, b) => b.total_impacted_items - a.total_impacted_items)
    .forEach((r) => {
      const downstream = [
        ...(r.affected_metrics || []),
        ...(r.affected_bi_reports || []),
        ...(r.affected_ai_tools || []),
      ].map(itemName);
      rows.push({
        id: `impact-${r.target_object}`,
        severity: r.severity === 'BREAKING' ? 'breaking' : 'warning',
        category: 'impact',
        title: `Changing ${r.target_object} would affect ${plural(r.total_impacted_items, 'downstream item')}`,
        chain: [r.target_object, ...downstream.slice(0, 2)],
        more: Math.max(0, downstream.length - 2),
        action: 'Details',
        kind: 'impact',
        payload: r,
      });
    });
  if (impactRan) {
    const safe = reports.length - impacted.length;
    if (safe > 0) {
      rows.push({
        id: 'impact-pass',
        severity: 'passing',
        category: 'impact',
        title: `${plural(safe, 'metric')} can change without breaking anything`,
        detail: 'No downstream metrics, reports or AI agents depend on them.',
        action: 'What if…',
        kind: 'whatIf',
      });
    }
  } else {
    rows.push({
      id: 'impact-pending',
      severity: 'pending',
      category: 'impact',
      title: 'Impact not checked yet',
      detail: 'Finds the reports, metrics and AI agents that depend on each metric.',
      action: 'What if…',
      kind: 'whatIf',
    });
  }

  // Parity (DAX vs SQL)
  if (recon.rows.length) {
    const drift = recon.rows.filter((r) => !r.matched);
    if (drift.length) {
      const worst = drift.reduce((a, b) => (Number(b.deltaPct) > Number(a.deltaPct) ? b : a));
      rows.push({
        id: 'parity',
        severity: 'breaking',
        category: 'parity',
        title: `Parity drift on ${recon.metric}`,
        detail: `Power BI DAX and warehouse SQL disagree by ${worst.deltaPct}% for ${recon.dimension} = ${worst.slice}`,
        action: 'Details',
        kind: 'parity',
      });
    } else {
      rows.push({
        id: 'parity',
        severity: 'passing',
        category: 'parity',
        title: `${recon.metric} matches in Power BI and SQL`,
        detail: `${plural(recon.rows.length, 'slice')} compared, no difference`,
        action: 'Details',
        kind: 'parity',
      });
    }
  } else {
    rows.push({
      id: 'parity',
      severity: 'pending',
      category: 'parity',
      title: 'Parity not checked yet',
      detail: "Compares a metric's Power BI DAX result with the warehouse SQL result, slice by slice.",
      action: 'Check',
      kind: 'parity',
    });
  }

  // Access (row-level security)
  if (rls.ddl) {
    rows.push({
      id: 'access',
      severity: 'passing',
      category: 'access',
      title: 'Row-level security compiled',
      detail: `Power BI role ${rls.role} on ${rls.table} compiled to a Snowflake row access policy`,
      action: 'View DDL',
      kind: 'access',
    });
  } else {
    rows.push({
      id: 'access',
      severity: policyCount > 0 ? 'passing' : 'pending',
      category: 'access',
      title: policyCount > 0 ? `${plural(policyCount, 'access policy', 'access policies')} defined in domains` : 'Row-level security not checked yet',
      detail: 'Compile a Power BI role filter into a Snowflake row access policy to keep both in sync.',
      action: 'Open',
      kind: 'access',
    });
  }

  return rows.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}
