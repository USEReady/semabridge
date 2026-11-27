// Which SemaBridge features exist for a project's target (backend
// TargetRules.features, GET /api/ontology/projects/{id}/target-features).
//
// `features` is null while unknown (not loaded, no project, request failed):
// then every feature counts as available, i.e. today's behaviour is kept and
// Snowflake projects never lose anything. Only a LOADED feature set can turn a
// feature off (e.g. Snowflake-only live SQL on a Databricks project).

export function featureUnsupported(features, name) {
  return Boolean(features && typeof features === 'object' && features[name] === false);
}

export function sqlLabel(features, fallback = 'Snowflake SQL') {
  const label = features && typeof features.sql_label === 'string' ? features.sql_label.trim() : '';
  return label && label !== 'SQL' ? label : fallback;
}

export function targetDisplayName(target) {
  const t = String(target || '').trim().toLowerCase();
  if (!t) return 'this target';
  return t === 'databricks' ? 'Databricks' : t === 'snowflake' ? 'Snowflake' : t;
}

// Occurrences of one ontology item from the target-facts read model.
export function itemKey(kind, table, name) {
  const norm = (v) => String(v || '').split(/\s+/).filter(Boolean).join(' ').toLowerCase();
  return `${norm(kind)}:${norm(table)}:${norm(name)}`;
}

export function measureFacts(facts, measureName) {
  return facts?.items?.[itemKey('measure', '', measureName)] || null;
}

// Short status text for an occurrence's verified state.
export function verifiedText(state) {
  if (!state) return 'not deployed';
  switch (state.status) {
    case 'verified': return `verified (revision ${state.revision})`;
    case 'unavailable': return `unavailable${state.note ? `: ${state.note}` : ''}`;
    case 'not_in_deployed_view': return 'not in the deployed view';
    case 'applied_unverified': return 'applied, not verified';
    default: return String(state.status || 'unknown');
  }
}
