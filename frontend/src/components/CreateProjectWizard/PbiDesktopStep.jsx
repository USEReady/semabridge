import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  RefreshCw,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Monitor,
  Upload,
  Check,
  Loader2,
  FileText,
  Table,
  Hash,
  Share2,
} from 'lucide-react';
import { api } from '../../utils/api';
import {
  isDesktopInstanceSelected,
  toggleDesktopSelection,
  reconcileDesktopSelections,
} from '../../utils/pbiDesktopSelection';

const SECTION_CARD = {
  border: '1px solid var(--border-main)',
  borderRadius: 16,
  padding: '32px 36px',
  background: 'var(--bg-surface)',
  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.08)',
  position: 'relative',
  overflow: 'visible',
};

export default function PbiDesktopStep({
  selectedFiles = [], // Array of { file_path, file_name, ... }
  onSelectionChange,
  onSwitchToPbix,
  preselectedServer = null,
  preselectedDatabase = null,
  isEditMode = false,
}) {
  const [instances, setInstances] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [preselectionNotice, setPreselectionNotice] = useState('');
  const [closedNotice, setClosedNotice] = useState('');
  const [inspectSummaries, setInspectSummaries] = useState({}); // { [filePathOrName]: { tables_count, ... } }
  const [inspectLoading, setInspectLoading] = useState({}); // { [key]: boolean }

  // Inspect one file (read-only metadata summary)
  const fetchInspect = useCallback(async (filePath, fileName) => {
    const key = filePath || fileName;
    if (!key) return;
    setInspectLoading((prev) => ({ ...prev, [key]: true }));
    try {
      const res = await api.inspectPbiDesktopFile(filePath || fileName);
      if (res) {
        setInspectSummaries((prev) => ({
          ...prev,
          [key]: res,
        }));
      }
    } catch (err) {
      // Non-fatal inspect failure
      setInspectSummaries((prev) => ({
        ...prev,
        [key]: { error: err?.response?.data?.detail || err?.message || 'Inspection failed' },
      }));
    } finally {
      setInspectLoading((prev) => ({ ...prev, [key]: false }));
    }
  }, []);

  const fetchInstances = useCallback(async () => {
    setLoading(true);
    setError('');
    setClosedNotice('');

    try {
      // 1. If pre-selected server & database are provided (e.g. from External Tools launcher)
      let preselectedInst = null;
      if (preselectedServer && preselectedDatabase) {
        try {
          const checkRes = await api.validatePbiDesktopPreselection(preselectedServer, preselectedDatabase);
          if (checkRes?.valid && checkRes?.instance) {
            preselectedInst = checkRes.instance;
            setPreselectionNotice('');
          } else {
            setPreselectionNotice("That file isn't open anymore");
          }
        } catch {
          setPreselectionNotice("That file isn't open anymore");
        }
      }

      // 2. Fetch all currently running Desktop instances
      const res = await api.getPbiDesktopInstances();
      const list = res?.instances || [];
      setInstances(list);

      // 3. Handle selection updates & closed file reconciliation
      if (preselectedInst) {
        // External tools launcher pre-selection
        const alreadySelected = selectedFiles.some(
          (f) =>
            f.file_path === preselectedInst.file_path ||
            f.file_name === preselectedInst.file_name ||
            (f.port === preselectedInst.port && f.database_name === preselectedInst.database_name)
        );
        if (!alreadySelected) {
          const newSelection = [
            {
              file_path: preselectedInst.file_path || preselectedInst.file_name,
              file_name: preselectedInst.file_name,
              port: preselectedInst.port,
              database_name: preselectedInst.database_name,
              has_unsaved_changes: preselectedInst.has_unsaved_changes,
            },
          ];
          onSelectionChange?.(newSelection);
          fetchInspect(preselectedInst.file_path || preselectedInst.file_name, preselectedInst.file_name);
        }
      } else if (selectedFiles.length > 0) {
        // Reconcile existing selections: keep those that are still open, drop closed ones with a note
        const { kept, closed, closedNotice: notice } = reconcileDesktopSelections(selectedFiles, list);

        if (closed.length > 0) {
          setClosedNotice(notice);
          onSelectionChange?.(kept);
        } else if (kept.length !== selectedFiles.length) {
          onSelectionChange?.(kept);
        }
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Failed to detect running Power BI Desktop instances.');
    } finally {
      setLoading(false);
    }
  }, [
    preselectedServer,
    preselectedDatabase,
    selectedFiles,
    onSelectionChange,
    fetchInspect,
  ]);

  useEffect(() => {
    fetchInstances();
  }, []); // Run on mount

  // Inspect any newly selected files
  useEffect(() => {
    selectedFiles.forEach((f) => {
      const key = f.file_path || f.file_name;
      if (key && !inspectSummaries[key] && !inspectLoading[key]) {
        fetchInspect(f.file_path, f.file_name);
      }
    });
  }, [selectedFiles, inspectSummaries, inspectLoading, fetchInspect]);

  const toggleSelectInstance = (inst) => {
    if (inst.is_live_connected || inst.is_unsaved_new_file) return; // Disabled

    const next = toggleDesktopSelection(selectedFiles, inst);
    const added = next.length > selectedFiles.length;
    if (added) {
      fetchInspect(inst.file_path, inst.file_name);
    }
    onSelectionChange?.(next);
  };

  const isInstanceSelected = (inst) => isDesktopInstanceSelected(inst, selectedFiles);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24, paddingBottom: 40 }}>
      <div>
        <h2 style={{ fontSize: 17, fontWeight: 700, color: 'var(--text-primary)', margin: '0 0 4px' }}>
          Select Open Power BI Desktop Models
        </h2>
        <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: 0 }}>
          SemaBridge connects directly to live semantic models running in Power BI Desktop on this machine.
        </p>
      </div>

      <div style={SECTION_CARD}>
        {/* Header with title and refresh button */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
              Running Power BI Desktop Instances
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
              Select one or multiple open files to include in this project.
            </div>
          </div>
          <button
            type="button"
            onClick={fetchInstances}
            disabled={loading}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '7px 14px',
              borderRadius: 8,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface-raised)',
              color: 'var(--text-primary)',
              fontSize: 12,
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'all 0.2s ease',
            }}
          >
            <RefreshCw size={13} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
            {loading ? 'Detecting...' : 'Refresh'}
          </button>
        </div>

        {/* Notices & Errors */}
        {preselectionNotice && (
          <div
            style={{
              padding: '12px 16px',
              borderRadius: 8,
              background: 'rgba(245, 158, 11, 0.1)',
              border: '1px solid var(--color-warning, #f59e0b)',
              color: 'var(--color-warning, #f59e0b)',
              fontSize: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 16,
            }}
          >
            <AlertTriangle size={16} />
            <span>{preselectionNotice}</span>
          </div>
        )}

        {closedNotice && (
          <div
            style={{
              padding: '12px 16px',
              borderRadius: 8,
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid var(--color-error, #ef4444)',
              color: 'var(--color-error, #ef4444)',
              fontSize: 12,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 16,
            }}
          >
            <AlertCircle size={16} />
            <span>{closedNotice}</span>
          </div>
        )}

        {error && (
          <div
            style={{
              padding: '16px 20px',
              borderRadius: 10,
              background: 'rgba(239, 68, 68, 0.1)',
              border: '1px solid var(--color-error, #ef4444)',
              color: 'var(--color-error, #ef4444)',
              fontSize: 13,
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
              marginBottom: 16,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
            <button
              type="button"
              onClick={fetchInstances}
              style={{
                alignSelf: 'flex-start',
                padding: '6px 14px',
                borderRadius: 6,
                background: 'var(--color-error, #ef4444)',
                color: '#fff',
                fontSize: 12,
                fontWeight: 600,
                border: 'none',
                cursor: 'pointer',
              }}
            >
              Retry
            </button>
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div
            style={{
              padding: '40px 20px',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 12,
              color: 'var(--text-tertiary)',
            }}
          >
            <Loader2 size={28} style={{ animation: 'spin 1s linear infinite', color: 'var(--accent-blue)' }} />
            <span style={{ fontSize: 13 }}>Detecting open Power BI Desktop models…</span>
          </div>
        )}

        {/* Empty State (No Desktop instances running) */}
        {!loading && !error && instances.length === 0 && (
          <div
            style={{
              padding: '36px 24px',
              borderRadius: 12,
              border: '1px dashed var(--border-main)',
              background: 'var(--bg-surface-raised)',
              textAlign: 'center',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 14,
            }}
          >
            <Monitor size={36} style={{ color: 'var(--text-tertiary)' }} />
            <div>
              <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                No running Power BI Desktop models detected
              </div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
                Open your .pbix in Power BI Desktop, then Refresh
              </div>
            </div>
            {onSwitchToPbix && (
              <button
                type="button"
                onClick={onSwitchToPbix}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '8px 18px',
                  borderRadius: 8,
                  background: 'var(--accent-blue, #3b82f6)',
                  color: '#fff',
                  fontSize: 12,
                  fontWeight: 600,
                  border: 'none',
                  cursor: 'pointer',
                  marginTop: 4,
                }}
              >
                <Upload size={14} />
                Switch to PBIX Upload
              </button>
            )}
          </div>
        )}

        {/* Instance Cards List */}
        {!loading && instances.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {instances.map((inst) => {
              const selected = isInstanceSelected(inst);
              const disabled = inst.is_live_connected || inst.is_unsaved_new_file;
              const inspectKey = inst.file_path || inst.file_name;
              const summary = inspectSummaries[inspectKey];
              const isInspecting = inspectLoading[inspectKey];

              return (
                <div
                  key={`${inst.port}-${inst.database_name}`}
                  onClick={() => !disabled && toggleSelectInstance(inst)}
                  style={{
                    padding: '16px 20px',
                    borderRadius: 12,
                    border: selected
                      ? '2px solid var(--accent-blue, #3b82f6)'
                      : '1px solid var(--border-main)',
                    background: disabled
                      ? 'rgba(255, 255, 255, 0.02)'
                      : selected
                      ? 'rgba(59, 130, 246, 0.06)'
                      : 'var(--bg-surface-raised)',
                    cursor: disabled ? 'not-allowed' : 'pointer',
                    opacity: disabled ? 0.6 : 1,
                    transition: 'all 0.15s ease',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 10,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <div
                        style={{
                          width: 18,
                          height: 18,
                          borderRadius: 4,
                          background: selected ? 'var(--accent-blue, #3b82f6)' : 'transparent',
                          border: selected ? 'none' : '2px solid var(--border-main)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          transition: 'all 0.15s ease',
                        }}
                      >
                        {selected && <Check size={13} color="white" strokeWidth={3} />}
                      </div>

                      <div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
                            {inst.file_name}
                          </span>
                          {inst.has_unsaved_changes && (
                            <span
                              style={{
                                fontSize: 11,
                                padding: '2px 8px',
                                borderRadius: 12,
                                background: 'rgba(245, 158, 11, 0.15)',
                                color: 'var(--color-warning, #f59e0b)',
                                border: '1px solid rgba(245, 158, 11, 0.3)',
                                fontWeight: 600,
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 4,
                              }}
                            >
                              <AlertTriangle size={11} />
                              Unsaved changes
                            </span>
                          )}
                        </div>

                        <div
                          style={{
                            fontSize: 11,
                            color: 'var(--text-tertiary)',
                            marginTop: 4,
                            wordBreak: 'break-all',
                          }}
                        >
                          {inst.file_path || (inst.is_unsaved_new_file ? 'Unsaved new file (not yet saved to disk)' : 'Location: Active Analysis Services Engine')}
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Live-connected warning disabled banner */}
                  {inst.is_live_connected && (
                    <div
                      style={{
                        padding: '8px 12px',
                        borderRadius: 6,
                        background: 'rgba(59, 130, 246, 0.08)',
                        border: '1px solid rgba(59, 130, 246, 0.2)',
                        color: 'var(--accent-blue)',
                        fontSize: 11,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                      }}
                    >
                      <AlertCircle size={13} />
                      <span>Uses a model in the Power BI service — use the Fabric connector</span>
                    </div>
                  )}

                  {/* Unsaved new file banner */}
                  {inst.is_unsaved_new_file && (
                    <div
                      style={{
                        padding: '8px 12px',
                        borderRadius: 6,
                        background: 'rgba(245, 158, 11, 0.08)',
                        border: '1px solid rgba(245, 158, 11, 0.25)',
                        color: 'var(--color-warning, #f59e0b)',
                        fontSize: 11,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                      }}
                    >
                      <AlertTriangle size={13} />
                      <span>Unsaved new file — save this file in Power BI Desktop before adding it as a project source</span>
                    </div>
                  )}

                  {/* Model inspect summary for selected file */}
                  {selected && !disabled && (
                    <div
                      style={{
                        marginTop: 4,
                        padding: '10px 14px',
                        borderRadius: 8,
                        background: 'rgba(59, 130, 246, 0.08)',
                        border: '1px solid rgba(59, 130, 246, 0.2)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 16,
                        fontSize: 12,
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {isInspecting && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />
                          <span>Reading model structure…</span>
                        </div>
                      )}
                      {!isInspecting && summary && !summary.error && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontWeight: 600, color: 'var(--text-primary)' }}>
                            <Table size={13} color="var(--accent-blue)" />
                            {summary.tables_count} Tables
                          </span>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontWeight: 600, color: 'var(--text-primary)' }}>
                            <Hash size={13} color="var(--accent-blue)" />
                            {summary.measures_count} Measures
                          </span>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontWeight: 600, color: 'var(--text-primary)' }}>
                            <Share2 size={13} color="var(--accent-blue)" />
                            {summary.relationships_count} Relationships
                          </span>
                        </div>
                      )}
                      {!isInspecting && summary?.error && (
                        <span style={{ color: 'var(--color-warning)', fontSize: 11 }}>
                          Summary unavailable: {summary.error}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Edit mode: show restored files status */}
      {isEditMode && selectedFiles.length > 0 && (
        <div style={SECTION_CARD}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>
            Configured Files Status
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {selectedFiles.map((f) => {
              const isOpen = instances.some(
                (inst) =>
                  (inst.file_path && f.file_path && inst.file_path.toLowerCase() === f.file_path.toLowerCase()) ||
                  (inst.file_name && f.file_name && inst.file_name.toLowerCase() === f.file_name.toLowerCase())
              );
              return (
                <div
                  key={f.file_path || f.file_name}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 12px',
                    borderRadius: 6,
                    background: 'var(--bg-surface-raised)',
                    fontSize: 12,
                  }}
                >
                  <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{f.file_name}</span>
                  <span
                    style={{
                      fontSize: 11,
                      fontWeight: 700,
                      padding: '2px 8px',
                      borderRadius: 12,
                      background: isOpen ? 'rgba(34, 197, 94, 0.15)' : 'rgba(239, 68, 68, 0.15)',
                      color: isOpen ? '#22c55e' : '#ef4444',
                    }}
                  >
                    {isOpen ? 'Open in Desktop' : 'Not Open'}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}