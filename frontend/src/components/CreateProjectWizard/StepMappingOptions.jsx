import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { Loader2, AlertTriangle, Play, Database, Cloud, Snowflake, Zap, Table2, ChevronDown, RefreshCw, Settings, Info } from 'lucide-react';
import SmartSearchBar from '../common/SmartSearchBar';
import { matchesSmartQuery } from '../common/smartSearchQuery';
import { resolveSourceTableName, sanitizeMappingName, isBlockingRow } from '../../utils/projectHelpers';
import { INPUT, MAPPING_FILTERS } from '../../utils/constants';
import { validateTargetName } from '../../utils/validateTargetName';
import SourceIcon from '../common/SourceIcon';
import { api } from '../../utils/api';
import { ToggleOption } from '../WizardUIComponents';
import DryRunMappingTable from '../DryRunMappingTable';
import FieldMappingEditor from '../FieldMappingEditor';

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
  onBulkFieldEdit,
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
            onFieldEdit={onFieldEdit}
            onBulkFieldEdit={onBulkFieldEdit}
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
