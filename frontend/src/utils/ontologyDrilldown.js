// Explorer drill-down: what a collision means and what can be done about it.
//
// conflict   – the same name with DIFFERENT formulas (severity ERROR): a real
//              disagreement about what a metric means. Needs attention.
// shared     – the same name and formula in several domains (WARNING): usually
//              the same model synced by several projects. Informational.
// A conflict whose domains all belong to ONE project can be resolved for that
// project (priority: one domain's definition wins; namespace: keep both,
// prefixed by domain). Across projects each project keeps its own definition.

export function classifyCollision(col, domainProject = {}) {
  const domains = col.conflicting_domains || [];
  const projects = [...new Set(domains.map((d) => domainProject[d]).filter(Boolean))];
  const kind = String(col.severity || '').toUpperCase() === 'ERROR' ? 'conflict' : 'shared';
  return {
    ...col,
    kind,
    projects,
    resolvableIn: kind === 'conflict' && projects.length === 1 && domains.length > 1 ? projects[0] : null,
  };
}

export function splitCollisions(collisions = [], domainProject = {}) {
  const all = collisions.map((c) => classifyCollision(c, domainProject));
  return {
    conflicts: all.filter((c) => c.kind === 'conflict'),
    shared: all.filter((c) => c.kind === 'shared'),
  };
}

// Rows for the metric / concept lists, one per item per domain.
export function metricRows(domains = [], details = {}) {
  return domains.flatMap((d) =>
    ((details[d.domain]?.parsed?.metrics) || []).map((m) => ({
      key: `${d.domain}::${m.name}`,
      name: m.label || m.name,
      domain: d.domain,
      formula: m.sql_expression || '',
      dax: m.dax_expression || '',
      boundToBi: (m.maps_to || []).length > 0,
    }))
  );
}

export function conceptRows(domains = [], details = {}) {
  return domains.flatMap((d) =>
    ((details[d.domain]?.parsed?.concepts) || []).map((c) => ({
      key: `${d.domain}::${c.name}`,
      name: c.label || c.logical_name || c.name,
      domain: d.domain,
      table: c.physical_table || '',
      attributes: (c.attributes || []).length,
    }))
  );
}

export function matchesQuery(row, q) {
  const s = String(q || '').trim().toLowerCase();
  if (!s) return true;
  return Object.values(row).some((v) => typeof v === 'string' && v.toLowerCase().includes(s));
}
