/**
 * nameChanges — which fields will deploy under a different name than the
 * source, and why. Feeds the "Name changes" panel of the dry-run table so a
 * user can see (and edit) every rename, including the ones that needed no
 * action (Snowflake naming rules, collisions already resolved).
 *
 * A plain case/space change ("Order Date" -> ORDER_DATE) is not listed: it is
 * how every name is written in Snowflake and would only add noise.
 */

import { namingFor, plainSnowflakeName } from './targetNaming.js';

export { plainSnowflakeName };

const VIEW_WORDS = {
  snowflake: { view: 'semantic view', assistants: 'Cortex verified queries' },
  databricks: { view: 'metric view', assistants: 'Genie instructions or example queries' },
};
const viewWords = (naming) => VIEW_WORDS[naming.id] || VIEW_WORDS.snowflake;

/**
 * changeReason(row, targetType) -> { kind, label, detail } or null when the name is not
 * changed (or the row is an open collision, which the table already shows).
 *   kind: 'renamed_in_snowflake' | 'renamed_by_you' | 'clash_resolved' | 'naming_rules'
 * 'renamed_in_snowflake': the backend found the column deployed under another
 * name last time (row.deployed_name_change, e.g. COL_DATE -> DATE). The
 * platform's naming object (utils/targetNaming.js) decides what counts as a
 * plain spelling change and explains its rule.
 */
export function changeReason(row, targetType = 'snowflake') {
  if (!row || row.collision_detected || String(row.status || '').toLowerCase() === 'collision') return null;
  const naming = namingFor(targetType || 'snowflake');
  const words = viewWords(naming);
  const deployed = row.deployed_name_change;
  if (deployed && deployed.from && deployed.to && String(deployed.from).toUpperCase() !== String(deployed.to).toUpperCase()) {
    return {
      kind: 'renamed_in_snowflake',
      label: `Renamed in ${naming.label}`,
      detail: `Deployed as ${deployed.from} today; the next sync deploys it as ${deployed.to}. `
        + `Queries, reports, dashboards or ${words.assistants} that use ${deployed.from} must be updated.`,
    };
  }
  const source = String(row.source_field || row.source_name || '').trim();
  const target = String(row.target_field || row.target_name || '').trim();
  if (!source || !target) return null;
  const sourceName = String(row.renamed_from || source);
  if (naming.isPlainChange(sourceName, target)) return null;
  if (String(row.status || '').toLowerCase() === 'manual') {
    if (row.renamed_from) {
      return {
        kind: 'clash_resolved',
        label: 'Clash resolved',
        detail: `"${row.renamed_from}" clashed with another column of the same table in ${naming.label}; you chose ${target}.`,
      };
    }
    return { kind: 'renamed_by_you', label: 'Renamed', detail: `You (or Resolve All) set this name to ${target}.` };
  }
  return { kind: 'naming_rules', label: 'Naming rules', detail: naming.ruleDetail(sourceName, target) };
}

/**
 * Project-level note when the last deployed names are unknown (the backend
 * could not read the last deploy: deployedNamesKnown false): columns that
 * would have deployed as COL_X under Snowflake's old rule ('Date' -> COL_DATE)
 * now deploy as X. '' for platforms without an older rule (Databricks).
 */
export function legacyNamingNote(rows, deployedNamesKnown = false, targetType = 'snowflake') {
  return namingFor(targetType || 'snowflake').legacyNote(rows, deployedNamesKnown);
}

/** Rows whose deployed name differs from the source, with their reason. */
export function collectNameChanges(rows, targetType = 'snowflake') {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => ({ row, reason: changeReason(row, targetType) }))
    .filter((entry) => entry.reason);
}

/**
 * The note shown whenever a name is changed: what the rename does and does
 * not change, so nothing downstream breaks.
 */
export function renameImpactNote(row, newName, targetType = 'snowflake') {
  const naming = namingFor(targetType || 'snowflake');
  const words = viewWords(naming);
  const oldName = String(row?.target_field || row?.target_name || '').trim();
  const next = String(newName || '').trim();
  if (!next || next.toUpperCase() === oldName.toUpperCase()) return '';
  const isMeasure = ['measure', 'metric'].includes(String(row?.field_type || row?.entity_kind || '').toLowerCase());
  if (isMeasure) {
    return `Renames the metric in the ${words.view} from ${oldName || 'its current name'} to ${naming.display(next)}. `
      + `Update any reports, dashboards, saved queries or ${words.assistants} that use the old name.`;
  }
  const table = String(row?.source_table_name || row?.parent_table || '').trim();
  // The physical column read, when the platform derives it from the name
  // (Snowflake: backend sanitize_column, 'Date' -> COL_DATE).
  const physical = naming.physicalColumnName(row?.source_field || row?.source_name || '');
  if (!physical) {
    return `Renames the field in the ${words.view} only. It still reads the same physical column `
      + `${table ? `of ${table} ` : ''}in ${naming.label}; update any reports or saved queries that use `
      + `${oldName || 'the old name'}.`;
  }
  return `Renames the field in the ${words.view} only. It still reads the column `
    + `${table ? `${table.toUpperCase()}.` : ''}${physical} in ${naming.label}: make sure that column exists in the table under that name, `
    + `and update any reports or saved queries that use ${oldName || 'the old name'}.`;
}
