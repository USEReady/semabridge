// Metrics that couldn't be translated (or that Snowflake rejected) are still
// deployed, as CAST(NULL AS DOUBLE) placeholders — the backend records them
// in the drop ledger with a reason starting "placeholder:".

export const PLACEHOLDER_NOTE =
  "This metric couldn't be translated, so it could not be added to the target with its real calculation. "
  + 'It was added as a NULL placeholder (CAST(NULL AS DOUBLE)) so the sync could complete. '
  + 'To fix it, edit the semantic view in the target and replace the NULL with the correct expression.';

export function isPlaceholderEntry(entry) {
  return String(entry?.reason || '').trim().toLowerCase().startsWith('placeholder:');
}

export function isMetricEntry(entry) {
  return String(entry?.entity_kind || entry?.kind || '').toLowerCase() === 'metric';
}
