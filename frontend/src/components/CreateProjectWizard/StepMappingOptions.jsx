import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Loader2, AlertTriangle, Play, Database, Cloud, Snowflake, Zap, Table2, ChevronDown, RefreshCw, Settings, Info } from 'lucide-react';
import SmartSearchBar from '../common/SmartSearchBar';
import { matchesSmartQuery } from '../common/smartSearchQuery';
import { resolveSourceTableName, sanitizeMappingName, isBlockingRow } from '../../utils/projectHelpers';
import { INPUT, MAPPING_FILTERS } from '../../utils/constants';
import { validateTargetName } from '../../utils/validateTargetName';
import SourceIcon from '../common/SourceIcon';
import { api, authFetch } from '../../utils/api';
import { ToggleOption } from '../WizardUIComponents';
import DryRunMappingTable from '../DryRunMappingTable';
import FieldMappingEditor from '../FieldMappingEditor';
import MultiFileDryRunStatus from './MultiFileDryRunStatus';
import UpsertChangesView, { OntologyChangesList } from '../UpsertChangesView';
import PanelBoundary from '../common/PanelBoundary';
import TableBindingPlan from '../TableBindingPlan';
import { resolveMultiModelReadiness, flattenSectionRows } from '../../utils/multiFileDryRunReadiness';

const hasOntologyChanges = (c) => Boolean(c && typeof c === 'object' && (
  Object.values(c.applied || {}).some((n) => Number(n) > 0) ||
  ['descriptions', 'synonyms', 'verified_queries', 'formula_overrides_ignored'].some((k) => Array.isArray(c[k]) && c[k].length > 0)
));

const ONTOLOGY_API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');

async function ontologyConflictRequest(projectId, body) {
  const url = `${ONTOLOGY_API_BASE}/ontology/projects/${encodeURIComponent(projectId)}/conflict-resolution`;
  const res = await authFetch(url, body
    ? { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    const msg = Array.isArray(detail?.errors) ? detail.errors.join('; ')
      : (typeof detail === 'string' ? detail : detail?.message);
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return data;
}

/**
 * Choose how metrics defined with different formulas in several attached
 * domains are resolved: domain priority order (first wins) or namespacing
 * (every definition kept, prefixed by its domain). Saved per project and used
 * by the dry run and the deploy.
 */
function OntologyConflictResolver({ projectId, blockers = [], resolution, onApplied }) {
  const [state, setState] = useState(null);
  const [mode, setMode] = useState('priority');
  const [order, setOrder] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const enabled = Boolean(projectId && projectId !== 'preview');

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    ontologyConflictRequest(projectId)
      .then((s) => {
        if (!alive) return;
        setState(s);
        setMode(s?.mode || 'priority');
        setOrder(s?.priority || []);
      })
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, [enabled, projectId]);

  const resolved = resolution?.resolved || [];
  if (!blockers.length && !resolved.length) return null;

  const move = (i, d) => setOrder((prev) => {
    const next = [...prev];
    const j = i + d;
    if (j < 0 || j >= next.length) return prev;
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  });

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const s = await ontologyConflictRequest(projectId, { mode, priority: mode === 'priority' ? order : null });
      setState(s);
      setSaving(false);
      await onApplied?.();
    } catch (e) {
      setSaving(false);
      setError(e.message);
    }
  };

  const box = { marginTop: 6, padding: 10, borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', display: 'flex', flexDirection: 'column', gap: 6 };
  const small = { fontSize: 11.5, color: 'var(--text-secondary)' };
  const btnStyle = { fontSize: 11, padding: '2px 8px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised, var(--bg-surface))', color: 'var(--text-primary)', cursor: 'pointer' };

  return (
    <div style={box}>
      {resolved.map((r, i) => (
        <div key={`res-${i}`} style={{ ...small, color: 'var(--color-success, var(--text-secondary))' }}>Resolved: {r.message}</div>
      ))}
      {!enabled ? (
        <div style={small}>Save the project first to choose how conflicting metric definitions are resolved.</div>
      ) : (
        <>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>Resolve conflicting metric definitions</div>
          <label style={{ ...small, display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="radio" name="ontology-conflict-mode" checked={mode === 'priority'} onChange={() => setMode('priority')} />
            Domain priority: the first domain in this order defines the metric
          </label>
          {mode === 'priority' && order.length > 0 && (
            <ol style={{ margin: '0 0 0 22px', padding: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
              {order.map((d, i) => (
                <li key={d} style={{ ...small, display: 'flex', gap: 6, alignItems: 'center' }}>
                  <span style={{ minWidth: 120, color: 'var(--text-primary)' }}>{i + 1}. {d}</span>
                  <button type="button" style={btnStyle} disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${d} up`}>Up</button>
                  <button type="button" style={btnStyle} disabled={i === order.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${d} down`}>Down</button>
                </li>
              ))}
            </ol>
          )}
          <label style={{ ...small, display: 'flex', gap: 6, alignItems: 'center' }}>
            <input type="radio" name="ontology-conflict-mode" checked={mode === 'namespace'} onChange={() => setMode('namespace')} />
            Keep every definition, prefixed by its domain (e.g. Finance_Revenue, Sales_Revenue)
          </label>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button type="button" style={{ ...btnStyle, fontWeight: 600 }} disabled={saving} onClick={save}>
              {saving ? 'Saving…' : 'Save choice and re-run dry run'}
            </button>
            {state?.mode && <span style={small}>Saved choice: {state.mode}</span>}
          </div>
        </>
      )}
      {error && <div style={{ ...small, color: 'var(--color-error, #dc2626)' }}>{error}</div>}
    </div>
  );
}

export function StepMappingOptions({
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
  onSynonymUpdate,
  projectId,
  // Multi-model dry run (multiple PBIX files or multiple Fabric models):
  // when set, a background dry-run job is in flight/complete -- render one
  // collapsible section per model instead of the single-model table below.
  // Per-model state (sections) is owned by CreateProjectPage's
  // useMultiDryRunJob; every handler below is scoped to one model's file_id.
  multiDryRunJob,
  multiSections = [],
  multiJobStatus = null,
  multiPollError = '',
  onRetryMultiPolling,
  multiRerunningIds,
  multiRerunErrors,
  onMultiEditRow,
  onMultiBulkResolved,
  onMultiSynonymUpdate,
  onRetryMultiFile,
  onRemoveMultiFile,
}) {
  const [autoMappingMode, setAutoMappingMode] = useState(true);
  const [rows, setRows] = useState([]);
  const [needsRefresh, setNeedsRefresh] = useState(false);
  // The full mapping grid is one click away ("See all mappings"), not the default view.
  const [showAllMappings, setShowAllMappings] = useState(false);
  const mappingTableRef = useRef(null);
  const aiToggleInitRef = useRef(false);

  // Multi-model readiness: the job being CREATED (dryRunStatus='success' in
  // the parent) is not the job being DONE. Gate on every model's own
  // status + loaded result + blocking rows instead (see
  // utils/multiFileDryRunReadiness.js resolveMultiModelReadiness).
  const isMultiFileRun = Boolean(multiDryRunJob?.job_id);
  const multiReadiness = useMemo(() => resolveMultiModelReadiness(multiSections || []), [multiSections]);
  const multiRows = useMemo(
    () => (isMultiFileRun ? flattenSectionRows(multiSections || []) : []),
    [isMultiFileRun, multiSections],
  );
  const multiJobRunning = isMultiFileRun && multiReadiness.running;
  const multiJobSucceeded = isMultiFileRun && multiReadiness.allSucceeded;
  const multiJobHasFailures = isMultiFileRun && multiReadiness.hasFailures;

  const dryRunCompleted = isMultiFileRun ? multiJobSucceeded : dryRunStatus === 'success';
  const dryRunFailed = isMultiFileRun ? multiJobHasFailures : dryRunStatus === 'failed';
  const hasDryRunResult = dryRunCompleted;

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

  // Rows every summary below is computed over: the single-model table's rows,
  // or every model's rows for a multi-model run.
  const effectiveRows = isMultiFileRun ? multiRows : rows;

  const singleBlockingCount = useMemo(
    () => rows.filter((row) => isBlockingRow(row)).length,
    [rows],
  );
  const blockingCount = isMultiFileRun ? multiReadiness.totalBlocking : singleBlockingCount;

  const sourceModel = useMemo(() => ({
    name: isMultiFileRun
      ? `${(multiSections || []).length} models`
      : (selectedModelNames?.[0] || 'Selected Sources'),
    type: sourceConnector || 'Source Model',
    field_count: effectiveRows.length,
  }), [effectiveRows.length, isMultiFileRun, multiSections, selectedModelNames, sourceConnector]);

  const targetModel = useMemo(() => ({
    name: primaryTargetConnector || 'Target Model',
    type: primaryTargetConnector || 'Target Connector',
    field_count: effectiveRows.filter(row => String(row.target_field || '').trim()).length,
  }), [primaryTargetConnector, effectiveRows]);
  const targetDestination = useMemo(() => {
    const firstMapped = effectiveRows.find((row) => String(row?.target_field || '').trim());
    if (firstMapped?.target_field) return firstMapped.target_field;
    return `${String(primaryTargetConnector || 'target').toUpperCase()}.PUBLIC.<TABLE>`;
  }, [effectiveRows, primaryTargetConnector]);

  const explicitTables = useMemo(() => {
    const names = isMultiFileRun
      ? (multiSections || []).map((section) => section.label)
      : (selectedModelNames || []);
    return Array.from(new Set(names.map(name => String(name || '').trim()).filter(Boolean)));
  }, [isMultiFileRun, multiSections, selectedModelNames]);

  const inferredTables = useMemo(() => {
    const explicitUpper = new Set(explicitTables.map(name => name.toUpperCase()));
    const rowTables = effectiveRows.flatMap((row) => {
      if (row.field_type === 'measure') return Array.isArray(row.measure_source_tables) ? row.measure_source_tables : [];
      return row.source_table_name ? [row.source_table_name] : [];
    });
    return Array.from(new Set(
      rowTables
          .map(name => String(name || '').trim())
          .filter(Boolean)
          .filter(name => !explicitUpper.has(name.toUpperCase()))
    ));
  }, [explicitTables, effectiveRows]);
  const visibleInferredTables = useMemo(() => {
    if (!autoMappingMode && !autoRelationships) return [];
    return inferredTables;
  }, [autoMappingMode, autoRelationships, inferredTables]);

  const counts = useMemo(() => {
    const summary = { all: effectiveRows.length, auto: 0, manual: 0, unmapped: 0, collision: 0 };
    effectiveRows.forEach(row => {
      if (summary[row.status] !== undefined) summary[row.status] += 1;
    });
    return summary;
  }, [effectiveRows]);
  const estimatedVolume = useMemo(() => {
    const totalFields = isMultiFileRun
      ? effectiveRows.length
      : Number(dryRunData?.summary?.total_fields || rows.length || 0);
    return `${totalFields} mapped fields`;
  }, [dryRunData, effectiveRows.length, isMultiFileRun, rows.length]);

  const isLoading =
    mappingLoading ||
    (isMultiFileRun
      ? multiJobRunning
      : dryRunStatus === "loading" || dryRunStatus === "running");

  // Real signals from the dry-run result — must be declared before readyToProceed / readiness / checkState
  const compatScore  = isMultiFileRun
    ? multiReadiness.minCompatibilityScore
    : (typeof dryRunData?.compatibility_score === 'number' ? dryRunData.compatibility_score : null);
  const schemaIssues = isMultiFileRun
    ? multiReadiness.totalSchemaConflicts
    : (Array.isArray(dryRunData?.schema_conflicts) ? dryRunData.schema_conflicts.length : 0);
  const hasIssues    = blockingCount > 0 || dryRunFailed || schemaIssues > 0 || (compatScore !== null && compatScore < 90);

  // The dry-run check must have completed (at least once) before the user can
  // proceed — regardless of mapping mode.  Without this guard the bar shows
  // green immediately because compatScore is null before any run has run.
  // Multi-model: every model succeeded, every result is loaded, and no model
  // has blocking rows or a sub-70 compatibility score.
  const readyToProceed = isMultiFileRun
    ? multiReadiness.ready
    : (dryRunCompleted &&
      blockingCount === 0 &&
      (compatScore === null || compatScore >= 70));

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
  }, [onRunDryRun]);

  const handleInlineTargetChange = useCallback((rowId, value) => {
    const projectId = 'preview';
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
    if (isLoading) {
      return {
        message: isMultiFileRun
          ? `Dry run in progress — validating ${multiReadiness.total} models (${multiReadiness.succeededCount + multiReadiness.failedCount} of ${multiReadiness.total} finished)…`
          : 'Dry run in progress — validating schema…',
        background: 'rgba(245, 158, 11, 0.12)',
        border: '1px solid rgba(245, 158, 11, 0.35)',
        color: 'var(--accent-orange)',
      };
    }
    // Checked before requiring dryRunCompleted (which only ever means
    // "finished AND succeeded") -- a run that finished with failures must
    // show as an issue, not silently fall through to the generic
    // "run the check" prompt as if nothing had been attempted yet.
    if (dryRunFailed || (dryRunCompleted && hasIssues)) {
      const parts = [];
      if (blockingCount > 0) parts.push(`${blockingCount} collision${blockingCount !== 1 ? 's' : ''}`);
      if (schemaIssues > 0) parts.push(`${schemaIssues} schema gap${schemaIssues !== 1 ? 's' : ''}`);
      if (isMultiFileRun && multiJobHasFailures) {
        parts.push(`${multiReadiness.failedCount} model${multiReadiness.failedCount !== 1 ? 's' : ''} failed`);
      }
      if (isMultiFileRun && multiReadiness.blockedModels.length > 0) {
        parts.push(`blocking items in ${multiReadiness.blockedModels.join(', ')}`);
      }
      if (isMultiFileRun && multiReadiness.lowCompatModels.length > 0) {
        parts.push(`low compatibility in ${multiReadiness.lowCompatModels.join(', ')}`);
      }
      const detail = parts.length ? ` — ${parts.join(', ')}` : '';
      return {
        message: `Issues detected${detail}. Review before proceeding.`,
        background: 'rgba(239, 68, 68, 0.10)',
        border: '1px solid rgba(239, 68, 68, 0.35)',
        color: '#ef4444',
      };
    }
    if (dryRunCompleted && !hasIssues) {
      return {
        message: compatScore !== null
          ? `${compatScore}% compatible — ready to proceed`
          : 'Ready to proceed — mapping validation complete',
        background: 'rgba(34, 197, 94, 0.12)',
        border: '1px solid rgba(34, 197, 94, 0.35)',
        color: 'var(--color-success)',
      };
    }
    return {
      message: 'Run the check to validate your schema mappings before proceeding.',
      background: 'rgba(59, 130, 246, 0.12)',
      border: '1px solid rgba(59, 130, 246, 0.35)',
      color: 'var(--accent-blue)',
    };
  })();

  const checkState = useMemo(() => {
    if (isLoading) return 'running';
    if (dryRunFailed) return 'issues';
    if (!dryRunCompleted) return 'pending';
    if (hasIssues) return 'issues';
    return 'success';
  }, [isLoading, dryRunFailed, dryRunCompleted, hasIssues]);

  const card = { border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: 14, display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 };
  const lbl = { fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', color: 'var(--text-tertiary)', textTransform: 'uppercase' };
  const smallBtn = { display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)', fontSize: 11.5, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' };
  const mapped = counts.auto + counts.manual;
  const barTotal = Math.max(1, mapped + counts.unmapped + counts.collision);
  const statusPill = {
    pending: ['Not checked yet', 'var(--accent-blue)'],
    running: ['Checking…', 'var(--accent-blue)'],
    issues: ['Needs attention', 'var(--color-warning)'],
    success: ['Ready to migrate', 'var(--color-success)'],
  }[checkState];
  const openGrid = () => {
    setShowAllMappings(true);
    setTimeout(() => mappingTableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 50);
  };
  // What the user still has to decide before continuing, in plain words.
  const decisions = [];
  if (dryRunFailed && !isMultiFileRun) {
    decisions.push({ tone: 'error', text: <><b>The check failed.</b> {dryRunError || mappingError || 'See the error above, fix the connection, then run the check again.'}</>, action: null });
  }
  if (isMultiFileRun && multiJobHasFailures) {
    decisions.push({ tone: 'error', text: <><b>{multiReadiness.failedCount} model{multiReadiness.failedCount === 1 ? '' : 's'} failed the check</b></>, action: ['Review', 'grid'] });
  }
  if (blockingCount > 0) {
    decisions.push({ tone: 'error', text: <><b>{blockingCount} name collision{blockingCount === 1 ? '' : 's'}</b> must be renamed before migrating</>, action: ['Fix now', 'grid'] });
  }
  if (schemaIssues > 0) {
    decisions.push({ tone: 'warning', text: <><b>{schemaIssues} column{schemaIssues === 1 ? '' : 's'}</b> differ from the target schema</>, action: ['Review', 'grid'] });
  }
  if (dryRunCompleted && counts.unmapped > 0) {
    decisions.push({
      tone: unmappedAcknowledged ? 'ok' : 'warning',
      text: <><b>{counts.unmapped} field{counts.unmapped === 1 ? ' has' : 's have'} no clear match</b>{unmappedAcknowledged ? ' · you chose to skip them' : ' · map them, or skip them'}</>,
      action: unmappedAcknowledged
        ? ['Undo', 'unskip']
        : ['Skip them', 'skip'],
      secondary: unmappedAcknowledged ? null : ['Map now', 'grid'],
    });
  }
  const runDecision = (kind) => {
    if (kind === 'grid') openGrid();
    else if (kind === 'skip') setUnmappedAcknowledged?.(true);
    else if (kind === 'unskip') setUnmappedAcknowledged?.(false);
  };
  const toneColor = { error: 'var(--color-error)', warning: 'var(--color-warning)', ok: 'var(--color-success)' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>Check before you migrate</h2>
          <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
            We test every table, column and measure in the models you chose against {targetModel.type || 'the target'}, without changing anything there.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {needsRefresh && <span style={{ fontSize: 11, color: 'var(--color-warning)' }}>AI settings changed. Run the check again to apply them.</span>}
          <button
            type="button"
            onClick={runDryRun}
            disabled={isLoading || mappingLoading}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 7, padding: '8px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
              border: '1px solid var(--accent-blue)',
              background: checkState === 'pending' ? 'var(--accent-blue)' : 'var(--bg-surface)',
              color: checkState === 'pending' ? '#fff' : 'var(--text-primary)',
              cursor: isLoading ? 'not-allowed' : 'pointer', opacity: isLoading ? 0.65 : 1,
            }}
          >
            {isLoading ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} /> : (checkState === 'pending' ? <Play size={14} /> : <RefreshCw size={14} />)}
            {isLoading ? 'Checking…' : (checkState === 'pending' ? 'Run check' : 'Re-run check')}
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
        {/* Readiness */}
        <div style={card}>
          <span style={lbl}>Readiness</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 24, fontWeight: 700, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
              {compatScore !== null ? `${compatScore}%` : (dryRunCompleted ? '✓' : '—')}
            </span>
            <span style={{ fontSize: 10.5, fontWeight: 700, padding: '1px 8px', borderRadius: 999, color: statusPill[1], background: `color-mix(in srgb, ${statusPill[1]} 14%, transparent)` }}>
              {statusPill[0]}
            </span>
          </div>
          <div style={{ display: 'flex', height: 12, borderRadius: 5, overflow: 'hidden', background: 'var(--bg-surface-raised)' }}>
            {checkState === 'running' && <i style={{ width: '35%', background: 'var(--accent-blue)', opacity: 0.6, animation: 'pulse 1s ease-in-out infinite' }} />}
            {checkState !== 'running' && counts.all > 0 && (
              <>
                <i style={{ width: `${(100 * mapped) / barTotal}%`, background: 'var(--color-success)' }} />
                <i style={{ width: `${(100 * counts.unmapped) / barTotal}%`, background: 'var(--color-warning)' }} />
                <i style={{ width: `${(100 * counts.collision) / barTotal}%`, background: 'var(--color-error)' }} />
              </>
            )}
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: 11, color: 'var(--text-tertiary)' }}>
            {counts.all > 0 ? (
              <>
                <span><i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: 'var(--color-success)', marginRight: 5 }} />{mapped} map directly</span>
                <span><i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: 'var(--color-warning)', marginRight: 5 }} />{counts.unmapped} unclear</span>
                <span><i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 2, background: 'var(--color-error)', marginRight: 5 }} />{counts.collision} collisions</span>
              </>
            ) : <span>{readiness.message}</span>}
          </div>
        </div>

        {/* What's included */}
        <div style={card}>
          <span style={lbl}>What's included</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5, fontWeight: 600, color: 'var(--text-primary)', flexWrap: 'wrap' }}>
            <SourceIcon source={sourceModel.type} size={14} />
            {sourceModel.type === 'fabric' ? 'Microsoft Fabric' : sourceModel.type}
            <span style={{ color: 'var(--text-tertiary)' }}>→</span>
            <SourceIcon source={targetModel.type} size={14} />
            {targetModel.type}
          </div>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {explicitTables.length || 1} model{(explicitTables.length || 1) === 1 ? '' : 's'}{counts.all ? ` · ${counts.all} fields` : ''}
          </span>
          <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={targetDestination}>
            Target: {targetDestination}
          </span>
          <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)' }}>{estimatedVolume}</span>
        </div>

        {/* AI assistance */}
        <div style={card}>
          <span style={lbl}>AI assistance</span>
          <ToggleOption
            label="Fill gaps in column mapping"
            description="Matches source and target fields by meaning."
            checked={autoMappingMode}
            onChange={toggleAutoMappingMode}
          />
          <ToggleOption
            label="Find missing relationships"
            description="Suggests joins the source didn't declare."
            checked={autoRelationships}
            onChange={setAutoRelationships}
          />
          <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', cursor: 'pointer' }}>
            <span title="Drafts descriptions for tables and fields during the run.">Write descriptions <Info size={12} color="var(--text-tertiary)" style={{ verticalAlign: 'middle' }} /></span>
            <input type="checkbox" checked={generateDescriptions} onChange={(e) => setGenerateDescriptions(e.target.checked)} />
          </label>
        </div>
      </div>

      {/* To decide before continuing */}
      <div style={{ ...card, gap: 0, padding: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px' }}>
          <span style={lbl}>To decide before continuing</span>
          {decisions.length > 0 && (
            <span style={{ fontSize: 10.5, fontWeight: 700, padding: '0 7px', borderRadius: 999, background: 'var(--color-warning-bg)', color: 'var(--color-warning)' }}>{decisions.filter(d => d.tone !== 'ok').length}</span>
          )}
          <span style={{ flex: 1 }} />
          {counts.all > 0 && (
            <button type="button" onClick={() => (showAllMappings ? setShowAllMappings(false) : openGrid())} style={{ ...smallBtn, border: 'none', background: 'transparent', color: 'var(--accent-blue)' }}>
              {showAllMappings ? 'Hide mappings' : `See all ${counts.all} mappings →`}
            </button>
          )}
        </div>
        {decisions.length === 0 && (
          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border-light)', fontSize: 12, color: 'var(--text-tertiary)' }}>
            {checkState === 'pending' ? 'Run the check to see what needs a decision.' : checkState === 'running' ? 'Checking…' : 'Nothing to decide — everything maps.'}
          </div>
        )}
        {decisions.map((d, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 10, alignItems: 'center', padding: '9px 14px', borderTop: '1px solid var(--border-light)', fontSize: 12, color: 'var(--text-secondary)' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: toneColor[d.tone] }} />
            <span style={{ minWidth: 0 }}>{d.text}</span>
            <span style={{ display: 'flex', gap: 6 }}>
              {d.secondary && <button type="button" style={smallBtn} onClick={() => runDecision(d.secondary[1])}>{d.secondary[0]}</button>}
              {d.action && <button type="button" style={smallBtn} onClick={() => runDecision(d.action[1])}>{d.action[0]}</button>}
            </span>
          </div>
        ))}
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

      {/* ── Multi-PBIX per-file status list — replaces the single-file table
           entirely when a background dry-run job is active for this
           project (see CreateProjectPage.jsx's handleDryRun). ─────────── */}
      {multiDryRunJob?.job_id && (showAllMappings || isLoading || multiJobHasFailures) && (
        <div ref={mappingTableRef}>
          <MultiFileDryRunStatus
            sections={multiSections}
            jobStatus={multiJobStatus}
            projectId={projectId}
            relationships={(() => {
              try {
                const stored = sessionStorage.getItem('detectedRelationships');
                return stored ? JSON.parse(stored) : [];
              } catch { return []; }
            })()}
            rerunningIds={multiRerunningIds}
            rerunErrors={multiRerunErrors}
            pollError={multiPollError}
            onRetryPolling={onRetryMultiPolling}
            onEditRow={onMultiEditRow}
            onBulkResolved={onMultiBulkResolved}
            onSynonymUpdate={onMultiSynonymUpdate}
            onRetryFile={onRetryMultiFile}
            onRemoveFile={onRemoveMultiFile}
          />
        </div>
      )}

      {/* ── New DryRunMappingTable — shown after a successful dry run ─────── */}
      {!multiDryRunJob?.job_id && showAllMappings && dryRunStatus === 'success' && detectedMappings.length > 0 && (
        <div ref={mappingTableRef}>
          <PanelBoundary name="Target tables">
            <TableBindingPlan plan={dryRunData?.table_binding_plan} style={{ marginTop: 0, marginBottom: 12 }} />
          </PanelBoundary>
          {(dryRunData?.blocking_conflicts?.length > 0 ||
            dryRunData?.ontology_changes?.conflict_resolution?.resolved?.length > 0 ||
            hasOntologyChanges(dryRunData?.ontology_changes)) && (
            <div className="mb-3 p-3 rounded-lg border border-main bg-surface-raised text-xs space-y-1.5">
              <div className="font-bold text-primary">Ontology changes</div>
              {(dryRunData?.blocking_conflicts || []).map((b, i) => (
                <div key={`blk-${i}`} className="text-red-500 font-medium">
                  Blocker: {b.message} (Choose a namespace or priority order before deploying.)
                </div>
              ))}
              <OntologyConflictResolver
                projectId={projectId}
                blockers={dryRunData?.blocking_conflicts || []}
                resolution={dryRunData?.ontology_changes?.conflict_resolution}
                onApplied={onRunDryRun}
              />
              {/* UPSERT: the per-item changes are listed in the delta view below. */}
              <OntologyChangesList
                changes={dryRunData?.upsert_delta
                  ? { applied: dryRunData?.ontology_changes?.applied, domains: dryRunData?.ontology_changes?.domains, formula_overrides_ignored: dryRunData?.ontology_changes?.formula_overrides_ignored }
                  : dryRunData?.ontology_changes}
              />
            </div>
          )}
          <UpsertChangesView delta={dryRunData?.upsert_delta} modelLabel={dryRunData?.model_name || dryRunData?.label} isDryRun>
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
              onSynonymUpdate={onSynonymUpdate}
              projectId={projectId}
              schemaConflicts={dryRunData?.schema_conflicts || []}
              compatibilityScore={dryRunData?.compatibility_score ?? null}
              onReSync={onRunDryRun}
              droppedEntities={dryRunData?.dropped_entities || []}
              unresolvedReportFieldReferences={dryRunData?.unresolved_report_field_references || []}
            />
          </UpsertChangesView>
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

    </div>
  );
}
