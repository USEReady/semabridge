/**
 * normalizeRows — converts a raw /dry-run (or dry-run-job file) API response
 * into the flat row shape DryRunMappingTable and friends consume.
 *
 * Extracted verbatim out of CreateProjectPage.jsx (no behavior change) so it
 * can be unit/contract-tested with Node's built-in test runner, matching
 * mappingFilterUtils.js/riskLabels.js/dryRunPayload.js's convention of
 * keeping pure transform logic in plain, JSX-free .js modules.
 *
 * This extraction exists specifically because this function's own object
 * literal is an EXPLICIT ALLOWLIST, not a spread of the raw API row — any
 * backend field not listed in the returned object below is silently dropped
 * before it ever reaches the rendered table, regardless of whether the API
 * response actually included it. That exact pattern has caused three
 * confirmed silent-drop bugs in this codebase so far: advisory_categories,
 * source_file, and complexity_tier (see git history / the multi-PBIX
 * feature's investigation for the first two). Before this extraction,
 * normalizeRows() lived inside a JSX file with no test runner able to
 * import it — the bug went undetected because the specific boundary the
 * bug lived at was, structurally, untestable. See
 * normalizeRows.test.js's boundary-sweep test, which is meant to make a
 * fourth occurrence of this exact bug class impossible to miss.
 */

export function normalizeStatus(item) {
  const validationStatus = String(item?.validation_status || '').toLowerCase();
  const validationCode = String(item?.validation_code || '').toUpperCase();
  const explicit = String(item?.status || '').toLowerCase();
  const targetField = String(item?.target_field || item?.target_name || item?.target || '').trim();

  if (
    item?.collision_detected === true ||
    validationStatus === 'invalid' ||
    validationStatus === 'collision' ||
    (validationCode && validationCode !== 'OK')
  ) return 'collision';

  if (!targetField) return 'unmapped';
  if (explicit === 'manual') return 'manual';
  return 'auto';
}

export function parseDatasetFromPath(pathValue) {
  const path = String(pathValue || '').trim();
  if (!path) return '';
  const direct = /^datasets\.([^.]+)$/i.exec(path);
  if (direct?.[1]) return String(direct[1]).trim();
  const nested = /^datasets\.([^.]+)\./i.exec(path);
  if (nested?.[1]) return String(nested[1]).trim();
  return '';
}

export function resolveMeasureSourceTables(row) {
  const fromRow = Array.isArray(row?.measure_source_tables)
    ? row.measure_source_tables.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
  if (fromRow.length > 0) return [...new Set(fromRow)];

  const fallback = parseDatasetFromPath(row?.parent_source_path);
  return fallback ? [fallback] : [];
}

export function resolveColumnSourceTable(row, parentTable = '') {
  const fromPath = parseDatasetFromPath(row?.source_path);
  if (fromPath) return fromPath;

  const fromParentPath = parseDatasetFromPath(row?.parent_source_path);
  if (fromParentPath) return fromParentPath;

  return String(parentTable || row?.parent_table || row?.source_table_name || '').trim();
}

export function resolveMeasureExpression(row) {
  return String(row?.source_expression || row?.measure_expression || row?.expression || '').trim();
}

export function normalizeRows(data) {
  console.log('[normalizeRows] Input data:', data);
  const entityRows = Array.isArray(data?.entity_mappings) ? data.entity_mappings : [];
  console.log('[normalizeRows] Total entity rows:', entityRows.length);
  console.log('[normalizeRows] Entity kinds:', entityRows.map(r => r?.entity_kind));

  if (entityRows.length > 0) {
    const fieldRows = entityRows.filter((row) => {
      const kind = String(row?.entity_kind || '').toLowerCase();
      const isTable = kind === 'table';
      if (isTable) {
        console.log('[normalizeRows] Filtering out table row:', row?.source_entity || row?.source_name);
      }
      return !isTable;
    });

    console.log('[normalizeRows] Field rows after filtering:', fieldRows.length);
    if (fieldRows.length === 0 && entityRows.length > 0) {
      console.error('[normalizeRows] No field rows! Entity kinds present:',
        [...new Set(entityRows.map(r => r?.entity_kind))]);
    }

    return fieldRows.map((row, index) => {
      const kind = String(row?.entity_kind || 'column').toLowerCase();
      const isMeasure = kind === 'metric' || kind === 'measure';
      const sourceName = String(row?.source_name || row?.name || '').trim();
      const targetName = String(row?.target_name || row?.name || '').trim();
      return {
        id: String(row?.id || `entity-${index}`),
        project_id: String(row?.project_id || data?.project_id || ''),
        model_name: String(row?.model_name || data?.model_name || data?.semantic_model_name || ''),
        source_field: sourceName || `field_${index + 1}`,
        source_path: String(row?.source_path || '').trim(),
        entity_kind: kind,
        parent_source_path: String(row?.parent_source_path || '').trim(),
        source_type: String(row?.source_data_type || row?.data_type || kind || 'unknown'),
        field_type: isMeasure ? 'measure' : 'column',
        measure_source_tables: isMeasure ? resolveMeasureSourceTables(row) : [],
        source_table_name: !isMeasure ? resolveColumnSourceTable(row) : '',
        measure_expression: isMeasure ? resolveMeasureExpression(row) : '',
        target_field: targetName,
        target_type: String(row?.target_data_type || row?.source_data_type || row?.data_type || 'unknown'),
        status: row?.collision_detected ? 'collision' : normalizeStatus({ ...row, target_field: targetName }),
        validation_status: String(row?.validation_status || ''),
        validation_code: String(row?.validation_code || ''),
        validation_message: String(row?.validation_message || ''),
        suggested_target_name: String(row?.suggested_target_name || ''),
        collision_detected: Boolean(row?.collision_detected),
        target_expression: row?.target_expression || '',
        sync_enabled: row?.sync_enabled !== false,
        sync_failure_reason: row?.sync_failure_reason || '',
        depends_on_measures: Array.isArray(row?.depends_on_measures) ? row.depends_on_measures : [],
        synonym_overrides: Array.isArray(row?.synonym_overrides) ? row.synonym_overrides : [],
        synonyms: Array.isArray(row?.synonyms) ? row.synonyms : [],
        // Static risk tier (metric-only; null for columns) and the Tier-5
        // provider's own self-reported estimate (metric-only, Tier-5-
        // only; null otherwise) -- see utils/riskLabels.js and
        // project_mapping_engine.py's _compute_static_risk_tier. Passed
        // through as-is (no renaming/coercion): both are already null
        // for every row that shouldn't show them, straight from the
        // backend.
        static_risk_tier: row?.static_risk_tier ?? null,
        static_risk_label: row?.static_risk_label ?? null,
        llm_self_reported_confidence: row?.llm_self_reported_confidence ?? null,
        // advisory_categories: general-purpose, informational-only tags
        // (see sml/models.py's SMLMetric.advisory_categories) -- e.g.
        // 'enrichment_column_unverifiable' (utils/riskLabels.js's
        // getEnrichmentUnverifiableCaveat). Was already returned by the
        // backend but silently dropped here before this fix -- same
        // allowlist-gap pattern as static_risk_tier/llm_self_reported_
        // confidence above.
        advisory_categories: Array.isArray(row?.advisory_categories) ? row.advisory_categories : [],
        // advisory_notes: the human-readable sibling of advisory_categories
        // above (see sml/models.py's SMLMetric.advisory_notes) -- same
        // parallel-index relationship, and found missing from this
        // allowlist during the same sweep that caught complexity_tier
        // below. Already computed and sent by the backend
        // (mappings_controller.py's filtered_mappings dict); no frontend
        // code reads it yet, but it must survive this boundary regardless,
        // or it's unusable by design the moment someone wires up a caveat
        // banner for it.
        advisory_notes: Array.isArray(row?.advisory_notes) ? row.advisory_notes : [],
        // complexity_tier: DAX complexity tier (1-5; see
        // sml/models.py's SMLMetric.complexity_tier and
        // mappingFilterUtils.js's rowComplexityTier, which already reads
        // this field and has done so correctly since it was written --
        // rowComplexityTier was simply never getting a populated value
        // because this allowlist dropped it first.
        complexity_tier: row?.complexity_tier ?? null,
        // Full path to the .pbix file this row was extracted from — null for
        // single-file dry-runs. See mappingFilterUtils.js's rowSourceFile().
        // Passed through explicitly here for the same reason the comment
        // above this field explains for advisory_categories: this object
        // literal is an explicit allowlist, not a spread of `row`, so any
        // backend field not listed here is silently dropped before it ever
        // reaches DryRunMappingTable — regardless of whether the API
        // response actually included it.
        source_file: row?.source_file ?? null,
        isDirty: false,
      };
    });
  }

  const rows = [];
  const tableMappings = Array.isArray(data?.mappings) ? data.mappings : [];
  tableMappings.forEach((table, tableIndex) => {
    const tableSource = String(table?.source || table?.name || '').trim();
    const columns = Array.isArray(table?.columns) ? table.columns : [];

    if (columns.length === 0) {
      return;
    }

    columns.forEach((column, columnIndex) => {
      const colSource = String(column?.source || column?.name || '').trim();
      const colTarget = String(column?.target || '').trim();
      const fieldType = String(column?.field_type || '').toLowerCase() === 'measure' ? 'measure' : 'column';
      rows.push({
        id: `${table?.id || tableSource || `t${tableIndex}`}::${column?.source_path || colSource || columnIndex}`,
        project_id: String(column?.project_id || data?.project_id || ''),
        model_name: String(column?.model_name || data?.model_name || data?.semantic_model_name || ''),
        source_field: colSource || `column_${columnIndex + 1}`,
        source_path: String(column?.source_path || '').trim(),
        entity_kind: String(column?.entity_kind || 'column').toLowerCase(),
        source_type: String(column?.type || column?.data_type || 'unknown'),
        field_type: fieldType,
        source_table_name: resolveColumnSourceTable(column, tableSource),
        measure_source_tables: [],
        measure_expression: '',
        target_field: colTarget,
        target_type: String(column?.type || column?.data_type || 'unknown'),
        status: normalizeStatus({ ...column, target_field: colTarget }),
        validation_status: String(column?.validation_status || ''),
        validation_code: String(column?.validation_code || ''),
        validation_message: String(column?.validation_message || ''),
        suggested_target_name: String(column?.suggested_target_name || ''),
        collision_detected: Boolean(column?.collision_detected),
        target_expression: column?.target_expression || '',
        sync_enabled: column?.sync_enabled !== false,
        sync_failure_reason: column?.sync_failure_reason || '',
        depends_on_measures: Array.isArray(column?.depends_on_measures) ? column.depends_on_measures : [],
        parent_table: tableSource,
        synonym_overrides: Array.isArray(column?.synonym_overrides) ? column.synonym_overrides : [],
        isDirty: false,
      });
    });
  });

  return rows;
}
