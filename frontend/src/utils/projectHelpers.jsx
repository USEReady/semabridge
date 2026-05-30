import { Cloud, Database, Snowflake } from 'lucide-react';

export function shortDeterministicHash(value) {
  let hash = 2166136261;
  const text = String(value || '');
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function sanitizeMappingName(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const cleaned = raw
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!cleaned) return '';
  return /^\d/.test(cleaned) ? `N_${cleaned}` : cleaned;
}

export function resolveSourceTableName(column, tableSource = '') {
  const fromColumn = String(column?.source_table_name || column?.parent_table || '').trim();
  if (fromColumn) return fromColumn;
  const path = String(column?.source_path || column?.parent_source_path || '').trim();
  const direct = /^datasets\.([^.]+)/i.exec(path);
  if (direct?.[1]) return direct[1];
  return String(tableSource || '').trim();
}

export function normalizeTargetStatus(item) {
  if (item?.collision_detected) return 'collision';
  const explicit = String(item?.status || '').toLowerCase();
  if (!String(item?.target || item?.target_name || item?.target_field || '').trim()) return 'unmapped';
  if (explicit === 'manual') return 'manual';
  return 'auto';
}

export function buildSourceFingerprint(mapping, column) {
  return [
    mapping?.source_schema,
    mapping?.schema,
    mapping?.source,
    column?.source_table_name,
    column?.parent_table,
    column?.source_path,
    column?.source,
  ].map(part => String(part || '').trim()).filter(Boolean).join('.');
}

export function applyHashDeduplication(mappings) {
  return (mappings || []).map((mapping) => {
    const columns = Array.isArray(mapping?.columns) ? mapping.columns : [];
    if (columns.length === 0) return mapping;

    const grouped = new Map();
    columns.forEach((column) => {
      const key = sanitizeMappingName(column?.target || column?.source || '');
      if (!key) return;
      grouped.set(key, [...(grouped.get(key) || []), column]);
    });

    const nextColumns = columns.map((column) => {
      const baseTarget = sanitizeMappingName(column?.target || column?.source || '');
      const duplicates = grouped.get(baseTarget) || [];
      if (duplicates.length < 2) {
        return { ...column, status: normalizeTargetStatus(column), collision_detected: false };
      }
      const hash = shortDeterministicHash(buildSourceFingerprint(mapping, column));
      const resolved = `${baseTarget}_${hash}`;
      return {
        ...column,
        target: resolved,
        suggested_target_name: resolved,
        original_target_name: baseTarget,
        status: 'manual',
        auto_resolved: true,
        collision_detected: false,
      };
    });

    return {
      ...mapping,
      columns: nextColumns,
      status: String(mapping?.status || 'auto').toLowerCase(),
      collision_detected: false,
    };
  });
}

export function TypeBadge({ type }) {
  const label = String(type || 'unknown');
  const normalized = label.toLowerCase();
  let hash = 0;
  for (let i = 0; i < normalized.length; i += 1) {
    hash = ((hash << 5) - hash) + normalized.charCodeAt(i);
    hash |= 0;
  }
  const hue = Math.abs(hash) % 360;
  const color = `hsl(${hue}, 72%, 58%)`;
  return (
    <span style={{
      fontFamily: 'monospace',
      fontSize: 10,
      fontWeight: 700,
      padding: '2px 6px',
      borderRadius: 4,
      background: `${color}20`,
      color,
      border: `1px solid ${color}35`,
      whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  );
}

export function FieldKindBadge({ kind = 'Column', fieldType }) {
  const resolvedKind = String(fieldType || kind || 'column').toLowerCase();
  const isMeasure = resolvedKind === 'measure' || resolvedKind === 'metric';
  return (
    <span style={{
      fontSize: 10,
      fontWeight: 700,
      padding: '2px 6px',
      borderRadius: 999,
      border: isMeasure ? '1px solid rgba(56, 189, 248, 0.45)' : '1px solid var(--border-main)',
      background: isMeasure ? 'rgba(56, 189, 248, 0.14)' : 'var(--bg-surface-raised)',
      color: isMeasure ? '#7dd3fc' : 'var(--text-secondary)',
      whiteSpace: 'nowrap',
    }}>
      {isMeasure ? 'fx Measure' : 'Column'}
    </span>
  );
}

export function SourceTableBadge({ tables, hasIssue = false }) {
  const uniqueTables = Array.isArray(tables)
    ? [...new Set(tables.map((name) => String(name || '').trim()).filter(Boolean))]
    : [];
  if (uniqueTables.length === 0) return null;

  const isMulti = uniqueTables.length > 1;
  const label = isMulti ? 'Table: Multi-table' : `Table: ${uniqueTables[0]}`;
  const tooltip = isMulti ? uniqueTables.join(', ') : uniqueTables[0];
  return (
    <span
      title={tooltip}
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 6px',
        borderRadius: 999,
        border: hasIssue ? '1px solid rgba(239, 68, 68, 0.45)' : '1px solid rgba(56, 189, 248, 0.45)',
        background: hasIssue ? 'rgba(239, 68, 68, 0.12)' : 'rgba(56, 189, 248, 0.12)',
        color: hasIssue ? 'var(--color-error)' : '#7dd3fc',
        maxWidth: 190,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  );
}

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

export function isBlockingRow(row) {
  const status = String(row?.status || '').toLowerCase();
  if (status === 'collision' || status === 'unmapped') return true;

  const validationStatus = String(row?.validation_status || '').toLowerCase();
  if (validationStatus === 'invalid' || validationStatus === 'collision') return true;

  const validationCode = String(row?.validation_code || '').toUpperCase();
  return Boolean(validationCode && validationCode !== 'OK');
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
        status: normalizeStatus({ ...row, target_field: targetName }),
        validation_status: String(row?.validation_status || ''),
        validation_code: String(row?.validation_code || ''),
        validation_message: String(row?.validation_message || ''),
        suggested_target_name: String(row?.suggested_target_name || ''),
        collision_detected: Boolean(row?.collision_detected),
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
        parent_table: tableSource,
        isDirty: false,
      });
    });
  });

  return rows;
}

export function countResponseFields(data) {
  const entityRows = Array.isArray(data?.entity_mappings)
    ? data.entity_mappings.filter((row) => String(row?.entity_kind || '').toLowerCase() !== 'table')
    : [];
  if (entityRows.length > 0) return entityRows.length;

  const tableMappings = Array.isArray(data?.mappings) ? data.mappings : [];
  return tableMappings.flatMap((mapping) => {
    const columns = Array.isArray(mapping?.columns) ? mapping.columns : [];
    return columns;
  }).length;
}

export function getConnectorPresentation(type) {
  const normalized = String(type || '').toLowerCase();
  if (normalized.includes('fabric')) {
    return { label: 'Microsoft Fabric', icon: <Cloud size={14} />, accent: '#60a5fa' };
  }
  if (normalized.includes('snowflake')) {
    return { label: 'Snowflake', icon: <Snowflake size={14} />, accent: '#38bdf8' };
  }
  if (normalized.includes('databricks')) {
    return { label: 'Databricks', icon: <Database size={14} />, accent: '#fb923c' };
  }
  return { label: normalized || 'Connector', icon: <Database size={14} />, accent: '#94a3b8' };
}
