import React, { useState } from 'react';
import {
  Star, Check, X, ShieldAlert, Copy, CheckCircle2, AlertTriangle, AlertCircle,
  HelpCircle, Trash2, Calendar, Database, Code, RefreshCw, Clock,
} from 'lucide-react';
import TimeWindowEditor from './TimeWindowEditor';
import { formatTimestamp, formatTruthValue } from '../../utils/biTruthLabels';

const CARD = {
  padding: 14,
  borderRadius: 8,
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-main)',
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
};

const INPUT = {
  padding: '5px 8px',
  borderRadius: 6,
  background: 'var(--bg-surface-raised)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '5px 10px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 11.5,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_PRIMARY = {
  ...BTN,
  borderColor: 'var(--accent-blue)',
  background: 'var(--accent-blue)',
  color: '#fff',
};

function formatDisplayValue(val) {
  return formatTruthValue(val);
}

function TablePreview({ data }) {
  if (!Array.isArray(data) || data.length === 0 || typeof data[0] !== 'object' || data[0] === null) {
    return null;
  }
  const cols = Object.keys(data[0]);
  return (
    <div style={{ marginTop: 8, maxHeight: 130, overflowY: 'auto', border: '1px solid var(--border-main)', borderRadius: 6, background: 'var(--bg-surface)' }}>
      <table style={{ width: '100%', fontSize: 10.5, borderCollapse: 'collapse', textAlign: 'left' }}>
        <thead>
          <tr style={{ background: 'var(--bg-surface-raised)' }}>
            {cols.map((c) => (
              <th key={c} style={{ padding: '4px 8px', borderBottom: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontWeight: 600 }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.slice(0, 5).map((row, idx) => (
            <tr key={idx} style={{ borderBottom: '1px solid var(--border-main)' }}>
              {cols.map((c) => (
                <td key={c} style={{ padding: '4px 8px', color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                  {formatTruthValue(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {data.length > 5 && (
        <div style={{ padding: '3px 8px', fontSize: 10, color: 'var(--text-tertiary)', background: 'var(--bg-surface-raised)' }}>
          + {data.length - 5} more rows
        </div>
      )}
    </div>
  );
}

export default function BiTruthCaseDetail({
  caseItem,
  result,
  onUpdateStatus,
  onUpdateCritical,
  onUpdateWindows,
  onAcceptBenchmark,
  onDelete,
}) {
  const [editingCritical, setEditingCritical] = useState(false);
  const [tolerance, setTolerance] = useState(
    caseItem?.tolerance !== undefined && caseItem?.tolerance !== null
      ? String(caseItem.tolerance)
      : '0.001'
  );
  const [copiedSql, setCopiedSql] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState(null);
  const [acceptSuccess, setAcceptSuccess] = useState(false);

  const handleAcceptBenchmarkAction = async () => {
    if (!result) return;
    setAccepting(true);
    setAcceptError(null);
    setAcceptSuccess(false);
    try {
      await onAcceptBenchmark(caseItem.id, result.actual_value, result.actual_row_count);
      setAcceptSuccess(true);
      setTimeout(() => setAcceptSuccess(false), 4000);
    } catch (err) {
      setAcceptError(err?.message || String(err));
    } finally {
      setAccepting(false);
    }
  };
  const hasGenerated = Boolean(result?.generated_sql);
  const hasExpected = Boolean(caseItem?.expected_sql || caseItem?.dax_expression);
  const hasDax = Boolean(caseItem?.expected_dax || caseItem?.dax_expression);

  const defaultTab = hasGenerated ? 'generated' : hasExpected ? 'expected' : 'generated';
  const [userTab, setUserTab] = useState(null);
  const [prevCaseId, setPrevCaseId] = useState(caseItem?.id);
  if (caseItem?.id !== prevCaseId) {
    setPrevCaseId(caseItem?.id);
    setUserTab(null);
  }
  const sqlTab = userTab || defaultTab;

  if (!caseItem) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          background: 'var(--bg-surface)',
          borderRadius: 10,
          border: '1px solid var(--border-main)',
          color: 'var(--text-tertiary)',
          padding: 32,
          textAlign: 'center',
          gap: 8,
        }}
      >
        <Database size={32} style={{ opacity: 0.3 }} />
        <div style={{ fontSize: 13, fontWeight: 600 }}>Select a question to inspect details</div>
        <div style={{ fontSize: 11.5, maxWidth: 300 }}>
          View live warehouse verification results, benchmark values, generated SQL, and manage slice windows.
        </div>
      </div>
    );
  }

  const isApproved = caseItem.status === 'approved';
  const isRejected = caseItem.status === 'rejected';
  const isCritical = Boolean(caseItem.is_critical);

  const handleToggleCritical = () => {
    const nextCrit = !isCritical;
    const parsedTol = parseFloat(tolerance) || 0.001;
    onUpdateCritical(caseItem.id, nextCrit, parsedTol);
  };

  const handleSaveTolerance = () => {
    const parsedTol = parseFloat(tolerance) || 0.001;
    onUpdateCritical(caseItem.id, isCritical, parsedTol);
    setEditingCritical(false);
  };

  const handleCopy = (text) => {
    if (!text) return;
    navigator.clipboard.writeText(text);
    setCopiedSql(true);
    setTimeout(() => setCopiedSql(false), 2000);
  };

  const canAcceptBenchmark =
    result &&
    result.actual_value !== null &&
    result.actual_value !== undefined &&
    !result.passed;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: 'var(--bg-surface)',
        borderRadius: 10,
        border: '1px solid var(--border-main)',
        overflowY: 'auto',
        padding: 16,
        gap: 14,
        boxSizing: 'border-box',
      }}
    >
      {/* 1. Header & Actions Bar */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, borderBottom: '1px solid var(--border-main)', paddingBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
              <span
                style={{
                  fontSize: 10.5,
                  fontWeight: 700,
                  padding: '2px 8px',
                  borderRadius: 4,
                  textTransform: 'uppercase',
                  background: isApproved
                    ? 'rgba(34,197,94,0.15)'
                    : isRejected
                    ? 'rgba(239,68,68,0.15)'
                    : 'rgba(59,130,246,0.15)',
                  color: isApproved
                    ? 'var(--color-success, #22c55e)'
                    : isRejected
                    ? 'var(--color-error, #ef4444)'
                    : 'var(--accent-blue, #3b82f6)',
                }}
              >
                {caseItem.status}
              </span>

              {caseItem.metric_name && (
                <span style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 600, color: 'var(--accent-blue)' }}>
                  [{caseItem.metric_name}]
                </span>
              )}

              {isCritical && (
                <span
                  style={{
                    fontSize: 10.5,
                    fontWeight: 700,
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: 'rgba(234,179,8,0.15)',
                    color: '#eab308',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                  }}
                >
                  <Star size={11} fill="currentColor" /> CRITICAL GATE ({(caseItem.tolerance ? caseItem.tolerance * 100 : 0.1).toFixed(2)}%)
                </span>
              )}
            </div>

            <h2 style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', margin: 0, lineHeight: 1.35 }}>
              {caseItem.question}
            </h2>
          </div>

          {/* Top Actions */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            <button
              type="button"
              onClick={handleToggleCritical}
              style={{
                ...BTN,
                background: isCritical ? 'rgba(234,179,8,0.15)' : 'transparent',
                borderColor: isCritical ? '#eab308' : 'var(--border-main)',
                color: isCritical ? '#eab308' : 'var(--text-secondary)',
              }}
              title={isCritical ? 'Remove Critical deploy flag' : 'Mark as Critical Deploy Gate measure'}
            >
              <Star size={12} fill={isCritical ? 'currentColor' : 'none'} />
              {isCritical ? 'Critical' : 'Make Critical'}
            </button>

            {isApproved ? (
              <button
                type="button"
                style={{ ...BTN, color: 'var(--color-error)' }}
                onClick={() => onUpdateStatus(caseItem.id, 'rejected')}
              >
                <X size={12} /> Reject
              </button>
            ) : (
              <button
                type="button"
                style={{
                  ...BTN,
                  background: 'var(--color-success, #22c55e)',
                  color: '#fff',
                  borderColor: 'var(--color-success, #22c55e)',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
                }}
                onClick={() => onUpdateStatus(caseItem.id, 'approved')}
              >
                <Check size={12} /> Approve
              </button>
            )}

            <button
              type="button"
              style={{ ...BTN, color: 'var(--color-error)', border: 'none', background: 'transparent', padding: 4 }}
              onClick={() => {
                if (window.confirm('Delete this test case?')) {
                  onDelete(caseItem.id);
                }
              }}
              title="Delete question"
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        {/* Critical tolerance setting bar if critical */}
        {isCritical && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 11.5,
              background: 'rgba(234,179,8,0.06)',
              padding: '6px 10px',
              borderRadius: 6,
              border: '1px solid rgba(234,179,8,0.25)',
            }}
          >
            <ShieldAlert size={14} style={{ color: '#eab308' }} />
            <span style={{ color: 'var(--text-secondary)' }}>Deploy Gate Relative Tolerance:</span>
            {editingCritical ? (
              <>
                <input
                  type="number"
                  step="0.0001"
                  value={tolerance}
                  onChange={(e) => setTolerance(e.target.value)}
                  style={{ ...INPUT, width: 80 }}
                />
                <button type="button" style={{ ...BTN, background: 'var(--accent-blue)', color: '#fff' }} onClick={handleSaveTolerance}>
                  Save
                </button>
                <button type="button" style={BTN} onClick={() => setEditingCritical(false)}>
                  Cancel
                </button>
              </>
            ) : (
              <>
                <strong style={{ color: 'var(--text-primary)' }}>
                  {caseItem.tolerance ? `${(caseItem.tolerance * 100).toFixed(3)}%` : '0.1%'}
                </strong>
                <button
                  type="button"
                  style={{ ...BTN, padding: '2px 6px', fontSize: 10.5 }}
                  onClick={() => setEditingCritical(true)}
                >
                  Edit
                </button>
              </>
            )}
          </div>
        )}
      </div>

      {/* 2. Verification Result & Benchmark Comparison */}
      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Database size={14} style={{ color: 'var(--accent-blue)' }} />
            Warehouse Benchmark &amp; Accuracy Verification
          </div>
          {result ? (
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                padding: '2px 8px',
                borderRadius: 4,
                background: result.passed
                  ? 'rgba(34,197,94,0.15)'
                  : result.comparable === false
                  ? 'rgba(156,163,175,0.15)'
                  : 'rgba(239,68,68,0.15)',
                color: result.passed
                  ? 'var(--color-success, #22c55e)'
                  : result.comparable === false
                  ? 'var(--text-tertiary)'
                  : 'var(--color-error, #ef4444)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              {result.passed ? (
                <>
                  <CheckCircle2 size={12} /> PASSED (Exact Match)
                </>
              ) : result.comparable === false ? (
                <>
                  <HelpCircle size={12} /> NOT COMPARABLE
                </>
              ) : (
                <>
                  <AlertCircle size={12} /> DISCREPANCY DETECTED
                  {result.variance_pct !== undefined && result.variance_pct !== null && (
                    <span>({result.variance_pct > 0 ? '+' : ''}{Number(result.variance_pct).toFixed(2)}%)</span>
                  )}
                </>
              )}
            </span>
          ) : (
            <span
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: '2px 8px',
                borderRadius: 4,
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-tertiary)',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
              }}
            >
              <Clock size={12} /> PENDING RUN
            </span>
          )}
        </div>

        {/* Informative banner for un-evaluated / draft cases */}
        {!result && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '8px 12px',
              borderRadius: 6,
              background: 'rgba(59, 130, 246, 0.08)',
              border: '1px solid rgba(59, 130, 246, 0.2)',
              fontSize: 11.5,
              color: 'var(--text-secondary)',
            }}
          >
            <HelpCircle size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
            <span>
              This case has not been evaluated yet. Approve it and run the suite to produce live warehouse values and verify against benchmarks.
            </span>
          </div>
        )}

        {/* Side-by-side metric values */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 10,
            background: 'var(--bg-surface-raised)',
            padding: 10,
            borderRadius: 6,
            border: '1px solid var(--border-main)',
          }}
        >
          <div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', fontWeight: 600 }}>
              Stored Benchmark
            </div>
            <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }}>
              {caseItem.expected_value !== null && caseItem.expected_value !== undefined
                ? formatDisplayValue(caseItem.expected_value)
                : 'Pending evaluation'}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--text-secondary)', marginTop: 2 }}>
              {caseItem.expected_value !== null && caseItem.expected_value !== undefined
                ? `${caseItem.expected_row_count !== null && caseItem.expected_row_count !== undefined ? `Rows: ${caseItem.expected_row_count}` : 'Rows: unspecified'}${caseItem.expected_evaluated_at ? ` • ${formatTimestamp(caseItem.expected_evaluated_at)}` : ''}`
                : 'Benchmark will be captured when evaluated or approved'}
            </div>
            <TablePreview data={caseItem.expected_value} />
          </div>

          <div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', textTransform: 'uppercase', fontWeight: 600 }}>
              Live Warehouse Value
            </div>
            <div
              style={{
                fontSize: 16,
                fontWeight: 700,
                color:
                  result?.passed
                    ? 'var(--color-success, #22c55e)'
                    : result?.actual_value !== null && result?.actual_value !== undefined
                    ? 'var(--color-error, #ef4444)'
                    : 'var(--text-tertiary)',
                marginTop: 2,
              }}
            >
              {result?.actual_value !== null && result?.actual_value !== undefined
                ? formatDisplayValue(result.actual_value)
                : 'Not evaluated yet'}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--text-secondary)', marginTop: 2 }}>
              {result?.actual_row_count !== null && result?.actual_row_count !== undefined
                ? `Rows: ${result.actual_row_count}`
                : 'Runs against warehouse when approved'}
              {result?.mode && ` • Mode: ${result.mode}`}
              {result?.platform && ` • Platform: ${result.platform}`}
            </div>
            <TablePreview data={result?.actual_value} />
          </div>
        </div>

        {/* Accept Benchmark Action */}
        {canAcceptBenchmark && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              padding: '10px 12px',
              borderRadius: 6,
              background: 'rgba(59,130,246,0.08)',
              border: '1px solid rgba(59,130,246,0.25)',
              gap: 8,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
              <div style={{ fontSize: 11.5, color: 'var(--text-primary)' }}>
                Warehouse query returned <strong>{formatTruthValue(result.actual_value)}</strong>. Is this the intended new baseline?
              </div>
              <button
                type="button"
                disabled={accepting}
                style={{ ...BTN_PRIMARY, background: 'var(--accent-blue)', color: '#fff', fontSize: 11, opacity: accepting ? 0.7 : 1 }}
                onClick={handleAcceptBenchmarkAction}
              >
                {accepting ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
                {accepting ? 'Accepting...' : 'Accept as Benchmark'}
              </button>
            </div>
            {acceptError && (
              <div style={{ color: 'var(--color-error, #ef4444)', fontSize: 11, display: 'flex', alignItems: 'center', gap: 6 }}>
                <AlertCircle size={13} style={{ flexShrink: 0 }} />
                <span>{acceptError}</span>
              </div>
            )}
            {acceptSuccess && (
              <div style={{ color: 'var(--color-success, #22c55e)', fontSize: 11, display: 'flex', alignItems: 'center', gap: 6 }}>
                <CheckCircle2 size={13} style={{ flexShrink: 0 }} />
                <span>Benchmark updated successfully.</span>
              </div>
            )}
          </div>
        )}

        {result?.message && (
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', fontStyle: 'italic' }}>
            {result.message}
          </div>
        )}
      </div>

      {/* 3. SQL Context (Generated vs Expected) */}
      <div style={CARD}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <Code size={14} style={{ color: 'var(--accent-blue)' }} />
            <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--text-primary)' }}>
              Query Expressions
            </span>
          </div>

          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            {[
              { id: 'generated', label: hasGenerated ? 'Generated SQL' : 'Generated SQL (Pending)' },
              { id: 'expected', label: 'Expected SQL' },
              ...(hasDax ? [{ id: 'dax', label: 'DAX' }] : []),
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setUserTab(tab.id)}
                style={{
                  ...BTN,
                  padding: '3px 8px',
                  fontSize: 10.5,
                  background: sqlTab === tab.id ? 'var(--bg-surface-hover)' : 'transparent',
                  borderColor: sqlTab === tab.id ? 'var(--accent-blue)' : 'transparent',
                  color: sqlTab === tab.id ? 'var(--accent-blue)' : 'var(--text-secondary)',
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* SQL Viewer */}
        <div style={{ position: 'relative' }}>
          <pre
            style={{
              margin: 0,
              padding: 10,
              borderRadius: 6,
              background: 'var(--bg-surface-raised)',
              border: '1px solid var(--border-main)',
              fontFamily: 'monospace',
              fontSize: 11.5,
              lineHeight: 1.5,
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
              maxHeight: 140,
              overflowY: 'auto',
            }}
          >
            {sqlTab === 'generated'
              ? result?.generated_sql ||
                (caseItem.expected_sql
                  ? `-- Live execution pending suite run\n-- Certified Reference Query:\n${caseItem.expected_sql}`
                  : (isApproved
                    ? 'No SQL generated yet. Case is approved and queued for the next suite run.'
                    : 'No SQL generated yet for this question. It will be generated and run once approved.'))
              : sqlTab === 'expected'
              ? caseItem.expected_sql || caseItem.dax_expression || 'No certified SQL recorded.'
              : caseItem.expected_dax || caseItem.dax_expression || 'No DAX recorded.'}
          </pre>

          <button
            type="button"
            onClick={() => {
              const text =
                sqlTab === 'generated'
                  ? (result?.generated_sql || caseItem.expected_sql)
                  : sqlTab === 'expected'
                  ? caseItem.expected_sql
                  : caseItem.expected_dax;
              handleCopy(text);
            }}
            style={{
              position: 'absolute',
              top: 6,
              right: 6,
              background: 'rgba(0,0,0,0.2)',
              border: '1px solid var(--border-main)',
              borderRadius: 4,
              color: copiedSql ? 'var(--color-success)' : 'var(--text-secondary)',
              padding: '3px 6px',
              fontSize: 10,
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 3,
            }}
          >
            <Copy size={10} /> {copiedSql ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>

      {/* 4. Time & Slice Windows Editor */}
      <div style={CARD}>
        <TimeWindowEditor
          caseId={caseItem.id}
          projectId={caseItem.project_id}
          windows={caseItem.windows || []}
          onChange={(nextWindows) => onUpdateWindows(caseItem.id, nextWindows)}
        />
      </div>
    </div>
  );
}
