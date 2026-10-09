import { useState, useEffect, useRef } from 'react';
import { ChevronDown, ChevronRight, FileText, Download, AlertTriangle, RefreshCw, Copy, Check, Info } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import StatusBadge from '../common/StatusBadge';
import RunReportSummary from '../RunReportSummary';
import { api } from '../../utils/api';
import { cleanPbixLabel } from '../../utils/pbixLabel';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

const STATUS_BADGE = {
  success: { status: 'success' },
  failed: { status: 'failed' },
  error: { status: 'failed' },
  conflict: { status: 'warning', label: 'Conflict' },
  partial: { status: 'partial' },
  warning: { status: 'warning' },
  running: { status: 'running' },
  unknown: { status: 'pending', label: 'Unknown' },
};

const actionButtonStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 600,
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-main)',
  cursor: 'pointer',
  transition: 'all 0.15s ease',
};

const disabledActionButtonStyle = {
  ...actionButtonStyle,
  opacity: 0.45,
  cursor: 'not-allowed',
};

function ReconciliationSummary({ reconciliation }) {
  if (!reconciliation) return null;
  const unaccounted = reconciliation.unaccounted && typeof reconciliation.unaccounted === 'object'
    ? Object.entries(reconciliation.unaccounted)
    : [];
  const clean = reconciliation.is_clean === true;
  const accent = reconciliation.ddl_error
    ? 'var(--color-error)'
    : clean ? 'var(--color-success)' : 'var(--color-warning)';

  return (
    <div
      style={{
        marginTop: 10,
        padding: 10,
        borderRadius: 6,
        background: 'var(--bg-input)',
        borderLeft: `3px solid ${accent}`,
        fontSize: 12,
        color: 'var(--text-secondary)',
        overflowWrap: 'anywhere',
      }}
    >
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Reconciliation{' '}
        <span style={{ color: accent, fontWeight: 600 }}>
          {reconciliation.ddl_error ? 'could not run' : clean ? 'clean' : 'needs attention'}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14 }}>
        {reconciliation.deployed_live_count != null && (
          <span>Live metrics: <strong style={{ color: 'var(--text-primary)' }}>{reconciliation.deployed_live_count}</strong></span>
        )}
        {reconciliation.deployed_dead_count != null && (
          <span>Dead metrics: <strong style={{ color: 'var(--text-primary)' }}>{reconciliation.deployed_dead_count}</strong></span>
        )}
        <span>Unaccounted: <strong style={{ color: unaccounted.length ? 'var(--color-warning)' : 'var(--text-primary)' }}>{unaccounted.length}</strong></span>
      </div>
      {unaccounted.length > 0 && (
        <div style={{ marginTop: 6, fontFamily: MONO, fontSize: 11 }}>
          {unaccounted.map(([name, count]) => `${name} (${count})`).join(', ')}
        </div>
      )}
      {reconciliation.ddl_error && (
        <div style={{ marginTop: 6, color: 'var(--color-error)' }}>{String(reconciliation.ddl_error)}</div>
      )}
    </div>
  );
}

const STAGE_CONFIG = {
  ddl_deployment: {
    label: 'Snowflake Deployment',
    color: '#ef4444',
    bg: 'rgba(239, 68, 68, 0.12)',
    border: 'rgba(239, 68, 68, 0.35)',
  },
  dax_translation: {
    label: 'DAX Translation',
    color: '#a855f7',
    bg: 'rgba(168, 85, 247, 0.12)',
    border: 'rgba(168, 85, 247, 0.35)',
  },
  schema_validation: {
    label: 'Schema Validation',
    color: '#f97316',
    bg: 'rgba(249, 115, 22, 0.12)',
    border: 'rgba(249, 115, 22, 0.35)',
  },
  ddl_emission: {
    label: 'DDL Generation',
    color: '#eab308',
    bg: 'rgba(234, 179, 8, 0.12)',
    border: 'rgba(234, 179, 8, 0.35)',
  },
  extraction: {
    label: 'Metadata Extraction',
    color: '#3b82f6',
    bg: 'rgba(59, 130, 246, 0.12)',
    border: 'rgba(59, 130, 246, 0.35)',
  },
  reconciliation: {
    label: 'Reconciliation',
    color: '#06b6d4',
    bg: 'rgba(6, 182, 212, 0.12)',
    border: 'rgba(6, 182, 212, 0.35)',
  },
};

function normalizeStage(stage, reason = '') {
  const s = String(stage || '').toLowerCase().trim();
  if (STAGE_CONFIG[s]) return s;
  const r = String(reason || '').toLowerCase();
  if (r.includes('snowflake') || r.includes('deployment') || r.includes('ddl execution')) {
    return 'ddl_deployment';
  }
  if (r.includes('dax') || r.includes('translation') || r.includes('syntax error in formula')) {
    return 'dax_translation';
  }
  if (r.includes('schema') || r.includes('type mismatch') || r.includes('conflict')) {
    return 'schema_validation';
  }
  return s || 'conversion';
}

function DroppedItemDetail({ detail }) {
  const [copied, setCopied] = useState(false);
  if (!detail) return null;

  const detailText = typeof detail === 'object' ? JSON.stringify(detail, null, 2) : String(detail);
  if (!detailText.trim()) return null;

  const handleCopy = (e) => {
    e.stopPropagation();
    try {
      navigator.clipboard.writeText(detailText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  return (
    <div
      style={{
        marginTop: 6,
        padding: '8px 10px',
        borderRadius: 4,
        background: 'rgba(0, 0, 0, 0.35)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        position: 'relative',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-tertiary)' }}>
          Detailed Error / Rejection Log
        </span>
        <button
          type="button"
          onClick={handleCopy}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 6px',
            fontSize: 10,
            borderRadius: 3,
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-main)',
            cursor: 'pointer',
          }}
          title="Copy error details"
        >
          {copied ? <Check size={11} style={{ color: 'var(--color-success, #10b981)' }} /> : <Copy size={11} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <div
        style={{
          fontFamily: MONO,
          fontSize: 11,
          lineHeight: 1.5,
          color: '#f87171',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: 160,
          overflowY: 'auto',
        }}
      >
        {detailText}
      </div>
    </div>
  );
}

function DroppedFieldsSection({ droppedEntities }) {
  const count = droppedEntities?.length || 0;
  // Default open when dropped fields exist so reasons are immediately visible
  const [open, setOpen] = useState(true);
  if (!count) return null;

  const rejectedCount = droppedEntities.filter(
    (e) => !e?.by_design || String(e?.stage || '').includes('deployment') || String(e?.reason || '').toLowerCase().includes('snowflake')
  ).length;

  const hasErrors = rejectedCount > 0;
  const accentColor = hasErrors ? 'var(--color-error, #ef4444)' : 'var(--color-warning, #f59e0b)';
  const accentBg = hasErrors ? 'rgba(239, 68, 68, 0.08)' : 'rgba(245, 158, 11, 0.08)';
  const accentBorder = hasErrors ? 'rgba(239, 68, 68, 0.35)' : 'rgba(245, 158, 11, 0.35)';

  return (
    <div
      style={{
        marginTop: 10,
        borderRadius: 6,
        border: `1px solid ${accentBorder}`,
        background: 'var(--bg-surface)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          padding: '9px 12px',
          border: 'none',
          background: accentBg,
          cursor: 'pointer',
          textAlign: 'left',
          fontSize: 12,
          fontWeight: 600,
          color: accentColor,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <AlertTriangle size={14} />
          <span style={{ fontWeight: 700 }}>
            Dropped Fields & Excluded Entities ({count})
          </span>
          {rejectedCount > 0 && (
            <span
              style={{
                fontSize: 10,
                padding: '1px 6px',
                borderRadius: 4,
                background: 'rgba(239, 68, 68, 0.2)',
                color: '#ef4444',
                fontWeight: 700,
                border: '1px solid rgba(239, 68, 68, 0.4)',
              }}
            >
              {rejectedCount} Rejected / Failed
            </span>
          )}
          {count > rejectedCount && (
            <span
              style={{
                fontSize: 10,
                padding: '1px 6px',
                borderRadius: 4,
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-tertiary)',
                fontWeight: 600,
              }}
            >
              {count - rejectedCount} by design
            </span>
          )}
        </div>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
          {open ? 'Collapse' : 'Expand details'}
        </span>
      </button>

      {open && (
        <div style={{ padding: '10px 12px', borderTop: '1px solid var(--border-subtle, var(--border-main))' }}>
          <div style={{ display: 'grid', gap: 8 }}>
            {droppedEntities.map((item, idx) => {
              const name = item.entity_name || item.name || item.field || item.entity || `Field ${idx + 1}`;
              const kind = item.entity_kind || item.kind || item.type || item.entity_type || 'field';
              const rawStage = normalizeStage(item.stage, item.reason);
              const stageMeta = STAGE_CONFIG[rawStage] || {
                label: item.stage ? String(item.stage).replace(/_/g, ' ') : 'Conversion',
                color: 'var(--text-secondary)',
                bg: 'var(--bg-surface-raised)',
                border: 'var(--border-main)',
              };
              const reason = item.reason || item.message || item.error || 'Excluded during sync';
              const detail = item.detail || item.details || item.raw_error || item.stack || item.sql || null;
              const byDesign = Boolean(item.by_design);
              const isSnowflakeReject = rawStage === 'ddl_deployment' || String(reason).toLowerCase().includes('snowflake');

              return (
                <div
                  key={idx}
                  style={{
                    padding: '10px 12px',
                    borderRadius: 6,
                    background: byDesign ? 'var(--bg-surface-raised)' : 'var(--bg-input)',
                    border: `1px solid ${isSnowflakeReject ? 'rgba(239, 68, 68, 0.4)' : byDesign ? 'var(--border-main)' : 'rgba(245, 158, 11, 0.3)'}`,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 6,
                  }}
                >
                  {/* Top Bar: Name + Badges */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <strong style={{ fontFamily: MONO, fontSize: 13, color: 'var(--text-primary)' }}>
                        {name}
                      </strong>
                      <span
                        style={{
                          fontSize: 10,
                          padding: '1px 6px',
                          borderRadius: 3,
                          background: 'var(--bg-surface)',
                          color: 'var(--text-tertiary)',
                          fontWeight: 700,
                          textTransform: 'uppercase',
                          border: '1px solid var(--border-main)',
                        }}
                      >
                        {kind}
                      </span>
                      {/* Stage Badge */}
                      <span
                        style={{
                          fontSize: 10,
                          padding: '1px 7px',
                          borderRadius: 3,
                          background: stageMeta.bg,
                          color: stageMeta.color,
                          border: `1px solid ${stageMeta.border}`,
                          fontWeight: 700,
                          textTransform: 'capitalize',
                        }}
                      >
                        {stageMeta.label}
                      </span>
                    </div>

                    <div>
                      {byDesign ? (
                        <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 3, background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', fontWeight: 600 }}>
                          Excluded by design
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 3, background: 'rgba(239, 68, 68, 0.15)', color: '#ef4444', fontWeight: 700, border: '1px solid rgba(239, 68, 68, 0.3)' }}>
                          {isSnowflakeReject ? 'Snowflake Rejection' : 'Sync Error'}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Reason text */}
                  <div style={{ fontSize: 12, color: 'var(--text-primary)', lineHeight: 1.5 }}>
                    <span style={{ fontWeight: 600, color: 'var(--text-secondary)', marginRight: 6 }}>
                      Exact Reason:
                    </span>
                    <span style={{ color: isSnowflakeReject ? '#f87171' : 'var(--text-primary)' }}>
                      {reason}
                    </span>
                  </div>

                  {/* Detailed Error / Rejection Log */}
                  <DroppedItemDetail detail={detail} />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ModelRunSection({
  entry,
  projectId,
  runId,
  isRunActive = false,
  isOpen,
  onToggle,
  formatDuration,
  defaultOpen = false,
}) {
  const [localOpen, setLocalOpen] = useState(defaultOpen);
  const open = isOpen !== undefined ? isOpen : localOpen;
  const handleToggle = () => {
    if (onToggle) onToggle();
    else setLocalOpen((v) => !v);
  };

  // Logs state
  const [logs, setLogs] = useState(entry?.logs || []);
  const logsContainerRef = useRef(null);

  // In-place Report preview state
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewTab, setPreviewTab] = useState('summary'); // 'summary' | 'raw'
  const [summaryData, setSummaryData] = useState(null);
  const [rawMarkdown, setRawMarkdown] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  const [downloadingJson, setDownloadingJson] = useState(false);

  // Sync logs when entry updates
  useEffect(() => {
    if (Array.isArray(entry?.logs)) {
      setLogs(entry.logs);
    }
  }, [entry?.logs]);

  const modelName = entry?.model || '';

  // Load preview data when preview is opened
  const loadPreview = async () => {
    if (!projectId || !runId || !modelName) return;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const [sum, raw] = await Promise.all([
        api.getRunModelReportSummary(projectId, runId, modelName).catch(() => null),
        api.getRunModelReport(projectId, runId, modelName).catch(() => null),
      ]);
      setSummaryData(sum);
      setRawMarkdown(raw || '');
    } catch (err) {
      setPreviewError(err?.message || 'Failed to load report preview');
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleTogglePreview = () => {
    const next = !previewOpen;
    setPreviewOpen(next);
    if (next && (!summaryData && !rawMarkdown)) {
      loadPreview();
    }
  };

  const handleDownloadMarkdown = async () => {
    if (!projectId || !runId || !modelName) return;
    try {
      await api.downloadRunModelReport(projectId, runId, modelName);
    } catch (err) {
      console.error('Failed to download markdown report:', err);
    }
  };

  const handleDownloadJsonSummary = async () => {
    if (!projectId || !runId || !modelName) return;
    setDownloadingJson(true);
    try {
      const data = summaryData || await api.getRunModelReportSummary(projectId, runId, modelName);
      if (data) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const safeLabel = cleanPbixLabel(modelName).replace(/[^a-zA-Z0-9._-]+/g, '_');
        const filename = `${projectId}_${safeLabel}_${runId}_summary.json`.replace(/\s+/g, '_');
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 5000);
      }
    } catch (err) {
      console.error('Failed to download JSON summary:', err);
    } finally {
      setDownloadingJson(false);
    }
  };

  if (!entry) return null;

  const badge = STATUS_BADGE[entry.status] || { status: entry.status || 'pending' };
  const durationLabel = entry.durationMs && formatDuration ? formatDuration(entry.durationMs) : null;
  const hasReport = Boolean(entry.has_report || entry.reportModel || entry.has_summary);

  return (
    <div
      style={{
        border: '1px solid var(--border-main)',
        borderRadius: 8,
        background: 'var(--bg-surface)',
        marginBottom: 10,
        minWidth: 0,
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={handleToggle}
        aria-expanded={open}
        style={{
          width: '100%',
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: 10,
          padding: '10px 12px',
          border: 'none',
          background: 'transparent',
          cursor: 'pointer',
          textAlign: 'left',
          color: 'var(--text-primary)',
        }}
      >
        {open
          ? <ChevronDown size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
          : <ChevronRight size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />}
        <span
          title={entry.model || entry.label}
          style={{ fontSize: 13, fontWeight: 600, flex: '1 1 180px', minWidth: 0, overflowWrap: 'anywhere' }}
        >
          {entry.label}
        </span>
        <StatusBadge status={badge.status} label={badge.label} size="sm" />
        {entry.viewName && (
          <span
            title="Semantic view"
            style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-secondary)', minWidth: 0, overflowWrap: 'anywhere' }}
          >
            {entry.viewName}
          </span>
        )}
        {durationLabel && (
          <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
            {durationLabel}
          </span>
        )}
      </button>

      {open && (
        <div style={{ padding: '0 12px 12px 12px', borderTop: '1px solid var(--border-subtle, var(--border-main))' }}>
          {/* Action Bar for this model */}
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 8, margin: '10px 0' }}>
            <button
              type="button"
              onClick={handleTogglePreview}
              disabled={!hasReport}
              style={hasReport ? actionButtonStyle : disabledActionButtonStyle}
              title={hasReport ? 'View in-place report preview' : 'Report is not yet generated for this model'}
            >
              <FileText size={14} /> {previewOpen ? 'Hide Preview' : 'View Report Preview'}
            </button>
            <button
              type="button"
              onClick={handleDownloadMarkdown}
              disabled={!hasReport}
              style={hasReport ? actionButtonStyle : disabledActionButtonStyle}
              title={hasReport ? 'Download Markdown report' : 'Report not available'}
            >
              <Download size={14} /> Markdown
            </button>
            <button
              type="button"
              onClick={handleDownloadJsonSummary}
              disabled={!hasReport || downloadingJson}
              style={hasReport && !downloadingJson ? actionButtonStyle : disabledActionButtonStyle}
              title={hasReport ? 'Download JSON summary' : 'Summary not available'}
            >
              <Download size={14} /> {downloadingJson ? 'Downloading...' : 'JSON Summary'}
            </button>
          </div>

          {/* In-place Report Preview Container */}
          {previewOpen && (
            <div
              style={{
                margin: '12px 0',
                padding: 14,
                borderRadius: 8,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <div style={{ display: 'flex', gap: 4 }}>
                  <button
                    type="button"
                    onClick={() => setPreviewTab('summary')}
                    style={{
                      padding: '4px 12px',
                      borderRadius: 6,
                      fontSize: 12,
                      fontWeight: 600,
                      border: 'none',
                      cursor: 'pointer',
                      background: previewTab === 'summary' ? 'var(--accent-primary, #3b82f6)' : 'transparent',
                      color: previewTab === 'summary' ? '#fff' : 'var(--text-secondary)',
                    }}
                  >
                    Summary
                  </button>
                  <button
                    type="button"
                    onClick={() => setPreviewTab('raw')}
                    style={{
                      padding: '4px 12px',
                      borderRadius: 6,
                      fontSize: 12,
                      fontWeight: 600,
                      border: 'none',
                      cursor: 'pointer',
                      background: previewTab === 'raw' ? 'var(--accent-primary, #3b82f6)' : 'transparent',
                      color: previewTab === 'raw' ? '#fff' : 'var(--text-secondary)',
                    }}
                  >
                    Raw Report
                  </button>
                </div>
                {previewLoading && (
                  <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                    <RefreshCw size={12} className="animate-spin" /> Loading report...
                  </div>
                )}
              </div>

              {previewError ? (
                <div style={{ color: 'var(--color-error)', fontSize: 12, padding: 8 }}>{previewError}</div>
              ) : previewLoading ? (
                <div style={{ color: 'var(--text-tertiary)', fontSize: 12, padding: 16, textAlign: 'center' }}>Loading report preview...</div>
              ) : previewTab === 'summary' ? (
                summaryData ? (
                  <RunReportSummary data={summaryData} />
                ) : (
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 12, padding: 12 }}>No summary data available.</div>
                )
              ) : (
                rawMarkdown ? (
                  <div
                    style={{
                      maxHeight: 400,
                      overflowY: 'auto',
                      padding: 12,
                      background: 'var(--bg-input)',
                      borderRadius: 6,
                      fontSize: 12,
                      lineHeight: 1.6,
                      color: 'var(--text-primary)',
                    }}
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{rawMarkdown}</ReactMarkdown>
                  </div>
                ) : (
                  <div style={{ color: 'var(--text-tertiary)', fontSize: 12, padding: 12 }}>No raw markdown available.</div>
                )
              )}
            </div>
          )}

          {entry.targetArtifactPath && (
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 8, overflowWrap: 'anywhere' }}>
              Artifact: <span style={{ fontFamily: MONO }}>{entry.targetArtifactPath}</span>
            </div>
          )}

          {/* Reconciliation */}
          <ReconciliationSummary reconciliation={entry.reconciliation} />

          {/* Dropped Fields */}
          <DroppedFieldsSection droppedEntities={entry.droppedEntities || entry.dropped_entities} />

          {/* Execution Stages / Logs */}
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', margin: '12px 0 8px 0' }}>
            Execution Stages
          </div>
          <div
            ref={logsContainerRef}
            style={{
              borderRadius: 8,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-input)',
              padding: 12,
              fontFamily: MONO,
              fontSize: 12,
              lineHeight: 1.65,
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
              overflowWrap: 'anywhere',
              maxHeight: 420,
              overflowY: 'auto',
            }}
          >
            {logs?.length ? logs.join('\n') : 'No logs recorded for this model.'}
          </div>
        </div>
      )}
    </div>
  );
}
