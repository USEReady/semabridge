/**
 * nameChanges — which fields will deploy under a different name than the
 * source, and why. Feeds the "Name changes" panel of the dry-run table so a
 * user can see (and edit) every rename, including the ones that needed no
 * action (Snowflake naming rules, collisions already resolved).
 *
 * A plain case/space change ("Order Date" -> ORDER_DATE) is not listed: it is
 * how every name is written in Snowflake and would only add noise.
 */

import { toPhysicalColumnName, toSnowflakeAlias } from './collisionResolution.js';

const SYMBOL_WORDS = [
  ['%', 'PCT'], ['$', 'DOL'], ['#', 'NUM'], ['@', 'AT'], ['&', 'AND'], ['+', 'PLUS'],
];

/** How the name looks when only case and spaces change. */
export function plainSnowflakeName(source) {
  return String(source || '').trim().toUpperCase().replace(/\s+/g, '_');
}

function namingRuleDetail(source, target) {
  const replaced = SYMBOL_WORDS
    .filter(([symbol, word]) => source.includes(symbol) && target.includes(word))
    .map(([symbol, word]) => `"${symbol}" became ${word}`);
  if (/^\d/.test(source.trim()) && /^_\d/.test(target)) replaced.push('a leading digit got a "_" prefix');
  if (/^COL_/.test(target) && !/^COL_/i.test(plainSnowflakeName(source))) replaced.push(`${target.slice(4)} is a Snowflake reserved word, so it got the COL_ prefix`);
  if (replaced.length) return `Snowflake naming rules: ${replaced.join(', ')}.`;
  return 'Snowflake naming rules: characters Snowflake does not allow in names were replaced.';
}

/**
 * changeReason(row) -> { kind, label, detail } or null when the name is not
 * changed (or the row is an open collision, which the table already shows).
 *   kind: 'renamed_in_snowflake' | 'renamed_by_you' | 'clash_resolved' | 'naming_rules'
 * 'renamed_in_snowflake': the backend found the column deployed under another
 * name last time (row.deployed_name_change, e.g. COL_DATE -> DATE).
 */
export function changeReason(row) {
  if (!row || row.collision_detected || String(row.status || '').toLowerCase() === 'collision') return null;
  const deployed = row.deployed_name_change;
  if (deployed && deployed.from && deployed.to && String(deployed.from).toUpperCase() !== String(deployed.to).toUpperCase()) {
    return {
      kind: 'renamed_in_snowflake',
      label: 'Renamed in Snowflake',
      detail: `Deployed as ${deployed.from} today; the next sync deploys it as ${deployed.to}. `
        + `Queries, reports, dashboards or Cortex verified queries that use ${deployed.from} must be updated.`,
    };
  }
  const source = String(row.source_field || row.source_name || '').trim();
  const target = String(row.target_field || row.target_name || '').trim();
  if (!source || !target) return null;
  const sourceName = String(row.renamed_from || source);
  if (plainSnowflakeName(sourceName) === target.toUpperCase()) return null;

  if (String(row.status || '').toLowerCase() === 'manual') {
    if (row.renamed_from) {
      return {
        kind: 'clash_resolved',
        label: 'Clash resolved',
        detail: `"${row.renamed_from}" clashed with another column of the same table in Snowflake; you chose ${target}.`,
      };
    }
    return { kind: 'renamed_by_you', label: 'Renamed', detail: `You (or Resolve All) set this name to ${target}.` };
  }
  return { kind: 'naming_rules', label: 'Naming rules', detail: namingRuleDetail(sourceName, target) };
}

/**
 * Project-level note when the last deployed names are unknown (the backend
 * could not read the last deploy: deployedNamesKnown false): columns that
 * would have deployed as COL_X under the old rule ('Date' -> COL_DATE) now
 * deploy as X. Returns '' when there are none or the rows are already
 * marked individually.
 */
export function legacyNamingNote(rows, deployedNamesKnown = false) {
  if (deployedNamesKnown) return '';
  const names = [];
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const kind = String(row?.field_type || row?.entity_kind || '').toLowerCase();
    if (['measure', 'metric'].includes(kind)) return;
    const source = String(row?.source_field || row?.source_name || '').trim();
    const target = String(row?.target_field || row?.target_name || '').trim().toUpperCase();
    if (!source || !target) return;
    const legacy = toPhysicalColumnName(source);
    if (legacy !== toSnowflakeAlias(source, 'column') && target === toSnowflakeAlias(source, 'column')) {
      names.push(`${target} (was ${legacy})`);
    }
  });
  if (names.length === 0) return '';
  const sample = names.slice(0, 3).join(', ');
  return `${names.length} column${names.length > 1 ? 's' : ''} no longer get the COL_ prefix: ${sample}`
    + `${names.length > 3 ? ` and ${names.length - 3} more` : ''}. If this project was synced before, `
    + 'these columns are renamed in Snowflake on the next sync: queries, reports, dashboards or Cortex verified '
    + 'queries that use the old COL_ names must be updated.';
}

/** Rows whose deployed name differs from the source, with their reason. */
export function collectNameChanges(rows) {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => ({ row, reason: changeReason(row) }))
    .filter((entry) => entry.reason);
}

/**
 * The note shown whenever a name is changed: what the rename does and does
 * not change, so nothing downstream breaks.
 */
export function renameImpactNote(row, newName) {
  const oldName = String(row?.target_field || row?.target_name || '').trim();
  const next = String(newName || '').trim();
  if (!next || next.toUpperCase() === oldName.toUpperCase()) return '';
  const isMeasure = ['measure', 'metric'].includes(String(row?.field_type || row?.entity_kind || '').toLowerCase());
  if (isMeasure) {
    return `Renames the metric in the semantic view from ${oldName || 'its current name'} to ${next.toUpperCase()}. `
      + 'Update any reports, dashboards, saved queries or Cortex verified queries that use the old name.';
  }
  const table = String(row?.source_table_name || row?.parent_table || '').trim();
  // The physical column SemaBridge reads (backend sanitize_column): 'Date' -> COL_DATE.
  const physical = toPhysicalColumnName(row?.source_field || row?.source_name || '');
  return `Renames the field in the semantic view only. It still reads the column `
    + `${table ? `${table.toUpperCase()}.` : ''}${physical} in Snowflake: make sure that column exists in the table under that name, `
    + `and update any reports or saved queries that use ${oldName || 'the old name'}.`;
}
