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

export function parseDatasetFromPath(pathValue) {
  const path = String(pathValue || '').trim();
  if (!path) return '';
  const direct = /^datasets\.([^.]+)$/i.exec(path);
  if (direct?.[1]) return String(direct[1]).trim();
  const nested = /^datasets\.([^.]+)\./i.exec(path);
  if (nested?.[1]) return String(nested[1]).trim();
  return '';
}

export function resolveColumnSourceTable(row, parentTable = '') {
  const direct = String(row?.source_table || '').trim();
  if (direct) return direct;

  const fromPath = parseDatasetFromPath(row?.source_path);
  if (fromPath) return fromPath;

  const fromParentPath = parseDatasetFromPath(row?.parent_source_path);
  if (fromParentPath) return fromParentPath;

  return String(parentTable || row?.parent_table || row?.source_table_name || '').trim();
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

export function normalizeTargetStatus(item) {
  if (item?.collision_detected) return 'collision';
  const explicit = String(item?.status || '').toLowerCase();
  if (!String(item?.target || item?.target_name || item?.target_field || '').trim()) return 'unmapped';
  if (explicit === 'manual') return 'manual';
  return 'auto';
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
      const tbl = sanitizeMappingName(
        resolveColumnSourceTable(column, mapping?.source || '') ||
        column?.source_table_name || column?.parent_table || mapping?.source || ''
      );
      const resolved = (tbl && tbl !== baseTarget)
        ? `${tbl}_${baseTarget}`
        : `${baseTarget}_${shortDeterministicHash(buildSourceFingerprint(mapping, column)).slice(0, 6)}`;
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

/**
 * Given a collision row and the full flat list of rows, return:
 *  - peers: the other rows that share the same target name (what we're colliding with)
 *  - suggestions: ordered list of rename options, best-first
 */
export function suggestCollisionResolutions(row, allRows) {
  const baseTarget = sanitizeMappingName(row?.target_field || row?.source_field || '');
  const tbl        = sanitizeMappingName(
    resolveColumnSourceTable(row) || row?.source_table_name || ''
  );
  const field      = sanitizeMappingName(row?.source_field || '');

  const peers = (allRows || []).filter(
    r => r.id !== row.id &&
         sanitizeMappingName(r.target_field || r.source_field || '') === baseTarget
  );

  const suggestions = [];

  if (Array.isArray(row?.resolution_suggestions) && row.resolution_suggestions.length > 0) {
    row.resolution_suggestions.forEach((item, idx) => {
      suggestions.push({
        id: `backend_${idx}`,
        label: sanitizeMappingName(item),
        description: `Backend resolution option`,
      });
    });
  } else {
    // Option A: table_prefix — SALES_AMOUNT (most readable, preferred)
    if (tbl && tbl !== baseTarget) {
      suggestions.push({
        id: 'table_prefix',
        label: `${tbl}_${field}`,
        description: `Prefix with source table "${tbl}"`,
      });
    }

    // Option B: kind_prefix — COL_AMOUNT or MSR_AMOUNT
    const kind = String(row?.entity_kind || row?.field_type || 'col').toUpperCase();
    const kindPrefix = kind === 'MEASURE' || kind === 'METRIC' ? 'MSR' : 'COL';
    const kindLabel = `${kindPrefix}_${field}`;
    if (kindLabel !== suggestions[0]?.label) {
      suggestions.push({
        id: 'kind_prefix',
        label: kindLabel,
        description: `Prefix with entity type (${kindPrefix})`,
      });
    }

    // Option C: short hash fallback — always last
    const hash = shortDeterministicHash(buildSourceFingerprint({}, row)).slice(0, 6);
    suggestions.push({
      id: 'hash',
      label: `${baseTarget}_${hash}`,
      description: 'Append short unique hash (last resort)',
    });
  }

  return { suggestions, peers };
}

/**
 * Resolves all collisions in a rows list by generating unique, collision-free names.
 * Returns a mapping of row.id -> candidateName.
 */
export function resolveCollisionsForRows(rows) {
  const mappings = Array.isArray(rows) ? rows : [];
  const collisionRows = mappings.filter(
    r => String(r?.status || '').toLowerCase() === 'collision'
  );
  if (!collisionRows.length) return {};

  const resolvedMap = {};
  const assignedNames = new Set(
    mappings
      .filter(r => String(r?.status || '').toLowerCase() !== 'collision')
      .map(r => String(r?.target_field || r?.target_name || '').trim().toUpperCase())
      .filter(Boolean)
  );

  collisionRows.forEach(row => {
    const { suggestions } = suggestCollisionResolutions(row, mappings);
    let candidate = '';
    let suggestionIdx = 0;

    if (suggestions.length > 0) {
      candidate = suggestions[0].label;
      while (assignedNames.has(candidate.toUpperCase()) && suggestionIdx < suggestions.length - 1) {
        suggestionIdx += 1;
        candidate = suggestions[suggestionIdx].label;
      }
    } else {
      candidate = row.target_field || row.source_field || '';
    }

    if (assignedNames.has(candidate.toUpperCase())) {
      const base = candidate;
      const hash = shortDeterministicHash(buildSourceFingerprint({}, row)).slice(0, 6).toUpperCase();
      candidate = `${base}_${hash}`;

      let counter = 2;
      while (assignedNames.has(candidate.toUpperCase())) {
        candidate = `${base}_${hash}_${counter}`;
        counter += 1;
      }
    }

    resolvedMap[row.id] = candidate;
    assignedNames.add(candidate.toUpperCase());
  });

  return resolvedMap;
}
