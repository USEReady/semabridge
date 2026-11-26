/**
 * platformTerms -- the words a screen uses for the target platform.
 * Snowflake (and an unknown / missing target) keep today's wording exactly.
 */
import { namingFor } from './targetNaming.js';

const SNOWFLAKE_TERMS = Object.freeze({
  platform: 'Snowflake',
  view: 'semantic view',
  views: 'semantic views',
  assistant: 'Cortex',
  location: 'database.schema',
});

const DATABRICKS_TERMS = Object.freeze({
  platform: 'Databricks',
  view: 'metric view',
  views: 'metric views',
  assistant: 'Genie',
  location: 'catalog.schema',
});

export function platformTerms(target = '') {
  const id = target ? namingFor(target).id : 'snowflake';
  return id === 'databricks' ? DATABRICKS_TERMS : SNOWFLAKE_TERMS;
}

export function isDatabricksTarget(target = '') {
  return platformTerms(target) === DATABRICKS_TERMS;
}
