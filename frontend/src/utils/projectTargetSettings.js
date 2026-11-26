/**
 * Databricks target settings in a project's YAML tree (project Settings page).
 *
 * A project keeps its target in `target:` (a mapping), `targets:` (a list, first
 * entry used), or both. Reading merges them the way the backend does
 * (targets[0] filled in under target). Writing updates whichever form(s) the
 * project already has -- it never adds the other form and never leaves a bare
 * `target:` holding only a type. Keys this page does not edit (planner,
 * stub_form, on_missing_source, table_mapping, ...) are kept as they are.
 */

const LIST_KEYS = ['source_schemas', 'deploy_principals', 'deployers'];

function isObj(v) {
  return v && typeof v === 'object' && !Array.isArray(v);
}

/** The target the backend uses: targets[0] with target on top. */
export function effectiveTarget(tree) {
  const t = tree || {};
  const first = Array.isArray(t.targets) && isObj(t.targets[0]) ? t.targets[0] : {};
  const target = isObj(t.target) ? t.target : null;
  return target ? { ...first, ...target } : { ...first };
}

function listText(value) {
  if (Array.isArray(value)) return value.map((v) => String(v).trim()).filter(Boolean).join(', ');
  return String(value || '').trim();
}

function toList(text) {
  return String(text || '').split(',').map((s) => s.trim()).filter(Boolean);
}

/** Form fields for a Databricks target. */
export function databricksFields(tree) {
  const t = effectiveTarget(tree);
  return {
    target_catalog: String(t.catalog || ''),
    target_schema: String(t.schema || t.schema_name || ''),
    target_source_schemas: listText(t.source_schemas),
    target_staging_schema: String(t.staging_schema || ''),
    target_deploy_principals: listText(t.deploy_principals),
    target_deployers: listText(t.deployers),
  };
}

function applyFields(entry, form) {
  const next = { ...(isObj(entry) ? entry : {}), type: 'databricks' };
  const scalar = {
    catalog: form.target_catalog,
    schema: form.target_schema,
    staging_schema: form.target_staging_schema,
  };
  for (const [key, value] of Object.entries(scalar)) {
    const v = String(value || '').trim();
    if (v) next[key] = v; else delete next[key];
  }
  delete next.schema_name;
  const lists = {
    source_schemas: form.target_source_schemas,
    deploy_principals: form.target_deploy_principals,
    deployers: form.target_deployers,
  };
  for (const key of LIST_KEYS) {
    const items = toList(lists[key]);
    if (items.length) next[key] = items; else delete next[key];
  }
  // Snowflake / Fabric-only keys never belong on a Databricks target.
  delete next.database;
  delete next.workspace_id;
  return next;
}

/** A new tree with the Databricks target fields applied (input not mutated). */
export function applyDatabricksTarget(tree, form) {
  const out = { ...(tree || {}) };
  const hasTarget = isObj(out.target);
  const hasTargets = Array.isArray(out.targets) && isObj(out.targets[0]);
  if (hasTargets) {
    out.targets = [applyFields(out.targets[0], form), ...out.targets.slice(1)];
  }
  if (hasTarget) {
    // a bare `target: {type}` written by an older save is completed from targets[0]
    const base = hasTargets ? { ...tree.targets[0], ...out.target } : out.target;
    out.target = applyFields(base, form);
  }
  if (!hasTarget && !hasTargets) {
    out.target = applyFields({}, form);
  }
  return out;
}
