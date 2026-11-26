/**
 * targetNaming -- one naming object per target platform, the browser side of the
 * backend's target_rules/<platform>/naming.py (SnowflakeNaming, DatabricksNaming).
 *
 *   namingFor('snowflake')  -> SnowflakeNaming   (upper case, COL_ for reserved words)
 *   namingFor('databricks') -> DatabricksNaming  (naming v2: lowercase snake_case)
 *   namingFor('fabric' | '') -> GenericNaming    (generic identifier, names not checked)
 *
 * Screens never branch on the platform name: they ask the naming object.
 * The Snowflake object reproduces the previous behaviour exactly.
 */
import {
  SNOWFLAKE_LEGACY_RESERVED_WORDS,
  SNOWFLAKE_RESERVED_WORDS,
  toGenericIdentifier,
  toPhysicalColumnName,
  toSnowflakeAlias,
  toDatabricksIdentifier,
  DATABRICKS_RESERVED_WORDS,
} from './collisionResolution.js';
import { isDatabricksFinalName, toDatabricksName } from './databricksNaming.js';

const isMetricKind = (kind) => ['metric', 'measure'].includes(String(kind || '').toLowerCase());

/** Base class: how names are made, checked and explained for one platform. */
export class TargetNaming {
  constructor(id, label) {
    this.id = id;
    this.label = label;
  }

  /** The name a field deploys under, given the name chosen for it. */
  targetName(value) {
    return String(value ?? '').trim();
  }

  /** Is `value` already in this platform's final form? */
  accepts(value, kind = 'column') {
    return this.targetName(value, kind) === String(value ?? '');
  }

  /** Whether typed names are checked against the rule at all. */
  get checksNames() {
    return true;
  }

  /** Names a field may not take (blocked as "reserved"). */
  reservedWords() {
    return new Set();
  }

  /** Keystroke-friendly form of a name being typed (keeps a trailing "_"). */
  normalizeTyped(value) {
    return String(value ?? '');
  }

  /** True when source -> target is only this platform's ordinary spelling (not worth listing). */
  isPlainChange(source, target) {
    return this.targetName(source, 'column') === target || this.targetName(source, 'metric') === target;
  }

  /** Why the naming rule changed a name (for the Name changes panel). */
  ruleDetail() {
    return `${this.label} naming rules: characters ${this.label} does not allow in names were replaced.`;
  }

  /** Project-level note about names an older rule gave (none by default). */
  legacyNote() {
    return '';
  }

  /** The physical column a renamed field still reads, if the platform derives it from the name. */
  physicalColumnName() {
    return null;
  }

  /** How the deployed name is shown in messages (Snowflake: upper case). */
  display(name) {
    return String(name ?? '');
  }
}

const SNOWFLAKE_SYMBOL_WORDS = [
  ['%', 'PCT'], ['$', 'DOL'], ['#', 'NUM'], ['@', 'AT'], ['&', 'AND'], ['+', 'PLUS'],
];

/** How a name looks in Snowflake when only case and spaces change. */
export function plainSnowflakeName(source) {
  return String(source || '').trim().toUpperCase().replace(/\s+/g, '_');
}

export class SnowflakeNaming extends TargetNaming {
  constructor() {
    super('snowflake', 'Snowflake');
  }

  targetName(value, kind = 'column') {
    return toSnowflakeAlias(value, kind);
  }

  accepts(value, kind = 'column') {
    return toSnowflakeAlias(value, kind) === String(value ?? '').trim().toUpperCase();
  }

  reservedWords(kind = 'column') {
    return isMetricKind(kind) ? SNOWFLAKE_LEGACY_RESERVED_WORDS : SNOWFLAKE_RESERVED_WORDS;
  }

  normalizeTyped(value) {
    return String(value ?? '').toUpperCase().replace(/[^A-Z0-9_]/g, '_');
  }

  isPlainChange(source, target) {
    return plainSnowflakeName(source) === String(target || '').toUpperCase();
  }

  ruleDetail(source, target) {
    const replaced = SNOWFLAKE_SYMBOL_WORDS
      .filter(([symbol, word]) => source.includes(symbol) && target.includes(word))
      .map(([symbol, word]) => `"${symbol}" became ${word}`);
    if (/^\d/.test(source.trim()) && /^_\d/.test(target)) replaced.push('a leading digit got a "_" prefix');
    if (/^COL_/.test(target) && !/^COL_/i.test(plainSnowflakeName(source))) {
      replaced.push(`${target.slice(4)} is a Snowflake reserved word, so it got the COL_ prefix`);
    }
    if (replaced.length) return `Snowflake naming rules: ${replaced.join(', ')}.`;
    return 'Snowflake naming rules: characters Snowflake does not allow in names were replaced.';
  }

  legacyNote(rows, deployedNamesKnown = false) {
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

  physicalColumnName(source) {
    return toPhysicalColumnName(source);
  }

  display(name) {
    return String(name ?? '').toUpperCase();
  }
}

export class DatabricksNaming extends TargetNaming {
  constructor() {
    super('databricks', 'Databricks');
  }

  /** Naming v2; names only the backend can make (unsupported scripts, > 255) stay as given. */
  targetName(value, kind = 'column') {
    return toDatabricksName(value, kind) || String(value ?? '').trim();
  }

  accepts(value) {
    return isDatabricksFinalName(String(value ?? ''));
  }

  normalizeTyped(value) {
    return String(value ?? '').toLowerCase().replace(/[^a-z0-9_]/g, '_');
  }

  ruleDetail() {
    return 'Databricks naming: lowercase snake_case (words split, symbols spelled out, e.g. % -> pct); '
      + 'the Power BI name is kept as the display name.';
  }
}

export class GenericNaming extends TargetNaming {
  constructor(id = 'generic', label = 'target') {
    super(id, label);
  }

  targetName(value) {
    return toGenericIdentifier(value);
  }

  get checksNames() {
    return false;
  }

  isPlainChange(source, target) {
    return plainSnowflakeName(source) === String(target || '').toUpperCase();
  }
}

/**
 * The existing Databricks publisher's rule (behavior.databricks.use_planner_path
 * off, the default): the browser mirror of utils/identifiers.databricks_target_name.
 */
export class PublisherDatabricksNaming extends TargetNaming {
  constructor() {
    super('databricks', 'Databricks');
  }

  targetName(value, kind = 'column') {
    return toDatabricksIdentifier(value, kind);
  }

  reservedWords() {
    return DATABRICKS_RESERVED_WORDS;
  }

  ruleDetail() {
    return 'Databricks naming rules: characters Databricks does not allow in names were replaced '
      + '(e.g. % became _Pct in measure names).';
  }
}

const SNOWFLAKE = new SnowflakeNaming();
const DATABRICKS = new DatabricksNaming();
const DATABRICKS_PUBLISHER = new PublisherDatabricksNaming();

// Which Databricks rule deploys: naming v2 only on the planner path. Set once
// at start-up from GET /api/system/databricks-planner (default: off).
let databricksPlannerOn = false;
export function setDatabricksPlannerOn(on) {
  databricksPlannerOn = Boolean(on);
}
export function isDatabricksPlannerOn() {
  return databricksPlannerOn;
}

/** The naming object for a target platform ('snowflake' by default). */
export function namingFor(target = 'snowflake') {
  const t = String(target ?? 'snowflake').toLowerCase();
  if (t.includes('databricks')) return databricksPlannerOn ? DATABRICKS : DATABRICKS_PUBLISHER;
  if (t.includes('snowflake')) return SNOWFLAKE;
  return new GenericNaming(t || 'generic', 'target');
}
