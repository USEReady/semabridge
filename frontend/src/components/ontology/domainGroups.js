/** Grouping helpers for the ontology Explorer (project -> model domains). */

export const viewTail = (v) => String(v || '').split('.').pop().replace(/"/g, '');
export const projectLabel = (pid) => String(pid || '').replace(/^proj-/i, '');

/**
 * Group domains by project, then by model: a multi-model project has one
 * domain per model (semantic view) plus optional shared domains.
 */
export function groupDomainsByProject(domains) {
  const groups = new Map();
  for (const d of domains) {
    const key = d.source_project_id ? projectLabel(d.source_project_id).toLowerCase() : '';
    if (!groups.has(key)) groups.set(key, { key, label: key ? projectLabel(d.source_project_id) : 'Other domains', items: [] });
    groups.get(key).items.push(d);
  }
  const out = [...groups.values()];
  for (const g of out) {
    g.multiModel = g.items.some((d) => d.source_model_id);
    g.items.sort((a, b) => {
      const ra = a.source_model_id ? 0 : 1;
      const rb = b.source_model_id ? 0 : 1;
      if (ra !== rb) return ra - rb;
      return String(a.source_model_name || a.domain).localeCompare(String(b.source_model_name || b.domain));
    });
  }
  return out.sort((a, b) => (a.key === '' ? 1 : b.key === '' ? -1 : a.label.localeCompare(b.label)));
}
