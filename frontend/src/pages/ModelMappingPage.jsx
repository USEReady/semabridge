import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Check, Cloud, Database, FileCode2, Loader2, Play, RefreshCw, Rocket, Search, Snowflake } from 'lucide-react';
import { parseDocument as parseYamlDocument } from 'yaml';
import SearchableSelect from '../components/common/SearchableSelect';
import PageHeader from '../components/common/PageHeader';
import StatusBadge from '../components/common/StatusBadge';
import { api } from '../utils/api';

const TYPE_COLORS = {
  string: '#6366f1', integer: '#10b981', decimal: '#f59e0b',
  boolean: '#8b5cf6', date: '#06b6d4', timestamp: '#ec4899',
  uuid: '#84cc16', text: '#6b7280', float: '#f97316',
};

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'auto', label: 'Auto' },
  { id: 'manual', label: 'Manual' },
  { id: 'unmapped', label: 'Unmapped' },
  { id: 'collision', label: 'Collision/Error' },
];

const SNOWFLAKE_RESERVED = new Set([
  'SELECT', 'GROUP', 'ORDER', 'TABLE', 'COLUMN', 'DATE', 'FROM', 'WHERE',
  'BY', 'JOIN', 'VIEW', 'UNION', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP',
]);

function TypeBadge({ type }) {
  const color = TYPE_COLORS[String(type || '').toLowerCase()] ?? '#6b7280';
  return (
    <span
      style={{
        fontFamily: 'monospace',
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 6px',
        borderRadius: 4,
        background: `${color}20`,
        color,
        border: `1px solid ${color}30`,
      }}
    >
      {type || '?'}
    </span>
  );
}

function FieldKindBadge({ fieldType }) {
  const kind = String(fieldType || 'column').toLowerCase();
  const isMeasure = kind === 'measure';
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 6px',
        borderRadius: 999,
        border: isMeasure ? '1px solid rgba(56, 189, 248, 0.45)' : '1px solid var(--border-main)',
        background: isMeasure ? 'rgba(56, 189, 248, 0.14)' : 'var(--bg-surface-raised)',
        color: isMeasure ? '#7dd3fc' : 'var(--text-secondary)',
      }}
    >
        {isMeasure ? 'fx Measure' : 'Column'}
    </span>
  );
}

function SourceTableBadge({ tables, hasIssue = false }) {
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

function getConnectorPresentation(type) {
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

function getProjectYamlLabel(project) {
  return String(project?.display_name || project?.name || project?.file_name || project?.project_name || project?.id || 'Project YAML').trim();
}

function getProjectYamlPath(project) {
  return String(project?.file_path || project?.yaml_path || '').trim();
}

function renderProjectYamlOption(project) {
  const label = getProjectYamlLabel(project);
  const fileName = String(project?.file_name || '').trim();
  const filePath = getProjectYamlPath(project);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <span style={{ fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
      <span style={{ fontSize: 11, color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {fileName || filePath}
      </span>
    </div>
  );
}

function normalizeStatus(item) {
  const validation = String(item?.validation_status || '').toLowerCase();
  const explicit = String(item?.status || '').toLowerCase();
  if (validation === 'invalid' || validation === 'collision' || item?.collision_detected) return 'collision';
  if (!String(item?.target_field || item?.target_name || '').trim()) return 'unmapped';
  if (explicit === 'manual') return 'manual';
  return 'auto';
}

function isBlockingRow(row) {
  const status = String(row?.status || '').toLowerCase();
  if (status === 'collision' || status === 'unmapped') return true;

  const validationStatus = String(row?.validation_status || '').toLowerCase();
  if (validationStatus === 'invalid' || validationStatus === 'collision') return true;

  const validationCode = String(row?.validation_code || '').toUpperCase();
  if (validationCode && validationCode !== 'OK') return true;

  return false;
}

function extractDryRunBlockers(error) {
  const detail = error?.payload?.detail;
  if (!detail || typeof detail !== 'object') return null;
  const mode = String(detail.mode || '').toUpperCase();
  const blockers = Array.isArray(detail.blocking_issues) ? detail.blocking_issues : [];
  if (mode !== 'DRY_RUN' || blockers.length === 0) return null;
  return {
    message: String(detail.message || '').trim(),
    blockers,
  };
}

function parseDatasetFromPath(pathValue) {
  const path = String(pathValue || '').trim();
  if (!path) return '';
  const direct = /^datasets\.([^\.]+)$/i.exec(path);
  if (direct?.[1]) return String(direct[1]).trim();
  const nested = /^datasets\.([^\.]+)\./i.exec(path);
  if (nested?.[1]) return String(nested[1]).trim();
  return '';
}

function resolveMeasureSourceTables(row) {
  const fromRow = Array.isArray(row?.measure_source_tables)
    ? row.measure_source_tables.map((item) => String(item || '').trim()).filter(Boolean)
    : [];
  if (fromRow.length > 0) return [...new Set(fromRow)];

  const fallback = parseDatasetFromPath(row?.parent_source_path);
  return fallback ? [fallback] : [];
}

function resolveColumnSourceTable(row, parentTable = '') {
  const fromPath = parseDatasetFromPath(row?.source_path);
  if (fromPath) return fromPath;

  const fromParentPath = parseDatasetFromPath(row?.parent_source_path);
  if (fromParentPath) return fromParentPath;

  const fallbackParent = String(parentTable || row?.parent_table || '').trim();
  return fallbackParent;
}

function resolveMeasureExpression(row) {
  const expression = String(row?.source_expression || row?.expression || '').trim();
  if (!expression) return '';
  return expression;
}

function normalizeRows(data) {
  const entityRows = Array.isArray(data?.entity_mappings) ? data.entity_mappings : [];
  if (entityRows.length > 0) {
    const scopedEntityRows = entityRows
      .filter((row) => {
        const kind = String(row?.entity_kind || '').toLowerCase();
        return kind === 'column' || kind === 'metric';
      })
      .map((row, index) => {
        const kind = String(row?.entity_kind || '').toLowerCase();
        const sourceName = String(row?.source_name || '').trim();
        const targetName = String(row?.target_name || '').trim();
        return {
          id: String(row?.id || `${sourceName || 'field'}-${index}`),
          source_field: sourceName || `field_${index + 1}`,
          source_path: String(row?.source_path || '').trim(),
          entity_kind: kind || 'column',
          parent_source_path: String(row?.parent_source_path || '').trim(),
          source_type: row?.source_data_type || kind || 'unknown',
          field_type: kind === 'metric' ? 'measure' : 'column',
          measure_source_tables: kind === 'metric' ? resolveMeasureSourceTables(row) : [],
          source_table_name: kind === 'column' ? resolveColumnSourceTable(row) : '',
          measure_expression: kind === 'metric' ? resolveMeasureExpression(row) : '',
          target_field: targetName,
          target_type: row?.target_data_type || row?.source_data_type || 'unknown',
          status: normalizeStatus({ ...row, target_field: targetName }),
          validation_status: row?.validation_status || '',
          validation_code: row?.validation_code || '',
          validation_message: row?.validation_message || '',
          suggested_target_name: row?.suggested_target_name || '',
          isDirty: false,
        };
      });
    if (scopedEntityRows.length > 0) {
      return scopedEntityRows;
    }
  }

  const rows = [];
  const tableMappings = Array.isArray(data?.mappings) ? data.mappings : [];

  tableMappings.forEach((table, tableIndex) => {
    const tableSource = String(table?.source || '').trim();
    const tableTarget = String(table?.target || '').trim();
    const columns = Array.isArray(table?.columns) ? table.columns : [];

    if (columns.length === 0) {
      rows.push({
        id: String(table?.id || `${tableSource}-${tableIndex}`),
        source_field: tableSource || `field_${tableIndex + 1}`,
        source_path: String(table?.source_path || tableSource || '').trim(),
        entity_kind: String(table?.entity_kind || table?.type || 'table').toLowerCase(),
        source_type: table?.type || 'table',
        field_type: String(table?.type || '').toLowerCase() === 'metric' ? 'measure' : 'column',
        target_field: tableTarget,
        target_type: table?.type || 'table',
        status: normalizeStatus({ ...table, target_field: tableTarget }),
        validation_status: table?.validation_status || '',
        validation_code: table?.validation_code || '',
        validation_message: table?.validation_message || '',
        suggested_target_name: table?.suggested_target_name || '',
        isDirty: false,
      });
      return;
    }

    columns.forEach((column, columnIndex) => {
      rows.push({
        id: `${table?.id || tableSource || `t${tableIndex}`}:${column?.source_path || column?.source || columnIndex}`,
        source_field: String(column?.source || '').trim() || `column_${columnIndex + 1}`,
        source_path: String(column?.source_path || '').trim(),
        entity_kind: String(column?.entity_kind || 'column').toLowerCase(),
        source_type: column?.type || 'unknown',
        field_type: String(column?.field_type || column?.type || '').toLowerCase() === 'metric' ? 'measure' : 'column',
        source_table_name: resolveColumnSourceTable(column, tableSource),
        target_field: String(column?.target || '').trim(),
        target_type: column?.type || 'unknown',
        status: normalizeStatus({ ...column, target_field: column?.target }),
        validation_status: column?.validation_status || '',
        validation_code: column?.validation_code || '',
        validation_message: column?.validation_message || '',
        suggested_target_name: column?.suggested_target_name || '',
        parent_table: tableSource,
        isDirty: false,
      });
    });
  });

  return rows;
}

function sanitizeIdentifier(value) {
  const raw = String(value || '').trim();
  if (!raw) return 'UNNAMED';
  const cleaned = raw
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!cleaned) return 'UNNAMED';
  return /^\d/.test(cleaned) ? `N_${cleaned}` : cleaned;
}

function validateTargetName(value, targetPlatform, sourceType, targetType) {
  const next = String(value || '').trim();
  if (!next) {
    return { isValid: false, code: 'EMPTY_TARGET', message: 'Target field cannot be empty.', suggestion: 'UNNAMED' };
  }

  const platform = String(targetPlatform || '').toLowerCase();
  const upper = next.toUpperCase();

  if (platform.includes('snowflake') && SNOWFLAKE_RESERVED.has(upper)) {
    return {
      isValid: false,
      code: 'RESERVED_KEYWORD',
      message: 'Target field is a Snowflake reserved keyword.',
      suggestion: `COL_${upper}`,
    };
  }

  const sanitized = sanitizeIdentifier(next);
  if (platform.includes('snowflake') && sanitized !== upper) {
    return {
      isValid: false,
      code: 'UNSUPPORTED_CHARACTERS',
      message: 'Target field has unsupported characters for Snowflake.',
      suggestion: sanitized,
    };
  }

  if (String(sourceType || '').toLowerCase() === 'boolean' && String(targetType || '').toLowerCase() === 'date') {
    return {
      isValid: false,
      code: 'INCOMPATIBLE_TYPE',
      message: 'Source and target data types are incompatible.',
      suggestion: sanitized,
    };
  }

  return { isValid: true, code: 'OK', message: '', suggestion: sanitized };
}

function parseProjectSemabridgeYaml(rawYaml) {
  if (!rawYaml || typeof rawYaml !== 'string') {
    return {
      projectName: 'Project YAML',
      sourceName: 'Source Model',
      sourceType: 'Unknown',
      targetName: 'Target Model',
      targetType: 'Unknown',
      selectedModels: [],
    };
  }

  try {
    const parsedDoc = parseYamlDocument(rawYaml, { uniqueKeys: false, prettyErrors: true });
    const tree = parsedDoc?.toJS ? parsedDoc.toJS() : {};
    const source = tree?.source && typeof tree.source === 'object' ? tree.source : {};
    const target = tree?.target && typeof tree.target === 'object'
      ? tree.target
      : (Array.isArray(tree?.targets) && tree.targets.length > 0 && typeof tree.targets[0] === 'object' ? tree.targets[0] : {});
    const selectedModels = Array.isArray(source?.models)
      ? source.models.map((item) => String(item || '').trim()).filter(Boolean)
      : [];
    const sourceName = selectedModels[0] || String(source?.name || source?.type || 'fabric');
    const targetName = String(target?.name || target?.type || 'snowflake');

    return {
      projectName: String(tree?.project_name || 'Project YAML'),
      sourceName,
      sourceType: String(source?.type || 'Unknown'),
      targetName,
      targetType: String(target?.type || 'Unknown'),
      selectedModels,
    };
  } catch {
    return {
      projectName: 'Project YAML',
      sourceName: 'Source Model',
      sourceType: 'Unknown',
      targetName: 'Target Model',
      targetType: 'Unknown',
      selectedModels: [],
    };
  }
}

export default function ModelMappingPage() {
  const queryProjectId = useMemo(() => {
    try {
      return new URLSearchParams(window.location.search).get('project_id') || '';
    } catch {
      return '';
    }
  }, []);

  const [projectFiles, setProjectFiles] = useState([]);
  const [selectedProjectId, setSelectedProjectId] = useState(queryProjectId);
  const [activeProjectLabel, setActiveProjectLabel] = useState('Project YAML');
  const [activeProjectPath, setActiveProjectPath] = useState('');
  const [availableModels, setAvailableModels] = useState([]);
  const [activeModelName, setActiveModelName] = useState('');
  const [sourceModel, setSourceModel] = useState({ name: 'Source Model', type: 'Unknown', field_count: 0 });
  const [targetModel, setTargetModel] = useState({ name: 'Target Model', type: 'Unknown', field_count: 0 });

  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isDryRunLoading, setIsDryRunLoading] = useState(false);
  const [deployLoading, setDeployLoading] = useState(false);
  const [hasDryRunResult, setHasDryRunResult] = useState(false);
  const [pageError, setPageError] = useState('');
  const [search, setSearch] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');

  const selectedProjectFile = useMemo(
    () => projectFiles.find((project) => String(project?.id || project?.project_id || '') === String(selectedProjectId || '')) || null,
    [projectFiles, selectedProjectId],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setPageError('');
        const list = await api.listProjectDiscovery();
        if (cancelled) return;
        const nextFiles = Array.isArray(list) ? list : [];
        setProjectFiles(nextFiles);
        if (nextFiles.length === 0) {
          setPageError('No YAML files were found in Config/projects.');
          return;
        }

        const preferred = nextFiles.find((project) => String(project?.id || project?.project_id || '') === String(queryProjectId || '')) || nextFiles[0];
        const preferredId = String(preferred?.id || preferred?.project_id || '').trim();
        if (preferredId) {
          setSelectedProjectId((current) => current || preferredId);
        }
        if (preferred) {
          setActiveProjectLabel(getProjectYamlLabel(preferred));
          setActiveProjectPath(getProjectYamlPath(preferred));
        }
      } catch {
        if (!cancelled) {
          setProjectFiles([]);
          setPageError('Failed to load project YAML files from Config/projects.');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [queryProjectId]);

  useEffect(() => {
    if (!selectedProjectFile) return;
    setActiveProjectLabel(getProjectYamlLabel(selectedProjectFile));
    setActiveProjectPath(getProjectYamlPath(selectedProjectFile));
  }, [selectedProjectFile]);

  const loadProjectMappings = useCallback(async (projectId) => {
    if (!projectId) {
      setRows([]);
      setLoading(false);
      return;
    }

    setLoading(true);
    setPageError('');
    setHasDryRunResult(false);

    try {
      const config = await api.getProjectConfig(projectId);
      const semabridgeContext = parseProjectSemabridgeYaml(config?.config_yaml || '');
      const discoveredModels = Array.isArray(semabridgeContext.selectedModels) ? semabridgeContext.selectedModels : [];
      setAvailableModels(discoveredModels);
      setActiveModelName((current) => {
        if (current && discoveredModels.includes(current)) return current;
        return discoveredModels[0] || '';
      });

      const mappingData = await api.getMappings(projectId);
      const nextRows = normalizeRows(mappingData);
      setRows(nextRows);
      setHasDryRunResult(false);

      const sourceLabel = String(semabridgeContext.sourceType || 'fabric');
      const targetLabel = String(semabridgeContext.targetType || 'snowflake');
      setActiveProjectLabel(getProjectYamlLabel(selectedProjectFile) || String(semabridgeContext.projectName || 'Project YAML'));
      setActiveProjectPath(getProjectYamlPath(selectedProjectFile) || String(config?.yaml_path || ''));
      setSourceModel({
        name: String(semabridgeContext.sourceName || 'fabric'),
        type: sourceLabel,
        field_count: nextRows.length,
      });
      setTargetModel({
        name: String(semabridgeContext.targetName || 'snowflake'),
        type: targetLabel,
        field_count: nextRows.filter((row) => String(row.target_field || '').trim()).length,
      });
    } catch (error) {
      setPageError(error?.message || 'Failed to load project mappings.');
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [selectedProjectFile]);

  useEffect(() => {
    loadProjectMappings(selectedProjectId);
  }, [loadProjectMappings, selectedProjectId]);

  const runDryMap = useCallback(async () => {
    if (!selectedProjectId) return;

    setIsDryRunLoading(true);
    setPageError('');

    try {
      const projectConfig = await api.getProjectConfig(selectedProjectId);
      const semabridgeContext = parseProjectSemabridgeYaml(projectConfig?.config_yaml || '');
      const discoveredModels = Array.isArray(semabridgeContext.selectedModels) ? semabridgeContext.selectedModels : [];
      const scopedModel = (activeModelName && discoveredModels.includes(activeModelName))
        ? activeModelName
        : (discoveredModels[0] || '');
      const dryRunPayload = {
        dry_run: true,
        config_yaml: projectConfig?.config_yaml || '',
        target_connector: String(semabridgeContext.targetType || targetModel.type || 'snowflake'),
        selected_model_names: scopedModel ? [scopedModel] : [],
        project_name: semabridgeContext.projectName,
      };

      const result = await api.autoMap({
        project_id: selectedProjectId,
        ...dryRunPayload,
      });

      const nextRows = normalizeRows(result);

      setRows(nextRows);
      setHasDryRunResult(nextRows.length > 0);
      if (nextRows.length === 0) {
        setPageError('Dry run completed, but no mappable columns were returned for this project/model scope.');
      }
    } catch (error) {
      setPageError(error?.message || 'Dry run failed for this project.');
      setHasDryRunResult(false);
    } finally {
      setIsDryRunLoading(false);
    }
  }, [activeModelName, selectedProjectId, targetModel.type]);

  const handleInlineTargetChange = useCallback((rowId, value) => {
    setRows((prev) => prev.map((row) => {
      if (row.id !== rowId) return row;
      const validation = validateTargetName(value, targetModel.type, row.source_type, row.target_type);
      if (validation.isValid) {
        return {
          ...row,
          target_field: value,
          status: 'manual',
          validation_status: 'valid',
          validation_code: 'OK',
          validation_message: '',
          suggested_target_name: validation.suggestion,
          isDirty: true,
        };
      }
      return {
        ...row,
        target_field: value,
        status: 'collision',
        validation_status: 'invalid',
        validation_code: validation.code,
        validation_message: validation.message,
        suggested_target_name: validation.suggestion,
        isDirty: true,
      };
    }));
  }, [targetModel.type]);

  const applySuggestion = useCallback((rowId) => {
    setRows((prev) => prev.map((row) => {
      if (row.id !== rowId) return row;
      const suggested = String(row.suggested_target_name || '').trim();
      if (!suggested) return row;
      const validation = validateTargetName(suggested, targetModel.type, row.source_type, row.target_type);
      return {
        ...row,
        target_field: suggested,
        status: validation.isValid ? 'manual' : 'collision',
        validation_status: validation.isValid ? 'valid' : 'invalid',
        validation_code: validation.code,
        validation_message: validation.message,
        isDirty: true,
      };
    }));
  }, [targetModel.type]);

  const blockingCount = useMemo(
    () => rows.filter((row) => isBlockingRow(row)).length,
    [rows],
  );

  const dirtyRowsCount = useMemo(
    () => rows.filter((row) => row.isDirty && row.id).length,
    [rows],
  );

  const deployReadiness = useMemo(() => {
    if (deployLoading) {
      return { canDeploy: false, reason: 'Deploy is currently running.' };
    }
    if (isDryRunLoading || loading) {
      return { canDeploy: false, reason: 'Wait for mapping validation to finish.' };
    }
    if (!hasDryRunResult) {
      return { canDeploy: false, reason: 'Run Auto-Map first to generate a dry-run result.' };
    }
    if (blockingCount > 0) {
      return { canDeploy: false, reason: `Resolve ${blockingCount} blocking row(s) before deploy.` };
    }
    if (dirtyRowsCount <= 0) {
      return { canDeploy: false, reason: 'No edited mappings to deploy yet.' };
    }
    return { canDeploy: true, reason: '' };
  }, [blockingCount, deployLoading, dirtyRowsCount, hasDryRunResult, isDryRunLoading, loading]);

  const counts = useMemo(() => {
    const summary = { all: rows.length, auto: 0, manual: 0, unmapped: 0, collision: 0 };
    rows.forEach((row) => {
      if (summary[row.status] !== undefined) summary[row.status] += 1;
    });
    return summary;
  }, [rows]);

  const filteredRows = useMemo(() => {
    const query = String(search || '').trim().toLowerCase();
    return rows.filter((row) => {
      if (activeFilter !== 'all' && row.status !== activeFilter) return false;
      if (!query) return true;
      const haystack = `${row.source_field} ${row.target_field} ${row.validation_message} ${row.field_type}`.toLowerCase();
      const originHaystack = Array.isArray(row.measure_source_tables)
        ? row.measure_source_tables.join(' ').toLowerCase()
        : '';
      const columnTableHaystack = String(row.source_table_name || '').toLowerCase();
      const expressionHaystack = String(row.measure_expression || '').toLowerCase();
      return `${haystack} ${originHaystack} ${columnTableHaystack} ${expressionHaystack}`.includes(query);
    });
  }, [activeFilter, rows, search]);

  const deployMappings = useCallback(async () => {
    if (!selectedProjectId || !deployReadiness.canDeploy) return;

    setDeployLoading(true);
    setPageError('');

    try {
      const dirtyRows = rows.filter((row) => row.isDirty && row.id);
      for (const row of dirtyRows) {
        await api.updateMapping(String(row.id), {
          project_id: selectedProjectId,
          source_path: row.source_path,
          entity_kind: row.entity_kind,
          source_name: row.source_field,
          target_name: row.target_field,
          status: row.status,
        });
      }
      await api.syncProject(selectedProjectId);
      await loadProjectMappings(selectedProjectId);
    } catch (error) {
      const blocked = extractDryRunBlockers(error);
      if (blocked) {
        const blockerById = new Map(
          blocked.blockers
            .map((item) => [String(item?.id || '').trim(), item])
            .filter(([id]) => Boolean(id)),
        );
        const blockerByPath = new Map(
          blocked.blockers
            .map((item) => [String(item?.source_path || '').trim(), item])
            .filter(([path]) => Boolean(path)),
        );
        setRows((prev) => prev.map((row) => {
          const byId = blockerById.get(String(row.id || '').trim());
          const byPath = blockerByPath.get(String(row.source_path || '').trim());
          const blocker = byId || byPath;
          if (!blocker) return row;
          return {
            ...row,
            status: 'collision',
            validation_status: String(blocker.validation_status || 'invalid').toLowerCase(),
            validation_code: String(blocker.validation_code || 'INVALID_IDENTIFIER_REFERENCE').toUpperCase(),
            validation_message: String(blocker.validation_message || 'Dry-run deploy blocker detected.'),
          };
        }));
        setHasDryRunResult(true);
        setPageError(blocked.message || 'Deploy blocked by dry-run mapping issues. Resolve blockers and retry.');
      } else {
        setPageError(error?.message || 'Deploy failed. Resolve issues and retry.');
      }
    } finally {
      setDeployLoading(false);
    }
  }, [deployReadiness.canDeploy, loadProjectMappings, rows, selectedProjectId]);

  return (
    <div style={{ padding: '28px 16px', minHeight: '100%', maxWidth: 1400, margin: '0 auto' }} className="md:px-10">
      <PageHeader
        breadcrumb={['Projects', 'Model Mapping']}
        title="Model Mapping Workspace"
        description="Select a project YAML from Config/projects, run auto-map as a dry-run validation, resolve collisions, then deploy validated mappings."
        action={{
          label: isDryRunLoading ? 'Running Auto-Map...' : 'Run Auto-Map',
          icon: isDryRunLoading ? <RefreshCw size={14} className="animate-spin" /> : <Play size={14} />,
          onClick: runDryMap,
        }}
      />

      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 320, maxWidth: 520, flex: '1 1 320px' }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-tertiary)', marginBottom: 6 }}>
            Project YAML
          </div>
          <SearchableSelect
            items={projectFiles}
            displayKey="name"
            valueKey="id"
            searchFields={['display_name', 'name', 'file_name', 'file_path']}
            value={selectedProjectId}
            placeholder="Select a YAML file from Config/projects..."
            onChange={(item) => setSelectedProjectId(String(item?.id || item?.project_id || '').trim())}
            loading={projectFiles.length === 0 && loading}
            clearable={false}
            renderItem={renderProjectYamlOption}
          />
        </div>

        <div style={{ border: '1px solid var(--border-main)', borderRadius: 999, background: 'var(--bg-surface-raised)', padding: '7px 12px', fontSize: 12, color: 'var(--text-secondary)', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <FileCode2 size={14} style={{ color: 'var(--accent-blue)' }} />
          <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>Active YAML</span>
          <span>{activeProjectLabel}</span>
        </div>
        {activeProjectPath && (
          <div style={{ border: '1px solid var(--border-main)', borderRadius: 999, background: 'var(--bg-surface-raised)', padding: '7px 12px', fontSize: 12, color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 560 }} title={activeProjectPath}>
            {activeProjectPath.replace(/\\/g, '/')}
          </div>
        )}
        {availableModels.length > 0 && (
          <div style={{ display: 'grid', gap: 6, minWidth: 240 }}>
            <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>
              Active Model Scope
            </div>
            <select
              value={activeModelName}
              onChange={(e) => setActiveModelName(String(e.target.value || ''))}
              style={{
                background: 'var(--bg-input)',
                border: '1px solid var(--border-main)',
                color: 'var(--text-primary)',
                borderRadius: 8,
                padding: '7px 10px',
                fontSize: 12,
              }}
            >
              {availableModels.map((modelName) => (
                <option key={modelName} value={modelName}>{modelName}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
        <FlowCard label="SOURCE MODEL" model={sourceModel} />
        <FlowCard label="TARGET MODEL" model={targetModel} />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {FILTERS.map((filter) => {
          const isActive = activeFilter === filter.id;
          const isCollision = filter.id === 'collision';
          const count = counts[filter.id] ?? counts.all;
          return (
            <button
              key={filter.id}
              type="button"
              onClick={() => setActiveFilter(filter.id)}
              style={{
                borderRadius: 999,
                border: isCollision ? '1px solid rgba(239, 68, 68, 0.45)' : '1px solid var(--border-main)',
                background: isActive
                  ? (isCollision ? 'rgba(239, 68, 68, 0.16)' : 'var(--accent-blue)22')
                  : 'var(--bg-surface-raised)',
                color: isCollision ? 'var(--color-error)' : 'var(--text-primary)',
                fontSize: 11,
                fontWeight: 700,
                padding: '6px 10px',
                cursor: 'pointer',
              }}
            >
              {filter.label} ({count})
            </button>
          );
        })}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ position: 'relative' }}>
            <Search size={13} style={{ position: 'absolute', left: 8, top: 8, color: 'var(--text-tertiary)' }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search fields..."
              style={{
                background: 'var(--bg-input)',
                border: '1px solid var(--border-main)',
                color: 'var(--text-primary)',
                borderRadius: 8,
                padding: '6px 10px 6px 28px',
                width: 240,
              }}
            />
          </div>
          <span title={deployReadiness.canDeploy ? 'Deploy validated mapping changes' : deployReadiness.reason}>
            <button
              type="button"
              disabled={!deployReadiness.canDeploy}
              onClick={deployMappings}
              style={{
                border: 'none',
                borderRadius: 8,
                padding: '8px 12px',
                background: 'var(--accent-blue)',
                color: '#fff',
                fontSize: 12,
                fontWeight: 700,
                cursor: deployReadiness.canDeploy ? 'pointer' : 'not-allowed',
                opacity: deployReadiness.canDeploy ? 1 : 0.55,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              {deployLoading ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
              Deploy Mapping
            </button>
          </span>
        </div>
      </div>

      {pageError && (
        <div style={{ border: '1px solid rgba(239, 68, 68, 0.45)', background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-error)', borderRadius: 8, padding: '10px 12px', marginBottom: 12, fontSize: 12 }}>
          {pageError}
        </div>
      )}

      <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1.2fr 150px', background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)', padding: '10px 12px', fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)' }}>
          <span>Source Field</span>
          <span>Target Field</span>
          <span>Status</span>
        </div>

        {loading ? (
          <CenteredNotice icon={<Loader2 size={14} className="animate-spin" />} text="Loading project mappings..." />
        ) : isDryRunLoading ? (
          <CenteredNotice icon={<Loader2 size={14} className="animate-spin" />} text="Running auto-map validation..." />
        ) : filteredRows.length === 0 ? (
          <CenteredNotice text={hasDryRunResult ? 'No rows match current filters.' : 'Run Auto-Map to generate validated field mappings.'} />
        ) : (
          filteredRows.map((row, index) => (
            <div key={row.id} style={{ display: 'grid', gridTemplateColumns: '1.2fr 1.2fr 150px', padding: '10px 12px', borderBottom: index < filteredRows.length - 1 ? '1px solid var(--border-main)' : 'none', background: row.status === 'collision' ? 'rgba(239, 68, 68, 0.07)' : 'transparent' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  <span style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.source_field}</span>
                  <TypeBadge type={row.source_type} />
                  <FieldKindBadge fieldType={row.field_type} />
                  {row.field_type === 'measure' ? (
                    <SourceTableBadge tables={row.measure_source_tables} hasIssue={row.status === 'collision'} />
                  ) : row.source_table_name ? (
                    <SourceTableBadge tables={[row.source_table_name]} hasIssue={row.status === 'collision'} />
                  ) : null}
                </div>
                {row.field_type === 'measure' && row.measure_expression ? (
                  <div
                    title={row.measure_expression}
                    style={{
                      fontFamily: 'monospace',
                      fontSize: 11,
                      color: 'var(--text-tertiary)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    Expr: {row.measure_expression}
                  </div>
                ) : null}
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <input
                    value={row.target_field || ''}
                    onChange={(e) => handleInlineTargetChange(row.id, e.target.value)}
                    placeholder="Enter target field"
                    style={{
                      width: '100%',
                      background: 'var(--bg-input)',
                      border: row.status === 'collision' ? '1px solid var(--color-error)' : '1px solid var(--border-main)',
                      color: 'var(--text-primary)',
                      borderRadius: 8,
                      padding: '7px 9px',
                      fontSize: 12,
                    }}
                  />
                  {row.target_type ? <TypeBadge type={row.target_type} /> : null}
                </div>
                {row.status === 'collision' && row.validation_message ? (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--color-error)' }}>
                    <AlertTriangle size={12} />
                    <span>{row.validation_message}</span>
                    {row.suggested_target_name ? (
                      <button
                        type="button"
                        onClick={() => applySuggestion(row.id)}
                        style={{
                          marginLeft: 4,
                          border: '1px solid rgba(239, 68, 68, 0.45)',
                          background: 'rgba(239, 68, 68, 0.12)',
                          color: 'var(--color-error)',
                          borderRadius: 999,
                          padding: '2px 8px',
                          fontSize: 10,
                          fontWeight: 700,
                          cursor: 'pointer',
                        }}
                      >
                        Use {row.suggested_target_name}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>

              <div style={{ display: 'flex', alignItems: 'center' }}>
                <StatusBadge
                  size="sm"
                  status={row.status === 'collision' ? 'error' : row.status === 'manual' ? 'warning' : row.status === 'auto' ? 'success' : 'draft'}
                  label={row.status === 'collision' ? 'Collision' : row.status === 'manual' ? 'Manual' : row.status === 'auto' ? 'Auto' : 'Unmapped'}
                />
                {row.isDirty ? <Check size={13} style={{ color: 'var(--color-success)', marginLeft: 8 }} /> : null}
              </div>
            </div>
          ))
        )}
      </div>

      <div style={{ marginTop: 12, borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', padding: '10px 12px', fontSize: 12, color: 'var(--text-secondary)' }}>
        {hasDryRunResult
          ? (deployReadiness.canDeploy
            ? `Ready to deploy ${dirtyRowsCount} edited mapping row(s).`
            : `Deploy blocked: ${deployReadiness.reason}`)
          : 'Auto-map has not been executed for this project session.'}
      </div>
    </div>
  );
}

function FlowCard({ label, model }) {
  const connector = getConnectorPresentation(model.type);
  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: '14px 16px', display: 'grid', gap: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: `${connector.accent}1f`, color: connector.accent, border: `1px solid ${connector.accent}33` }}>
          {connector.icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{model.name}</div>
          <div style={{ marginTop: 2, fontSize: 12, color: 'var(--text-tertiary)' }}>{connector.label} · {model.field_count} fields</div>
        </div>
      </div>
    </div>
  );
}

function CenteredNotice({ icon = null, text }) {
  return (
    <div style={{ padding: '38px 0', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-tertiary)', fontSize: 13 }}>
      {icon}
      <span>{text}</span>
    </div>
  );
}
