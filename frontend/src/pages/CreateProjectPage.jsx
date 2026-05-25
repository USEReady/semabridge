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
import SmartSearchBar from '../components/common/SmartSearchBar';
import { matchesSmartQuery } from '../components/common/smartSearchQuery.js';
import SourceIcon from '../components/common/SourceIcon';
import StatusBadge from '../components/common/StatusBadge';
import Modal from '../components/common/Modal';
import { useLogs } from '../context/LogsContext';
import { useUIStore } from '../store/uiStore';
import { useProjectWizardStore } from '../store/projectWizardStore';
import DraftToast from '../components/common/DraftToast';
import DraftBanner from '../components/common/DraftBanner';
import ErrorBoundary from '../components/ErrorBoundary';
import DryRunMappingTable, { isBlockingRow as isDryRunBlockingRow } from '../components/DryRunMappingTable';
import { buildDryRunPayload } from '../utils/dryRunPayload';
import { escapeYamlString } from '../utils/yaml';
import { StepBasicInfo } from '../components/CreateProjectWizard/StepBasicInfo';
import { StepConnectorConfig } from '../components/CreateProjectWizard/StepConnectorConfig';
import { StepSourceBrowser } from '../components/CreateProjectWizard/StepSourceBrowser';
import { StepMappingOptions } from '../components/CreateProjectWizard/StepMappingOptions';
import { StepFinish } from '../components/CreateProjectWizard/StepFinish';
import { WizardHeader } from '../components/CreateProjectWizard/WizardHeader';
import { WizardFooter } from '../components/CreateProjectWizard/WizardFooter';
import { useConnectorAccounts } from '../hooks/useConnectorAccounts';

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

const SECTION_CARD = {
  border: '1px solid rgba(255, 255, 255, 0.05)',
  borderRadius: 16,
  background: 'var(--bg-surface)',
  padding: 24,
  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
};

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
        status: normalizeStatus({ ...row, target_field: targetName }),
        validation_status: String(row?.validation_status || ''),
        validation_code: String(row?.validation_code || ''),
        validation_message: String(row?.validation_message || ''),
        suggested_target_name: String(row?.suggested_target_name || ''),
        collision_detected: Boolean(row?.collision_detected),
        synonym_overrides: Array.isArray(row?.synonym_overrides) ? row.synonym_overrides : [],
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
        parent_table: tableSource,
        synonym_overrides: Array.isArray(column?.synonym_overrides) ? column.synonym_overrides : [],
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
    modelQueryRegex, pbixSourceMode,
    selectedLocalFolderId, selectedPbixFilePath,
    expandedWs, selectedModels: selectedModelsRaw, selectedModelNameByKey,
    selectedDatabricksTables: selectedDatabricksTablesRaw, databricksQuery,
    autoRelationships, generateDescriptions,
    currentStepIndex: step,
  } = useProjectWizardStore(state => state.wizard);
  const [showStep1Validation, setShowStep1Validation] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createError, setCreateError] = useState('');
  const [runWarning, setRunWarning] = useState('');
  const isHydratedRef = useRef(false);
  const prevWorkspaceIdRef = useRef(null);
  const prevSourceConnectorRef = useRef(null);

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
  const setModelQueryRegex = (val) => setWizardState({ modelQueryRegex: !!val });
  const setPbixSourceMode = (val) => setWizardState({ pbixSourceMode: val });

  const [pbixFile, setPbixFile] = useState(null);
  const [pbixUploadPath, setPbixUploadPath] = useState('');
  const [pbixUploading, setPbixUploading] = useState(false);

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

  const savedModels = useMemo(() => {
    if (!editMode || !initialData) return new Set();
    const models = initialData.models || initialData.model || [];
    const modelNames = Array.isArray(models) ? models : [models];
    const s = new Set(modelNames);
    if (Array.isArray(initialData.selection_model_ids)) {
      initialData.selection_model_ids.forEach(id => { if (id) s.add(id); });
    }
    return s;
  }, [editMode, initialData]);

  // --- Hydrate form when editMode is active and initialData changes ---
  useEffect(() => {
    if (editMode && initialData && !isHydratedRef.current) {
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
      // sync/write strategy (copy | upsert)
      if (initialData.write_strategy != null) {
        setWizardState({ write_strategy: String(initialData.write_strategy).toLowerCase() });
      } else if (initialData.sync_mode != null) {
        // Some project records store sync_mode at the project level (copy|upsert)
        setWizardState({ write_strategy: String(initialData.sync_mode).toLowerCase() });
      }

      const st = String(initialData.source_type || '').toLowerCase();
      if (st === 'fabric') {
        const models = initialData.models || initialData.model || [];
        const modelNames = Array.isArray(models) ? models : [models];
        const nextSelectedModels = new Set(modelNames);
        const nextModelNameByKey = {};
        modelNames.forEach(n => { nextModelNameByKey[n] = n; });
        
        // Also check if we have GUIDs in selection
        if (Array.isArray(initialData.selection_model_ids)) {
          initialData.selection_model_ids.forEach(id => {
            if (id) nextSelectedModels.add(id);
          });
        }

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

      isHydratedRef.current = true;
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
    const normalized = (allWorkspacesFromApi || [])
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
  }, [allWorkspacesFromApi]);

  const selectedWorkspace = useMemo(() => {
    if (!fabricWorkspaceId) return null;
    return liveFabricWorkspaces.find(ws => ws.id === fabricWorkspaceId) || null;
  }, [liveFabricWorkspaces, fabricWorkspaceId]);

  const selectedModelNames = [...selectedModels]
    .map(modelKey => selectedModelNameByKey[modelKey])
    .filter(Boolean);

  const {
    fabricAccounts,
    snowflakeAccounts,
    databricksAccounts,
    isRefreshingWorkspaces,
    fetchFabricWorkspaces,
    workspaceDiscoveryError,
  } = useConnectorAccounts({
    step,
    sourceConnector,
    targetConnectors,
    selectedConnectionId,
    snowflakeAccountId,
    databricksAccountId,
    setSelectedConnectionId,
    setFabricAccountId,
    setSnowflakeAccountId,
    setDatabricksAccountId,
    setFabricWorkspaceId,
    setAllWorkspacesFromApi,
    setWorkspaces,
  });

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
    }
  }, [fabricWorkspaceId, liveFabricWorkspaces]);


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
    const wsChanged = fabricWorkspaceId !== prevWorkspaceIdRef.current;
    const scChanged = sourceConnector !== prevSourceConnectorRef.current;

    // During hydration or if values haven't changed, do nothing.
    if (!isHydratedRef.current || (!wsChanged && !scChanged)) {
      if (fabricWorkspaceId) prevWorkspaceIdRef.current = fabricWorkspaceId;
      if (sourceConnector) prevSourceConnectorRef.current = sourceConnector;
      return;
    }

    // If we're in editMode and the change is just setting the initial workspace, don't clear.
    if (editMode && wsChanged && fabricWorkspaceId === initialData?.workspace_id) {
      prevWorkspaceIdRef.current = fabricWorkspaceId;
      return;
    }

    console.log('[SemaBridge] Source/Workspace changed, clearing selections:', { sourceConnector, fabricWorkspaceId });
    setSelectedModels(new Set());
    setSelectedModelNameByKey({});
    setExpandedWs({});
    setWsModels({});
    setModelQuery('');

    prevWorkspaceIdRef.current = fabricWorkspaceId;
    prevSourceConnectorRef.current = sourceConnector;
  }, [fabricWorkspaceId, sourceConnector, setModelQuery, editMode, initialData?.workspace_id]);


  // Defensive: auto-select first available workspace if missing after loading
  useEffect(() => {
    if (step === 3 && sourceConnector === 'fabric' && !fabricWorkspaceId && !wsLoading) {
      const liveList = liveFabricWorkspaces;
      if (liveList.length > 0) {
        setFabricWorkspaceId(liveList[0].id);
      }
    }
  }, [step, sourceConnector, fabricWorkspaceId, wsLoading, liveFabricWorkspaces]);

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
          const models = data ?? [];
          setWsModels({ [fabricWorkspaceId]: models });

          // Auto-resolve names for any selected GUIDs during hydration/edit
          if (models.length > 0 && selectedModels.size > 0) {
            setSelectedModelNameByKey(prev => {
              const next = { ...prev };
              let changed = false;
              selectedModels.forEach(key => {
                const id = key.includes('::') ? key.split('::')[1] : key;
                const match = models.find(m => m.id === id);
                if (match?.name && next[key] !== match.name) {
                  next[key] = match.name;
                  changed = true;
                }
              });
              return changed ? next : prev;
            });
          }
          
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
        // include current sync/write strategy so EditProjectPage can persist it on the project
        const syncMode = (useProjectWizardStore.getState().wizard.write_strategy || 'copy');
        await onSaveConfig(configYaml, {
          name: name.trim(),
          description: description.trim(),
          tags: Array.from(tags),
          sync_mode: syncMode,
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
            await api.updateMapping(projectId, String(mapping.id), payloadMapping);
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

    if (selectedModels.size) {
      lines.push('selection:');
      lines.push('  model_ids:');
      
      const resolvedIds = new Set();
      [...selectedModels].forEach(modelKey => {
        const id = modelKey.includes('::') ? modelKey.split('::')[1] : modelKey;
        resolvedIds.add(id);

        // If it looks like a name (not a GUID), try to find its ID in the active workspace
        const isGuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
        if (!isGuid && fabricWorkspaceId && wsModels[fabricWorkspaceId]) {
          const match = wsModels[fabricWorkspaceId].find(m => m.name === id);
          if (match?.id) resolvedIds.add(match.id);
        }
      });

      [...resolvedIds].forEach(id => lines.push(`    - "${escapeYamlString(id)}"`));
    }

    lines.push('options:');
    lines.push(`  auto_relationships: ${autoRelationships}`);
    lines.push(`  generate_descriptions: ${generateDescriptions}`);
    // include write strategy if present in wizard state
    try {
      const ws = useProjectWizardStore.getState().wizard;
      const wsStrategy = String(ws?.write_strategy || 'copy').toLowerCase();
      if (wsStrategy) lines.push(`  write_strategy: ${wsStrategy}`);
    } catch (err) {
      // ignore — default handled by backend
    }
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

  const handleSynonymUpdate = useCallback((rowId, synonyms) => {
    setDetectedMappings(prev => prev.map(row =>
      row.id === rowId
        ? { ...row, synonym_overrides: Array.isArray(synonyms) ? synonyms : [], isDirty: true }
        : row
    ));
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
      <WizardHeader
        editMode={editMode}
        hasMeaningfulData={hasMeaningfulData}
        clearWizardState={clearWizardState}
        navigate={navigate}
        name={name}
        step={step}
      />

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
              workspacesLoading={isRefreshingWorkspaces}
              isRefreshingWorkspaces={isRefreshingWorkspaces}
              fetchFabricWorkspaces={fetchFabricWorkspaces}
              workspaceDiscoveryError={workspaceDiscoveryError}
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
              savedModels={savedModels}
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
                onSynonymUpdate={handleSynonymUpdate}
                projectId={createdProject?.id || createdProject?.project_id || dryRunData?.project_id || 'preview'}
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
              initialSelectedModels={savedModels}
              selectedModelNameByKey={selectedModelNameByKey}
              onClear={clearWizardState}
            />
          )}
        </div>
      </div>

      <WizardFooter
        createdProject={createdProject}
        step={step}
        goBack={goBack}
        goNext={goNext}
        canAdvance={canAdvance()}
        saving={saving}
        mappingLoading={mappingLoading}
        editMode={editMode}
      />

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
