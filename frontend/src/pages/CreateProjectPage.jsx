/**
 * CreateProjectPage — 5-step wizard at /projects/new
 *
 * Step 1: Name + connectors
 * Step 2: Source + target config (with live discovery)
 * Step 3: Source browser (Fabric workspaces/models)
 * Step 4: Model mapping settings
 * Step 5: Finish and configure
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import './mapping-styles.css';
import {
  ArrowLeft, ArrowRight, Check, X, Loader2,
  ChevronDown, ChevronRight, CheckSquare, Square, RefreshCw,
  Table2, AlertTriangle, Play, Search,
  Cloud, Database, Snowflake, Zap, Save,
  Info, Settings, Settings2,
} from 'lucide-react';
import { api } from '../utils/api';
import { useHPSearch } from '../hooks/useHPSearch';
import SearchableSelect from '../components/common/SearchableSelect';
import SmartSearchBar, { matchesSmartQuery } from '../components/common/SmartSearchBar';
import SourceIcon from '../components/common/SourceIcon';
import StatusBadge from '../components/common/StatusBadge';
import { useWorkspace } from '../context/WorkspaceContext';
import Modal from '../components/common/Modal';
import { useLogs } from '../context/LogsContext';
import { useUIStore } from '../store/uiStore';
import { useProjectWizardStore } from '../store/projectWizardStore';
import DraftToast from '../components/common/DraftToast';
import DraftBanner from '../components/common/DraftBanner';
import ErrorBoundary from '../components/ErrorBoundary';
import DryRunMappingTable, { isBlockingRow as isDryRunBlockingRow } from '../components/DryRunMappingTable';
import FieldMappingEditor from '../components/FieldMappingEditor';
import { buildDryRunPayload } from '../utils/dryRunPayload';

const STEPS = [
  { id: 1, label: 'Basic Info' },
  { id: 2, label: 'Connector Config' },
  { id: 3, label: 'Select Sources' },
  { id: 4, label: 'Mapping Options' },
  { id: 5, label: 'Finish' },
];

const CONNECTOR_TYPES = [
  { value: 'pbix', label: 'Local PBIX File' },
  { value: 'fabric', label: 'Microsoft Fabric' },
  { value: 'snowflake', label: 'Snowflake' },
  { value: 'databricks', label: 'Databricks' },
];

const TARGET_CONNECTOR_TYPES = [
  { value: 'snowflake', label: 'Snowflake' },
  { value: 'fabric', label: 'Microsoft Fabric' },
  { value: 'databricks', label: 'Databricks' },
];

const INTERMEDIATE_FORMAT_TYPES = [
  { value: 'osi', label: 'OSI (Open Semantic Interchange)' },
  { value: 'sml', label: 'SML' },
];

const PBIX_SOURCE_MODES = [
  { value: 'TAG', label: 'Use Folder Tag' },
  { value: 'MANUAL', label: 'Upload PBIX File' },
];

const INPUT = {
  display: 'block', width: '100%',
  background: 'var(--bg-input)', border: '1px solid var(--border-main)',
  borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px',
  fontSize: 13, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

const LABEL = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 };

const MAPPING_FILTERS = [
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

function shortDeterministicHash(value) {
  let hash = 2166136261;
  const text = String(value || '');
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0').slice(0, 4);
}

function sanitizeMappingName(value) {
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

function resolveSourceTableName(column, tableSource = '') {
  const fromColumn = String(column?.source_table_name || column?.parent_table || '').trim();
  if (fromColumn) return fromColumn;
  const path = String(column?.source_path || column?.parent_source_path || '').trim();
  const direct = /^datasets\.([^.]+)/i.exec(path);
  if (direct?.[1]) return direct[1];
  return String(tableSource || '').trim();
}

function normalizeTargetStatus(item) {
  if (item?.collision_detected) return 'collision';
  const explicit = String(item?.status || '').toLowerCase();
  if (!String(item?.target || item?.target_name || item?.target_field || '').trim()) return 'unmapped';
  if (explicit === 'manual') return 'manual';
  return 'auto';
}

function buildSourceFingerprint(mapping, column) {
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

function applyHashDeduplication(mappings) {
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

function TypeBadge({ type }) {
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

function FieldKindBadge({ kind = 'Column', fieldType }) {
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

function normalizeStatus(item) {
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

function isBlockingRow(row) {
  const status = String(row?.status || '').toLowerCase();
  if (status === 'collision' || status === 'unmapped') return true;

  const validationStatus = String(row?.validation_status || '').toLowerCase();
  if (validationStatus === 'invalid' || validationStatus === 'collision') return true;

  const validationCode = String(row?.validation_code || '').toUpperCase();
  return Boolean(validationCode && validationCode !== 'OK');
}

function parseDatasetFromPath(pathValue) {
  const path = String(pathValue || '').trim();
  if (!path) return '';
  const direct = /^datasets\.([^.]+)$/i.exec(path);
  if (direct?.[1]) return String(direct[1]).trim();
  const nested = /^datasets\.([^.]+)\./i.exec(path);
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

  return String(parentTable || row?.parent_table || row?.source_table_name || '').trim();
}

function resolveMeasureExpression(row) {
  return String(row?.source_expression || row?.measure_expression || row?.expression || '').trim();
}

function normalizeRows(data) {
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

function countResponseFields(data) {
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

export default function CreateProjectPage({ editMode = false, initialData = null, onSaveConfig = null }) {
  const navigate = useNavigate();
  const location = useLocation();
  const { addLog } = useLogs();
  const sidebarCollapsed = useUIStore(s => s.sidebarCollapsed);
  const setWizardState = useProjectWizardStore(state => state.setWizardState);
  const clearWizardState = useProjectWizardStore(state => state.clearWizardState);
  const hasRestoredDraft = useProjectWizardStore(state => state.hasRestoredDraft);
  const setHasRestoredDraft = useProjectWizardStore(state => state.setHasRestoredDraft);
  const hasMeaningfulData = useProjectWizardStore(state => state.hasMeaningfulData);

  // Destructure wizard state for local usage
  const {
    name, description, sourceConnector, targetConnectors: targetConnectorsRaw,
    intermediateFormat, tags: tagsRaw, tagInput,
    fabricAccountId, selectedConnectionId, snowflakeAccountId, databricksAccountId,
    fabricWorkspaceId, snowflakeDatabase, snowflakeSchema,
    targetDatabase, targetSchema, targetAccount, targetWarehouse,
    domainHint, modelQueryRegex, pbixSourceMode,
    selectedLocalFolderId, selectedPbixFilePath,
    expandedWs, selectedModels: selectedModelsRaw, selectedModelNameByKey,
    selectedDatabricksTables: selectedDatabricksTablesRaw, databricksQuery,
    autoRelationships, generateDescriptions,
    currentStepIndex: step,
  } = useProjectWizardStore(state => state.wizard);
  const {
    workspaces: availableWorkspaces,
    activeWorkspaceId,
    activeWorkspace,
    isLoading: workspacesLoading,
  } = useWorkspace();

  const [showStep1Validation, setShowStep1Validation] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState('');
  const [runWarning, setRunWarning] = useState('');

  const refreshLocalFolders = useCallback(async () => {
    setLocalFoldersLoading(true);
    try {
      const data = await api.listLocalFolders();
      setLocalFolders(Array.isArray(data) ? data : []);
    } catch {
      setLocalFolders([]);
    } finally {
      setLocalFoldersLoading(false);
    }
  }, []);

  // On CREATE mode mount — always wipe stale state so every new project starts blank.
  useEffect(() => {
    if (!editMode) {
      clearWizardState();
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Detection logic for resumed draft on mount (only relevant in edit mode)
  useEffect(() => {
    if (sourceConnector === 'pbix') {
      refreshLocalFolders();
    }
  }, [refreshLocalFolders, sourceConnector, step]);

  const setStep = useCallback((nextStep) => {
    const val = typeof nextStep === 'function' ? nextStep(step) : nextStep;
    setWizardState({ currentStepIndex: val });
  }, [step, setWizardState]);

  // Step 1
  const configMode = 'form';
  const targetConnectors = useMemo(() => new Set(targetConnectorsRaw || []), [targetConnectorsRaw]);
  const tags = useMemo(() => new Set(tagsRaw || []), [tagsRaw]);

  const setName = useCallback((nextName) => {
    setWizardState({ name: String(nextName ?? '') });
  }, [setWizardState]);
  const setDescription = useCallback((nextDescription) => {
    setWizardState({ description: String(nextDescription ?? '') });
  }, [setWizardState]);
  const setSourceConnector = useCallback((nextConnector) => {
    setWizardState({ sourceConnector: String(nextConnector ?? '') });
  }, [setWizardState]);
  const setIntermediateFormat = useCallback((nextFormat) => {
    setWizardState({ intermediateFormat: String(nextFormat ?? 'osi') });
  }, [setWizardState]);
  const setTagInput = useCallback((nextTagInput) => {
    setWizardState({ tagInput: String(nextTagInput ?? '') });
  }, [setWizardState]);
  const setTargetConnectors = useCallback((nextValue) => {
    const prev = new Set(useProjectWizardStore.getState()?.wizard?.targetConnectors || []);
    const resolved = typeof nextValue === 'function' ? nextValue(prev) : nextValue;
    setWizardState({ targetConnectors: Array.from(resolved || []) });
  }, [setWizardState]);
  const setTags = useCallback((nextValue) => {
    const prev = new Set(useProjectWizardStore.getState()?.wizard?.tags || []);
    const resolved = typeof nextValue === 'function' ? nextValue(prev) : nextValue;
    setWizardState({ tags: Array.from(resolved || []) });
  }, [setWizardState]);

  // Step 2
  const selectedModels = useMemo(() => new Set(selectedModelsRaw || []), [selectedModelsRaw]);
  const selectedDatabricksTables = useMemo(() => new Set(selectedDatabricksTablesRaw || []), [selectedDatabricksTablesRaw]);

  // Setters bound to store
  const setFabricAccountId = (val) => setWizardState({ fabricAccountId: val });
  const setSelectedConnectionId = (val) => setWizardState({ selectedConnectionId: val });
  const setSnowflakeAccountId = (val) => setWizardState({ snowflakeAccountId: val });
  const setDatabricksAccountId = (val) => setWizardState({ databricksAccountId: val });
  const setFabricWorkspaceId = (val) => setWizardState({ fabricWorkspaceId: val });
  const setSnowflakeDatabase = (val) => setWizardState({ snowflakeDatabase: val });
  const setSnowflakeSchema = (val) => setWizardState({ snowflakeSchema: val });
  const setTargetDatabase = (val) => setWizardState({ targetDatabase: val });
  const setTargetSchema = (val) => setWizardState({ targetSchema: val });
  const setTargetAccount = (val) => setWizardState({ targetAccount: val });
  const setTargetWarehouse = (val) => setWizardState({ targetWarehouse: val });
  const setDomainHint = (val) => setWizardState({ domainHint: val });
  const setModelQueryRegex = (val) => setWizardState({ modelQueryRegex: !!val });
  const setPbixSourceMode = (val) => setWizardState({ pbixSourceMode: val });

  const [pbixFile, setPbixFile] = useState(null);
  const [pbixUploadPath, setPbixUploadPath] = useState('');
  const [pbixUploading, setPbixUploading] = useState(false);
  const [fabricAccounts, setFabricAccounts] = useState([]);
  const [snowflakeAccounts, setSnowflakeAccounts] = useState([]);
  const [databricksAccounts, setDatabricksAccounts] = useState([]);

  const [localFolders, setLocalFolders] = useState([]);
  const [localFoldersLoading, setLocalFoldersLoading] = useState(false);
  const [pbixFiles, setPbixFiles] = useState([]);
  const [pbixFilesLoading, setPbixFilesLoading] = useState(false);
  const [pbixFilesError, setPbixFilesError] = useState('');

  const setSelectedLocalFolderId = (val) => setWizardState({ selectedLocalFolderId: val });
  const setSelectedPbixFilePath = (val) => setWizardState({ selectedPbixFilePath: val });

  const [syncJob, setSyncJob] = useState(null);
  const [syncStarting, setSyncStarting] = useState(false);
  const [syncError, setSyncError] = useState('');
  const [syncErrorOpen, setSyncErrorOpen] = useState(false);
  const [allWorkspacesFromApi, setAllWorkspacesFromApi] = useState([]);

  // Step 3
  const [workspaces, setWorkspaces] = useState([]);
  const [wsLoading, setWsLoading] = useState(false);
  const [wsModels, setWsModels] = useState({}); // wsid → [{id, name}]
  const [databricksObjects, setDatabricksObjects] = useState([]); // [{catalog, schema, table}]
  const [databricksLoading, setDatabricksLoading] = useState(false);
  const [databricksQueryRegex, setDatabricksQueryRegex] = useState(false);

  const setExpandedWs = (val) => setWizardState({ expandedWs: typeof val === 'function' ? val(expandedWs) : val });
  const setSelectedModels = (val) => {
    const prev = new Set(selectedModelsRaw || []);
    const next = typeof val === 'function' ? val(prev) : val;
    setWizardState({ selectedModels: Array.from(next || []) });
  };
  const setSelectedModelNameByKey = (val) => setWizardState({
    selectedModelNameByKey: typeof val === 'function' ? val(selectedModelNameByKey) : val
  });
  const setSelectedDatabricksTables = (val) => {
    const prev = new Set(selectedDatabricksTablesRaw || []);
    const next = typeof val === 'function' ? val(prev) : val;
    setWizardState({ selectedDatabricksTables: Array.from(next || []) });
  };
  const setDatabricksQuery = (val) => setWizardState({ databricksQuery: val });

  // Step 4
  const setAutoRelationships = (val) => setWizardState({ autoRelationships: !!val });
  const setGenerateDescriptions = (val) => setWizardState({ generateDescriptions: !!val });
  const [detectedMappings, setDetectedMappings] = useState([]);
  const [detectedEntityMappings, setDetectedEntityMappings] = useState([]);
  const [mappingLoading, setMappingLoading] = useState(false);
  const [mappingError, setMappingError] = useState('');
  const [mappingReadyToProceed, setMappingReadyToProceed] = useState(false);
  const [mappingDryRunStatus, setMappingDryRunStatus] = useState('idle');
  const [mappingDryRunSignature, setMappingDryRunSignature] = useState('');
  const [mappingDryRunError, setMappingDryRunError] = useState('');
  const [unmappedAcknowledged, setUnmappedAcknowledged] = useState(false);

  // Step 4 — dry-run → edit → deploy flow
  const [dryRunData, setDryRunData] = useState(null);
  const [editingRow, setEditingRow] = useState(null);
  const [isSavingEdit, setIsSavingEdit] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [deployError, setDeployError] = useState('');

  // Edit mode mapping options
  const [mappingMode, setMappingMode] = useState('saved');

  // Step 5
  const [createReverseProject, setCreateReverseProject] = useState(false);
  const [createdProject, setCreatedProject] = useState(null);

  const initialSelectedModels = useMemo(() => {
    if (!editMode || !initialData) return new Set();
    const models = initialData.models || initialData.model || [];
    return new Set(Array.isArray(models) ? models : [models]);
  }, [editMode, initialData]);

  // --- Hydrate form when editMode is active and initialData changes ---
  useEffect(() => {
    if (editMode && initialData) {
      if (initialData.name != null) setName(initialData.name);
      if (initialData.description != null) setDescription(initialData.description);
      if (initialData.source_type != null) setSourceConnector(initialData.source_type);
      if (initialData.target_type != null) setTargetConnectors(new Set([initialData.target_type]));
      if (initialData.output_format != null) setIntermediateFormat(initialData.output_format);
      if (Array.isArray(initialData.tags)) setTags(new Set(initialData.tags));
      if (initialData.workspace_id != null) setFabricWorkspaceId(initialData.workspace_id);
      if (initialData.identity_id != null) setFabricAccountId(initialData.identity_id);
      if (initialData.database != null) setSnowflakeDatabase(initialData.database);
      if (initialData.schema != null) setSnowflakeSchema(initialData.schema);
      if (initialData.target_database != null) setTargetDatabase(initialData.target_database);
      if (initialData.target_schema != null) setTargetSchema(initialData.target_schema);
      if (initialData.target_identity_id != null) setFabricAccountId(initialData.target_identity_id);
      if (initialData.target_workspace_id != null) setFabricWorkspaceId(initialData.target_workspace_id);

      if (initialData.source_type === 'fabric') {
        const models = initialData.models || initialData.model || [];
        const modelNames = Array.isArray(models) ? models : [models];
        const nextSelectedModels = new Set(modelNames);
        const nextModelNameByKey = {};
        modelNames.forEach(n => { nextModelNameByKey[n] = n; });
        setSelectedModels(nextSelectedModels);
        setSelectedModelNameByKey(nextModelNameByKey);
      }

      if (mappingMode === 'saved' && Array.isArray(initialData.mappings) && initialData.mappings.length > 0) {
        setDetectedMappings(initialData.mappings);
        setMappingDryRunStatus('success');
      } else if (mappingMode === 'auto') {
        setDetectedMappings([]);
        setMappingDryRunStatus('idle');
      }
    }
  }, [editMode, initialData, mappingMode]);






  /* ─── HP search for model browser ─── */
  const allModels = useMemo(() =>
    Object.entries(wsModels).flatMap(([wsid, models]) =>
      models.map(m => ({ ...m, wsid, _id: `${wsid}::${m.id}` }))
    ),
    [wsModels]
  );

  const searchFields = useMemo(() => ['name', 'description', 'wsid'], []);
  const searchOptions = useMemo(() => ({ idField: '_id' }), []);

  const { query: modelQuery, setQuery: setModelQuery } = useHPSearch(
    allModels, searchFields, searchOptions
  );

  const liveFabricWorkspaces = useMemo(() => {
    // If we have accounts at all, and source is Fabric, we should prioritize
    // the list fetched explicitly for the selected account in Step 2.
    // Fallback to availableWorkspaces (global default) only if we haven't
    // fetched the account-specific list yet.
    const baseList = allWorkspacesFromApi.length > 0
      ? allWorkspacesFromApi
      : (fabricAccountId ? [] : availableWorkspaces);

    const normalized = (baseList || [])
      .filter(Boolean)
      .map((ws) => ({
        ...ws,
        id: ws?.id || ws?.workspace_id || '',
        name: ws?.name || ws?.displayName || ws?.workspace_name || ws?.workspace_id || ws?.id || '',
        displayName: ws?.displayName || ws?.name || ws?.workspace_name || '',
      }))
      .filter((ws) => ws.id);

    const deduped = [];
    const seen = new Set();
    for (const ws of normalized) {
      if (seen.has(ws.id)) continue;
      seen.add(ws.id);
      deduped.push(ws);
    }
    return deduped;
  }, [availableWorkspaces, allWorkspacesFromApi, fabricAccountId]);

  const selectedWorkspace = useMemo(() => {
    if (!fabricWorkspaceId) return null;
    return liveFabricWorkspaces.find(ws => ws.id === fabricWorkspaceId) || null;
  }, [liveFabricWorkspaces, fabricWorkspaceId]);

  const selectedModelNames = [...selectedModels]
    .map(modelKey => selectedModelNameByKey[modelKey])
    .filter(Boolean);

  const [isRefreshingWorkspaces, setIsRefreshingWorkspaces] = useState(false);

  const selectedLocalFolder = useMemo(() => (
    localFolders.find(folder => String(folder.id) === String(selectedLocalFolderId)) || null
  ), [localFolders, selectedLocalFolderId]);

  const selectedLocalFolderTag = selectedLocalFolder?.tag_name || '';
  const selectedPbixFile = useMemo(() => {
    if (!selectedPbixFilePath) return null;
    return pbixFiles.find(file => file.path === selectedPbixFilePath) || null;
  }, [pbixFiles, selectedPbixFilePath]);
  const resolvedPbixPath = pbixSourceMode === 'TAG' ? selectedPbixFilePath : pbixUploadPath;

  const currentMappingSignature = useMemo(() => JSON.stringify({
    sourceConnector,
    targetConnectors: [...targetConnectors].sort(),
    intermediateFormat,
    fabricAccountId,
    selectedConnectionId,
    fabricWorkspaceId,
    snowflakeAccountId,
    snowflakeDatabase,
    snowflakeSchema,
    targetDatabase,
    targetSchema,
    targetAccount,
    targetWarehouse,
    databricksAccountId,
    selectedModels: [...selectedModels].sort(),
    selectedModelNames: [...selectedModelNames].sort(),
    selectedDatabricksTables: [...selectedDatabricksTables].sort(),
    pbixPath: resolvedPbixPath,
  }), [
    databricksAccountId, fabricAccountId, fabricWorkspaceId, intermediateFormat,
    resolvedPbixPath, selectedConnectionId, selectedDatabricksTables, selectedModelNames,
    selectedModels, snowflakeAccountId, snowflakeDatabase, snowflakeSchema,
    sourceConnector, targetAccount, targetConnectors, targetDatabase, targetSchema,
    targetWarehouse,
  ]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => {
    if (!mappingDryRunSignature || mappingDryRunSignature === currentMappingSignature) return;
    setMappingDryRunStatus('idle');
    setMappingDryRunError('');
    setMappingReadyToProceed(false);
    setUnmappedAcknowledged(false);
    setDetectedMappings([]);
    setDetectedEntityMappings([]);
  }, [currentMappingSignature, mappingDryRunSignature]);


  useEffect(() => {
    if (pbixSourceMode !== 'TAG' || sourceConnector !== 'pbix' || !selectedLocalFolderTag) {
      setPbixFiles([]);
      setPbixFilesError('');
      setSelectedPbixFilePath('');
      return;
    }

    if (step !== 3) {
      return;
    }

    let active = true;
    setPbixFilesLoading(true);
    setPbixFilesError('');
    api.getLocalFolderFiles(selectedLocalFolderTag)
      .then((data) => {
        if (!active) return;
        const discoveredFiles = Array.isArray(data?.files) ? data.files : [];
        setPbixFiles(discoveredFiles);

        if (!discoveredFiles.length) {
          setSelectedPbixFilePath('');
          return;
        }

        // Auto-select a discovered file so pbix_path is populated without extra clicks.
        setSelectedPbixFilePath((currentPath) => {
          if (currentPath && discoveredFiles.some(file => file.path === currentPath)) {
            return currentPath;
          }
          return discoveredFiles[0]?.path || '';
        });
      })
      .catch((err) => {
        if (!active) return;
        setPbixFiles([]);
        setSelectedPbixFilePath('');
        setPbixFilesError(err?.message || 'Failed to discover PBIX files for the selected tag.');
      })
      .finally(() => {
        if (active) setPbixFilesLoading(false);
      });

    return () => {
      active = false;
    };
  }, [pbixSourceMode, selectedLocalFolderTag, sourceConnector, step]);

  const fetchFabricWorkspaces = useCallback(async (accountId) => {
    if (!accountId) {
      setAllWorkspacesFromApi([]);
      setWorkspaces([]);
      return;
    }
    setIsRefreshingWorkspaces(true);
    try {
      const listData = await api.fabricListWorkspaces(accountId);

      const discovered = Array.isArray(listData?.workspaces) ? listData.workspaces : [];

      const apiWorkspaces = discovered.map(ws => ({
        id: ws.id || ws.workspace_id,
        name: ws.name || ws.displayName || ws.workspace_name || ws.id || ws.workspace_id,
        displayName: ws.name || ws.displayName || ws.workspace_name,
        type: ws.type,
      })).filter(ws => ws.id);

      const deduped = [];
      const seen = new Set();
      for (const ws of apiWorkspaces) {
        if (seen.has(ws.id)) continue;
        seen.add(ws.id);
        deduped.push(ws);
      }

      console.log('[SemaBridge] Resolved workspace list:', deduped);
      setAllWorkspacesFromApi(deduped);
    } catch {
      setAllWorkspacesFromApi([]);
    } finally {
      setIsRefreshingWorkspaces(false);
    }
  }, []);

  // Fetch Fabric accounts when step 2 opens
  useEffect(() => {
    const needsFabricWorkspaceConfig = sourceConnector === 'fabric' || targetConnectors.has('fabric');
    if (step === 2 && needsFabricWorkspaceConfig) {
      const fetchAccounts = async () => {
        try {
          const res = await api.getAccounts('FABRIC');
          const list = Array.isArray(res) ? res : (res?.accounts || []);
          setFabricAccounts(list);
          if (list.length === 0) {
            setSelectedConnectionId('');
            setFabricAccountId('');
            return;
          }

          const hasSelection = list.some(acc => String(acc?.id || '') === String(selectedConnectionId || ''));
          const nextConnectionId = hasSelection
            ? selectedConnectionId
            : String(list[0]?.id || '');

          if (nextConnectionId) {
            setSelectedConnectionId(nextConnectionId);
            setFabricAccountId(nextConnectionId);
          }
        } catch (e) {
          console.warn("[SemaBridge] Failed to fetch Fabric Accounts", e);
        }
      };
      fetchAccounts();
    }

    // Fetch Snowflake accounts when step 2 opens with a Snowflake source
    const needsSnowflakeAccounts = sourceConnector === 'snowflake' || targetConnectors.has('snowflake');
    if (step === 2 && needsSnowflakeAccounts) {
      const fetchSnowflakeAccounts = async () => {
        try {
          const res = await api.getAccounts('SNOWFLAKE');
          const list = Array.isArray(res) ? res : (res?.accounts || []);
          setSnowflakeAccounts(list);
          if (list.length > 0 && !snowflakeAccountId) {
            setSnowflakeAccountId(String(list[0]?.id || ''));
          }
        } catch (e) {
          console.warn('[SemaBridge] Failed to fetch Snowflake Accounts', e);
        }
      };
      fetchSnowflakeAccounts();
    }

    // Fetch Databricks accounts when step 2 opens with a Databricks target
    const needsDatabricksAccounts = targetConnectors.has('databricks') || sourceConnector === 'databricks';
    if (step === 2 && needsDatabricksAccounts) {
      const fetchDatabricksAccounts = async () => {
        try {
          const res = await api.getAccounts('DATABRICKS');
          const list = Array.isArray(res) ? res : (res?.accounts || []);
          setDatabricksAccounts(list);
          if (list.length > 0 && !databricksAccountId) {
            setDatabricksAccountId(String(list[0]?.id || ''));
          }
        } catch (e) {
          console.warn('[SemaBridge] Failed to fetch Databricks Accounts', e);
        }
      };
      fetchDatabricksAccounts();
    }
  }, [step, sourceConnector, targetConnectors, selectedConnectionId, fetchFabricWorkspaces]);

  // Account switch is the source of truth for workspace discovery.
  useEffect(() => {
    const needsFabricWorkspaceConfig = sourceConnector === 'fabric' || targetConnectors.has('fabric');
    if (step !== 2 || !needsFabricWorkspaceConfig) return;

    setAllWorkspacesFromApi([]);
    setWorkspaces([]);
    setFabricWorkspaceId('');

    if (!selectedConnectionId) return;

    setFabricAccountId(selectedConnectionId);
    fetchFabricWorkspaces(selectedConnectionId);
  }, [step, sourceConnector, targetConnectors, selectedConnectionId, fetchFabricWorkspaces]);

  // Ghost-purge: if the current selection no longer exists in the live list, force-clear it
  // so the auto-select above can immediately re-run and pick the correct workspace.
  // This eliminates the "Primary Workspace" zombie that was persisted in localStorage.
  useEffect(() => {
    if (!fabricWorkspaceId) return;
    const liveList = liveFabricWorkspaces;
    if (liveList.length === 0) return; // don't clear before we have data
    const stillExists = liveList.some(ws => ws.id === fabricWorkspaceId);
    if (!stillExists) {
      console.warn('[SemaBridge] Ghost workspace detected — force-clearing:', fabricWorkspaceId);
      setFabricWorkspaceId('');
      localStorage.removeItem('semabridge_workspace_id');
    }
  }, [fabricWorkspaceId, availableWorkspaces, allWorkspacesFromApi]);


  useEffect(() => {
    if (!sourceConnector) return;
    setTargetConnectors(prev => {
      if (!prev.has(sourceConnector)) return prev;
      const next = new Set(prev);
      next.delete(sourceConnector);
      return next;
    });
  }, [sourceConnector]);

  useEffect(() => {
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
    setExpandedWs({});
    setWsModels({});
    setModelQuery('');
  }, [fabricWorkspaceId, sourceConnector, setModelQuery]);


  // Defensive: auto-select first available workspace if missing after loading
  useEffect(() => {
    if (step === 3 && sourceConnector === 'fabric' && !fabricWorkspaceId && !wsLoading) {
      const liveList = liveFabricWorkspaces;
      if (liveList.length > 0) {
        setFabricWorkspaceId(liveList[0].id);
      }
    }
  }, [step, sourceConnector, fabricWorkspaceId, wsLoading, availableWorkspaces, allWorkspacesFromApi]);

  /* ─── Load selected Fabric workspace models on step 3 ─── */
  useEffect(() => {
    if (step !== 3) return;

    if (sourceConnector === 'fabric') {
      if (!fabricWorkspaceId) {
        setWorkspaces([]);
        return;
      }

      const workspace = selectedWorkspace || { id: fabricWorkspaceId, name: fabricWorkspaceId };
      setWorkspaces([workspace]);
      setExpandedWs(prev => ({ ...prev, [fabricWorkspaceId]: true }));
      setWsLoading(true);
      console.log('[SemaBridge] Discovering Fabric models for workspaceId:', fabricWorkspaceId);
      api.discoverFabricModels(fabricWorkspaceId, selectedConnectionId)
        .then(data => {
          if (!Array.isArray(data) || data.length === 0) {
            setRunWarning('No semantic models found for this workspace. Check Fabric permissions or workspace contents.');
          } else {
            setRunWarning('');
          }
          setWsModels({ [fabricWorkspaceId]: data ?? [] });
          setWsLoading(false); // Set loading to false immediately after 200 OK
        })
        .catch((err) => {
          setRunWarning('Failed to load semantic models: ' + (err?.message || 'Unknown error'));
          setWsModels({ [fabricWorkspaceId]: [] });
          setWsLoading(false); // Also set loading to false on error
        });
      return;
    }

    if (sourceConnector === 'snowflake') {
      const rootId = 'snowflake';
      setWorkspaces([{ id: rootId, name: 'Snowflake' }]);
      setExpandedWs(prev => ({ ...prev, [rootId]: true }));
      setWsLoading(true);
      api.discoverSnowflakeModels()
        .then(data => {
          const normalized = (data ?? []).map((m, idx) => {
            const fallbackName = m?.name || m?.displayName || m?.id || `model_${idx + 1}`;
            const fallbackId = m?.id || m?.name || m?.displayName || `snowflake_model_${idx + 1}`;
            return {
              ...m,
              id: fallbackId,
              name: fallbackName,
            };
          });
          setWsModels({ [rootId]: normalized });
        })
        .catch(() => setWsModels({ [rootId]: [] }))
        .finally(() => setWsLoading(false));
      return;
    }

    setWorkspaces([]);
  }, [step, sourceConnector, fabricWorkspaceId, selectedConnectionId, selectedWorkspace, liveFabricWorkspaces]);

  const loadWsModels = useCallback(async (wsid) => {
    if (wsModels[wsid]) return;
    try {
      const models = await api.discoverFabricModels(wsid, selectedConnectionId);
      setWsModels(prev => ({ ...prev, [wsid]: models ?? [] }));
    } catch {
      setWsModels(prev => ({ ...prev, [wsid]: [] }));
    }
  }, [wsModels, selectedConnectionId]);

  const toggleWorkspace = (wsid) => {
    const next = { ...expandedWs, [wsid]: !expandedWs[wsid] };
    setExpandedWs(next);
    if (next[wsid]) loadWsModels(wsid);
  };

  const clearSelectedModels = () => {
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
  };

  const toggleModel = (modelKey, modelName = '') => {
    setSelectedModels(prev => {
      const s = new Set(prev);
      s.has(modelKey) ? s.delete(modelKey) : s.add(modelKey);
      return s;
    });
    setSelectedModelNameByKey(prev => {
      const next = { ...prev };
      if (modelKey in next) {
        delete next[modelKey];
      } else if (modelName) {
        next[modelKey] = modelName;
      }
      return next;
    });
  };

  /* ─── Submit ─── */
  const handleFinish = async () => {
    setCreateError('');
    setRunWarning('');

    if ((sourceConnector === 'fabric' || sourceConnector === 'snowflake') && selectedModels.size > 0 && selectedModelNames.length === 0) {
      setCreateError('Selected models could not be resolved. Please reselect the model(s) and try again.');
      return;
    }

    if (sourceConnector === 'pbix' && !resolvedPbixPath) {
      setCreateError(pbixSourceMode === 'TAG'
        ? 'Select a PBIX file from the tagged folder before finishing.'
        : 'Upload a .pbix file before finishing.');
      return;
    }

    setSaving(true);
    try {
      const source = buildSourceConfig();
      const targets = buildTargetConfigs();

      // Get stored relationships
      let relationships = [];
      try {
        const storedRels = sessionStorage.getItem('detectedRelationships');
        if (storedRels) {
          relationships = JSON.parse(storedRels);
          sessionStorage.removeItem('detectedRelationships'); // Clean up after use
        }
      } catch {
        relationships = [];
      }

      if (editMode && onSaveConfig) {
        const configYaml = buildConfigYaml(source, targets);
        await onSaveConfig(configYaml, {
          name: name.trim(),
          description: description.trim(),
          tags: Array.from(tags),
        });
        // Navigate directly to config/sync page after saving edits
        if (initialData?.id) {
          navigate(`/projects/${initialData.id}/config`);
        }
        setSaving(false);
        return;
      }


      const payload = {
        name: name.trim(),
        description: description.trim() || undefined,
        source,
        targets,
        tags: [...tags],
        mappings: detectedMappings.length > 0 ? detectedMappings : undefined,
        relationships: relationships.length > 0 ? relationships : undefined,
        mapping_options: {
          auto_detect_relationships: autoRelationships,
          generate_descriptions: generateDescriptions,
        },
        preferred_interface: 'ui',
        config_yaml: buildConfigYaml(source, targets),
      };

      // Validation Check: Ensure selectedAccountId and selectedWorkspaceId match
      if (sourceConnector === 'fabric' && selectedWorkspace) {
        payload.selectedWorkspaceId = selectedWorkspace.id || fabricWorkspaceId;
        payload.selectedAccountId = selectedWorkspace.account_id || selectedWorkspace.accountId;
        payload.account_id = selectedWorkspace.account_id || selectedWorkspace.accountId;
      }

      let project = await api.createProject(payload);

      // Compatibility fallback: some backend modes return create responses
      // without a concrete project id. Resolve by matching latest project name.
      if (!project?.id && !project?.project_id) {
        try {
          const allProjects = await api.listProjects();
          const candidates = (allProjects || [])
            .filter(p => String(p?.name || '').trim() === payload.name)
            .sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
          if (candidates.length > 0) {
            project = candidates[0];
          }
        } catch {
          // Keep original project object if fallback discovery fails.
        }
      }

      setCreatedProject(project);
      const projectId = project?.id || project?.project_id;

      if (projectId && detectedEntityMappings.length > 0) {
        try {
          await api.deleteMappings(projectId);
          for (const mapping of detectedEntityMappings) {
            const payloadMapping = {
              ...mapping,
              project_id: projectId,
            };
            await api.updateMapping(String(mapping.id), payloadMapping);
          }
        } catch (mappingPersistErr) {
          const persistMsg = mappingPersistErr?.message || 'Mappings could not be fully persisted.';
          setRunWarning(prev => {
            const base = prev ? `${prev} ` : '';
            return `${base}${persistMsg}`.trim();
          });
        }
      }

      // Persist any manually-edited dry-run mappings so the sync run picks them up.
      // The config YAML already contains mappings_overrides built from detectedMappings
      // (see buildConfigYaml above), so the sync pipeline will pick them up automatically.
      // We skip the per-row API calls here to avoid 500s from mismatched mapping IDs.

      if (sourceConnector === 'pbix' && projectId && pbixFile) {
        try {
          const uploadResp = await api.uploadProjectPbix(projectId, pbixFile);
          const persistedPath = String(uploadResp?.path || '').trim();
          if (persistedPath) {
            setPbixUploadPath(persistedPath);
            const updatedSource = { ...source, pbix_path: persistedPath, pbix_file_path: persistedPath };
            const updatedYaml = buildConfigYaml(updatedSource, targets);
            await api.saveProjectConfig(projectId, updatedYaml);
            setCreatedProject(prev => ({ ...(prev || {}), pbix_file_path: persistedPath }));
          }
        } catch (uploadErr) {
          const uploadMsg = uploadErr?.message || 'PBIX file uploaded but could not be linked to project.';
          setRunWarning(prev => {
            const base = prev ? `${prev} ` : '';
            return `${base}${uploadMsg}`.trim();
          });
        }
      }

      if (createReverseProject && targets.length > 0) {
        const reverseSource = { ...targets[0] };
        const reverseTargets = [{ type: source.type }];
        const reverseName = `${payload.name}_${targets[0].type}_to_${source.type}`;

        try {
          let reverseProject = await api.createProject({
            ...payload,
            name: reverseName,
            source: reverseSource,
            targets: reverseTargets,
            target: reverseTargets[0],
            config_yaml: buildConfigYaml(reverseSource, reverseTargets, { projectName: reverseName }),
          });

          if (!reverseProject?.id && !reverseProject?.project_id) {
            const allProjects = await api.listProjects();
            const candidates = (allProjects || [])
              .filter(p => String(p?.name || '').trim() === reverseName)
              .sort((a, b) => String(b?.created_at || '').localeCompare(String(a?.created_at || '')));
            if (candidates.length > 0) reverseProject = candidates[0];
          }
        } catch (reverseErr) {
          const reverseMsg = reverseErr?.message || 'Reverse project could not be created.';
          setRunWarning(prev => {
            const base = prev ? `${prev} ` : '';
            return `${base}Primary project was created. Reverse project warning: ${reverseMsg}`.trim();
          });
        }
      }

      if (projectId) {
        navigate(`/projects/${projectId}/config`);
      }

    } catch (err) {
      setCreatedProject(null);
      setCreateError(err?.message || 'Create project failed.');
      console.error('Create project failed:', err);
    } finally {
      setSaving(false);
    }
  };

  const buildSourceConfig = () => {
    const source = { type: sourceConnector };

    if (sourceConnector === 'fabric') {
      if (fabricAccountId) source.identity_id = fabricAccountId;
      if (fabricWorkspaceId) source.workspace_id = fabricWorkspaceId;
      if (selectedWorkspace?.name) source.workspace = selectedWorkspace.name;
      if (selectedModelNames.length > 0) {
        source.models = selectedModelNames;
      }
    }

    if (sourceConnector === 'snowflake') {
      if (snowflakeAccountId) source.identity_id = snowflakeAccountId;
      if (snowflakeDatabase.trim()) source.database = snowflakeDatabase.trim();
      if (snowflakeSchema.trim()) source.schema = snowflakeSchema.trim();
      if (selectedModelNames.length > 0) {
        source.models = selectedModelNames;
      }
    }

    if (sourceConnector === 'pbix') {
      if (pbixSourceMode === 'TAG') {
        if (selectedLocalFolderId) source.local_folder_id = selectedLocalFolderId;
        if (selectedLocalFolderTag) source.local_folder_tag = selectedLocalFolderTag;
        if (selectedPbixFile?.name) source.file_name = selectedPbixFile.name;
      } else if (pbixFile?.name) {
        source.file_name = pbixFile.name;
      }

      if (resolvedPbixPath) {
        source.pbix_path = resolvedPbixPath;
        source.pbix_file_path = resolvedPbixPath;
      }
    }

    return source;
  };

  const buildTargetConfigs = () => {
    return Array.from(targetConnectors).map(connector => {
      const target = { type: connector };

      if (connector === 'snowflake') {
        if (targetAccount.trim()) target.account = targetAccount.trim();
        if (targetWarehouse.trim()) target.warehouse = targetWarehouse.trim();
        if (targetDatabase.trim()) target.database = targetDatabase.trim();
        if (targetSchema.trim()) target.schema = targetSchema.trim();
      }

      if (connector === 'fabric') {
        if (fabricAccountId) target.identity_id = fabricAccountId;
        if (fabricWorkspaceId) target.workspace_id = fabricWorkspaceId;
        if (selectedWorkspace?.name) target.workspace = selectedWorkspace.name;
      }

      if (connector === 'databricks') {
        if (databricksAccountId) target.identity_id = databricksAccountId;
      }

      if (connector === 'snowflake') {
        if (snowflakeAccountId) target.identity_id = snowflakeAccountId;
      }

      return target;
    });
  };

  const buildConfigYaml = (source, targets, overrides = {}) => {
    const projectName = String(overrides.projectName || name.trim() || 'Untitled Project').trim();
    const projectDescription = overrides.description ?? description.trim();
    const lines = [`project_name: "${escapeYamlString(projectName)}"`];
    if (projectDescription) lines.push(`description: "${escapeYamlString(projectDescription)}"`);

    lines.push('source:');
    lines.push(`  type: ${source.type}`);
    if (source.identity_id) lines.push(`  identity_id: "${escapeYamlString(source.identity_id)}"`);
    if (source.workspace_id) lines.push(`  workspace_id: "${escapeYamlString(source.workspace_id)}"`);
    if (source.workspace) lines.push(`  workspace: "${escapeYamlString(source.workspace)}"`);
    if (source.database) lines.push(`  database: "${escapeYamlString(source.database)}"`);
    if (source.schema) lines.push(`  schema: "${escapeYamlString(source.schema)}"`);
    if (source.models?.length) {
      lines.push('  models:');
      source.models.forEach(modelName => lines.push(`    - "${escapeYamlString(modelName)}"`));
    } else if (source.model) {
      lines.push(`  model: "${escapeYamlString(source.model)}"`);
    }
    if (source.pbix_path) lines.push(`  pbix_path: "${escapeYamlString(source.pbix_path)}"`);
    if (source.pbix_file_path) lines.push(`  pbix_file_path: "${escapeYamlString(source.pbix_file_path)}"`);
    if (source.local_folder_id) lines.push(`  local_folder_id: "${escapeYamlString(source.local_folder_id)}"`);
    if (source.local_folder_tag) lines.push(`  local_folder_tag: "${escapeYamlString(source.local_folder_tag)}"`);

    lines.push('targets:');
    targets.forEach((target) => {
      lines.push(`  - type: ${target.type}`);
      if (target.database) lines.push(`    database: "${escapeYamlString(target.database)}"`);
      if (target.schema) lines.push(`    schema: "${escapeYamlString(target.schema)}"`);
      if (target.account) lines.push(`    account: "${escapeYamlString(target.account)}"`);
      if (target.warehouse) lines.push(`    warehouse: "${escapeYamlString(target.warehouse)}"`);
      if (target.identity_id) lines.push(`    identity_id: "${escapeYamlString(target.identity_id)}"`);
      if (target.workspace_id) lines.push(`    workspace_id: "${escapeYamlString(target.workspace_id)}"`);
      if (target.workspace) lines.push(`    workspace: "${escapeYamlString(target.workspace)}"`);
    });

    // Persist user-edited field/model names for later deploy runs.
    const mappingOverrides = (detectedMappings || [])
      .map((row) => ({
        source_path: String(row?.source_path || '').trim(),
        target_name: String(row?.target_field || row?.target_name || row?.target || '').trim(),
      }))
      .filter((row) => row.source_path && row.target_name);

    if (mappingOverrides.length > 0) {
      lines.push('mappings_overrides:');
      mappingOverrides.forEach((row) => {
        lines.push(`  - source_path: "${escapeYamlString(row.source_path)}"`);
        lines.push(`    target_name: "${escapeYamlString(row.target_name)}"`);
      });
    }

    // Add relationships section
    let relationships = [];
    try {
      const storedRels = sessionStorage.getItem('detectedRelationships');
      if (storedRels) relationships = JSON.parse(storedRels);
    } catch {
      relationships = [];
    }

    if (relationships.length > 0 && autoRelationships) {
      lines.push('relationships:');
      relationships.forEach((rel) => {
        lines.push(`  - source: "${escapeYamlString(rel.source)}"`);
        lines.push(`    target: "${escapeYamlString(rel.target)}"`);
        lines.push(`    join_type: ${rel.joinType}`);
        lines.push(`    condition: "${escapeYamlString(rel.condition)}"`);
        lines.push(`    confidence: ${rel.confidence}`);
      });
    }

    lines.push('ui:');
    lines.push(`  intermediate_format: "${escapeYamlString(intermediateFormat)}"`);
    lines.push(`  editor_mode: "${escapeYamlString(configMode)}"`);
    if (domainHint.trim()) lines.push(`  domain_hint: "${escapeYamlString(domainHint.trim())}"`);

    if (selectedModels.size) {
      lines.push('selection:');
      lines.push('  model_ids:');
      [...selectedModels]
        .map(modelKey => modelKey.includes('::') ? modelKey.split('::')[1] : modelKey)
        .forEach(modelId => lines.push(`    - "${escapeYamlString(modelId)}"`));
    }

    lines.push('options:');
    lines.push(`  auto_relationships: ${autoRelationships}`);
    lines.push(`  generate_descriptions: ${generateDescriptions}`);
    return lines.join('\n');
  };

  const modelResultsByConnector = useMemo(() => {
    if (!modelQuery.trim()) {
      return {
        snowflake: (wsModels.snowflake || []).map(m => ({ ...m, _id: m.id })),
        fabric: allModels,
      };
    }

    return {
      snowflake: (wsModels.snowflake || [])
        .map(m => ({ ...m, _id: m.id }))
        .filter(m => matchesSmartQuery(`${m.name || ''} ${m.id || ''}`, modelQuery, modelQueryRegex)),
      fabric: allModels.filter(m =>
        matchesSmartQuery(`${m.name || ''} ${m.description || ''} ${m.wsid || ''}`, modelQuery, modelQueryRegex)
      ),
    };
  }, [allModels, modelQuery, modelQueryRegex, wsModels]);

  /* ─── Step validity ─── */
  const canAdvance = () => {
    if (step === 1) return name.trim().length > 0;
    if (step === 4) return mappingReadyToProceed;
    if (step === 5) return true;
    return true;
  };

  const fetchMappings = useCallback(async ({ dryRun = false, resetManual = true } = {}) => {
    setMappingError('');
    setMappingDryRunError('');
    if (dryRun) setMappingDryRunStatus('running');
    setMappingLoading(true);
    try {
      const projectId = createdProject?.id || createdProject?.project_id || 'preview';
      const { sourceConfig, targetConfig } = buildDryRunPayload({
        sourceConnector,
        targetConnectors,
        fabricAccountId,
        fabricWorkspaceId,
        snowflakeDatabase,
        targetDatabase,
        selectedModelNames,
      });

      const payload = {
        source_config: sourceConfig,
        target_config: targetConfig,
        selected_sources: selectedModelNames,
        reset_manual: resetManual
      };

      let response;
      if (dryRun) {
        response = await api.runProjectDryRun(projectId, payload);
      } else {
        response = await api.rerunAutoMap(projectId, payload);
      }

      console.log('[Dry Run] Raw response:', JSON.stringify(response, null, 2));

      if (response?.entity_mappings) {
        const kinds = response.entity_mappings.map(m => m.entity_kind);
        console.log('[Dry Run] Entity kinds in response:', kinds);
        console.log('[Dry Run] Has table entities:', kinds.includes('table'));
        console.log('[Dry Run] Has field entities:', kinds.includes('field'));
      }

      const entityMappings = Array.isArray(response?.entity_mappings) ? response.entity_mappings : [];
      // If we don't have mappings array, we can use entityMappings directly for our table
      setDetectedMappings(response?.mappings || []);
      setDetectedEntityMappings(entityMappings);

      if (dryRun) {
        setMappingDryRunStatus('success');
        setMappingDryRunSignature(currentMappingSignature);
        setUnmappedAcknowledged(false);
      }

      if (response?.summary?.collisions > 0 || (Array.isArray(response?.collisions) && response.collisions.length > 0)) {
        const count = response?.summary?.collisions || response.collisions.length;
        addLog('warning', 'Mapping', `${count} naming collision(s) auto-resolved with deterministic hash suffixes.`);
      }
      return { ok: true, response };
    } catch (err) {
      const msg = err?.message || 'Failed to generate mappings.';
      setMappingError(msg);
      if (dryRun) {
        setMappingDryRunStatus('failed');
        setMappingDryRunError(msg);
      }
      addLog('error', 'Mapping', msg);
      return { ok: false, error: msg };
    } finally {
      setMappingLoading(false);
    }
  }, [addLog, currentMappingSignature, sourceConnector, targetConnectors, selectedModelNames, createdProject, fabricWorkspaceId, snowflakeDatabase, targetDatabase]);

  // ── handleDryRun — triggers the dry-run API and populates detectedMappings ──
  const handleDryRun = useCallback(async () => {
    setMappingDryRunStatus('loading');
    setMappingError('');

    const projectId = createdProject?.id || createdProject?.project_id || 'preview';
    const { sourceConfig, targetConfig } = buildDryRunPayload({
      sourceConnector,
      targetConnectors,
      fabricAccountId,
      fabricWorkspaceId,
      snowflakeDatabase,
      targetDatabase,
      selectedModelNames,
    });

    const selectedSources = selectedModelNames;

    try {
      const response = await api.runProjectDryRun(projectId, {
        source_config: sourceConfig,
        target_config: targetConfig,
        selected_sources: selectedSources,
      });

      if (response?.success === false) {
        setMappingError(response?.error || 'Dry run failed.');
        setMappingDryRunStatus('error');
        return;
      }

      const rows = normalizeRows(response);
      setDetectedMappings(rows);
      setDryRunData(response);
      setMappingDryRunStatus('success');
      setMappingDryRunSignature(currentMappingSignature);
      setUnmappedAcknowledged(false);
      // Dry run succeeded — unlock the Continue button so the user can proceed to Step 5
      setMappingReadyToProceed(true);
    } catch (err) {
      setMappingError(err?.message || 'Dry run failed.');
      setMappingDryRunStatus('error');
      // preserve existing detectedMappings on failure
    }
  }, [
    createdProject, sourceConnector, fabricWorkspaceId, snowflakeDatabase,
    targetConnectors, targetDatabase, selectedModelNames, currentMappingSignature,
  ]);

  // ── handleFieldEdit — saves a single field mapping edit via the API ──────────
  const handleFieldEdit = useCallback(async (rowId, updates) => {
    setIsSavingEdit(true);

    const targetPlatform = Array.from(targetConnectors)[0] || 'snowflake';
    const editingRowData = detectedMappings.find(r => r.id === rowId);
    const sourceType = editingRowData?.source_type || '';

    const validation = validateTargetName(
      updates.target_name,
      targetPlatform,
      sourceType,
      updates.target_data_type,
    );

    if (!validation.isValid) {
      setIsSavingEdit(false);
      return; // error shown inline in modal
    }

    const projectId = createdProject?.id || createdProject?.project_id || 'preview';

    try {
      await api.updateMapping(projectId, rowId, {
        target_name: updates.target_name,
        target_data_type: updates.target_data_type,
        status: 'manual',
      });

      setDetectedMappings(prev =>
        prev.map(row =>
          row.id === rowId
            ? {
              ...row,
              target_field: updates.target_name,
              target_type: updates.target_data_type,
              status: 'manual',
              isDirty: true,
            }
            : row
        )
      );
      setEditingRow(null);
    } catch (err) {
      setMappingError(err?.message || 'Failed to save field edit.');
      // do NOT update the row on failure
    } finally {
      setIsSavingEdit(false);
    }
  }, [targetConnectors, detectedMappings, createdProject]);

  const handleBulkResolved = useCallback((resolvedMap) => {
    if (!resolvedMap || typeof resolvedMap !== 'object') return;

    setDetectedMappings((prev) => prev.map((row) => {
      const nextTarget = resolvedMap[row.id];
      if (!nextTarget) return row;
      return {
        ...row,
        target_field: nextTarget,
        status: 'auto_resolved',
        validation_status: 'valid',
        validation_code: 'OK',
        validation_message: '',
        collision_detected: false,
        auto_resolved: true,
        isDirty: true,
      };
    }));
  }, []);

  // ── handleDeploy — deploys finalized mappings and advances to Step 5 ─────────
  const handleDeploy = useCallback(async () => {
    const blockingRows = detectedMappings.filter(row => isDryRunBlockingRow(row));
    if (blockingRows.length > 0) {
      setDeployError('Resolve all collisions before deploying.');
      return;
    }

    setIsDeploying(true);
    setDeployError('');

    const projectId = createdProject?.id || createdProject?.project_id || 'preview';

    const fieldMappings = detectedMappings.map(row => ({
      id: row.id,
      source_name: row.source_field,
      target_name: row.target_field,
      target_data_type: row.target_type,
      status: row.status,
      entity_kind: row.entity_kind,
    }));

    try {
      const result = await api.deployMappings(projectId, fieldMappings);

      if (result?.success) {
        if (result.project_id) {
          setCreatedProject({ id: result.project_id, project_id: result.project_id });
        }
        setMappingReadyToProceed(true);
        setStep(5);
      } else {
        const errMsg = (Array.isArray(result?.errors) && result.errors[0])
          ? `Deploy failed: ${result.errors[0]}`
          : 'Deploy failed: unknown error';
        setDeployError(errMsg);
      }
    } catch (err) {
      setDeployError(err?.message || 'Deploy failed.');
    } finally {
      setIsDeploying(false);
    }
  }, [detectedMappings, createdProject]);

  const goNext = async () => {
    if (step === 5) { handleFinish(); return; }
    if (step === 1) {
      const isStep1Valid = Boolean(sourceConnector) && targetConnectors.size > 0 && Boolean(intermediateFormat);
      if (!isStep1Valid) {
        setShowStep1Validation(true);
        return;
      }
      setShowStep1Validation(false);
    }
    if (step === 3) {
      setMappingError('');
      sessionStorage.removeItem('detectedRelationships');
      if (mappingDryRunSignature !== currentMappingSignature) {
        setDetectedMappings([]);
        setDetectedEntityMappings([]);
        setMappingReadyToProceed(false);
        setMappingDryRunStatus('idle');
        setMappingDryRunError('');
        setMappingDryRunSignature('');
        setUnmappedAcknowledged(false);
      }
    }
    if (step === 4 && !mappingReadyToProceed) {
      setMappingError('Resolve blocking mapping validation issues before continuing.');
      return;
    }
    setStep(s => Math.min(5, s + 1));
  };
  const goBack = () => setStep(s => Math.max(1, s - 1));

  const handleDeployMapping = useCallback(async (fieldMappings) => {
    try {
      const projectId = createdProject?.id || createdProject?.project_id || 'preview';
      const result = await api.deployMappings(projectId, fieldMappings);

      if (result.success) {
        if (result.project_id && projectId === 'preview') {
          // Store the created project so we use it moving forward
          setCreatedProject({ id: result.project_id, project_id: result.project_id });
        }
        setMappingReadyToProceed(true);
        goNext();
      } else {
        setMappingError('Deployment returned without success.');
      }
    } catch (err) {
      setMappingError(err.message || 'Deploy failed');
      console.error('Deploy failed:', err);
    }
  }, [createdProject, goNext]);

  const updateTableMappingTarget = useCallback((mappingId, nextTarget) => {
    const normalized = String(nextTarget || '').trim();
    setDetectedMappings(prev => prev.map((mapping) => (
      mapping.id === mappingId
        ? { ...mapping, target: normalized, status: 'manual' }
        : mapping
    )));
    setDetectedEntityMappings(prev => prev.map((mapping) => (
      mapping.id === mappingId
        ? { ...mapping, target_name: normalized, is_user_edited: true, status: 'manual' }
        : mapping
    )));
  }, []);

  const updateColumnMappingTarget = useCallback((tableMappingId, sourceColumnName, nextTarget) => {
    const normalized = String(nextTarget || '').trim();
    let updatedSourcePath = '';
    setDetectedMappings(prev => prev.map((mapping) => {
      if (mapping.id !== tableMappingId) return mapping;
      updatedSourcePath = String(mapping.source_path || '');
      return {
        ...mapping,
        status: 'manual',
        columns: (mapping.columns || []).map((column) => (
          String(column.source || '') === String(sourceColumnName || '')
            ? { ...column, target: normalized }
            : column
        )),
      };
    }));
    setDetectedEntityMappings(prev => prev.map((mapping) => {
      const isMatchingColumn = String(mapping.entity_kind || '') === 'column'
        && String(mapping.parent_source_path || '') === String(updatedSourcePath || '')
        && String(mapping.source_name || '') === String(sourceColumnName || '');
      return isMatchingColumn
        ? { ...mapping, target_name: normalized, is_user_edited: true, status: 'manual' }
        : mapping;
    }));
  }, []);

  useEffect(() => {
    if (step !== 1 && showStep1Validation) {
      setShowStep1Validation(false);
    }
  }, [step, showStep1Validation]);

  const projectDiff = useMemo(() => {
    if (!editMode || !initialData) return null;
    const diff = {};
    if (name !== initialData.name) diff.name = { old: initialData.name, new: name };
    if (description !== initialData.description) diff.description = { old: initialData.description, new: description };
    const oldTags = Array.isArray(initialData.tags) ? initialData.tags : [];
    const newTags = Array.from(tags);
    if (JSON.stringify(oldTags.sort()) !== JSON.stringify(newTags.sort())) {
      diff.tags = { old: oldTags.join(', '), new: newTags.join(', ') };
    }
    if (sourceConnector !== initialData.source_type) diff.sourceConnector = { old: initialData.source_type, new: sourceConnector };
    if (!targetConnectors.has(initialData.target_type)) diff.targetConnectors = { old: initialData.target_type, new: Array.from(targetConnectors).join(', ') };
    return Object.keys(diff).length > 0 ? diff : null;
  }, [editMode, initialData, name, description, tags, sourceConnector, targetConnectors]);

  /* ─── Render ─── */
  return (
    <div style={{ padding: '0', minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-main)', position: 'relative' }}>
      {/* Modern Premium Top Header */}
      <div style={{
        height: 72,
        padding: '0 48px',
        background: 'rgba(18, 20, 28, 0.95)',
        backdropFilter: 'blur(12px)',
        borderBottom: '1px solid var(--border-main)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        position: 'sticky',
        top: 0,
        zIndex: 100,
        boxShadow: '0 4px 20px rgba(0, 0, 0, 0.15)'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
          <button
            onClick={() => {
              if (hasMeaningfulData && !editMode) {
                if (window.confirm('Discard project draft?')) {
                  clearWizardState();
                  navigate('/projects');
                }
              } else {
                navigate('/projects');
              }
            }}
            style={{
              background: 'var(--bg-surface-raised)',
              border: '1px solid var(--border-main)',
              borderRadius: 10,
              width: 38,
              height: 38,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              color: 'var(--text-secondary)',
              transition: 'all 0.2s'
            }}
            title="Exit Wizard"
          >
            <X size={18} />
          </button>

          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <h1 style={{ fontSize: 18, fontWeight: 800, color: 'var(--text-primary)', margin: 0, letterSpacing: '-0.01em' }}>
                {editMode ? 'Modernize Project' : 'New Semantic Project'}
              </h1>
              {name.trim() && (
                <div style={{
                  background: 'var(--accent-blue)15',
                  color: 'var(--accent-blue)',
                  padding: '2px 10px',
                  borderRadius: 6,
                  fontSize: 11,
                  fontWeight: 700,
                  border: '1px solid var(--accent-blue)30'
                }}>
                  {name}
                </div>
              )}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Settings size={10} />
              Step {step} of 5 — {STEPS[step - 1].label}
            </div>
          </div>
        </div>

        {/* Wizard Progress Stepper (Compact) */}
        <div style={{ display: 'flex', gap: 4 }}>
          {STEPS.map((s, idx) => {
            const isCompleted = step > idx + 1;
            const isActive = step === idx + 1;
            return (
              <div
                key={s.id}
                style={{
                  width: 40,
                  height: 4,
                  borderRadius: 2,
                  background: isActive ? 'var(--accent-blue)' : (isCompleted ? 'var(--color-success)' : 'var(--border-main)'),
                  transition: 'all 0.3s'
                }}
                title={s.label}
              />
            );
          })}
        </div>
      </div>

      {/* Main Content Area */}
      <div style={{
        flex: 1,
        padding: '48px',
        paddingBottom: 140, // Space for the fixed footer
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        overflowY: 'auto'
      }}>
        <div style={{ 
          width: '100%', 
          maxWidth: (step === 3 || step === 4) ? 1400 : 900,
          transition: 'max-width 0.3s ease'
        }}>
          {/* Step content */}
          {step === 1 && (
            <StepBasicInfo
              name={name} setName={setName}
              description={description} setDescription={setDescription}
              sourceConnector={sourceConnector} setSourceConnector={setSourceConnector}
              targetConnectors={targetConnectors} setTargetConnectors={setTargetConnectors}
              intermediateFormat={intermediateFormat} setIntermediateFormat={setIntermediateFormat}
              tags={tags} setTags={setTags}
              tagInput={tagInput} setTagInput={setTagInput}
              showValidation={showStep1Validation}
              editMode={editMode}
            />
          )}
          {step === 2 && (
            <StepConnectorConfig
              sourceConnector={sourceConnector}
              targetConnectors={targetConnectors}
              fabricAccountId={selectedConnectionId}
              setFabricAccountId={(nextId) => {
                setSelectedConnectionId(nextId);
                setFabricAccountId(nextId);
              }}
              fabricAccounts={fabricAccounts}
              fabricWorkspaceId={fabricWorkspaceId}
              setFabricWorkspaceId={setFabricWorkspaceId}
              snowflakeAccountId={snowflakeAccountId}
              setSnowflakeAccountId={setSnowflakeAccountId}
              snowflakeAccounts={snowflakeAccounts}
              databricksAccountId={databricksAccountId}
              setDatabricksAccountId={setDatabricksAccountId}
              databricksAccounts={databricksAccounts}
              snowflakeDatabase={snowflakeDatabase} setSnowflakeDatabase={setSnowflakeDatabase}
              snowflakeSchema={snowflakeSchema} setSnowflakeSchema={setSnowflakeSchema}
              targetDatabase={targetDatabase} setTargetDatabase={setTargetDatabase}
              targetSchema={targetSchema} setTargetSchema={setTargetSchema}
              targetAccount={targetAccount} setTargetAccount={setTargetAccount}
              targetWarehouse={targetWarehouse} setTargetWarehouse={setTargetWarehouse}
              domainHint={domainHint} setDomainHint={setDomainHint}
              pbixFile={pbixFile}
              setPbixFile={setPbixFile}
              pbixUploadPath={pbixUploadPath}
              pbixUploading={pbixUploading}
              setPbixUploading={setPbixUploading}
              pbixSourceMode={pbixSourceMode}
              setPbixSourceMode={setPbixSourceMode}
              localFolders={localFolders}
              localFoldersLoading={localFoldersLoading}
              selectedLocalFolderId={selectedLocalFolderId}
              setSelectedLocalFolderId={setSelectedLocalFolderId}
              onUploadSuccess={({ file, path }) => {
                if (file) setPbixFile(file);
                setPbixUploadPath(String(path || '').trim());
              }}
              workspaces={liveFabricWorkspaces}
              workspacesLoading={workspacesLoading}
              isRefreshingWorkspaces={isRefreshingWorkspaces}
              fetchFabricWorkspaces={() => fetchFabricWorkspaces(selectedConnectionId)}
              runWarning={runWarning}
            />
          )}
          {step === 3 && (
            <StepSourceBrowser
              sourceConnector={sourceConnector}
              selectedWorkspace={selectedWorkspace}
              workspaces={workspaces} wsLoading={wsLoading}
              expandedWs={expandedWs} toggleWorkspace={toggleWorkspace}
              wsModels={wsModels}
              selectedModels={selectedModels} toggleModel={toggleModel}
              clearSelectedModels={clearSelectedModels}
              modelQuery={modelQuery} setModelQuery={setModelQuery}
              modelQueryRegex={modelQueryRegex}
              setModelQueryRegex={setModelQueryRegex}
              modelResults={modelResultsByConnector.fabric}
              snowflakeResults={modelResultsByConnector.snowflake}
              databricksQuery={databricksQuery}
              setDatabricksQuery={setDatabricksQuery}
              databricksQueryRegex={databricksQueryRegex}
              setDatabricksQueryRegex={setDatabricksQueryRegex}
              databricksObjects={databricksObjects}
              databricksLoading={databricksLoading}
              selectedDatabricksTables={selectedDatabricksTables}
              setSelectedDatabricksTables={setSelectedDatabricksTables}
              allModels={allModels}
              pbixSourceMode={pbixSourceMode}
              selectedLocalFolderTag={selectedLocalFolderTag}
              pbixFiles={pbixFiles}
              pbixFilesLoading={pbixFilesLoading}
              pbixFilesError={pbixFilesError}
              selectedPbixFilePath={selectedPbixFilePath}
              onSelectPbixFile={setSelectedPbixFilePath}
              savedModels={initialSelectedModels}
            />
          )}
          {step === 4 && (
            <ErrorBoundary>
              {editMode && (
                <div style={{ marginBottom: 20, padding: '16px', background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: '12px' }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Mapping Mode</div>
                  <div style={{ display: 'flex', gap: '24px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)' }}>
                      <input type="radio" value="saved" checked={mappingMode === 'saved'} onChange={() => setMappingMode('saved')} style={{ accentColor: 'var(--accent-blue)' }} />
                      Use Last Saved Mapping
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', fontSize: 13, color: 'var(--text-secondary)' }}>
                      <input type="radio" value="auto" checked={mappingMode === 'auto'} onChange={() => setMappingMode('auto')} style={{ accentColor: 'var(--accent-blue)' }} />
                      Auto-Detect Again
                    </label>
                  </div>
                </div>
              )}
              <StepMappingOptions
                autoRelationships={autoRelationships} setAutoRelationships={setAutoRelationships}
                generateDescriptions={generateDescriptions} setGenerateDescriptions={setGenerateDescriptions}
                detectedMappings={detectedMappings}
                mappingLoading={mappingLoading}
                mappingError={mappingError}
                dryRunStatus={mappingDryRunStatus}
                dryRunError={mappingDryRunError}
                unmappedAcknowledged={unmappedAcknowledged}
                setUnmappedAcknowledged={setUnmappedAcknowledged}
                selectedModelNames={selectedModelNames}
                sourceConnector={sourceConnector}
                onUpdateTableTarget={updateTableMappingTarget}
                onUpdateColumnTarget={updateColumnMappingTarget}
                onRunDryRun={handleDryRun}
                onClearMappings={() => {
                  setDetectedMappings([]);
                  setDetectedEntityMappings([]);
                }}
                onRowsChange={(rows) => {
                  setDetectedEntityMappings(
                    rows
                      .filter(r => r.isDirty)
                      .map(r => ({
                        id: r.id,
                        source_path: r.source_path,
                        entity_kind: r.entity_kind,
                        source_name: r.source_field,
                        target_name: r.target_field,
                        status: r.status,
                      }))
                  );
                }}
                onDeploy={handleDeployMapping}
                onProceedStateChange={setMappingReadyToProceed}
                primaryTargetConnector={[...targetConnectors][0] || ''}
                dryRunData={dryRunData}
                editingRow={editingRow}
                setEditingRow={setEditingRow}
                isSavingEdit={isSavingEdit}
                isDeploying={isDeploying}
                deployError={deployError}
                onFieldEdit={handleFieldEdit}
                onDeployMappings={handleDeploy}
                targetConnectors={targetConnectors}
                onBulkResolved={handleBulkResolved}
              />
            </ErrorBoundary>
          )}
          {step === 5 && (
            <StepFinish
              name={name}
              saving={saving}
              createdProject={createdProject}
              createError={createError}
              runWarning={runWarning}
              createReverseProject={createReverseProject}
              setCreateReverseProject={setCreateReverseProject}
              sourceConnector={sourceConnector}
              targetConnectors={targetConnectors}
              intermediateFormat={intermediateFormat}
              selectedWorkspace={selectedWorkspace}
              navigate={navigate}
              editMode={editMode}
              diff={projectDiff}
              selectedModels={selectedModels}
              initialSelectedModels={initialSelectedModels}
              selectedModelNameByKey={selectedModelNameByKey}
              onClear={clearWizardState}
            />
          )}
        </div>
      </div>

      {/* Full-Width Sticky Footer */}
      {/* Modern Persistent Footer */}
      {!createdProject && (
        <div style={{
          height: 80,
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          background: 'rgba(18, 20, 28, 0.95)',
          backdropFilter: 'blur(12px)',
          borderTop: '1px solid var(--border-main)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 1000,
          boxShadow: '0 -4px 20px rgba(0, 0, 0, 0.2)'
        }}>
          <div style={{
            width: '100%',
            maxWidth: (step === 3 || step === 4) ? 1400 : 900,
            padding: '0 48px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between'
          }}>
            {step > 1 ? (
              <button
                onClick={goBack}
                className="flex items-center gap-2 px-6 py-2.5 text-xs font-bold transition-all rounded-lg border border-[var(--border-main)] hover:bg-[var(--bg-surface-raised)] text-[var(--text-secondary)]"
              >
                <ArrowLeft size={14} /> BACK
              </button>
            ) : <div />}

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <button
                onClick={goNext}
                disabled={!canAdvance() || saving || mappingLoading}
                className={`flex items-center gap-2 px-8 py-2.5 text-xs font-bold transition-all rounded-lg shadow-lg ${
                  !canAdvance() || saving || mappingLoading
                    ? 'bg-[var(--bg-surface-raised)] text-[var(--text-tertiary)] cursor-not-allowed opacity-50'
                    : 'bg-[var(--accent-blue)] text-white hover:bg-blue-600 shadow-blue-900/20'
                }`}
              >
                {(saving || mappingLoading) && <Loader2 size={14} className="animate-spin" />}
                {step === 5 ? (
                  <>
                    <Save size={14} />
                    {saving ? 'SAVING…' : (editMode ? 'SAVE CHANGES' : 'CREATE PROJECT')}
                  </>
                ) : (
                  <>
                    CONTINUE
                    <ArrowRight size={14} />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      <Modal
        open={syncErrorOpen}
        onClose={() => setSyncErrorOpen(false)}
        title="Sync Error"
        size="md"
      >
        <div style={{ fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6 }}>
          {syncError || 'An unexpected sync error occurred.'}
        </div>
      </Modal>
      <DraftToast
        visible={hasRestoredDraft}
        onStartFresh={() => {
          clearWizardState();
          window.location.reload();
        }}
        onDismiss={() => setHasRestoredDraft(false)}
      />
    </div>
  );
}

function StepBasicInfo({
  name, setName, description, setDescription,
  sourceConnector, setSourceConnector,
  targetConnectors, setTargetConnectors,
  intermediateFormat, setIntermediateFormat,
  tags, setTags, tagInput, setTagInput,
  showValidation, editMode
}) {
  const isSourceMissing = !sourceConnector;
  const isTargetsMissing = targetConnectors.size === 0;
  const isFormatMissing = !intermediateFormat;

  const LABEL = {
    display: 'block', fontSize: 11, fontWeight: 700,
    color: 'var(--text-tertiary)', marginBottom: 8,
    textTransform: 'uppercase', letterSpacing: '0.05em'
  };
  const INPUT_STYLE = {
    width: '100%', padding: '10px 14px', borderRadius: 8,
    background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
    color: 'var(--text-primary)', fontSize: 13, outline: 'none',
    transition: 'all 0.2s ease',
  };
  const SECTION_CARD = {
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    background: 'var(--bg-surface)',
    padding: 24,
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 32, paddingBottom: 40 }}>
      {/* 1. Project Identity */}
      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 20 }}>
          <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>Project Identity</h3>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>Basic details to identify this synchronization pipeline.</p>
        </div>
        
        <div style={{ display: 'grid', gap: 24 }}>
          <div>
            <label style={LABEL}>Project Name</label>
            <input
              type="text" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. Sales Analytics Sync"
              style={{ ...INPUT_STYLE, maxWidth: 500 }}
              disabled={editMode}
            />
            {showValidation && !name && (
              <p style={{ color: 'var(--color-error)', fontSize: 11, marginTop: 4 }}>Name is required</p>
            )}
          </div>
          <div>
            <label style={LABEL}>Description (optional)</label>
            <textarea
              value={description} onChange={e => setDescription(e.target.value)}
              placeholder="Describe the purpose of this data pipeline..."
              style={{ ...INPUT_STYLE, minHeight: 80, resize: 'vertical' }}
            />
          </div>

          <div>
            <label style={LABEL}>Project Tags</label>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
              {[...tags].map(tag => (
                <div
                  key={tag}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 6,
                    padding: '4px 10px', borderRadius: 6,
                    background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-blue)',
                    fontSize: 12, fontWeight: 600,
                  }}
                >
                  {tag}
                  <button
                    type="button"
                    onClick={() => { const next = new Set(tags); next.delete(tag); setTags(next); }}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0, display: 'flex' }}
                  >
                    <X size={13} />
                  </button>
                </div>
              ))}
            </div>
            <input
              type="text" value={tagInput} onChange={e => setTagInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && tagInput.trim()) {
                  const next = new Set(tags);
                  next.add(tagInput.trim());
                  setTags(next);
                  setTagInput('');
                }
              }}
              placeholder="Add tag and press Enter..."
              style={INPUT_STYLE}
            />
          </div>
        </div>
      </div>

      {/* 2. Unified Pipeline Configuration Island */}
      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>Pipeline Configuration</h3>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>Define how data flows from your source to the target destinations.</p>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
          {/* Source & Target Row */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 32, alignItems: 'stretch' }}>
            {/* Source Column */}
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div style={LABEL}>Source Connector</div>
                <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: 'rgba(59, 130, 246, 0.1)', color: 'var(--accent-blue)' }}>SINGLE</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
                {CONNECTOR_TYPES.map(c => {
                  const isSelected = sourceConnector === c.value;
                  const shouldDim = Boolean(sourceConnector) && !isSelected;
                  return (
                    <button
                      key={c.value} type="button" onClick={() => setSourceConnector(c.value)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderRadius: 10, cursor: 'pointer',
                        background: isSelected ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface-raised)',
                        border: `1.5px solid ${isSelected ? 'var(--accent-blue)' : 'transparent'}`,
                        color: isSelected ? 'var(--accent-blue)' : shouldDim ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                        fontSize: 13, fontWeight: isSelected ? 600 : 400, opacity: shouldDim ? 0.4 : 1, transition: 'all 0.2s ease',
                        boxShadow: isSelected ? '0 0 0 1px rgba(59, 130, 246, 0.2)' : 'none',
                      }}
                    >
                      <div style={{ 
                        width: 16, height: 16, borderRadius: '50%', 
                        border: isSelected ? '5px solid var(--accent-blue)' : '2px solid var(--border-main)',
                        background: 'transparent',
                        transition: 'all 0.2s ease'
                      }} />
                      <SourceIcon source={c.value} size={18} />
                      <span style={{ flex: 1, textAlign: 'left' }}>{c.label}</span>
                    </button>
                  );
                })}
              </div>
              {showValidation && isSourceMissing && (
                <p style={{ color: 'var(--color-error)', fontSize: 11, marginTop: 12 }}>Please select a source</p>
              )}
            </div>

            {/* Target Column */}
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                <div style={LABEL}>Target Connector(s)</div>
                <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: 'rgba(34, 197, 94, 0.1)', color: 'var(--color-success)' }}>MULTIPLE</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 1 }}>
                {TARGET_CONNECTOR_TYPES.map(t => {
                  const isSelected = targetConnectors.has(t.value);
                  const isDisabled = sourceConnector === t.value;
                  return (
                    <button
                      key={t.value} type="button" disabled={isDisabled}
                      onClick={() => {
                        const next = new Set(targetConnectors);
                        if (next.has(t.value)) next.delete(t.value);
                        else next.add(t.value);
                        setTargetConnectors(next);
                      }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderRadius: 10,
                        cursor: isDisabled ? 'not-allowed' : 'pointer',
                        background: isSelected ? 'rgba(59, 130, 246, 0.08)' : 'var(--bg-surface-raised)',
                        border: `1.5px solid ${isSelected ? 'var(--accent-blue)' : 'transparent'}`,
                        color: isSelected ? 'var(--accent-blue)' : isDisabled ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                        fontSize: 13, fontWeight: isSelected ? 600 : 400, opacity: isDisabled ? 0.2 : 1, transition: 'all 0.2s ease',
                        boxShadow: isSelected ? '0 0 0 1px rgba(59, 130, 246, 0.2)' : 'none',
                      }}
                    >
                      <div style={{ 
                        width: 16, height: 16, borderRadius: 4, 
                        background: isSelected ? 'var(--accent-blue)' : 'transparent', 
                        border: isSelected ? 'none' : '2px solid var(--border-main)', 
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        transition: 'all 0.2s ease'
                      }}>
                        {isSelected && <Check size={12} color="white" strokeWidth={3} />}
                      </div>
                      <SourceIcon source={t.value} size={18} />
                      <span style={{ flex: 1, textAlign: 'left' }}>{t.label}</span>
                    </button>
                  );
                })}
              </div>
              {showValidation && isTargetsMissing && (
                <p style={{ color: 'var(--color-error)', fontSize: 11, marginTop: 12 }}>Select at least one target</p>
              )}
            </div>
          </div>

          {/* Format Selection (Bottom Row) */}
          <div style={{ borderTop: '1px solid rgba(255, 255, 255, 0.05)', paddingTop: 24 }}>
            <label style={LABEL}>Intermediate Format</label>
            <div style={{ display: 'flex', gap: 12, marginTop: 12 }}>
              {INTERMEDIATE_FORMAT_TYPES.map(t => (
                <ConnectorChip
                  key={t.value} label={t.label}
                  selected={intermediateFormat === t.value}
                  onClick={() => setIntermediateFormat(t.value)}
                />
              ))}
            </div>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 16, lineHeight: 1.5 }}>
              The intermediate format determines the schema representation used during the synchronization process. 
              This is critical for cross-platform semantic translation.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConnectorChip({ icon, label, selected, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 20px', borderRadius: 12, cursor: 'pointer',
        background: selected ? 'rgba(59, 130, 246, 0.1)' : 'var(--bg-surface-raised)',
        border: `1.5px solid ${selected ? 'var(--accent-blue)' : 'rgba(255, 255, 255, 0.05)'}`,
        color: selected ? 'var(--accent-blue)' : 'var(--text-secondary)',
        fontSize: 13, fontWeight: selected ? 600 : 500, transition: 'all 0.2s ease',
        boxShadow: selected ? '0 0 0 1px rgba(59, 130, 246, 0.2)' : 'none',
      }}
    >
      {icon && <div style={{ display: 'flex' }}>{icon}</div>}
      {label}
      {selected && <Check size={14} strokeWidth={3} />}
    </button>
  );
}

/* ─── Step 2: Connector Config ─── */
function StepConnectorConfig({
  sourceConnector,
  targetConnectors,
  fabricAccountId,
  setFabricAccountId,
  fabricAccounts,
  fabricWorkspaceId,
  setFabricWorkspaceId,
  snowflakeAccountId,
  setSnowflakeAccountId,
  snowflakeAccounts,
  databricksAccountId,
  setDatabricksAccountId,
  databricksAccounts,
  snowflakeDatabase,
  setSnowflakeDatabase,
  snowflakeSchema,
  setSnowflakeSchema,
  targetDatabase,
  setTargetDatabase,
  targetSchema,
  setTargetSchema,
  targetAccount,
  setTargetAccount,
  targetWarehouse,
  setTargetWarehouse,
  domainHint,
  setDomainHint,
  pbixFile,
  setPbixFile,
  pbixUploadPath,
  pbixUploading,
  setPbixUploading,
  pbixSourceMode,
  setPbixSourceMode,
  localFolders,
  localFoldersLoading,
  selectedLocalFolderId,
  setSelectedLocalFolderId,
  onUploadSuccess,
  workspaces,
  workspacesLoading,
  isRefreshingWorkspaces,
  fetchFabricWorkspaces,
  runWarning,
}) {
  const [pbixDragOver, setPbixDragOver] = useState(false);
  const [pbixUploadError, setPbixUploadError] = useState('');
  const uploadPbixFile = async (file) => {
    if (!file) return;
    if (!String(file.name || '').toLowerCase().endsWith('.pbix')) {
      setPbixUploadError('Only .pbix files are supported.');
      return;
    }

    setPbixUploadError('');
    setPbixUploading(true);
    try {
      const response = await api.uploadPbix(file);
      const uploadedPath = String(response?.path || '').trim();
      setPbixFile(file);
      onUploadSuccess?.({ file, path: uploadedPath });
      if (!uploadedPath) {
        setPbixUploadError('Upload succeeded but server did not return a file path.');
      }
    } catch (err) {
      setPbixUploadError(err?.message || 'PBIX upload failed.');
    } finally {
      setPbixUploading(false);
    }
  };

  const handlePbixDrop = async (event) => {
    event.preventDefault();
    event.stopPropagation();
    setPbixDragOver(false);
    const droppedFile = event.dataTransfer?.files?.[0] || null;
    if (droppedFile) await uploadPbixFile(droppedFile);
  };
  const selectedTargets = [...targetConnectors];
  const sourceLabel = CONNECTOR_TYPES.find(c => c.value === sourceConnector)?.label || sourceConnector;
  const activeLocalFolders = (localFolders || []).filter(folder => folder?.is_active !== false);

  const SECTION_CARD = {
    border: '1px solid rgba(255, 255, 255, 0.05)',
    borderRadius: 16,
    background: 'var(--bg-surface)',
    padding: 24,
    boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
  };

  const LABEL = {
    display: 'block', fontSize: 11, fontWeight: 700,
    color: 'var(--text-tertiary)', marginBottom: 6,
    textTransform: 'uppercase', letterSpacing: '0.05em'
  };
  const INPUT = {
    width: '100%', padding: '10px 14px', borderRadius: 8,
    background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
    color: 'var(--text-primary)', fontSize: 13, outline: 'none',
    transition: 'all 0.2s ease',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, paddingBottom: 40 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Connector Configuration</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Configure the source and target connectors for your project.
        </p>
      </div>

      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>1. Source Configuration: {sourceLabel}</div>
        </div>

        {sourceConnector === 'snowflake' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={LABEL}>Source Account (override)</label>
              <input
                type="text" value={snowflakeAccountId} onChange={e => setSnowflakeAccountId(e.target.value)}
                placeholder="Use saved Snowflake account"
                style={INPUT}
                onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
            </div>
            <div>
              <label style={LABEL}>Source Warehouse (override)</label>
              <input
                type="text" value={targetWarehouse} onChange={e => setTargetWarehouse(e.target.value)}
                placeholder="Use saved Snowflake warehouse"
                style={INPUT}
                onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
            </div>
            <div style={{ gridColumn: 'span 2', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <label style={LABEL}>Source Database</label>
                <input
                  type="text" value={snowflakeDatabase} onChange={e => setSnowflakeDatabase(e.target.value)}
                  placeholder="e.g. SNOWFLAKE_SAMPLE_DATA"
                  style={INPUT}
                  onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                  onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                />
              </div>
              <div>
                <label style={LABEL}>Source Schema</label>
                <input
                  type="text" value={snowflakeSchema} onChange={e => setSnowflakeSchema(e.target.value)}
                  placeholder="e.g. PUBLIC"
                  style={INPUT}
                  onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                  onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                />
              </div>
            </div>
          </div>
        )}

        {sourceConnector === 'fabric' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label style={LABEL}>Fabric Account</label>
              <select
                value={fabricAccountId}
                onChange={e => {
                  const nextAccountId = e.target.value;
                  setFabricAccountId(nextAccountId);
                  setFabricWorkspaceId('');
                  fetchFabricWorkspaces(nextAccountId);
                }}
                style={INPUT}
              >
                <option value="" disabled>Select Fabric connection</option>
                {fabricAccounts.length === 0 && <option value="" disabled>No accounts available</option>}
                {fabricAccounts.length > 0 && fabricAccounts.map(acc => (
                  <option key={acc.id} value={acc.id}>
                    {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
                  </option>
                ))}
              </select>
            </div>

            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <label style={{ ...LABEL, margin: 0 }}>Source Fabric Workspace</label>
                <div style={{ flex: 1 }} />
                <button
                  onClick={() => fetchFabricWorkspaces(fabricAccountId)}
                  disabled={isRefreshingWorkspaces || !fabricAccountId}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 4,
                    background: 'none', border: 'none', cursor: (isRefreshingWorkspaces || !fabricAccountId) ? 'not-allowed' : 'pointer',
                    fontSize: 11, color: 'var(--text-tertiary)', padding: '2px 6px',
                    borderRadius: 4, transition: 'all 0.2s ease',
                    opacity: (isRefreshingWorkspaces || !fabricAccountId) ? 0.6 : 1
                  }}
                  title="Refresh workspaces"
                >
                  <RefreshCw size={12} style={{ animation: isRefreshingWorkspaces ? 'spin 1s linear infinite' : 'none' }} />
                  Refresh
                </button>
              </div>
              <SearchableSelect
                items={workspaces}
                displayKey="name"
                valueKey="id"
                searchFields={['name', 'id', 'workspace_id']}
                placeholder={isRefreshingWorkspaces ? 'Refreshing...' : "Choose a workspace"}
                value={fabricWorkspaceId}
                onChange={item => setFabricWorkspaceId(item?.id || '')}
                loading={workspacesLoading || isRefreshingWorkspaces}
                clearable={false}
              />
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                Select the workspace containing the semantic models you want to migrate.
              </p>
            </div>
          </div>
        )}

        {sourceConnector === 'pbix' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div>
              <label style={LABEL}>PBIX Source Mode</label>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {PBIX_SOURCE_MODES.map(mode => {
                  const active = pbixSourceMode === mode.value;
                  return (
                    <button
                      key={mode.value}
                      type="button"
                      onClick={() => setPbixSourceMode(mode.value)}
                      style={{
                        border: `1px solid ${active ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                        background: active ? 'var(--accent-blue)14' : 'var(--bg-surface)',
                        color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
                        borderRadius: 999,
                        padding: '7px 12px',
                        cursor: 'pointer',
                        fontSize: 12,
                        fontWeight: 700,
                      }}
                    >
                      {mode.label}
                    </button>
                  );
                })}
              </div>
            </div>

            {pbixSourceMode === 'TAG' ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div>
                  <label style={LABEL}>Folder Tag</label>
                  <select
                    value={selectedLocalFolderId}
                    onChange={(event) => setSelectedLocalFolderId(event.target.value)}
                    style={INPUT}
                  >
                    <option value="">Select Folder Tag...</option>
                    {activeLocalFolders.map(folder => (
                      <option key={folder.id} value={folder.id}>
                        {folder.tag_name} ({folder.absolute_path})
                      </option>
                    ))}
                  </select>
                  <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                    {localFoldersLoading
                      ? 'Loading trusted local folders…'
                      : activeLocalFolders.length === 0
                        ? 'No active local folders are registered in Settings yet.'
                        : 'Step 3 will show the PBIX files inside the selected tagged folder.'}
                  </p>
                </div>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <label style={LABEL}>PBIX Upload</label>
                <label
                  onDragOver={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setPbixDragOver(true);
                  }}
                  onDragLeave={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setPbixDragOver(false);
                  }}
                  onDrop={handlePbixDrop}
                  style={{
                    border: '1px dashed var(--accent-blue)',
                    borderRadius: 12,
                    padding: 18,
                    background: pbixDragOver ? 'var(--accent-blue)14' : 'var(--accent-blue)08',
                    color: 'var(--text-secondary)',
                    cursor: pbixUploading ? 'progress' : 'pointer',
                  }}
                >
                  <input
                    type="file"
                    accept=".pbix"
                    style={{ display: 'none' }}
                    disabled={pbixUploading}
                    onChange={async (event) => {
                      const nextFile = event.target.files?.[0] || null;
                      if (nextFile) await uploadPbixFile(nextFile);
                    }}
                  />
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                    Drag and drop a `.pbix` file here or click to browse
                    {pbixUploading && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                    {pbixUploading
                      ? 'Saving file to server...'
                      : pbixUploadPath
                        ? `Saved path: ${pbixUploadPath}`
                        : pbixFile
                          ? `Selected file: ${pbixFile.name}`
                          : 'The file is uploaded temporarily and passed to LocalPBIXConnector during sync.'}
                  </div>
                  {pbixUploading && (
                    <div style={{ marginTop: 10, width: '100%', height: 6, borderRadius: 999, background: 'var(--border-main)', overflow: 'hidden' }}>
                      <div style={{ width: '100%', height: '100%', background: 'var(--accent-blue)', animation: 'pulse 1.2s ease-in-out infinite' }} />
                    </div>
                  )}
                  {pbixUploadError && (
                    <div style={{ marginTop: 8, fontSize: 12, color: 'var(--color-error)' }}>
                      {pbixUploadError}
                    </div>
                  )}
                </label>
              </div>
            )}
          </div>
        )}

        {sourceConnector !== 'fabric' && sourceConnector !== 'snowflake' && sourceConnector !== 'pbix' && (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
            Source connector defaults will be resolved from the saved global connection.
          </div>
        )}
      </div>

      <div style={SECTION_CARD}>
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>2. Target Configuration</div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
            Configure each selected target separately. Leave fields empty to use global defaults.
          </div>
        </div>

        {selectedTargets.length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
            No target connector selected. Go back to Basic Info and choose at least one target.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {selectedTargets.map(target => {
            const targetMeta = TARGET_CONNECTOR_TYPES.find(t => t.value === target) || { label: target };
            return (
              <details key={target} open style={{ border: '1px solid var(--border-main)', borderRadius: 10 }}>
                <summary
                  style={{
                    listStyle: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8,
                    padding: '10px 12px',
                    background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)',
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                    <SourceIcon source={targetMeta.value ?? target} size={16} />
                    {targetMeta.label} Target Configuration
                  </span>
                  <ChevronDown size={14} />
                </summary>

                <div style={{ padding: 12, background: 'var(--bg-surface)' }}>
                  {target === 'snowflake' && (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                      <div>
                        <label style={LABEL}>Snowflake Account (override)</label>
                        <input
                          type="text" value={targetAccount} onChange={e => setTargetAccount(e.target.value)}
                          placeholder="Use saved Snowflake account"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Warehouse (override)</label>
                        <input
                          type="text" value={targetWarehouse} onChange={e => setTargetWarehouse(e.target.value)}
                          placeholder="Use saved Snowflake warehouse"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Database (optional)</label>
                        <input
                          type="text" value={targetDatabase} onChange={e => setTargetDatabase(e.target.value)}
                          placeholder="Use global Snowflake database"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                      <div>
                        <label style={LABEL}>Snowflake Schema (optional)</label>
                        <input
                          type="text" value={targetSchema} onChange={e => setTargetSchema(e.target.value)}
                          placeholder="Use global Snowflake schema"
                          style={INPUT}
                          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
                        />
                      </div>
                    </div>
                  )}

                  {target === 'fabric' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div>
                        <label style={LABEL}>Fabric Account</label>
                        <select
                          value={fabricAccountId}
                          onChange={e => {
                            const nextAccountId = e.target.value;
                            setFabricAccountId(nextAccountId);
                            setFabricWorkspaceId('');
                          }}
                          style={INPUT}
                        >
                          {fabricAccounts.length === 0 && <option value="" disabled>No accounts available</option>}
                          {fabricAccounts.length > 0 && fabricAccounts.map(acc => (
                            <option key={acc.id} value={acc.id}>
                              {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
                            </option>
                          ))}
                        </select>
                        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                          Choose the Fabric identity for target deployment.
                        </p>
                      </div>

                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <label style={{ ...LABEL, margin: 0 }}>Target Fabric Workspace</label>
                          <div style={{ flex: 1 }} />
                          <button
                            onClick={() => fetchFabricWorkspaces(fabricAccountId)}
                            disabled={isRefreshingWorkspaces}
                            style={{
                              display: 'flex', alignItems: 'center', gap: 4,
                              background: 'none', border: 'none', cursor: isRefreshingWorkspaces ? 'not-allowed' : 'pointer',
                              fontSize: 11, color: 'var(--text-tertiary)', padding: '2px 6px',
                              borderRadius: 4, transition: 'all 0.2s ease',
                              opacity: isRefreshingWorkspaces ? 0.6 : 1
                            }}
                            title="Refresh workspaces"
                          >
                            <RefreshCw size={12} style={{ animation: isRefreshingWorkspaces ? 'spin 1s linear infinite' : 'none' }} />
                            Refresh
                          </button>
                        </div>

                        <SearchableSelect
                          items={workspaces}
                          displayKey="name"
                          valueKey="id"
                          searchFields={['name', 'id', 'workspace_id']}
                          placeholder={isRefreshingWorkspaces ? 'Refreshing...' : 'Choose a target workspace'}
                          value={fabricWorkspaceId}
                          onChange={item => {
                            const newId = item?.id || '';
                            setFabricWorkspaceId(newId);
                          }}
                          loading={workspacesLoading || isRefreshingWorkspaces}
                          clearable={false}
                        />

                        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                          Choose the destination Fabric workspace for this target sync.
                        </p>
                      </div>
                    </div>
                  )}

                  {target === 'databricks' && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      {databricksAccounts.length > 0 && (
                        <div>
                          <label style={LABEL}>Databricks Account</label>
                          <select
                            value={databricksAccountId}
                            onChange={e => setDatabricksAccountId(e.target.value)}
                            style={INPUT}
                          >
                            {databricksAccounts.map(acc => (
                              <option key={acc.id} value={acc.id}>
                                {(acc.tag || acc.identity_email || acc.id)} ({acc.identity_email || 'N/A'})
                              </option>
                            ))}
                          </select>
                          <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
                            Select which Databricks identity to use for deployment.
                          </p>
                        </div>
                      )}
                      {databricksAccounts.length === 0 && (
                        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                          No Databricks accounts configured. This target will use global connection defaults from Settings.
                        </div>
                      )}
                    </div>
                  )}

                  {target !== 'snowflake' && target !== 'fabric' && target !== 'databricks' && (
                    <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                      This target will use global connection defaults from Settings.
                    </div>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      </div>

      <div style={SECTION_CARD}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>Optional Metadata</div>
        <label style={LABEL}>Domain Hint (optional)</label>
        <input
          type="text" value={domainHint} onChange={e => setDomainHint(e.target.value)}
          placeholder="e.g. finance, sales, hr — helps AI generate better names"
          style={INPUT}
          onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
          onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
        />
        <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
          A domain hint improves generated labels and descriptions. It does not change connector behavior.
        </p>
      </div>
    </div>
  );
}

/* ─── Step 3: Source Browser ─── */
function StepSourceBrowser({
  sourceConnector, selectedWorkspace, workspaces, wsLoading,
  expandedWs, toggleWorkspace, wsModels,
  selectedModels, toggleModel, clearSelectedModels,
  modelQuery, setModelQuery,
  modelQueryRegex, setModelQueryRegex,
  modelResults, snowflakeResults,
  pbixSourceMode = 'TAG',
  selectedLocalFolderTag = '',
  pbixFiles = [],
  pbixFilesLoading = false,
  pbixFilesError = '',
  selectedPbixFilePath = '',
  onSelectPbixFile = () => { },
  // Databricks props
  databricksObjects = [],
  databricksLoading = false,
  selectedDatabricksTables = new Set(),
  setSelectedDatabricksTables = () => { },
  databricksQuery = '',
  setDatabricksQuery = () => { },
  databricksQueryRegex = false,
  setDatabricksQueryRegex = () => { },
  savedModels = new Set(),
}) {

  if (sourceConnector === 'pbix') {
    if (pbixSourceMode === 'MANUAL') {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>PBIX Source Ready</h2>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
              PBIX sync skips model discovery. Continue to the finish step to launch the pipeline and monitor Extraction, OSI Conversion, SML Generation, and Snowflake Deployment.
            </p>
          </div>
          <div style={{ padding: '18px 20px', borderRadius: 12, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Upload the `.pbix` file in Connector Config, then use Start Sync after the project is created.
            </div>
          </div>
        </div>
      );
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select PBIX File</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            Choose a file from the tagged folder before continuing. The selected row will populate the final `pbix_file_path`.
          </p>
        </div>

        <div style={{ padding: '14px 16px', borderRadius: 12, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            Folder tag: <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>{selectedLocalFolderTag || 'Not selected'}</span>
          </div>
        </div>

        {pbixFilesError && (
          <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-error)30', background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
            {pbixFilesError}
          </div>
        )}

        <div className="custom-scrollbar" style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {pbixFilesLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering PBIX files…
            </div>
          ) : pbixFiles.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No `.pbix` files were found in the selected folder.
            </div>
          ) : pbixFiles.map(file => {
            const isSelected = selectedPbixFilePath === file.path;
            return (
              <div
                key={file.path}
                onClick={() => onSelectPbixFile(file.path)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '12px 14px',
                  cursor: 'pointer',
                  borderBottom: '1px solid var(--border-subtle)',
                  background: isSelected ? 'var(--accent-blue)0a' : 'transparent',
                }}
                onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
              >
                <input type="radio" checked={isSelected} readOnly style={{ accentColor: 'var(--accent-blue)' }} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{file.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3 }}>
                    {file.modified_at ? `Last modified ${new Date(file.modified_at).toLocaleString()}` : file.path}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (sourceConnector === 'fabric') {
    // Conditional rendering for Fabric step
    if (!selectedWorkspace) {
      return (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
          Choose a Fabric workspace in Connector Config before selecting models.
        </div>
      );
    }

    const fabricModels = wsModels[selectedWorkspace.id] || [];
    const displayModels = modelQuery
      ? fabricModels.filter(m =>
        matchesSmartQuery(
          `${m.name || ''} ${m.id || ''} ${m.description || ''}`,
          modelQuery,
          modelQueryRegex,
        )
      )
      : fabricModels.map(m => ({ ...m, _id: m.id }));

    // Show loader if loading
    if (wsLoading) {
      return (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
            <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
            Discovering Fabric semantic models…
          </div>
        </div>
      );
    }

    // Show models if available
    if (displayModels.length > 0) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Models</h2>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
              Choose which Fabric semantic models to include from {selectedWorkspace.name}. Leave all unchecked to include everything in this workspace.
            </p>
          </div>
          <SmartSearchBar
            value={modelQuery}
            onChange={setModelQuery}
            useRegex={modelQueryRegex}
            onToggleRegex={setModelQueryRegex}
            placeholder={`Search models in ${selectedWorkspace.name}`}
          />
          {selectedModels.size > 0 && (
            <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
              {selectedModels.size} model{selectedModels.size !== 1 ? 's' : ''} selected
              <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
                Clear
              </button>
            </div>
          )}
          <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
            {displayModels.map(m => {
              const modelId = m._id || m.id;
              return (
                <ModelRow
                  key={modelId}
                  model={{ ...m, _id: modelId }}
                  selected={selectedModels.has(modelId)}
                  isSaved={savedModels.has(modelId)}
                  onToggle={() => toggleModel(modelId, m.name || m.id)}
                />
              );
            })}
          </div>
        </div>
      );
    }

    // Show no models found placeholder
    return (
      <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
        <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
          No Fabric semantic models found. Check connector setup in Settings.
        </div>
      </div>
    );
  }

  if (sourceConnector === 'databricks') {
    // Flatten all tables for search
    const allTables = (databricksObjects || []).flatMap(obj =>
      (obj.tables || []).map(tbl => ({
        catalog: obj.catalog,
        schema: obj.schema,
        table: tbl,
        key: `${obj.catalog}.${obj.schema}.${tbl}`
      }))
    );
    const filteredTables = databricksQuery
      ? allTables.filter(t =>
        matchesSmartQuery(
          `${t.table || ''} ${t.schema || ''} ${t.catalog || ''}`,
          databricksQuery,
          databricksQueryRegex,
        )
      )
      : allTables;

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Databricks Tables</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            Choose which Databricks tables to include. Leave all unchecked to include everything.
          </p>
        </div>
        <SmartSearchBar
          value={databricksQuery}
          onChange={setDatabricksQuery}
          useRegex={databricksQueryRegex}
          onToggleRegex={setDatabricksQueryRegex}
          placeholder="Search Databricks tables"
        />
        {selectedDatabricksTables.size > 0 && (
          <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
            {selectedDatabricksTables.size} table{selectedDatabricksTables.size !== 1 ? 's' : ''} selected
            <button onClick={() => setSelectedDatabricksTables(new Set())} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
              Clear
            </button>
          </div>
        )}
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {databricksLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering Databricks tables…
            </div>
          ) : filteredTables.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No Databricks tables found. Check connector setup in Settings.
            </div>
          ) : (
            filteredTables.map(t => (
              <div
                key={t.key}
                onClick={() => {
                  setSelectedDatabricksTables(prev => {
                    const s = new Set(prev);
                    s.has(t.key) ? s.delete(t.key) : s.add(t.key);
                    return s;
                  });
                }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 12px', cursor: 'pointer', userSelect: 'none',
                  background: selectedDatabricksTables.has(t.key) ? 'var(--accent-blue)0a' : 'transparent',
                  borderBottom: '1px solid var(--border-subtle)'
                }}
                onMouseEnter={e => { if (!selectedDatabricksTables.has(t.key)) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                onMouseLeave={e => { if (!selectedDatabricksTables.has(t.key)) e.currentTarget.style.background = 'transparent'; }}
              >
                {selectedDatabricksTables.has(t.key)
                  ? <CheckSquare size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
                  : <Square size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
                <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{t.table}</span>
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>{t.catalog}.{t.schema}</span>
              </div>
            ))
          )}
        </div>
      </div>
    );
  }

  if (sourceConnector === 'fabric') {
    // Conditional rendering for Fabric step
    if (!selectedWorkspace) {
      return (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
          Choose a Fabric workspace in Connector Config before selecting models.
        </div>
      );
    }

    const fabricModels = wsModels[selectedWorkspace.id] || [];
    const displayModels = modelQuery
      ? fabricModels.filter(m =>
        matchesSmartQuery(
          `${m.name || ''} ${m.id || ''} ${m.description || ''}`,
          modelQuery,
          modelQueryRegex,
        )
      )
      : fabricModels.map(m => ({ ...m, _id: m.id }));

    // Show loader if loading
    if (wsLoading) {
      return (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
            <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
            Discovering Fabric semantic models…
          </div>
        </div>
      );
    }

    // Show models if available
    if (displayModels.length > 0) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Models</h2>
            <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
              Choose which Fabric semantic models to include from {selectedWorkspace.name}. Leave all unchecked to include everything in this workspace.
            </p>
          </div>
          <SmartSearchBar
            value={modelQuery}
            onChange={setModelQuery}
            useRegex={modelQueryRegex}
            onToggleRegex={setModelQueryRegex}
            placeholder={`Search models in ${selectedWorkspace.name}`}
          />
          {selectedModels.size > 0 && (
            <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
              {selectedModels.size} model{selectedModels.size !== 1 ? 's' : ''} selected
              <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
                Clear
              </button>
            </div>
          )}
          <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
            {displayModels.map(m => {
              const modelId = m._id || m.id;
              return (
                <ModelRow
                  key={modelId}
                  model={{ ...m, _id: modelId }}
                  selected={selectedModels.has(modelId)}
                  isSaved={savedModels.has(modelId) || (m.name && savedModels.has(m.name))}
                  onToggle={() => toggleModel(modelId, m.name || m.id)}
                />
              );
            })}
          </div>
        </div>
      );
    }

    // Show no models found placeholder
    return (
      <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
        <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
          No Fabric semantic models found. Check connector setup in Settings.
        </div>
      </div>
    );
  }

  if (sourceConnector === 'snowflake') {
    const snowflakeModels = wsModels.snowflake || [];
    const displayModels = modelQuery ? snowflakeResults : snowflakeModels.map(m => ({ ...m, _id: m.id }));

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Sources</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            Choose which Snowflake semantic objects to include. Leave all unchecked to include everything.
          </p>
        </div>

        <SmartSearchBar
          value={modelQuery}
          onChange={setModelQuery}
          useRegex={modelQueryRegex}
          onToggleRegex={setModelQueryRegex}
          placeholder="Search Snowflake semantic objects"
        />

        {selectedModels.size > 0 && (
          <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
            {selectedModels.size} object{selectedModels.size !== 1 ? 's' : ''} selected
            <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
              Clear
            </button>
          </div>
        )}

        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {wsLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering Snowflake semantic objects…
            </div>
          ) : displayModels.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No Snowflake semantic objects found. Check connector setup in Settings.
            </div>
          ) : (
            displayModels.map(m => {
              const modelId = m._id || m.id;
              return (
                <ModelRow
                  key={modelId}
                  model={{ ...m, _id: modelId }}
                  selected={selectedModels.has(modelId)}
                  isSaved={savedModels.has(modelId) || (m.name && savedModels.has(m.name))}
                  onToggle={() => toggleModel(modelId, m.name || m.id)}
                />
              );
            })
          )}
        </div>
      </div>
    );
  }

  const displayModels = modelQuery ? modelResults : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Select Models</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Choose which Fabric semantic models to include from {selectedWorkspace.name}. Leave all unchecked to include everything in this workspace.
        </p>
      </div>

      {/* Search */}
      <SmartSearchBar
        value={modelQuery}
        onChange={setModelQuery}
        useRegex={modelQueryRegex}
        onToggleRegex={setModelQueryRegex}
        placeholder={`Search models in ${selectedWorkspace.name}`}
      />

      {selectedModels.size > 0 && (
        <div style={{ fontSize: 11, color: 'var(--accent-blue)', padding: '4px 0' }}>
          {selectedModels.size} model{selectedModels.size !== 1 ? 's' : ''} selected
          <button onClick={clearSelectedModels} style={{ marginLeft: 8, background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', fontSize: 11 }}>
            Clear
          </button>
        </div>
      )}

      {/* Flat search results */}
      {displayModels && (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8 }}>
          {displayModels.map(m => (
            <ModelRow key={m._id} model={m} selected={selectedModels.has(m._id)} isSaved={savedModels.has(m._id)} onToggle={() => toggleModel(m._id, m.name || m.id)} showWs />
          ))}
        </div>
      )}

      {/* Tree view when no search */}
      {!displayModels && (
        <div className="custom-scrollbar" style={{ maxHeight: 400, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
          {wsLoading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', margin: '0 auto 8px', display: 'block' }} />
              Discovering workspaces…
            </div>
          ) : workspaces.length === 0 ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>
              No workspaces found. Check your Fabric connector credentials in Settings.
            </div>
          ) : workspaces.map((ws) => {
            const wsid = ws.workspace_id || ws.id;
            const wsName = ws.display_name || ws.name || wsid;
            if (!wsid) return null;
            return (
              <WorkspaceRow
                key={wsid}
                ws={{ ...ws, id: wsid, name: wsName }}
                expanded={!!expandedWs[wsid]}
                models={wsModels[wsid]}
                selectedModels={selectedModels}
                savedModels={savedModels}
                onToggle={() => toggleWorkspace(wsid)}
                onModelToggle={(mid, modelName) => toggleModel(`${wsid}::${mid}`, modelName)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

function WorkspaceRow({ ws, expanded, models, selectedModels, savedModels, onToggle, onModelToggle }) {
  return (
    <div>
      <div
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '9px 12px', cursor: 'pointer', userSelect: 'none',
          borderBottom: '1px solid var(--border-subtle)',
          background: expanded ? 'var(--bg-surface-raised)' : 'transparent',
        }}
        onMouseEnter={e => { if (!expanded) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
        onMouseLeave={e => { if (!expanded) e.currentTarget.style.background = 'transparent'; }}
      >
        {expanded ? <ChevronDown size={13} style={{ color: 'var(--text-tertiary)' }} /> : <ChevronRight size={13} style={{ color: 'var(--text-tertiary)' }} />}
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{ws.name || ws.id}</span>
        {models && (
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', marginLeft: 4 }}>({models.length} models)</span>
        )}
      </div>
      {expanded && models && (
        <div className="custom-scrollbar" style={{ maxHeight: 250, overflowY: 'auto' }}>
          {models.map(m => (
            <ModelRow
              key={m.id}
              model={{ ...m, _id: `${ws.id}::${m.id}` }}
              selected={selectedModels.has(`${ws.id}::${m.id}`)}
              isSaved={savedModels?.has(`${ws.id}::${m.id}`)}
              onToggle={() => onModelToggle(m.id, m.name || m.id)}
              indent
            />
          ))}
        </div>
      )}
      {expanded && !models && (
        <div style={{ padding: '8px 32px', fontSize: 12, color: 'var(--text-tertiary)' }}>
          <Loader2 size={12} style={{ animation: 'spin 1s linear infinite', display: 'inline', marginRight: 6 }} />
          Loading models…
        </div>
      )}
    </div>
  );
}

function ModelRow({ model, selected, onToggle, indent, showWs, isSaved }) {
  return (
    <div
      onClick={onToggle}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: `7px ${indent ? 32 : 12}px`,
        cursor: 'pointer', userSelect: 'none',
        background: selected ? 'var(--accent-blue)0a' : 'transparent',
        borderBottom: '1px solid var(--border-subtle)',
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
    >
      {selected
        ? <CheckSquare size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
        : <Square size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
      <span style={{ fontSize: 12, color: 'var(--text-primary)' }}>{model.name || model.id}</span>
      {isSaved && (
        <span style={{ fontSize: 9, fontWeight: 700, color: 'var(--text-secondary)', background: 'var(--border-main)40', padding: '2px 6px', borderRadius: 4, textTransform: 'uppercase', marginLeft: 4 }}>Saved</span>
      )}
      {showWs && <span style={{ fontSize: 10, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>{model.wsid}</span>}
    </div>
  );
}

/* ─── Step 4: Mapping Options ─── */
function StepMappingOptionsOld({
  autoRelationships,
  setAutoRelationships,
  generateDescriptions,
  setGenerateDescriptions,
  detectedMappings,
  mappingLoading,
  mappingError,
  dryRunStatus,
  dryRunError,
  unmappedAcknowledged,
  setUnmappedAcknowledged,
  selectedModelNames,
  onUpdateTableTarget,
  onUpdateColumnTarget,
  onRunDryRun,
  onClearMappings,
  onProceedStateChange,
  primaryTargetConnector,
}) {
  const [autoMappingMode, setAutoMappingMode] = useState(true);
  const [expandedMapping, setExpandedMapping] = useState(null);
  const [mappingSearch, setMappingSearch] = useState('');
  const [activeFieldFilter, setActiveFieldFilter] = useState('all');
  const [showOnlyCollisions, setShowOnlyCollisions] = useState(false);
  const [showOnlyEdited, setShowOnlyEdited] = useState(false);
  const [showOnlyExpandedColumns, setShowOnlyExpandedColumns] = useState(true);
  const dryRunCompleted = dryRunStatus === 'success';
  const dryRunFailed = dryRunStatus === 'failed';

  // Get relationships from session storage (read-only)
  const detectedRelationships = (() => {
    try {
      const stored = sessionStorage.getItem('detectedRelationships');
      return stored ? JSON.parse(stored) : [];
    } catch {
      return [];
    }
  })();

  const explicitTables = useMemo(() => {
    return Array.from(new Set((selectedModelNames || []).map(name => String(name || '').trim()).filter(Boolean)));
  }, [selectedModelNames]);

  const inferredTables = useMemo(() => {
    const explicitUpper = new Set(explicitTables.map(name => name.toUpperCase()));
    return Array.from(new Set(
      (detectedMappings || [])
        .map(mapping => String(mapping?.source || '').trim())
        .filter(Boolean)
        .filter(name => !explicitUpper.has(name.toUpperCase()))
    ));
  }, [detectedMappings, explicitTables]);

  const targetCollisionMap = useMemo(() => {
    const byTarget = new Map();
    (detectedMappings || []).forEach((mapping) => {
      (mapping?.columns || []).forEach((column) => {
        const key = sanitizeMappingName(column?.target);
        if (!key) return;
        byTarget.set(key, [...(byTarget.get(key) || []), `${mapping.id}:${column.source}`]);
      });
    });
    return byTarget;
  }, [detectedMappings]);

  const getColumnStatus = useCallback((mapping, column) => {
    const targetKey = sanitizeMappingName(column?.target);
    const targetCount = targetKey ? (targetCollisionMap.get(targetKey) || []).length : 0;
    if (column?.collision_detected || targetCount > 1) return 'collision';
    if (!String(column?.target || '').trim()) return 'unmapped';
    const explicit = String(column?.status || '').toLowerCase();
    if (explicit === 'manual' || column?.auto_resolved) return 'manual';
    return String(mapping?.status || '').toLowerCase() === 'manual' ? 'manual' : 'auto';
  }, [targetCollisionMap]);

  const fieldRows = useMemo(() => {
    const rows = [];
    (detectedMappings || []).forEach((mapping) => {
      (mapping?.columns || []).forEach((column, index) => {
        const status = getColumnStatus(mapping, column);
        rows.push({
          id: `${mapping.id || mapping.source}:${column.source || index}`,
          mappingId: mapping.id,
          mapping,
          column,
          status,
          sourceTable: resolveSourceTableName(column, mapping.source),
        });
      });
    });
    return rows;
  }, [detectedMappings, getColumnStatus]);

  const fieldCounts = useMemo(() => {
    const counts = { all: fieldRows.length, auto: 0, manual: 0, unmapped: 0, collision: 0 };
    fieldRows.forEach((row) => {
      if (counts[row.status] !== undefined) counts[row.status] += 1;
    });
    return counts;
  }, [fieldRows]);

  const collisionCount = fieldCounts.collision;
  const unmappedCount = fieldCounts.unmapped;
  const editedCount = fieldCounts.manual;

  const manualEditLocked = !dryRunCompleted;

  useEffect(() => {
    const ready = dryRunCompleted && collisionCount === 0 && (unmappedCount === 0 || unmappedAcknowledged);
    onProceedStateChange?.(ready);
  }, [collisionCount, dryRunCompleted, onProceedStateChange, unmappedAcknowledged, unmappedCount]);

  const toggleAutoMappingMode = useCallback(() => {
    setExpandedMapping(null);
    setAutoMappingMode((prev) => {
      const next = !prev;
      if (!next) {
        onClearMappings?.();
      }
      return next;
    });
  }, [onClearMappings]);

  const runDryRun = useCallback(async () => {
    await onRunDryRun?.();
  }, [onRunDryRun]);

  const filteredMappings = useMemo(() => {
    const query = String(mappingSearch || '').trim();

    return (detectedMappings || []).filter((mapping) => {
      const mappingText = [
        mapping?.source,
        mapping?.target,
        ...(Array.isArray(mapping?.columns)
          ? mapping.columns.flatMap((column) => [column?.source, column?.target, column?.type])
          : []),
      ]
        .filter(Boolean)
        .join(' ');

      if (query && !matchesSmartQuery(mappingText, query)) return false;
      if (showOnlyCollisions) {
        const hasCollision = Boolean(mapping?.collision_detected)
          || (mapping?.columns || []).some((column) => getColumnStatus(mapping, column) === 'collision');
        if (!hasCollision) return false;
      }
      if (showOnlyEdited && !(mapping?.columns || []).some((column) => getColumnStatus(mapping, column) === 'manual')) {
        return false;
      }
      if (activeFieldFilter !== 'all') {
        const hasStatus = (mapping?.columns || []).some((column) => getColumnStatus(mapping, column) === activeFieldFilter);
        if (!hasStatus) return false;
      }
      return true;
    });
  }, [activeFieldFilter, detectedMappings, getColumnStatus, mappingSearch, showOnlyCollisions, showOnlyEdited]);

  const filterButtonStyle = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    padding: '8px 12px',
    borderRadius: 8,
    border: '1px solid var(--border-main)',
    background: 'transparent',
    color: 'var(--text-secondary)',
    fontSize: 12,
    fontWeight: 600,
    cursor: 'pointer',
    minWidth: 0,
  };



  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Mapping Options & Verification</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Review detected mappings and relationships before proceeding. These will transform your source model.
        </p>
      </div>

      <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)', display: 'grid', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)' }}>Auto-Detect Mapping</div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              Switch off to enter manual override mode. Manual mode requires a dry run before edits are allowed.
            </div>
          </div>
          <button
            type="button"
            onClick={toggleAutoMappingMode}
            style={{
              ...filterButtonStyle,
              minWidth: 180,
              background: autoMappingMode ? 'var(--color-success-bg)' : 'rgba(245, 158, 11, 0.14)',
              color: autoMappingMode ? 'var(--color-success)' : 'var(--accent-orange)',
              border: autoMappingMode ? '1px solid rgba(34, 197, 94, 0.35)' : '1px solid rgba(245, 158, 11, 0.35)',
            }}
          >
            {autoMappingMode ? 'AUTO MODE ON' : 'MANUAL MODE'}
          </button>
        </div>

        {!autoMappingMode && (
          <div style={{ borderRadius: 8, border: '1px dashed rgba(245, 158, 11, 0.35)', padding: 12, background: 'rgba(245, 158, 11, 0.08)', fontSize: 12, color: 'var(--accent-orange)' }}>
            Manual mode active. All mappings must be set explicitly.
          </div>
        )}
      </div>

      {!dryRunCompleted && (
        <div style={{ borderRadius: 10, border: dryRunFailed ? '1px solid rgba(239, 68, 68, 0.45)' : '1px solid var(--border-main)', padding: 18, background: dryRunFailed ? 'rgba(239, 68, 68, 0.08)' : 'var(--bg-surface)', display: 'grid', gap: 14 }}>
          <div>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 6px' }}>
              Dry Run Required
            </h3>
            <p style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.6, margin: 0 }}>
              A dry run is required before you can view and edit field-level mappings. This validates your connector config and selected sources without writing any data.
            </p>
          </div>
          {mappingLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--accent-blue)' }}>
              <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
              Running dry run validation...
            </div>
          )}
          {dryRunFailed && (
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: 12, color: 'var(--color-error)', lineHeight: 1.5 }}>
              <AlertTriangle size={14} style={{ marginTop: 1, flexShrink: 0 }} />
              <span>{dryRunError || mappingError || 'Dry run failed. Review connector settings and selected sources, then retry.'}</span>
            </div>
          )}
          <button
            type="button"
            onClick={runDryRun}
            disabled={mappingLoading}
            style={{
              ...filterButtonStyle,
              width: 'fit-content',
              background: 'var(--accent-blue)',
              color: '#fff',
              border: '1px solid var(--accent-blue)',
              opacity: mappingLoading ? 0.7 : 1,
              cursor: mappingLoading ? 'not-allowed' : 'pointer',
            }}
          >
            {mappingLoading ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={13} />}
            {dryRunFailed ? 'Retry Dry Run' : 'Run Dry Run'}
          </button>
        </div>
      )}

      {!dryRunCompleted ? null : (
        <>

          {/* TABLE MAPPINGS SECTION */}
          <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
            <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 10px' }}>
              Scope Verification
            </h3>
            <p style={{ fontSize: 11, color: 'var(--text-tertiary)', margin: '0 0 10px' }}>
              Extraction will only include explicitly selected models/tables and will always block internal/system patterns.
            </p>
            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                <strong>Explicitly selected:</strong> {explicitTables.length > 0 ? explicitTables.join(', ') : 'None'}
              </div>
              <div style={{ fontSize: 11, color: inferredTables.length > 0 ? 'var(--accent-orange)' : 'var(--text-secondary)' }}>
                <strong>Inferred from mapping:</strong> {inferredTables.length > 0 ? inferredTables.join(', ') : 'None'}
              </div>
            </div>
          </div>

          {mappingLoading && (
            <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)', fontSize: 12, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
              Generating mappings from backend...
            </div>
          )}

          {mappingError && !mappingLoading && (
            <div style={{ borderRadius: 10, border: '1px solid var(--color-error)', padding: 16, background: 'var(--color-error-bg)', fontSize: 12, color: 'var(--text-primary)' }}>
              {mappingError}
            </div>
          )}

          {/* Modern High-Density Summary Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 16 }}>
            <div style={{ 
              background: 'var(--bg-surface-raised)', 
              borderRadius: 12, 
              border: '1px solid var(--border-main)', 
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
              position: 'relative',
              overflow: 'hidden'
            }}>
              <div style={{ position: 'absolute', top: -10, right: -10, opacity: 0.05 }}>
                <Database size={80} />
              </div>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Source Identity
              </div>
              <div>
                <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 4 }}>
                  {explicitTables[0] || 'Selected Sources'}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
                  <Cloud size={14} style={{ color: 'var(--accent-blue)' }} />
                  {sourceConnector.toUpperCase()} Connector
                </div>
              </div>
              <div style={{ height: 1, background: 'var(--border-main)', margin: '4px 0' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Total Fields</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{fieldCounts.all}</span>
              </div>
            </div>

            <div style={{ 
              background: 'var(--bg-surface-raised)', 
              borderRadius: 12, 
              border: '1px solid var(--border-main)', 
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
              position: 'relative',
              overflow: 'hidden'
            }}>
              <div style={{ position: 'absolute', top: -10, right: -10, opacity: 0.05 }}>
                <Snowflake size={80} />
              </div>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Target Architecture
              </div>
              <div>
                <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 4 }}>
                  {primaryTargetConnector.toUpperCase() || 'Target System'}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
                  <Zap size={14} style={{ color: 'var(--accent-orange)' }} />
                  Verification Strategy: OSI Strict
                </div>
              </div>
              <div style={{ height: 1, background: 'var(--border-main)', margin: '4px 0' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Mapped Fields</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--accent-blue)' }}>
                  {fieldRows.filter(row => String(row.column?.target || '').trim()).length}
                </span>
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            {MAPPING_FILTERS.map((filter) => {
              const count = fieldCounts[filter.id] ?? fieldCounts.all;
              const active = activeFieldFilter === filter.id;
              const isCollision = filter.id === 'collision' && count > 0;
              return (
                <button
                  key={filter.id}
                  type="button"
                  onClick={() => {
                    setActiveFieldFilter(filter.id);
                    if (filter.id === 'collision') setShowOnlyCollisions(count > 0);
                    else setShowOnlyCollisions(false);
                  }}
                  style={{
                    ...filterButtonStyle,
                    borderRadius: 999,
                    background: active ? (isCollision ? 'rgba(239, 68, 68, 0.16)' : 'var(--accent-blue)20') : 'var(--bg-surface-raised)',
                    color: isCollision ? 'var(--color-error)' : (active ? 'var(--accent-blue)' : 'var(--text-primary)'),
                    border: isCollision ? '1px solid rgba(239, 68, 68, 0.45)' : filterButtonStyle.border,
                  }}
                >
                  {filter.label} ({count})
                </button>
              );
            })}
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <div style={{ width: 240 }}>
                <SmartSearchBar
                  value={mappingSearch}
                  onChange={setMappingSearch}
                  placeholder="Search fields..."
                />
              </div>
              <button type="button" onClick={runDryRun} disabled={mappingLoading} style={{ ...filterButtonStyle }}>
                {mappingLoading ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={13} />}
                Run Auto-Map
              </button>
            </div>
          </div>

          {detectedMappings.length > 0 && !mappingLoading && (
            <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 12 }}>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: 0, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <Table2 size={16} color="var(--accent-blue)" />
                    Table Mappings
                  </span>
                  <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 4 }}>
                    {filteredMappings.length} visible
                  </span>
                  <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-secondary)', background: 'var(--border-main)20', padding: '2px 8px', borderRadius: 4 }}>
                    {detectedMappings.length} total
                  </span>
                  {editedCount > 0 && (
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-success)', background: 'var(--color-success-bg)', padding: '2px 8px', borderRadius: 4 }}>
                      {editedCount} edited
                    </span>
                  )}
                  {collisionCount > 0 && (
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent-orange)', background: 'rgba(245, 158, 11, 0.14)', padding: '2px 8px', borderRadius: 4 }}>
                      {collisionCount} collisions auto-resolved
                    </span>
                  )}
                </h3>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10, alignItems: 'center' }}>
                  <SmartSearchBar
                    value={mappingSearch}
                    onChange={setMappingSearch}
                    placeholder="Search tables, columns, targets..."
                  />
                  <button
                    type="button"
                    onClick={() => setShowOnlyCollisions((prev) => !prev)}
                    style={{
                      ...filterButtonStyle,
                      background: showOnlyCollisions ? 'rgba(245, 158, 11, 0.14)' : 'transparent',
                      color: showOnlyCollisions ? 'var(--accent-orange)' : 'var(--text-secondary)',
                      border: showOnlyCollisions ? '1px solid rgba(245, 158, 11, 0.35)' : filterButtonStyle.border,
                    }}
                  >
                    Only Collisions
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowOnlyEdited((prev) => !prev)}
                    style={{
                      ...filterButtonStyle,
                      background: showOnlyEdited ? 'var(--color-success-bg)' : 'transparent',
                      color: showOnlyEdited ? 'var(--color-success)' : 'var(--text-secondary)',
                      border: showOnlyEdited ? '1px solid rgba(34, 197, 94, 0.35)' : filterButtonStyle.border,
                    }}
                  >
                    Only Edited
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowOnlyExpandedColumns((prev) => !prev)}
                    style={filterButtonStyle}
                  >
                    {showOnlyExpandedColumns ? 'Compact Columns' : 'Show All Columns'}
                  </button>
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {filteredMappings.map(mapping => (
                  <div key={mapping.id}>
                    <button
                      onClick={() => setExpandedMapping(expandedMapping === mapping.id ? null : mapping.id)}
                      style={{
                        width: '100%', textAlign: 'left',
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: '12px 14px', borderRadius: 8, cursor: 'pointer',
                        background: 'var(--bg-main)', border: '1.5px solid var(--border-main)',
                        color: 'var(--text-primary)', transition: 'all 0.2s ease',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-blue)40'; }}
                      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-main)'; }}
                    >
                      <div style={{ flex: 1, minWidth: 180 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>
                          {mapping.source}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap', overflowWrap: 'anywhere' }}>
                          <span>→</span> {mapping.target}
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                        <div style={{ fontSize: 10, color: 'var(--text-tertiary)', background: 'var(--border-main)20', padding: '3px 8px', borderRadius: 4 }}>
                          {(mapping.columns || []).length} columns
                        </div>
                        {(mapping.collision_detected || (mapping.columns || []).some(column => getColumnStatus(mapping, column) === 'collision')) && (
                          <div
                            style={{
                              padding: '3px 10px', borderRadius: 4,
                              background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-error)',
                              fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
                            }}
                          >
                            Collision
                          </div>
                        )}
                        <div
                          style={{
                            padding: '3px 10px', borderRadius: 4,
                            background: 'var(--accent-blue)20', color: 'var(--accent-blue)',
                            fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
                          }}
                        >
                          {mapping.status}
                        </div>
                        <ChevronDown
                          size={14} color="var(--text-tertiary)"
                          style={{ transform: expandedMapping === mapping.id ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                        />
                      </div>
                    </button>

                    {/* Column Details */}
                    {expandedMapping === mapping.id && mapping.columns && (
                      <div style={{
                        marginTop: 8, padding: '12px', borderRadius: 6,
                        background: 'var(--bg-main)', border: '1px solid var(--accent-blue)20',
                      }}>
                        {mapping.collision_detected && (
                          <div style={{ marginBottom: 10, fontSize: 11, color: 'var(--accent-orange)', lineHeight: 1.4 }}>
                            Destination table name collided after sanitization, so a deterministic hash suffix was added automatically.
                          </div>
                        )}
                        <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 8, textTransform: 'uppercase' }}>
                          Column Mappings
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
                          <label style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Destination table name</label>
                          <input
                            value={mapping.target || ''}
                            disabled={manualEditLocked}
                            onChange={(e) => onUpdateTableTarget?.(mapping.id, e.target.value)}
                            style={{ ...INPUT, fontSize: 11, padding: '6px 8px', maxWidth: 320, opacity: manualEditLocked ? 0.6 : 1 }}
                          />
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          {(showOnlyExpandedColumns ? mapping.columns : mapping.columns.slice(0, 6)).map((col, idx) => (
                            <div key={idx} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 1fr) auto minmax(180px, 1.3fr)', gap: 8, alignItems: 'center', fontSize: 11 }}>
                              <span style={{ color: 'var(--text-secondary)', fontWeight: 500, overflowWrap: 'anywhere' }}>{col.source}</span>
                              <span style={{ color: 'var(--text-tertiary)', textAlign: 'center' }}>→</span>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                <input
                                  value={col.target || ''}
                                  disabled={manualEditLocked}
                                  onChange={(e) => onUpdateColumnTarget?.(mapping.id, col.source, e.target.value)}
                                  style={{
                                    ...INPUT,
                                    fontSize: 11,
                                    padding: '4px 8px',
                                    minWidth: 140,
                                    flex: '1 1 180px',
                                    opacity: manualEditLocked ? 0.6 : 1,
                                    background: col.auto_resolved ? 'rgba(245, 158, 11, 0.12)' : INPUT.background,
                                    border: getColumnStatus(mapping, col) === 'collision'
                                      ? '1px solid var(--color-error)'
                                      : (col.auto_resolved ? '1px solid rgba(245, 158, 11, 0.55)' : INPUT.border),
                                  }}
                                />
                                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', background: 'var(--border-main)20', padding: '1px 6px', borderRadius: 3 }}>
                                  {col.type}
                                </span>
                                {col.collision_detected && <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--accent-orange)' }}>COLLISION</span>}
                                {col.auto_resolved && <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--accent-orange)' }}>AUTO-RESOLVED</span>}
                                {col.key && <span style={{ fontSize: 9, fontWeight: 600, color: 'var(--accent-blue)' }}>🔑 PRIMARY KEY</span>}
                              </div>
                            </div>
                          ))}
                          {!showOnlyExpandedColumns && (mapping.columns || []).length > 6 && (
                            <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                              +{mapping.columns.length - 6} more columns. Switch to "Show All Columns" when you need the full list.
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
              {filteredMappings.length === 0 && (
                <div style={{ padding: 16, borderRadius: 8, background: 'var(--bg-main)', border: '1px dashed var(--border-main)', fontSize: 12, color: 'var(--text-tertiary)' }}>
                  No mappings match the current filters. Clear search or toggles to see more results.
                </div>
              )}
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 12, marginBottom: 0 }}>
                ℹ️ These mappings were automatically detected from your selected models. Review each mapping to ensure accuracy before proceeding.
              </p>
            </div>
          )}



          {/* RELATIONSHIPS SECTION */}
          {detectedRelationships.length > 0 && autoRelationships && (
            <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>Detected Relationships</span>
                <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 4 }}>
                  {detectedRelationships.length} relationships
                </span>
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {detectedRelationships.map(rel => (
                  <div
                    key={rel.id}
                    style={{
                      padding: '12px 14px', borderRadius: 8,
                      background: 'var(--bg-main)', border: '1px solid var(--border-main)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {rel.source}
                      </div>
                      <span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>{rel.joinType}</span>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {rel.target}
                      </div>
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace', padding: '8px 10px', borderRadius: 4, background: 'var(--bg-surface)', marginBottom: 6 }}>
                      {rel.condition}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 8px', borderRadius: 3, textTransform: 'uppercase' }}>
                        {rel.confidence} confidence
                      </span>
                    </div>
                  </div>
                ))}
              </div>
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 12, marginBottom: 0 }}>
                ℹ️ Relationships were inferred from foreign keys and naming conventions. Enable "Auto-detect Relationships" toggle below to use them during mapping.
              </p>
            </div>
          )}
        </>
      )}

      {/* OPTIONS */}
      <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 16, background: 'var(--bg-surface)' }}>
        <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', margin: '0 0 12px' }}>Transformation Options</h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <ToggleOption
            label="Auto-detect Relationships"
            description="Automatically infer joins and relationships from foreign keys and naming conventions."
            checked={autoRelationships}
            onChange={setAutoRelationships}
          />
          <ToggleOption
            label="Generate AI Descriptions"
            description="Use the LLM to auto-generate descriptions for tables and fields during sync."
            checked={generateDescriptions}
            onChange={setGenerateDescriptions}
          />
        </div>
      </div>

      {dryRunCompleted && unmappedCount > 0 && (
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: 12, borderRadius: 8, background: 'rgba(245, 158, 11, 0.10)', border: '1px solid rgba(245, 158, 11, 0.35)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={unmappedAcknowledged}
            onChange={(e) => setUnmappedAcknowledged?.(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <span style={{ fontSize: 12, color: 'var(--accent-orange)', lineHeight: 1.5 }}>
            Acknowledge {unmappedCount} unmapped field{unmappedCount === 1 ? '' : 's'} as intentional.
          </span>
        </label>
      )}

      <div style={{ padding: 12, borderRadius: 8, background: 'var(--accent-blue)08', border: '1px solid var(--accent-blue)20' }}>
        <p style={{ fontSize: 11, color: 'var(--accent-blue)', margin: 0, lineHeight: 1.6 }}>
          {dryRunCompleted && collisionCount === 0 && (unmappedCount === 0 || unmappedAcknowledged)
            ? 'Ready to proceed: dry run is complete and mapping validation is clear. Click Continue to move to the next step.'
            : `Action required: ${dryRunCompleted ? `resolve ${collisionCount} collision/error row(s) and ${unmappedCount} unmapped row(s).` : 'run dry run before continuing.'}`}
        </p>
      </div>
    </div>
  );
}

function ToggleOption({ label, description, checked, onChange }) {
  return (
    <div
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 12,
        padding: '14px 16px', borderRadius: 10, cursor: 'pointer',
        background: checked ? 'var(--accent-blue)08' : 'var(--bg-surface)',
        border: checked ? '1.5px solid var(--accent-blue)40' : '1px solid var(--border-main)',
      }}
      onClick={() => onChange(v => !v)}
    >
      <div
        style={{
          width: 40, height: 22, borderRadius: 11, flexShrink: 0, marginTop: 2,
          background: checked ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
          position: 'relative', transition: 'background 0.2s',
        }}
      >
        <div style={{
          position: 'absolute', top: 3, left: checked ? 20 : 3,
          width: 16, height: 16, borderRadius: '50%',
          background: '#fff', transition: 'left 0.2s',
          boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
        }} />
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>{description}</div>
      </div>
    </div>
  );
}

/* ─── Step 5: Finish ─── */
function StepFinish({
  name,
  saving,
  createReverseProject,
  setCreateReverseProject,
  createdProject,
  createError,
  runWarning,
  sourceConnector,
  targetConnectors,
  intermediateFormat,
  selectedWorkspace,
  navigate,
  editMode,
  diff,
  selectedModels,
  initialSelectedModels,
  selectedModelNameByKey,
  onClear,
}) {
  const createdProjectId = createdProject?.id || createdProject?.project_id;

  if (saving && !createdProject) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0' }}>
        <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'var(--accent-blue)20', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
          <Loader2 size={28} style={{ color: 'var(--accent-blue)', animation: 'spin 1s linear infinite' }} />
        </div>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>{editMode ? 'Saving Changes' : 'Creating Project'}</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 24 }}>
          {editMode ? `Updating configuration for "${name}"...` : `Setting up configurations and initializing "${name}"...`}
        </p>
      </div>
    );
  }

  if (createdProject) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '20px 0' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ width: 56, height: 56, borderRadius: '50%', background: 'var(--color-success)20', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 16px' }}>
            <Check size={28} style={{ color: 'var(--color-success)' }} />
          </div>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>{editMode ? 'Project Updated!' : 'Project Created!'}</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', marginBottom: 24 }}>
            {editMode ? `Project "${name}" has been updated successfully. You can now review your configuration or start your first sync.` : `Project "${name}" has been created successfully. You can now review your configuration or start your first sync.`}
          </p>
        </div>
        {runWarning && (
          <div style={{ maxWidth: 640, padding: '12px 14px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.5, textAlign: 'left' }}>
            {runWarning}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
          <button onClick={() => { onClear?.(); navigate('/projects'); }} style={footerBtn('secondary')}>Back to Projects</button>
          {createdProjectId ? (
            <button onClick={() => navigate(`/projects/${createdProjectId}/config`)} style={footerBtn('primary')}>
              Go to Config &amp; Sync
            </button>
          ) : (
            <button disabled style={{ ...footerBtn('secondary'), opacity: 0.6, cursor: 'not-allowed' }}>
              Configure Project (ID unavailable)
            </button>
          )}
        </div>
      </div>
    );
  }

  const renderDiff = (key, label) => {
    if (!diff || !diff[key]) return null;
    return (
      <div style={{ padding: '12px 14px', borderRadius: 8, background: 'var(--bg-surface-raised)', border: '1px solid var(--accent-blue)40', marginBottom: 12 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--accent-blue)', textTransform: 'uppercase', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Settings2 size={12} /> Edited: {label}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 2 }}>Original</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', textDecoration: 'line-through' }}>{diff[key].old || <span style={{ fontStyle: 'italic', opacity: 0.5 }}>Empty</span>}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 2 }}>New</div>
            <div style={{ fontSize: 12, color: 'var(--text-primary)', fontWeight: 500 }}>{diff[key].new || <span style={{ fontStyle: 'italic', opacity: 0.5 }}>Empty</span>}</div>
          </div>
        </div>
      </div>
    );
  };

  const showDiffs = editMode && diff && Object.keys(diff).length > 0;

  const currentSelectionNames = selectedModels?.size > 0
    ? Array.from(selectedModels).map(id => selectedModelNameByKey?.[id] || id.split('::').pop()).join(', ')
    : 'Everything (*)';

  const wasSelectionNames = initialSelectedModels?.size > 0
    ? Array.from(initialSelectedModels).map(id => selectedModelNameByKey?.[id] || id.split('::').pop()).join(', ')
    : 'Everything (*)';

  const modelsChanged = editMode && currentSelectionNames !== wasSelectionNames;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>
          {editMode ? 'Review Changes' : 'Ready to Create'}
        </h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          {editMode ? 'Review your changes and save the project configuration.' : 'Review your choices and create the project.'}
        </p>
      </div>

      {showDiffs && (
        <div style={{ padding: '16px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12, textTransform: 'uppercase' }}>Configuration Changes</div>
          {renderDiff('name', 'Project Name')}
          {renderDiff('description', 'Description')}
          {renderDiff('tags', 'Tags')}
          {renderDiff('sourceConnector', 'Source Connector')}
          {renderDiff('targetConnectors', 'Target Connector(s)')}
        </div>
      )}

      {/* The beautiful summary layout */}
      <div style={{ padding: '16px', borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', display: 'grid', gap: 16 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Project Name</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{name}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Source</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{sourceConnector}</div>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Intermediate Format</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{intermediateFormat}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Workspace</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{selectedWorkspace?.name || 'My workspace'}</div>
          </div>
        </div>
        <div>
          <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>Selected Models</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{currentSelectionNames}</span>
              {!modelsChanged && editMode && (
                <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent-blue)', background: 'var(--accent-blue)20', padding: '2px 6px', borderRadius: 4, textTransform: 'uppercase' }}>Saved</span>
              )}
            </div>
            {modelsChanged && (
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textDecoration: 'line-through' }}>
                Was: {wasSelectionNames}
              </div>
            )}
          </div>
        </div>
      </div>

      {createError && (
        <div style={{ padding: '12px 14px', borderRadius: 10, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.28)', color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.5 }}>
          {createError}
        </div>
      )}

      {!editMode && (
        <ToggleOption
          label="Create Reverse Project"
          description={`Also create ${name || 'the project'}_${Array.from(targetConnectors)[0] || 'target'}_to_${sourceConnector} using reversed source/target roles.`}
          checked={createReverseProject}
          onChange={setCreateReverseProject}
        />
      )}
    </div>
  );
}

function footerBtn(variant) {
  return {
    padding: '9px 20px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    background: variant === 'primary' ? 'var(--accent-blue)' : 'transparent',
    color: variant === 'primary' ? '#fff' : 'var(--text-secondary)',
    border: variant === 'primary' ? 'none' : '1px solid var(--border-main)',
  };
}

function escapeYamlString(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
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

function DryRunCTA({ mappingLoading, dryRunFailed, dryRunError, mappingError, onRunDryRun }) {
  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: '34px 18px', display: 'grid', justifyItems: 'center', gap: 14, textAlign: 'center' }}>
      <div style={{ width: 44, height: 44, borderRadius: 12, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--accent-blue)18', color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)30' }}>
        <Play size={20} />
      </div>
      <div style={{ display: 'grid', gap: 5 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Run a dry run to generate field-level mappings.</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.55, maxWidth: 460 }}>
          A dry run validates your connector config and selected sources without writing any data.
        </div>
      </div>
      {dryRunFailed && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--color-error)', lineHeight: 1.5 }}>
          <AlertTriangle size={13} style={{ color: 'var(--color-error)' }} />
          <span>{dryRunError || mappingError || 'Dry run failed. Review connector settings and selected sources, then retry.'}</span>
        </div>
      )}
      <button
        type="button"
        onClick={onRunDryRun}
        disabled={mappingLoading}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 7,
          padding: '9px 14px',
          borderRadius: 8,
          background: 'var(--accent-blue)',
          color: '#fff',
          border: '1px solid var(--accent-blue)',
          fontSize: 12,
          fontWeight: 700,
          cursor: mappingLoading ? 'not-allowed' : 'pointer',
          opacity: mappingLoading ? 0.7 : 1,
        }}
      >
        {mappingLoading ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={13} />}
        Run Dry Run
      </button>
    </div>
  );
}

function useFieldMappings({ rows, filteredRows }) {
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const requiresPagination = filteredRows.length > 1000;
  const pageCount = requiresPagination ? Math.max(1, Math.ceil(filteredRows.length / pageSize)) : 1;

  useEffect(() => {
    setPage(0);
  }, [filteredRows.length]);

  const visibleRows = useMemo(() => {
    if (!requiresPagination) return filteredRows;
    const start = page * pageSize;
    return filteredRows.slice(start, start + pageSize);
  }, [filteredRows, page, requiresPagination]);

  return {
    totalCount: rows.length,
    filteredCount: filteredRows.length,
    visibleRows,
    page,
    pageCount,
    pageSize,
    requiresPagination,
    setPage,
  };
}

function StepMappingOptions({
  autoRelationships,
  setAutoRelationships,
  generateDescriptions,
  setGenerateDescriptions,
  detectedMappings,
  mappingLoading,
  mappingError,
  dryRunStatus,
  dryRunError,
  unmappedAcknowledged,
  setUnmappedAcknowledged,
  selectedModelNames,
  sourceConnector,
  onRunDryRun,
  onClearMappings,
  onRowsChange,
  onProceedStateChange,
  primaryTargetConnector,
  // New dry-run → edit → deploy props
  dryRunData,
  editingRow,
  setEditingRow,
  isSavingEdit,
  isDeploying,
  deployError,
  onFieldEdit,
  onDeployMappings,
  targetConnectors,
  onBulkResolved,
}) {
  const [autoMappingMode, setAutoMappingMode] = useState(true);
  const [rows, setRows] = useState([]);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const mappingTableRef = useRef(null);
  const dryRunCompleted = dryRunStatus === 'success';
  const dryRunFailed = dryRunStatus === 'failed';
  const hasDryRunResult = dryRunCompleted;
  const aiToggleInitRef = useRef(false);

  useEffect(() => {
    onRowsChange?.(rows);
  }, [rows]); // eslint-disable-line react-hooks/exhaustive-deps

  // If AI options change after initial mount, prompt a quick refresh.
  useEffect(() => {
    if (!aiToggleInitRef.current) {
      aiToggleInitRef.current = true;
      return;
    }
    setNeedsRefresh(true);
  }, [autoRelationships, generateDescriptions, aiToggleInitRef]);

  // Sync internal rows from detectedMappings when the parent updates it (e.g. after handleDryRun)
  useEffect(() => {
    if (Array.isArray(detectedMappings) && detectedMappings.length > 0) {
      setRows(detectedMappings);
      setNeedsRefresh(false);
    }
  }, [detectedMappings]); // eslint-disable-line react-hooks/exhaustive-deps

  const blockingCount = useMemo(
    () => rows.filter((row) => isBlockingRow(row)).length,
    [rows],
  );

  const sourceModel = useMemo(() => ({
    name: selectedModelNames?.[0] || 'Selected Sources',
    type: sourceConnector || 'Source Model',
    field_count: rows.length,
  }), [rows.length, selectedModelNames, sourceConnector]);

  const targetModel = useMemo(() => ({
    name: primaryTargetConnector || 'Target Model',
    type: primaryTargetConnector || 'Target Connector',
    field_count: rows.filter(row => String(row.target_field || '').trim()).length,
  }), [primaryTargetConnector, rows]);
  const connectorHealthy = Boolean(sourceConnector) && Boolean(primaryTargetConnector);
  const targetDestination = useMemo(() => {
    const firstMapped = rows.find((row) => String(row?.target_field || '').trim());
    if (firstMapped?.target_field) return firstMapped.target_field;
    return `${String(primaryTargetConnector || 'target').toUpperCase()}.PUBLIC.<TABLE>`;
  }, [rows, primaryTargetConnector]);

  const explicitTables = useMemo(() => (
    Array.from(new Set((selectedModelNames || []).map(name => String(name || '').trim()).filter(Boolean)))
  ), [selectedModelNames]);

  const inferredTables = useMemo(() => {
    const explicitUpper = new Set(explicitTables.map(name => name.toUpperCase()));
    const rowTables = rows.flatMap((row) => {
      if (row.field_type === 'measure') return Array.isArray(row.measure_source_tables) ? row.measure_source_tables : [];
      return row.source_table_name ? [row.source_table_name] : [];
    });
    return Array.from(new Set(
      rowTables
        .map(name => String(name || '').trim())
        .filter(Boolean)
        .filter(name => !explicitUpper.has(name.toUpperCase()))
    ));
  }, [explicitTables, rows]);
  const visibleInferredTables = useMemo(() => {
    if (!autoMappingMode && !autoRelationships) return [];
    return inferredTables;
  }, [autoMappingMode, autoRelationships, inferredTables]);

  const counts = useMemo(() => {
    const summary = { all: rows.length, auto: 0, manual: 0, unmapped: 0, collision: 0 };
    rows.forEach(row => {
      if (summary[row.status] !== undefined) summary[row.status] += 1;
    });
    return summary;
  }, [rows]);
  const estimatedVolume = useMemo(() => {
    const totalFields = Number(dryRunData?.summary?.total_fields || rows.length || 0);
    return `${totalFields} mapped fields`;
  }, [dryRunData, rows.length]);

  const readyToProceed = autoMappingMode
    ? blockingCount === 0
    : dryRunCompleted && blockingCount === 0;

  useEffect(() => {
    onProceedStateChange?.(readyToProceed);
  }, [readyToProceed]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleAutoMappingMode = useCallback(() => {
    setAutoMappingMode((prev) => {
      const next = !prev;
      if (!next) {
        onClearMappings?.();
        setRows([]);
      }
      return next;
    });
  }, [onClearMappings]);

  const runDryRun = useCallback(async () => {
    await onRunDryRun?.();
    // rows will be synced from detectedMappings via useEffect below
  }, [onRunDryRun]);

  const handleInlineTargetChange = useCallback((rowId, value) => {
    const projectId = 'preview'; // Replace with real one if accessible, but endpoints handle preview
    // Optimistic update
    setRows((prev) => prev.map((row) => {
      if (row.id !== rowId) return row;
      const validation = validateTargetName(value, primaryTargetConnector, row.source_type, row.target_type);
      if (validation.isValid) {
        return {
          ...row,
          target_field: value,
          status: 'manual',
          validation_status: 'valid',
          validation_code: 'OK',
          validation_message: '',
          suggested_target_name: validation.suggestion,
          collision_detected: false,
          isDirty: true,
        };
      }
      return {
        ...row,
        target_field: value,
        status: 'collision',
        validation_status: 'invalid',
        validation_code: 'INVALID_IDENTIFIER',
        validation_message: validation.error,
        suggested_target_name: validation.suggestion,
        collision_detected: true,
        isDirty: true,
      };
    }));

    // Call API
    api.updateMapping(projectId, rowId, value).catch(err => {
      console.error('Failed to update mapping:', err);
      // Let user know mapping failed. We would normally rollback here, but a toast or error state works.
    });
  }, [primaryTargetConnector]);


  const applySuggestion = useCallback((rowId) => {
    setRows((prev) => prev.map((row) => {
      if (row.id !== rowId) return row;
      const suggested = String(row.suggested_target_name || '').trim();
      if (!suggested) return row;
      return {
        ...row,
        target_field: suggested,
        status: 'manual',
        validation_status: 'valid',
        validation_code: 'OK',
        validation_message: '',
        collision_detected: false,
        isDirty: true,
      };
    }));
  }, []);

  const readiness = (() => {
    if (mappingLoading || dryRunStatus === 'running' || dryRunStatus === 'loading') {
      return {
        message: 'Dry Run in Progress / Validating Schema...',
        background: 'rgba(245, 158, 11, 0.12)',
        border: '1px solid rgba(245, 158, 11, 0.35)',
        color: 'var(--accent-orange)',
      };
    }
    if (dryRunCompleted && blockingCount === 0) {
      return {
        message: 'Ready to proceed — mapping validation complete',
        background: 'rgba(34, 197, 94, 0.12)',
        border: '1px solid rgba(34, 197, 94, 0.35)',
        color: 'var(--color-success)',
      };
    }
    return {
      message: 'Ready for Dry Run',
      background: 'rgba(59, 130, 246, 0.12)',
      border: '1px solid rgba(59, 130, 246, 0.35)',
      color: 'var(--accent-blue)',
    };
  })();
  const checkState = useMemo(() => {
    if (mappingLoading || dryRunStatus === 'running' || dryRunStatus === 'loading') return 'running';
    if (!dryRunCompleted) return 'pending';
    if (blockingCount > 0 || dryRunFailed) return 'issues';
    return 'success';
  }, [mappingLoading, dryRunStatus, dryRunCompleted, blockingCount, dryRunFailed]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Mapping Options & Verification</h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          Review field-level mappings, resolve validation issues, and keep manual overrides synced before project creation.
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, minmax(0, 1fr))', gap: 16 }}>
        {/* Top Row: Connectors (Left) & Settings/Stats (Right) */}
        <div style={{ gridColumn: 'span 8', display: 'grid', gap: 12 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: 12, alignItems: 'center' }}>
            <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 12, display: 'grid', gap: 6 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>Source</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <SourceIcon source={sourceModel.type} size={16} />
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{sourceModel.type === 'fabric' ? 'Microsoft Fabric' : sourceModel.type}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{sourceModel.name}</span>
                <span style={{ fontSize: 10, fontWeight: 700, borderRadius: 999, padding: '2px 8px', border: '1px solid rgba(34, 197, 94, 0.35)', background: 'rgba(34, 197, 94, 0.12)', color: 'var(--color-success)' }}>
                  {connectorHealthy ? 'Active' : 'Check'}
                </span>
              </div>
            </div>
            <div
              title="Source to Target Mapping Direction"
              style={{
                width: 36,
                height: 36,
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                border: '1px solid var(--border-main)',
                background: 'var(--bg-main)',
                color: 'var(--accent-blue)',
                fontSize: 18,
                fontWeight: 800,
              }}
            >
              →
            </div>
            <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 12, display: 'grid', gap: 6 }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>Target</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <SourceIcon source={targetModel.type} size={16} />
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>{targetModel.type}</div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{targetDestination}</span>
                <span style={{ fontSize: 10, fontWeight: 700, borderRadius: 999, padding: '2px 8px', border: '1px solid rgba(34, 197, 94, 0.35)', background: 'rgba(34, 197, 94, 0.12)', color: 'var(--color-success)' }}>
                  {connectorHealthy ? 'Active' : 'Check'}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div style={{ gridColumn: 'span 4', display: 'grid', gap: 12 }}>
          <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 14, background: 'var(--bg-surface)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', textTransform: 'uppercase' }}>AI Enhancements</div>
              <button type="button" title="Configure AI semantic matching and prompt behavior." style={{ border: 'none', background: 'transparent', padding: 0, cursor: 'pointer' }}>
                <Settings size={14} color="var(--text-tertiary)" />
              </button>
            </div>
            <div style={{ display: 'grid', gap: 8 }}>
              <ToggleOption
                label="Auto-Detect Mapping"
                description="Maps source to target by semantics."
                checked={autoMappingMode}
                onChange={toggleAutoMappingMode}
              />
              <ToggleOption
                label="Auto-detect Relationships"
                description="Infers FK constraints automatically."
                checked={autoRelationships}
                onChange={setAutoRelationships}
              />
              <div style={{ borderRadius: 8, border: '1px solid var(--border-main)', padding: 10, background: 'var(--bg-main)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>Generate AI Descriptions</span>
                    <span title="Uses LLM orchestration (such as LangChain) to automatically generate technical descriptions for tables and fields during sync.">
                      <Info size={13} color="var(--text-tertiary)" />
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Drafts metadata documentation.</div>
                </div>
                <input type="checkbox" checked={generateDescriptions} onChange={(e) => setGenerateDescriptions(e.target.checked)} />
              </div>
            </div>
          </div>

          <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', padding: 14, background: 'var(--bg-surface)', height: '100%' }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', textTransform: 'uppercase', marginBottom: 10 }}>Scope Verification</div>
            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}><strong>Explicitly selected:</strong> {explicitTables.length > 0 ? explicitTables.join(', ') : 'None'}</div>
              <div style={{ fontSize: 11, color: visibleInferredTables.length > 0 ? 'var(--accent-orange)' : 'var(--text-secondary)' }}>
                <strong>Inferred:</strong> {visibleInferredTables.length > 0 ? visibleInferredTables.join(', ') : 'None'}
                {visibleInferredTables.length > 0 && (
                  <button
                    type="button"
                    onClick={() => mappingTableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                    style={{ marginLeft: 8, border: 'none', background: 'transparent', color: 'var(--accent-blue)', cursor: 'pointer', fontSize: 11, textDecoration: 'underline' }}
                  >
                    Review
                  </button>
                )}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}><strong>Unsupported types:</strong> 0</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}><strong>Estimated Volume:</strong> {estimatedVolume}</div>
            </div>
          </div>
        </div>

        <div style={{ gridColumn: 'span 12' }}>
          <div style={{ border: '1px solid var(--border-main)', borderRadius: 12, background: 'linear-gradient(135deg, rgba(30,41,59,0.7), rgba(15,23,42,0.9))', padding: 20, display: 'grid', gap: 16, minHeight: 160 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
              <div style={{ display: 'grid', gap: 4 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>Pre-migration Integrity Check</div>
                <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Simulate sync and validate schema mappings before writing data.</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 11, color: 'var(--accent-blue)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                  {checkState === 'pending' && 'Status: Pending'}
                  {checkState === 'running' && 'Status: Validating...'}
                  {checkState === 'issues' && `Status: ${counts.collision} Conflict${counts.collision === 1 ? '' : 's'}`}
                  {checkState === 'success' && 'Status: Ready'}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 2 }}>
                  {explicitTables.length || 1} Sources · {primaryTargetConnector ? 1 : 0} Target
                </div>
              </div>
            </div>

            <div style={{ display: 'grid', gap: 8 }}>
              <div style={{ height: 8, borderRadius: 999, background: '#2A2A35', position: 'relative', overflow: 'hidden', border: '1px solid rgba(71,85,105,0.35)' }}>
                {checkState === 'running' && (
                  <div style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '35%',
                    height: '100%',
                    borderRadius: 999,
                    background: 'linear-gradient(90deg, rgba(59,130,246,0.2), rgba(59,130,246,0.9), rgba(59,130,246,0.2))',
                    animation: 'pulse 1s ease-in-out infinite',
                  }} />
                )}
                {checkState === 'success' && (
                  <div style={{ position: 'absolute', inset: 0, background: 'rgba(34,197,94,0.85)', borderRadius: 999 }} />
                )}
                {checkState === 'issues' && (
                  <div style={{ position: 'absolute', inset: 0, background: 'rgba(239,68,68,0.75)', borderRadius: 999 }} />
                )}
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap', marginTop: 8 }}>
              {checkState === 'pending' || checkState === 'running' ? (
                <button
                  type="button"
                  onClick={runDryRun}
                  disabled={mappingLoading}
                  style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '10px 16px', minWidth: 200, borderRadius: 8, border: '1px solid var(--accent-blue)', background: 'var(--accent-blue)', color: '#fff', fontSize: 12, fontWeight: 700, cursor: mappingLoading ? 'not-allowed' : 'pointer', opacity: mappingLoading ? 0.65 : 1 }}
                >
                  {mappingLoading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={14} />}
                  {mappingLoading ? 'Running Check...' : 'Run Check'}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={runDryRun}
                    disabled={mappingLoading}
                    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '10px 16px', minWidth: 160, borderRadius: 8, border: '1px solid var(--border-main)', background: 'transparent', color: 'var(--text-primary)', fontSize: 12, fontWeight: 700, cursor: mappingLoading ? 'not-allowed' : 'pointer', opacity: mappingLoading ? 0.65 : 1 }}
                  >
                    <RefreshCw size={14} />
                    Re-run Check
                  </button>
                  <button
                    type="button"
                    disabled={!readyToProceed}
                    onClick={() => onProceedStateChange?.(true)}
                    style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '10px 16px', minWidth: 160, borderRadius: 8, border: '1px solid var(--accent-blue)', background: 'var(--accent-blue)', color: '#fff', fontSize: 12, fontWeight: 700, cursor: !readyToProceed ? 'not-allowed' : 'pointer', opacity: !readyToProceed ? 0.6 : 1 }}
                  >
                    Proceed
                  </button>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8 }}>
        {needsRefresh && (
          <span style={{ fontSize: 11, color: 'var(--accent-orange)' }}>
            AI settings changed. Refresh mappings to apply.
          </span>
        )}
        <button
          type="button"
          onClick={runDryRun}
          disabled={mappingLoading}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '8px 12px',
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-primary)',
            fontSize: 12,
            fontWeight: 700,
            cursor: mappingLoading ? 'not-allowed' : 'pointer',
            opacity: mappingLoading ? 0.65 : 1,
          }}
        >
          {mappingLoading ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <RefreshCw size={13} />}
          Refresh Mappings
        </button>
      </div>

      {mappingError && !mappingLoading && !dryRunFailed && (
        <div style={{ border: '1px solid rgba(239, 68, 68, 0.45)', background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-error)', borderRadius: 8, padding: '10px 12px', fontSize: 12 }}>
          {mappingError}
        </div>
      )}

      {deployError && (
        <div style={{ border: '1px solid rgba(239, 68, 68, 0.45)', background: 'rgba(239, 68, 68, 0.12)', color: 'var(--color-error)', borderRadius: 8, padding: '10px 12px', fontSize: 12 }}>
          {deployError}
        </div>
      )}

      {/* ── New DryRunMappingTable — shown after a successful dry run ─────── */}
      {dryRunStatus === 'success' && detectedMappings.length > 0 && (
        <div ref={mappingTableRef}>
          <DryRunMappingTable
            mappings={detectedMappings}
            summary={dryRunData?.summary}
            relationships={(() => {
              try {
                const stored = sessionStorage.getItem('detectedRelationships');
                return stored ? JSON.parse(stored) : [];
              } catch { return []; }
            })()}
            onEdit={(rowId) => {
              const row = detectedMappings.find(r => r.id === rowId);
              if (row) setEditingRow(row);
            }}
            onBulkResolved={onBulkResolved}
          />
        </div>
      )}

      {/* ── FieldMappingEditor modal — shown when a row is being edited ───── */}
      {editingRow && (
        <FieldMappingEditor
          row={editingRow}
          onSave={onFieldEdit}
          onClose={() => setEditingRow(null)}
          targetPlatform={Array.from(targetConnectors || new Set())[0] || 'snowflake'}
          isSaving={isSavingEdit}
        />
      )}

      {mappingLoading && (
        <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 10 }}>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 8 }}>Generating Mappings...</div>
          <div style={{ width: '100%', height: 6, borderRadius: 999, background: 'rgba(71,85,105,0.35)', overflow: 'hidden' }}>
            <div style={{ width: '40%', height: '100%', background: 'var(--accent-blue)', borderRadius: 999, animation: 'pulse 1.2s ease-in-out infinite' }} />
          </div>
        </div>
      )}

      {dryRunCompleted && counts.unmapped > 0 && (
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: 12, borderRadius: 8, background: 'rgba(245, 158, 11, 0.10)', border: '1px solid rgba(245, 158, 11, 0.35)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={unmappedAcknowledged}
            onChange={(e) => setUnmappedAcknowledged?.(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <span style={{ fontSize: 12, color: 'var(--accent-orange)', lineHeight: 1.5 }}>
            Acknowledge {counts.unmapped} unmapped field{counts.unmapped === 1 ? '' : 's'} as intentional.
          </span>
        </label>
      )}

      {/* Readiness indicator moved to main footer when on this step */}
      <div style={{ padding: 12, borderRadius: 8, background: readiness.background, border: readiness.border }}>
        <p style={{ fontSize: 11, color: readiness.color, margin: 0, lineHeight: 1.6, fontWeight: 700, textAlign: 'center' }}>
          {readiness.message}
        </p>
      </div>
    </div>
  );
}
