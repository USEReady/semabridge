// Detail panels for the Health & Governance view: Blast Radius Impact,
// "What if…" simulator, Golden Parity Reconciliation, RLS Access Policy,
// Grouped Quality Linter Issues, and Collision Details.
import React from 'react';
import {
  Play,
  CheckCheck,
  Lock,
  Copy,
  Download,
  Upload,
  AlertTriangle,
  CheckCircle2,
  RotateCcw,
  Edit3,
  SlidersHorizontal,
  ChevronDown,
  ChevronRight,
  Check,
} from 'lucide-react';
import { BTN_SM, BTN_PRIMARY, INPUT_STYLE, SELECT_STYLE, MONO, plural } from './styles';
import { Pill } from './ui';

const PANEL = {
  padding: 16,
  borderRadius: 10,
  background: 'var(--bg-app)',
  border: '1px solid var(--border-main)',
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
};

const itemLabel = (x) => (x && typeof x === 'object' ? x.name || x.object_name || String(x) : String(x));

function ImpactGroup({ title, items, tone, onOpenParity }) {
  const isMetrics = title === 'Metrics';
  return (
    <div style={{ padding: 12, borderRadius: 10, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: items.length ? tone : 'var(--text-tertiary)', marginTop: 2 }}>
        {items.length}
      </div>
      {items.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {items.slice(0, 12).map((it, i) => {
            const obj = it && typeof it === 'object' ? it : null;
            const hop = obj?.hop_level || (obj?.impact_level === 'TRANSITIVE' ? 'TRANSITIVE' : 'DIRECT');
            const src = obj?.source || obj?.dependency_source;
            const recert = Boolean(obj?.re_certify || obj?.re_certification_required);
            const mName = itemLabel(it);
            return (
              <span
                key={i}
                title={isMetrics && onOpenParity ? `${obj?.details || ''} (Click to test in Golden Query Verifier)` : (obj?.details || '')}
                onClick={isMetrics && onOpenParity ? () => onOpenParity(mName) : undefined}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  fontFamily: MONO,
                  fontSize: 10.5,
                  padding: '2px 7px',
                  borderRadius: 5,
                  border: '1px solid var(--border-main)',
                  background: 'var(--bg-app)',
                  cursor: isMetrics && onOpenParity ? 'pointer' : 'default',
                }}
              >
                <span>{mName}</span>
                {obj && (
                  <span style={{ fontSize: 9.5, fontWeight: 700, color: hop === 'TRANSITIVE' ? 'var(--color-warning)' : 'var(--accent-blue)' }}>
                    [{hop}]
                  </span>
                )}
                {src && <span style={{ fontSize: 9.5, color: 'var(--text-tertiary)' }}>[{src}]</span>}
                {recert && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpenParity && onOpenParity(mName);
                    }}
                    style={{
                      fontSize: 9.5,
                      fontWeight: 700,
                      color: 'var(--color-error)',
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      padding: 0,
                      textDecoration: 'underline',
                    }}
                  >
                    [Re-certify]
                  </button>
                )}
              </span>
            );
          })}
          {items.length > 12 && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>+{items.length - 12}</span>}
        </div>
      )}
    </div>
  );
}

export function ImpactReport({ report, onPickSuggestion, onOpenParity }) {
  if (!report) return null;
  const sev = report.severity;
  const sevTone = sev === 'BREAKING' ? 'bad' : sev === 'SAFE' ? 'ok' : sev === 'NOT_FOUND' ? 'n' : 'warn';
  const sevLabel = sev === 'SAFE' ? 'Safe' : sev === 'BREAKING' ? 'Breaking' : sev === 'NOT_FOUND' ? 'NOT_FOUND' : 'Degraded';
  return (
    <div style={PANEL}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Pill tone={sevTone}>{sevLabel}</Pill>
          <strong style={{ fontSize: 13 }}>
            {report.change_type?.replace('_', ' ').toLowerCase()} {report.target_object}
          </strong>
        </div>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          {sev === 'NOT_FOUND' ? 'Target not found in scope' : `${plural(report.total_impacted_items || 0, 'downstream item')} affected`}
        </span>
      </div>

      {sev === 'NOT_FOUND' && Array.isArray(report.suggestions) && report.suggestions.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, fontSize: 12 }}>
          <span style={{ color: 'var(--text-secondary)' }}>Did you mean:</span>
          {report.suggestions.map((s) => (
            <button
              key={s}
              type="button"
              style={{ ...BTN_SM, fontFamily: MONO }}
              onClick={() => onPickSuggestion && onPickSuggestion(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {sev !== 'NOT_FOUND' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
          <ImpactGroup title="Metrics" items={report.affected_metrics || []} tone="var(--color-warning)" onOpenParity={onOpenParity} />
          <ImpactGroup title="BI reports" items={report.affected_bi_reports || []} tone="var(--color-error)" />
          <ImpactGroup title="AI agents" items={report.affected_ai_tools || []} tone="var(--accent-purple)" />
        </div>
      )}

      {report.remediation_steps?.length > 0 && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          <strong style={{ color: 'var(--text-primary)' }}>What to do</strong>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {report.remediation_steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
          {onOpenParity && (
            <div style={{ marginTop: 10 }}>
              <button
                type="button"
                style={{
                  ...BTN_SM,
                  background: 'var(--accent-blue, #2563eb)',
                  color: '#ffffff',
                  border: 'none',
                  fontWeight: 600,
                  padding: '5px 12px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                }}
                onClick={() => {
                  const firstMetric = report.affected_metrics?.[0];
                  const mName = typeof firstMetric === 'object' ? (firstMetric.name || firstMetric.object_name) : firstMetric;
                  onOpenParity(mName || report.target_object);
                }}
                title="Open Golden Query Verifier (Parity check) with this metric prefilled"
              >
                Run Golden Query Verifier (Parity) →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function WhatIfSimulator({ gov, metricOptions, onOpenParity }) {
  const {
    blastTarget,
    setBlastTarget,
    blastType,
    setBlastType,
    blastChangeType,
    setBlastChangeType,
    blastTableContext,
    setBlastTableContext,
    govProjectId,
    setGovProjectId,
    projectsList,
    runningBlast,
    onRunBlast,
    blastReport,
  } = gov;

  const effectiveOpenParity = onOpenParity || gov?.onOpenParity;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          Simulate a schema or metric change across direct and transitive dependencies.
        </span>
        {setGovProjectId && (
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
            Governance scope:
            <select
              value={govProjectId || ''}
              onChange={(e) => setGovProjectId(e.target.value)}
              style={{ ...SELECT_STYLE, fontSize: 12 }}
              aria-label="Governance scope"
            >
              <option value="">All loaded domains</option>
              {(projectsList || []).map((p) => (
                <option key={p.id || p.name} value={p.id || p.name}>
                  {p.name || p.id}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <input
          type="text"
          list="ontology-whatif-metrics"
          value={blastTarget}
          onChange={(e) => setBlastTarget(e.target.value)}
          placeholder="Metric, column or table name"
          style={INPUT_STYLE}
        />
        <datalist id="ontology-whatif-metrics">
          {(metricOptions || []).map((n) => (
            <option key={n} value={n} />
          ))}
        </datalist>
        <input
          type="text"
          value={blastTableContext || ''}
          onChange={(e) => setBlastTableContext?.(e.target.value)}
          placeholder="Table context (optional)"
          style={INPUT_STYLE}
        />
        <select value={blastType} onChange={(e) => setBlastType(e.target.value)} style={SELECT_STYLE}>
          <option value="METRIC">Metric</option>
          <option value="COLUMN">Column</option>
          <option value="TABLE">Table</option>
        </select>
        <select value={blastChangeType} onChange={(e) => setBlastChangeType(e.target.value)} style={SELECT_STYLE}>
          <option value="MODIFY_EXPRESSION">MODIFY_EXPRESSION</option>
          <option value="MODIFY_TYPE">MODIFY_TYPE</option>
          <option value="RENAME">RENAME</option>
          <option value="DROP">DROP</option>
        </select>
        <button
          type="button"
          disabled={runningBlast || !blastTarget.trim()}
          onClick={() => onRunBlast()}
          style={{ ...BTN_PRIMARY, opacity: runningBlast || !blastTarget.trim() ? 0.6 : 1 }}
        >
          <Play size={13} /> {runningBlast ? 'Checking…' : 'Check impact'}
        </button>
      </div>
      <ImpactReport
        report={blastReport}
        onOpenParity={effectiveOpenParity}
        onPickSuggestion={(sug) => {
          setBlastTarget(sug);
          onRunBlast(sug);
        }}
      />
    </div>
  );
}

export function ParityPanel({ gov = {}, onOpenMetric, metricOptions = [], dimensionOptions = [], semanticViewOptions = [] }) {
  const {
    govProjectId = '',
    setGovProjectId,
    projectsList = [],
    reconMetric,
    setReconMetric,
    reconExpr,
    setReconExpr,
    reconDaxExpr,
    setReconDaxExpr,
    reconTable,
    setReconTable,
    reconTargetKind,
    setReconTargetKind,
    reconDims,
    setReconDims,
    reconDaxText,
    setReconDaxText,
    reconSqlText,
    setReconSqlText,
    reconAbsTol,
    setReconAbsTol,
    reconRelTol,
    setReconRelTol,
    reconMaxMismatchPct,
    setReconMaxMismatchPct,
    reconRegisterVerified,
    setReconRegisterVerified,
    runningRecon,
    onGenerateReconQueries,
    onRunLiveSql,
    onRunAutoDaxCertify,
    onRunRecon,
    onResetRecon,
    reconResult,
    reconSliceRows,
    reconQueries,
    reconError,
    parityProjectMetrics = [],
    allMetrics = [],
    prefillParity,
    onUpdateMetricSql,
  } = gov || {};

  const [showQueries, setShowQueries] = React.useState(true);
  const [showAdvanced, setShowAdvanced] = React.useState(false);
  const [isEditingSql, setIsEditingSql] = React.useState(false);
  const [editableSql, setEditableSql] = React.useState(reconExpr || '');
  const [lastReconExpr, setLastReconExpr] = React.useState(reconExpr);
  if (reconExpr !== lastReconExpr) {
    setLastReconExpr(reconExpr);
    setEditableSql(reconExpr || '');
  }
  const [copiedKey, setCopiedKey] = React.useState(null);
  const [expectedPbiTotal, setExpectedPbiTotal] = React.useState('');
  const [showPbiWebHelp, setShowPbiWebHelp] = React.useState(false);

  const copyToClipboard = (text, key) => {
    if (!text) return;
    navigator.clipboard?.writeText(text);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const availableMetrics = React.useMemo(() => {
    if (parityProjectMetrics?.length > 0) return parityProjectMetrics;
    if (allMetrics?.length > 0) {
      if (govProjectId) {
        return allMetrics.filter((m) => {
          const domStr = String(m.domain || m._domain || '').toLowerCase();
          const projStr = String(govProjectId).toLowerCase();
          return domStr.includes(projStr) || m.source_project_id === govProjectId;
        });
      }
      return allMetrics;
    }
    return (metricOptions || []).map((name) => ({ name, logical_name: name }));
  }, [parityProjectMetrics, allMetrics, govProjectId, metricOptions]);

  const handleSelectMetric = (val) => {
    if (!val) return;
    const match = availableMetrics.find(
      (m) => m.name === val || m.logical_name === val || `${m.concept}.${m.logical_name}` === val
    );
    if (match && prefillParity) {
      prefillParity(match);
    } else {
      setReconMetric(val);
      if (match?.sql_expression) setReconExpr(match.sql_expression);
      if (match?.dax_expression) setReconDaxExpr?.(match.dax_expression);
    }
  };

  // Robust CSV / TSV parser that supports quotes, commas within numbers ("1,234.50"), and copy-paste from Power BI Web
  const parsePowerBiCsv = (text) => {
    if (!text || !text.trim()) return [];
    const lines = text.trim().split(/\r?\n/).filter((l) => l.trim());
    if (lines.length < 2) return [];
    const delim = lines[0].includes('\t') ? '\t' : ',';

    const parseRow = (line) => {
      const res = [];
      let cur = '';
      let inQuotes = false;
      for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (c === '"') {
          if (inQuotes && line[i + 1] === '"') {
            cur += '"';
            i++;
          } else {
            inQuotes = !inQuotes;
          }
        } else if (c === delim && !inQuotes) {
          res.push(cur.trim());
          cur = '';
        } else {
          cur += c;
        }
      }
      res.push(cur.trim());
      return res;
    };

    const headers = parseRow(lines[0]).map((h) => h.replace(/^["']|["']$/g, '').trim());
    const rows = [];
    for (let r = 1; r < lines.length; r++) {
      const vals = parseRow(lines[r]);
      const obj = {};
      headers.forEach((h, idx) => {
        let raw = vals[idx] ?? '';
        raw = raw.replace(/^["']|["']$/g, '').trim();
        const cleanNum = raw.replace(/[$€£%,]/g, '').trim();
        const num = Number(cleanNum);
        if (cleanNum !== '' && Number.isFinite(num)) {
          obj[h] = num;
        } else {
          obj[h] = raw;
        }
      });
      rows.push(obj);
    }
    return rows;
  };

  const handleDaxFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result || '').trim();
      if (!content) return;
      let rows = [];
      if (file.name.toLowerCase().endsWith('.json')) {
        try {
          const parsed = JSON.parse(content);
          rows = Array.isArray(parsed) ? parsed : [parsed];
        } catch {
          return;
        }
      } else {
        rows = parsePowerBiCsv(content);
      }
      if (rows.length > 0) {
        const jsonStr = JSON.stringify(rows, null, 2);
        setReconDaxText(jsonStr);
        // Automatically run live parity certification using these uploaded Power BI Web rows!
        if (onRunAutoDaxCertify && reconMetric.trim() && reconTable.trim()) {
          onRunAutoDaxCertify({ daxResults: rows });
        }
      }
    };
    reader.readAsText(file);
  };

  const handlePastedText = (text) => {
    if (!text || !text.trim()) return;
    let rows = [];
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) rows = parsed;
    } catch {
      rows = parsePowerBiCsv(text);
    }
    if (rows.length > 0) {
      setReconDaxText(JSON.stringify(rows, null, 2));
      if (onRunAutoDaxCertify && reconMetric.trim() && reconTable.trim()) {
        onRunAutoDaxCertify({ daxResults: rows });
      }
    } else {
      setReconDaxText(text);
    }
  };

  const exportDaxCsv = () => {
    if (!reconSliceRows.length) return;
    const header = 'Slice,PowerBI_DAX,Warehouse_SQL,Abs_Delta,Delta_Pct,Matched';
    const body = reconSliceRows
      .map((r) => `"${String(r.slice).replace(/"/g, '""')}",${r.daxValue},${r.sqlValue},${r.absDiff ?? ''},${r.deltaPct},${r.matched}`)
      .join('\n');
    const blob = new Blob([`${header}\n${body}\n`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${reconMetric || 'parity'}_reconciliation.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const parsedSqlRows = React.useMemo(() => {
    if (!reconSqlText) return [];
    try {
      const parsed = JSON.parse(reconSqlText);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }, [reconSqlText]);

  const sqlColumns = React.useMemo(() => {
    if (!parsedSqlRows.length) return [];
    return Object.keys(parsedSqlRows[0]);
  }, [parsedSqlRows]);

  const numericCol = React.useMemo(() => {
    if (!parsedSqlRows.length) return null;
    return sqlColumns.find((c) => {
      const v = parsedSqlRows[0][c];
      return typeof v === 'number' || (!isNaN(Number(v)) && v !== '' && v !== null);
    }) || sqlColumns[sqlColumns.length - 1];
  }, [sqlColumns, parsedSqlRows]);

  const sfTotalSum = React.useMemo(() => {
    if (!parsedSqlRows.length || !numericCol) return null;
    return parsedSqlRows.reduce((acc, row) => {
      const n = Number(row[numericCol]);
      return acc + (isNaN(n) ? 0 : n);
    }, 0);
  }, [parsedSqlRows, numericCol]);

  const benchmarkComparison = React.useMemo(() => {
    if (!expectedPbiTotal.trim() || sfTotalSum === null) return null;
    const pbiNum = Number(expectedPbiTotal.replace(/,/g, '').trim());
    if (isNaN(pbiNum)) return null;
    const delta = sfTotalSum - pbiNum;
    const absDelta = Math.abs(delta);
    const driftPct = pbiNum !== 0 ? (absDelta / Math.abs(pbiNum)) * 100 : 0;
    const isMatched = driftPct <= (Number(reconMaxMismatchPct) || 0.01) || absDelta <= (Number(reconAbsTol) || 0.01);
    return {
      sfTotal: sfTotalSum,
      pbiTotal: pbiNum,
      delta,
      absDelta,
      driftPct: driftPct.toFixed(2),
      isMatched,
    };
  }, [expectedPbiTotal, sfTotalSum, reconMaxMismatchPct, reconAbsTol]);

  const isMismatch = reconResult && reconResult.status !== 'CERTIFIED';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* ── 1. PROJECT & DAX SELECTION CARD ─────────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
        gap: 12,
        padding: 16,
        borderRadius: 10,
        background: 'var(--bg-surface-raised)',
        border: '1px solid var(--border-main)',
      }}>
        <div>
          <label style={{ display: 'block', fontSize: 11.5, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 6 }}>
            1. Select Project
          </label>
          <select
            value={govProjectId || ''}
            onChange={(e) => setGovProjectId?.(e.target.value)}
            style={{ ...SELECT_STYLE, width: '100%', fontSize: 12.5 }}
          >
            <option value="">All Projects / Global</option>
            {projectsList.map((p) => (
              <option key={p.id || p.name} value={p.id || p.name}>
                {p.name || p.id}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label style={{ display: 'block', fontSize: 11.5, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 6 }}>
            2. Choose DAX Measure / Metric
          </label>
          <select
            value={reconMetric || ''}
            onChange={(e) => handleSelectMetric(e.target.value)}
            style={{ ...SELECT_STYLE, width: '100%', fontSize: 12.5, fontFamily: MONO }}
          >
            <option value="">-- Choose DAX Measure from Project --</option>
            {availableMetrics.map((m, idx) => {
              const val = m.name || m.logical_name;
              const formulaPreview = m.dax_expression ? ` = ${m.dax_expression.slice(0, 32)}…` : '';
              return (
                <option key={`${val}-${idx}`} value={val}>
                  {val}{formulaPreview}
                </option>
              );
            })}
          </select>
        </div>
      </div>

      {/* ── 2. SIDE-BY-SIDE POWER BI DAX vs SNOWFLAKE SQL DISPLAY ──────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 12 }}>
        {/* Left: Power BI DAX */}
        <div style={{
          ...PANEL,
          padding: 14,
          border: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>Power BI DAX</span>
              {reconMetric && <Pill tone="n">[{reconMetric}]</Pill>}
            </div>
            {reconQueries?.dax_query && (
              <button
                type="button"
                onClick={() => copyToClipboard(reconQueries.dax_query, 'dax')}
                style={{ ...BTN_SM, padding: '2px 8px', fontSize: 11 }}
                title="Copy DAX Query"
              >
                {copiedKey === 'dax' ? <Check size={11} color="var(--color-success)" /> : <Copy size={11} />}
                {copiedKey === 'dax' ? 'Copied' : 'Copy'}
              </button>
            )}
          </div>

          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
            Original DAX Measure Definition:
          </div>
          <div style={{
            padding: '8px 10px',
            borderRadius: 6,
            background: 'var(--bg-app)',
            border: '1px solid var(--border-main)',
            fontFamily: MONO,
            fontSize: 11.5,
            color: 'var(--accent-blue)',
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            minHeight: 38,
          }}>
            {reconDaxExpr ? `[${reconMetric || 'Measure'}] = ${reconDaxExpr}` : (reconMetric ? `[${reconMetric}]` : 'Select a DAX measure from the dropdown above')}
          </div>

          {showQueries && reconQueries?.dax_query && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Compiled DAX Execution Query:
              </div>
              <pre style={{
                margin: 0,
                padding: '8px 10px',
                borderRadius: 6,
                background: 'var(--bg-app)',
                border: '1px solid var(--border-main)',
                fontFamily: MONO,
                fontSize: 11,
                lineHeight: 1.45,
                whiteSpace: 'pre-wrap',
                maxHeight: 140,
                overflowY: 'auto',
              }}>
                {reconQueries.dax_query}
              </pre>
            </div>
          )}
        </div>

        {/* Right: Snowflake SQL */}
        <div style={{
          ...PANEL,
          padding: 14,
          border: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>Generated Snowflake SQL</span>
              {reconTable && <Pill tone="ok">{reconTable}</Pill>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <button
                type="button"
                onClick={() => setIsEditingSql((v) => !v)}
                style={{ ...BTN_SM, padding: '2px 8px', fontSize: 11, ...(isEditingSql ? { borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' } : {}) }}
                title="Edit Snowflake SQL expression"
              >
                <Edit3 size={11} /> {isEditingSql ? 'Close Editor' : 'Edit SQL'}
              </button>
              {reconQueries?.sql_query && (
                <button
                  type="button"
                  onClick={() => copyToClipboard(reconQueries.sql_query, 'sql')}
                  style={{ ...BTN_SM, padding: '2px 8px', fontSize: 11 }}
                  title="Copy Snowflake SQL Query"
                >
                  {copiedKey === 'sql' ? <Check size={11} color="var(--color-success)" /> : <Copy size={11} />}
                  {copiedKey === 'sql' ? 'Copied' : 'Copy'}
                </button>
              )}
            </div>
          </div>

          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
            Compiled Snowflake SQL Expression:
          </div>
          <div style={{
            padding: '8px 10px',
            borderRadius: 6,
            background: 'var(--bg-app)',
            border: '1px solid var(--border-main)',
            fontFamily: MONO,
            fontSize: 11.5,
            color: 'var(--color-success)',
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            minHeight: 38,
          }}>
            {reconExpr || (reconMetric ? reconMetric : 'Select a DAX measure from the dropdown above')}
          </div>

          {showQueries && reconQueries?.sql_query && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Compiled Snowflake Execution Query:
              </div>
              <pre style={{
                margin: 0,
                padding: '8px 10px',
                borderRadius: 6,
                background: 'var(--bg-app)',
                border: '1px solid var(--border-main)',
                fontFamily: MONO,
                fontSize: 11,
                lineHeight: 1.45,
                whiteSpace: 'pre-wrap',
                maxHeight: 140,
                overflowY: 'auto',
              }}>
                {reconQueries.sql_query}
              </pre>
            </div>
          )}
        </div>
      </div>

      {/* ── 3. INLINE EDIT & UPDATE SNOWFLAKE SQL WORKFLOW ───────────────── */}
      {(isEditingSql || (isMismatch && reconMetric)) && (
        <div style={{
          padding: 14,
          borderRadius: 10,
          background: isMismatch ? 'rgba(239, 68, 68, 0.04)' : 'var(--bg-surface-raised)',
          border: `1px solid ${isMismatch ? 'rgba(239, 68, 68, 0.35)' : 'var(--accent-blue)'}`,
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Edit3 size={15} style={{ color: isMismatch ? 'var(--color-error)' : 'var(--accent-blue)' }} />
              <strong style={{ fontSize: 12.5 }}>
                {isMismatch ? 'Drift Detected — Tune Snowflake SQL Expression' : 'Edit Snowflake SQL Expression'}
              </strong>
            </div>
            <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
              Updates the metric translation directly in the semantic model.
            </span>
          </div>

          {isMismatch && (
            <div style={{ fontSize: 12, color: 'var(--color-error)', lineHeight: 1.4 }}>
              Power BI DAX and Snowflake SQL values do not match. You can tune the SQL formula below (e.g. adjust rounding, COALESCE, or column casting) and click <strong>Update SQL &amp; Re-Check</strong> to re-verify parity.
            </div>
          )}

          <div>
            <textarea
              rows={3}
              value={editableSql}
              onChange={(e) => setEditableSql(e.target.value)}
              placeholder="e.g. SUM(COALESCE(SALES.AMOUNT, 0))"
              style={{
                ...INPUT_STYLE,
                width: '100%',
                boxSizing: 'border-box',
                fontFamily: MONO,
                fontSize: 12,
                padding: '8px 10px',
              }}
            />
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            <button
              type="button"
              disabled={runningRecon || !editableSql.trim()}
              onClick={() => onUpdateMetricSql?.(editableSql, true)}
              style={{ ...BTN_PRIMARY, opacity: runningRecon || !editableSql.trim() ? 0.6 : 1 }}
            >
              <CheckCheck size={13} /> {runningRecon ? 'Updating & Verifying…' : 'Update SQL & Re-Check Parity'}
            </button>
            <button
              type="button"
              disabled={runningRecon || !editableSql.trim()}
              onClick={() => onUpdateMetricSql?.(editableSql, false)}
              style={BTN_SM}
            >
              Save SQL to Model Only
            </button>
            <button
              type="button"
              disabled={runningRecon}
              onClick={() => setEditableSql(reconExpr || '')}
              style={{ ...BTN_SM, display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              <RotateCcw size={12} /> Reset to Generated
            </button>
            <button
              type="button"
              onClick={() => setIsEditingSql(false)}
              style={BTN_SM}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── 4. PRIMARY CONTROLS & PARITY RUN BUTTONS ───────────────────── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            disabled={runningRecon || !reconMetric.trim() || !reconTable.trim()}
            onClick={() => onRunAutoDaxCertify()}
            style={{
              ...BTN_PRIMARY,
              padding: '8px 16px',
              fontSize: 13,
              fontWeight: 700,
              opacity: runningRecon || !reconMetric.trim() || !reconTable.trim() ? 0.6 : 1,
            }}
          >
            <CheckCheck size={15} /> {runningRecon ? 'Checking Parity…' : 'Run Live Parity Check'}
          </button>

          <button
            type="button"
            style={BTN_SM}
            disabled={runningRecon || !reconMetric.trim() || !reconTable.trim()}
            onClick={onRunLiveSql}
          >
            Run Live Snowflake SQL
          </button>

          <label
            style={{ ...BTN_SM, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4, background: 'var(--bg-surface-raised)', borderColor: 'var(--accent-blue)' }}
            title="Upload CSV exported from Power BI Web (app.powerbi.com)"
          >
            <Upload size={12} color="var(--accent-blue)" /> <strong>Upload Power BI Web CSV</strong>
            <input type="file" accept=".json,.csv,.tsv" style={{ display: 'none' }} onChange={(e) => handleDaxFile(e.target.files?.[0])} />
          </label>

          <button
            type="button"
            style={{ ...BTN_SM, fontSize: 11 }}
            onClick={() => setShowPbiWebHelp((v) => !v)}
          >
            How to export from Power BI Web?
          </button>

          <button
            type="button"
            style={BTN_SM}
            onClick={() => setShowAdvanced((v) => !v)}
          >
            <SlidersHorizontal size={12} /> {showAdvanced ? 'Hide Settings' : 'Settings & Tolerances'}
          </button>

          {reconQueries && (
            <button type="button" style={BTN_SM} onClick={() => setShowQueries((v) => !v)}>
              {showQueries ? 'Hide Queries' : 'Show Queries'}
            </button>
          )}

          {onResetRecon && (reconResult || reconQueries || reconSliceRows.length > 0 || reconError || reconDaxText || reconSqlText) && (
            <button type="button" style={BTN_SM} disabled={runningRecon} onClick={onResetRecon}>
              Reset
            </button>
          )}
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
          {reconSliceRows.length > 0 && (
            <button type="button" style={BTN_SM} onClick={exportDaxCsv}>
              <Download size={12} /> Export CSV
            </button>
          )}
          <a
            href={`/api/reconciliation/evidence-pack?format=pdf${govProjectId ? `&project_id=${encodeURIComponent(govProjectId)}` : ''}`}
            target="_blank"
            rel="noreferrer"
            style={{ ...BTN_SM, textDecoration: 'none' }}
          >
            <Download size={12} /> Evidence PDF
          </a>
          <a
            href={`/api/reconciliation/evidence-pack?format=xlsx${govProjectId ? `&project_id=${encodeURIComponent(govProjectId)}` : ''}`}
            target="_blank"
            rel="noreferrer"
            style={{ ...BTN_SM, textDecoration: 'none' }}
          >
            <Download size={12} /> Evidence Excel
          </a>
        </div>
      </div>

      {/* ── POWER BI WEB EXPORT INSTRUCTIONS CALLOUT ──────────────────── */}
      {showPbiWebHelp && (
        <div style={{
          padding: 14,
          borderRadius: 8,
          background: 'rgba(59, 130, 246, 0.05)',
          border: '1px solid rgba(59, 130, 246, 0.25)',
          fontSize: 12,
          lineHeight: 1.5,
          color: 'var(--text-secondary)',
        }}>
          <strong style={{ color: 'var(--text-primary)', display: 'block', marginBottom: 4 }}>
            How to export and verify data from Power BI Web (app.powerbi.com):
          </strong>
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            <li>Open your report on <strong>Power BI Service (app.powerbi.com)</strong>.</li>
            <li>On the visual displaying this measure, click <strong>`...` (More options) &rarr; Export data</strong>.</li>
            <li>Select <strong>Summarized data &rarr; .csv</strong> (or copy the visual data directly to clipboard).</li>
            <li>Click <strong>Upload Power BI Web CSV</strong> above (or paste the data below). SemaBridge will automatically execute the Snowflake SQL live on your warehouse and compare the slices!</li>
          </ol>
        </div>
      )}

      {/* ── 5. ADVANCED CONFIGURATION (COLLAPSIBLE) ────────────────────── */}
      {showAdvanced && (
        <div style={{
          padding: 14,
          borderRadius: 8,
          background: 'var(--bg-app)',
          border: '1px solid var(--border-main)',
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
        }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 10 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>Deployed View / Table</label>
              <input
                type="text"
                list="parity-view-list"
                value={reconTable}
                onChange={(e) => setReconTable(e.target.value)}
                placeholder="Deployed view or table"
                style={INPUT_STYLE}
              />
              <datalist id="parity-view-list">
                {semanticViewOptions.map((v) => (
                  <option key={v} value={v} />
                ))}
              </datalist>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>Target Kind</label>
              <select value={reconTargetKind || 'semantic_view'} onChange={(e) => setReconTargetKind?.(e.target.value)} style={SELECT_STYLE}>
                <option value="semantic_view">Target: SEMANTIC_VIEW</option>
                <option value="table">Target: Table / View</option>
              </select>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>Dimensions (Slices)</label>
              <input
                type="text"
                list="parity-dim-list"
                value={reconDims}
                onChange={(e) => setReconDims(e.target.value)}
                placeholder="Dimensions (e.g. Sales.Region)"
                style={INPUT_STYLE}
              />
              <datalist id="parity-dim-list">
                {dimensionOptions.map((d) => (
                  <option key={d} value={d} />
                ))}
              </datalist>
            </div>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12, fontSize: 12 }}>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              Abs tolerance:
              <input
                type="number"
                min="0"
                step="0.01"
                value={reconAbsTol}
                onChange={(e) => setReconAbsTol(e.target.value)}
                style={{ ...INPUT_STYLE, width: 72 }}
              />
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              Rel tolerance:
              <input
                type="number"
                min="0"
                step="0.0001"
                value={reconRelTol}
                onChange={(e) => setReconRelTol(e.target.value)}
                style={{ ...INPUT_STYLE, width: 80 }}
              />
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              Max mismatch %:
              <input
                type="number"
                min="0"
                max="100"
                step="0.1"
                value={reconMaxMismatchPct}
                onChange={(e) => setReconMaxMismatchPct(e.target.value)}
                style={{ ...INPUT_STYLE, width: 68 }}
              />
            </label>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--text-secondary)' }}>
              <input
                type="checkbox"
                checked={Boolean(reconRegisterVerified)}
                onChange={(e) => setReconRegisterVerified?.(e.target.checked)}
              />
              Register verified query when certified
            </label>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Paste Power BI Web CSV / Table rows directly:
            </label>
            <textarea
              rows={3}
              value={reconDaxText}
              onChange={(e) => handlePastedText(e.target.value)}
              placeholder="Paste comma-separated or tab-separated data copied from Power BI Web..."
              style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', fontFamily: MONO, fontSize: 11.5 }}
            />
          </div>
        </div>
      )}

      {/* ── 6. ERROR DISPLAY ────────────────────────────────────────────── */}
      {reconError && (
        <div style={{
          padding: '10px 14px',
          borderRadius: 8,
          background: 'rgba(239, 68, 68, 0.1)',
          border: '1px solid rgba(239, 68, 68, 0.3)',
          color: 'var(--color-error)',
          fontSize: 12.5,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <AlertTriangle size={15} style={{ flexShrink: 0 }} />
          <span>{reconError}</span>
        </div>
      )}

      {/* ── 7. PARITY & DRIFT KPI SUMMARY BANNER ────────────────────────── */}
      {reconResult && (
        <div style={{
          ...PANEL,
          padding: 16,
          background: reconResult.status === 'CERTIFIED' ? 'rgba(34, 197, 94, 0.05)' : 'rgba(239, 68, 68, 0.05)',
          border: `1px solid ${reconResult.status === 'CERTIFIED' ? 'rgba(34, 197, 94, 0.35)' : 'rgba(239, 68, 68, 0.35)'}`,
          gap: 12,
        }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              {reconResult.status === 'CERTIFIED' ? (
                <CheckCircle2 size={22} style={{ color: 'var(--color-success)' }} />
              ) : (
                <AlertTriangle size={22} style={{ color: 'var(--color-error)' }} />
              )}
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Pill tone={reconResult.status === 'CERTIFIED' ? 'ok' : 'bad'}>
                    {reconResult.status === 'CERTIFIED' ? 'CERTIFIED PARITY' : 'DRIFT DETECTED'}
                  </Pill>
                  <strong style={{ fontSize: 13.5 }}>{reconResult.certification_badge}</strong>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                  {reconResult.status === 'CERTIFIED'
                    ? 'Values in Snowflake match Power BI within specified tolerances.'
                    : 'Mathematical discrepancy identified between Power BI and Snowflake.'}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 16 }}>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Matched Rows</div>
                <div style={{ fontSize: 16, fontWeight: 700, fontFamily: MONO, color: reconResult.matched_rows === reconResult.total_rows ? 'var(--color-success)' : 'var(--color-error)' }}>
                  {reconResult.matched_rows} / {reconResult.total_rows}
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Max Drift %</div>
                <div style={{ fontSize: 16, fontWeight: 700, fontFamily: MONO, color: Number(reconResult.max_percentage_delta || 0) === 0 ? 'var(--color-success)' : 'var(--color-error)' }}>
                  {Number(reconResult.max_percentage_delta || 0).toFixed(2)}%
                </div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Max Abs Delta</div>
                <div style={{ fontSize: 16, fontWeight: 700, fontFamily: MONO }}>
                  {Number(reconResult.max_absolute_delta || 0).toLocaleString()}
                </div>
              </div>
            </div>
          </div>

          {reconResult.column_mapping && Object.keys(reconResult.column_mapping).length > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', fontFamily: MONO }}>
              Dimension mapping: {Object.entries(reconResult.column_mapping).map(([k, v]) => `${k} ↔ ${v}`).join(', ')}
            </div>
          )}
          {Array.isArray(reconResult.errors) && reconResult.errors.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--color-error)' }}>{reconResult.errors.join('; ')}</div>
          )}
        </div>
      )}

      {/* ── 8. SLICE-BY-SLICE COMPARISON & DRIFT TABLE ─────────────────── */}
      {reconSliceRows.length > 0 && (
        <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', overflow: 'hidden' }}>
          <div style={{
            padding: '10px 14px',
            background: 'var(--bg-surface-raised)',
            borderBottom: '1px solid var(--border-main)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}>
            <strong style={{ fontSize: 12.5 }}>Slice-by-Slice Parity &amp; Drift Comparison (Power BI vs Snowflake)</strong>
            <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
              {reconSliceRows.length} slice{reconSliceRows.length === 1 ? '' : 's'} evaluated
            </span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, background: 'var(--bg-surface)' }}>
            <thead>
              <tr style={{ background: 'var(--bg-app)', textAlign: 'left', color: 'var(--text-secondary)' }}>
                <th style={{ padding: '8px 12px', fontWeight: 600 }}>Slice Dimension</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Power BI Value</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Snowflake SQL</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Difference (Δ)</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Drift % (Δ%)</th>
                <th style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 600 }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {reconSliceRows.map((r, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border-main)' }}>
                  <td style={{ padding: '8px 12px', fontWeight: 600 }}>{r.slice}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO }}>
                    {typeof r.daxValue === 'number' ? r.daxValue.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(r.daxValue)}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO }}>
                    {typeof r.sqlValue === 'number' ? r.sqlValue.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(r.sqlValue)}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO }}>
                    {r.absDiff !== undefined ? Number(r.absDiff).toLocaleString(undefined, { maximumFractionDigits: 4 }) : (Number(r.sqlValue) - Number(r.daxValue)).toFixed(2)}
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO, fontWeight: 700, color: r.matched ? 'var(--color-success)' : 'var(--color-error)' }}>
                    {r.deltaPct}%
                  </td>
                  <td style={{ padding: '8px 12px', textAlign: 'center' }}>
                    <Pill tone={r.matched ? 'ok' : 'bad'}>
                      {r.matched ? 'MATCH' : 'DRIFT'}
                    </Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── 9. LIVE SNOWFLAKE RESULTS & QUICK PBI BENCHMARK (OPTION 2) ─ */}
      {parsedSqlRows.length > 0 && (
        <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', overflow: 'hidden', background: 'var(--bg-surface)' }}>
          <div style={{
            padding: '12px 16px',
            background: 'var(--bg-surface-raised)',
            borderBottom: '1px solid var(--border-main)',
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 10,
          }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
              <strong style={{ fontSize: 13 }}>Live Snowflake Warehouse Execution Results</strong>
              <Pill tone="ok">{parsedSqlRows.length} rows returned</Pill>
              {sfTotalSum !== null && (
                <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  Sum of {numericCol}: <strong style={{ color: 'var(--color-success)', fontFamily: MONO }}>{sfTotalSum.toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
                </span>
              )}
            </div>

            {/* Quick Benchmark Comparison with Power BI screen */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <label style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
                Compare with Power BI visual total:
              </label>
              <input
                type="text"
                value={expectedPbiTotal}
                onChange={(e) => setExpectedPbiTotal(e.target.value)}
                placeholder="e.g. 150000 or 150,000"
                style={{ ...INPUT_STYLE, width: 150, fontSize: 11.5, padding: '4px 8px', fontFamily: MONO }}
              />
            </div>
          </div>

          {/* If user typed an expected benchmark total, show live comparison banner */}
          {benchmarkComparison && (
            <div style={{
              padding: '10px 16px',
              background: benchmarkComparison.isMatched ? 'rgba(34, 197, 94, 0.08)' : 'rgba(239, 68, 68, 0.08)',
              borderBottom: '1px solid var(--border-main)',
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {benchmarkComparison.isMatched ? (
                  <CheckCircle2 size={18} style={{ color: 'var(--color-success)' }} />
                ) : (
                  <AlertTriangle size={18} style={{ color: 'var(--color-error)' }} />
                )}
                <span style={{ fontSize: 12.5, fontWeight: 700 }}>
                  {benchmarkComparison.isMatched ? 'Values Match Benchmark!' : 'Discrepancy with Power BI Benchmark'}
                </span>
                <Pill tone={benchmarkComparison.isMatched ? 'ok' : 'bad'}>
                  {benchmarkComparison.isMatched ? 'MATCH' : 'DRIFT'}
                </Pill>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 16, fontSize: 12 }}>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Power BI: </span>
                  <strong style={{ fontFamily: MONO }}>{benchmarkComparison.pbiTotal.toLocaleString()}</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Snowflake: </span>
                  <strong style={{ fontFamily: MONO }}>{benchmarkComparison.sfTotal.toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Difference (Δ): </span>
                  <strong style={{ fontFamily: MONO }}>{benchmarkComparison.delta.toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Drift: </span>
                  <strong style={{ fontFamily: MONO, color: benchmarkComparison.isMatched ? 'var(--color-success)' : 'var(--color-error)' }}>
                    {benchmarkComparison.driftPct}%
                  </strong>
                </div>
              </div>
            </div>
          )}

          <div style={{ maxHeight: 320, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: 'var(--bg-app)', textAlign: 'left', color: 'var(--text-secondary)', position: 'sticky', top: 0, zIndex: 1 }}>
                  {sqlColumns.map((c) => (
                    <th key={c} style={{ padding: '8px 12px', fontWeight: 600, borderBottom: '1px solid var(--border-main)' }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {parsedSqlRows.slice(0, 100).map((row, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border-main)' }}>
                    {sqlColumns.map((col) => {
                      const val = row[col];
                      const isNum = typeof val === 'number';
                      return (
                        <td
                          key={col}
                          style={{
                            padding: '7px 12px',
                            fontFamily: isNum ? MONO : 'inherit',
                            textAlign: isNum ? 'right' : 'left',
                          }}
                        >
                          {isNum ? val.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(val ?? '')}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {parsedSqlRows.length > 100 && (
            <div style={{ padding: '6px 12px', fontSize: 11, color: 'var(--text-tertiary)', background: 'var(--bg-app)' }}>
              Showing first 100 of {parsedSqlRows.length} rows
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function AccessPanel({ gov }) {
  const {
    rlsRoleName,
    setRlsRoleName,
    rlsTableName,
    setRlsTableName,
    rlsDaxExpr,
    setRlsDaxExpr,
    rlsTargetDb,
    setRlsTargetDb,
    rlsTargetSchema,
    setRlsTargetSchema,
    runningRls,
    onTranslateRls,
    rlsDdl,
    rlsStatus,
    rlsReason,
  } = gov;
  const ready = rlsRoleName.trim() && rlsTableName.trim() && rlsDaxExpr.trim() && rlsTargetDb.trim() && rlsTargetSchema.trim();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        Translate exact Power BI column comparison rules (combined with AND / OR) into Snowflake ROW ACCESS POLICY DDL. Dynamic functions like USERPRINCIPALNAME() or RELATED() return "not translated".
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
        <input type="text" value={rlsTargetDb} onChange={(e) => setRlsTargetDb(e.target.value)} placeholder="Target database (required)" style={INPUT_STYLE} />
        <input type="text" value={rlsTargetSchema} onChange={(e) => setRlsTargetSchema(e.target.value)} placeholder="Target schema (required)" style={INPUT_STYLE} />
        <input type="text" value={rlsRoleName} onChange={(e) => setRlsRoleName(e.target.value)} placeholder="Role name" style={INPUT_STYLE} />
        <input type="text" value={rlsTableName} onChange={(e) => setRlsTableName(e.target.value)} placeholder="Table name" style={INPUT_STYLE} />
        <input
          type="text"
          value={rlsDaxExpr}
          onChange={(e) => setRlsDaxExpr(e.target.value)}
          placeholder='DAX filter, e.g. [Region] = "EMEA"'
          style={{ ...INPUT_STYLE, fontFamily: MONO }}
        />
        <button type="button" disabled={runningRls || !ready} onClick={onTranslateRls} style={{ ...BTN_PRIMARY, opacity: runningRls || !ready ? 0.6 : 1 }}>
          <Lock size={13} /> {runningRls ? 'Compiling…' : 'Compile to Snowflake'}
        </button>
      </div>
      {rlsStatus === 'not translated' && (
        <div style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--color-warning-bg)', color: 'var(--color-warning)', fontSize: 12 }}>
          <strong>Not translated:</strong> {rlsReason || 'Unsupported or ambiguous DAX filter.'}
        </div>
      )}
      {rlsDdl && (
        <div style={{ position: 'relative' }}>
          <button
            type="button"
            onClick={() => navigator.clipboard?.writeText(rlsDdl)}
            style={{ ...BTN_SM, position: 'absolute', top: 8, right: 8 }}
          >
            <Copy size={11} /> Copy
          </button>
          <pre style={{ ...PANEL, margin: 0, display: 'block', color: 'var(--color-success)', fontFamily: MONO, fontSize: 12, overflowX: 'auto' }}>
            {rlsDdl}
          </pre>
        </div>
      )}
    </div>
  );
}

export function QualityIssues({ issues, onOpenMetric, gov }) {
  const [expandedGroups, setExpandedGroups] = React.useState({});
  const toggleGroup = (k) => setExpandedGroups((prev) => ({ ...prev, [k]: !prev[k] }));

  // Group warnings (and >5 errors) by (rule_id, domain) while keeping rule_id unclipped.
  const groups = React.useMemo(() => {
    const map = {};
    for (const iss of issues) {
      const rule = iss.rule_id || 'RULE';
      const dom = iss.domain || 'Global';
      const key = `${iss.severity}::${rule}::${dom}`;
      if (!map[key]) map[key] = { key, severity: iss.severity, rule, domain: dom, items: [] };
      map[key].items.push(iss);
    }
    return Object.values(map);
  }, [issues]);

  const stewardSelector = gov?.setLintStewardSeverity && (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 12 }}>
      <span style={{ color: 'var(--text-secondary)' }}>Data Steward Rule:</span>
      <select
        value={gov.lintStewardSeverity}
        onChange={(e) => {
          gov.setLintStewardSeverity(e.target.value);
          gov.onRunLintWithSteward?.(e.target.value);
        }}
        style={{ ...SELECT_STYLE, fontSize: 12 }}
        aria-label="Data Steward Rule severity"
      >
        <option value="WARNING">WARNING (Recommended)</option>
        <option value="ERROR">ERROR</option>
        <option value="OFF">OFF</option>
      </select>
    </div>
  );

  if (!issues.length) {
    return (
      <div>
        {stewardSelector}
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>No issues found.</div>
      </div>
    );
  }

  return (
    <div>
      {stewardSelector}
      <div style={{ ...PANEL, gap: 0, padding: 0 }}>
        {groups.map((grp, gIdx) => {
          const shouldGroup = grp.items.length > 1 && (grp.severity !== 'ERROR' || grp.items.length > 5);
          if (!shouldGroup) {
            return grp.items.map((iss, idx) => {
              const name = iss.object_name || iss.target_name;
              return (
                <div
                  key={`${grp.key}-${idx}`}
                  style={{
                    padding: '10px 14px',
                    borderTop: gIdx || idx ? '1px solid var(--border-main)' : 'none',
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 10,
                    fontSize: 12,
                  }}
                >
                  <Pill tone={iss.severity === 'ERROR' ? 'bad' : iss.severity === 'INFO' ? 'n' : 'warn'}>{iss.severity}</Pill>
                  <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap', flexShrink: 0 }}>
                    {grp.rule}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div>
                      <b>{name}</b>
                      {iss.domain && <span style={{ color: 'var(--text-tertiary)' }}> · {iss.domain}</span>}
                    </div>
                    <div style={{ color: 'var(--text-secondary)', marginTop: 2 }}>{iss.message}</div>
                    {iss.remediation && <div style={{ color: 'var(--text-tertiary)', marginTop: 2 }}>{iss.remediation}</div>}
                  </div>
                  {onOpenMetric && iss.object_type === 'METRIC' && iss.domain && !iss.domain.includes(',') && (
                    <button type="button" style={BTN_SM} onClick={() => onOpenMetric(name, iss.domain)}>
                      Open
                    </button>
                  )}
                </div>
              );
            });
          }

          const isOpen = !!expandedGroups[grp.key];
          return (
            <div key={grp.key} style={{ borderTop: gIdx ? '1px solid var(--border-main)' : 'none' }}>
              <div
                style={{
                  padding: '10px 14px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  fontSize: 12,
                }}
              >
                <Pill tone={grp.severity === 'ERROR' ? 'bad' : 'warn'}>{grp.severity}</Pill>
                <span style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-primary)', fontWeight: 700, whiteSpace: 'nowrap', flexShrink: 0 }}>
                  {grp.rule}
                </span>
                <span style={{ color: 'var(--text-secondary)', flex: 1 }}>
                  {grp.domain} · <b>{grp.items.length} items</b>
                </span>
                <button type="button" style={BTN_SM} onClick={() => toggleGroup(grp.key)}>
                  {isOpen ? 'Hide items' : `Inspect ${grp.items.length}`}
                </button>
              </div>
              {isOpen && (
                <div style={{ padding: '0 14px 10px 32px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {grp.items.map((iss, idx) => {
                    const name = iss.object_name || iss.target_name;
                    return (
                      <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5, color: 'var(--text-secondary)' }}>
                        <b style={{ color: 'var(--text-primary)', fontFamily: MONO }}>{name}</b>
                        <span style={{ flex: 1 }}>{iss.message}</span>
                        {onOpenMetric && iss.object_type === 'METRIC' && iss.domain && !iss.domain.includes(',') && (
                          <button type="button" style={BTN_SM} onClick={() => onOpenMetric(name, iss.domain)}>
                            Open
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CollisionDetail({ collision, onOpenMetric }) {
  const doms = collision.conflicting_domains || Object.keys(collision.expressions || {});
  return (
    <div style={{ ...PANEL, gap: 8 }}>
      {doms.map((d) => (
        <div key={d} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, fontSize: 12 }}>
          <b style={{ minWidth: 140 }}>{d}</b>
          <div style={{ flex: 1, minWidth: 0 }}>
            <code style={{ fontFamily: MONO, fontSize: 11.5 }}>{collision.expressions?.[d] || 'no formula'}</code>
            {collision.descriptions?.[d] && (
              <div style={{ color: 'var(--text-secondary)', marginTop: 2 }}>{collision.descriptions[d]}</div>
            )}
          </div>
          {onOpenMetric && (
            <button type="button" style={BTN_SM} onClick={() => onOpenMetric(collision.item_name, d)}>
              Open in {d}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

export function SimpleList({ items }) {
  return (
    <div style={{ ...PANEL, gap: 4 }}>
      {items.map((x, i) => (
        <code key={i} style={{ fontFamily: MONO, fontSize: 11.5, whiteSpace: 'pre-wrap' }}>
          {typeof x === 'object' ? JSON.stringify(x) : String(x)}
        </code>
      ))}
    </div>
  );
}
