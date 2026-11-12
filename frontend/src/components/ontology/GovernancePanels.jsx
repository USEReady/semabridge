// Detail panels for the Health & Governance view: Blast Radius Impact,
// "What if…" simulator, Golden Parity Reconciliation, RLS Access Policy,
// Grouped Quality Linter Issues, and Collision Details.
import React from 'react';
import { Play, CheckCheck, Lock, Copy, Download, Upload } from 'lucide-react';
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

function ImpactGroup({ title, items, tone }) {
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
            return (
              <span
                key={i}
                title={obj?.details || ''}
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
                }}
              >
                <span>{itemLabel(it)}</span>
                {obj && (
                  <span style={{ fontSize: 9.5, fontWeight: 700, color: hop === 'TRANSITIVE' ? 'var(--color-warning)' : 'var(--accent-blue)' }}>
                    [{hop}]
                  </span>
                )}
                {src && <span style={{ fontSize: 9.5, color: 'var(--text-tertiary)' }}>[{src}]</span>}
                {recert && <span style={{ fontSize: 9.5, fontWeight: 700, color: 'var(--color-error)' }}>[Re-certify]</span>}
              </span>
            );
          })}
          {items.length > 12 && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>+{items.length - 12}</span>}
        </div>
      )}
    </div>
  );
}

export function ImpactReport({ report, onPickSuggestion }) {
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
          <ImpactGroup title="Metrics" items={report.affected_metrics || []} tone="var(--color-warning)" />
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
        </div>
      )}
    </div>
  );
}

export function WhatIfSimulator({ gov, metricOptions }) {
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
        onPickSuggestion={(sug) => {
          setBlastTarget(sug);
          onRunBlast(sug);
        }}
      />
    </div>
  );
}

export function ParityPanel({ gov, onOpenMetric, metricOptions = [], dimensionOptions = [], semanticViewOptions = [] }) {
  const {
    reconMetric,
    setReconMetric,
    reconExpr,
    setReconExpr,
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
  } = gov;
  const [showQueries, setShowQueries] = React.useState(false);

  const handleDaxFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = String(reader.result || '').trim();
      if (!content) return;
      if (file.name.toLowerCase().endsWith('.json')) {
        setReconDaxText(content);
        return;
      }
      // Simple CSV parser -> JSON rows
      const lines = content.split(/\r?\n/).filter((l) => l.trim());
      if (lines.length < 2) return;
      const headers = lines[0].split(',').map((h) => h.trim().replace(/^"|"$/g, ''));
      const rows = lines.slice(1).map((line) => {
        const vals = line.split(',').map((v) => v.trim().replace(/^"|"$/g, ''));
        const obj = {};
        headers.forEach((h, idx) => {
          const raw = vals[idx] ?? '';
          const num = Number(raw);
          obj[h] = raw !== '' && Number.isFinite(num) ? num : raw;
        });
        return obj;
      });
      setReconDaxText(JSON.stringify(rows, null, 2));
    };
    reader.readAsText(file);
  };

  const exportDaxCsv = () => {
    if (!reconSliceRows.length) return;
    const header = 'Slice,PowerBI_DAX,Warehouse_SQL,Delta_Pct,Matched';
    const body = reconSliceRows
      .map((r) => `"${String(r.slice).replace(/"/g, '""')}",${r.daxValue},${r.sqlValue},${r.deltaPct},${r.matched}`)
      .join('\n');
    const blob = new Blob([`${header}\n${body}\n`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${reconMetric || 'parity'}_reconciliation.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 10 }}>
        <input
          type="text"
          list="parity-metric-list"
          value={reconMetric}
          onChange={(e) => setReconMetric(e.target.value)}
          placeholder="Metric name or TABLE.METRIC"
          style={INPUT_STYLE}
        />
        <datalist id="parity-metric-list">
          {metricOptions.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>

        <input
          type="text"
          list="parity-view-list"
          value={reconTable}
          onChange={(e) => setReconTable(e.target.value)}
          placeholder="Deployed semantic view or table"
          style={INPUT_STYLE}
        />
        <datalist id="parity-view-list">
          {semanticViewOptions.map((v) => (
            <option key={v} value={v} />
          ))}
        </datalist>

        <select value={reconTargetKind || 'semantic_view'} onChange={(e) => setReconTargetKind?.(e.target.value)} style={SELECT_STYLE}>
          <option value="semantic_view">Target: SEMANTIC_VIEW</option>
          <option value="table">Target: Table / View</option>
        </select>

        <input
          type="text"
          list="parity-dim-list"
          value={reconDims}
          onChange={(e) => setReconDims(e.target.value)}
          placeholder="Dimensions (comma separated)"
          style={INPUT_STYLE}
        />
        <datalist id="parity-dim-list">
          {dimensionOptions.map((d) => (
            <option key={d} value={d} />
          ))}
        </datalist>

        <input
          type="text"
          value={reconExpr}
          onChange={(e) => setReconExpr(e.target.value)}
          placeholder="SQL expression / TABLE.METRIC"
          style={{ ...INPUT_STYLE, fontFamily: MONO }}
        />
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, fontSize: 12 }}>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          Abs tolerance
          <input
            type="number"
            min="0"
            step="0.01"
            value={reconAbsTol}
            onChange={(e) => setReconAbsTol(e.target.value)}
            style={{ ...INPUT_STYLE, width: 76 }}
          />
        </label>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          Rel tolerance
          <input
            type="number"
            min="0"
            step="0.0001"
            value={reconRelTol}
            onChange={(e) => setReconRelTol(e.target.value)}
            style={{ ...INPUT_STYLE, width: 84 }}
          />
        </label>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          Max mismatch %
          <input
            type="number"
            min="0"
            max="100"
            step="0.1"
            value={reconMaxMismatchPct}
            onChange={(e) => setReconMaxMismatchPct(e.target.value)}
            style={{ ...INPUT_STYLE, width: 72 }}
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

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <button type="button" style={BTN_SM} disabled={runningRecon || !reconMetric.trim() || !reconTable.trim()} onClick={onGenerateReconQueries}>
          Generate DAX &amp; SQL
        </button>
        <button type="button" style={BTN_SM} disabled={runningRecon || !reconQueries?.sql_query} onClick={onRunLiveSql}>
          Run Live Snowflake SQL
        </button>
        <label style={{ ...BTN_SM, cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          <Upload size={12} /> Upload DAX JSON/CSV
          <input type="file" accept=".json,.csv" style={{ display: 'none' }} onChange={(e) => handleDaxFile(e.target.files?.[0])} />
        </label>
        <button
          type="button"
          disabled={runningRecon || !reconMetric.trim() || !reconTable.trim()}
          onClick={onRunAutoDaxCertify}
          style={{ ...BTN_PRIMARY, opacity: runningRecon || !reconMetric.trim() || !reconTable.trim() ? 0.6 : 1 }}
        >
          <CheckCheck size={13} /> {runningRecon ? 'Running…' : 'Auto-Run DAX & Certify'}
        </button>
        <button
          type="button"
          disabled={runningRecon || !reconMetric.trim() || !reconDaxText?.trim() || !reconSqlText?.trim()}
          onClick={onRunRecon}
          style={{ ...BTN_SM, opacity: runningRecon || !reconDaxText?.trim() || !reconSqlText?.trim() ? 0.6 : 1 }}
        >
          Verify uploaded rows
        </button>
        {onResetRecon && (reconResult || reconQueries || reconSliceRows.length > 0 || reconError || reconDaxText || reconSqlText) && (
          <button type="button" style={BTN_SM} disabled={runningRecon} onClick={onResetRecon}>
            Reset
          </button>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>Power BI DAX result rows (JSON array)</div>
          <textarea
            rows={4}
            value={reconDaxText}
            onChange={(e) => setReconDaxText(e.target.value)}
            placeholder="Paste DAX JSON rows or upload CSV/JSON above"
            style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', fontFamily: MONO, fontSize: 11.5 }}
          />
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>Warehouse SQL result rows (JSON array)</div>
          <textarea
            rows={4}
            value={reconSqlText}
            onChange={(e) => setReconSqlText(e.target.value)}
            placeholder="Populated by Run Live Snowflake SQL or paste SQL JSON rows"
            style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', fontFamily: MONO, fontSize: 11.5 }}
          />
        </div>
      </div>

      {reconError && (
        <div style={{ padding: '8px 12px', borderRadius: 8, background: 'var(--color-error-bg)', color: 'var(--color-error)', fontSize: 12 }}>
          {reconError}
        </div>
      )}

      {reconResult && (
        <div style={{ ...PANEL, padding: 12, gap: 8 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
            <Pill tone={reconResult.status === 'CERTIFIED' ? 'ok' : 'bad'}>{reconResult.status}</Pill>
            <strong style={{ fontSize: 12.5 }}>{reconResult.certification_badge}</strong>
            <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {reconResult.matched_rows}/{reconResult.total_rows} rows matched
            </span>
          </div>
          {reconResult.column_mapping && Object.keys(reconResult.column_mapping).length > 0 && (
            <div style={{ fontSize: 11.5, color: 'var(--text-secondary)', fontFamily: MONO }}>
              Column mapping: {Object.entries(reconResult.column_mapping).map(([k, v]) => `${k} → ${v}`).join(', ')}
            </div>
          )}
          {Array.isArray(reconResult.errors) && reconResult.errors.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--color-error)' }}>{reconResult.errors.join('; ')}</div>
          )}
        </div>
      )}

      {reconSliceRows.length > 0 && (
        <div style={{ borderRadius: 10, border: '1px solid var(--border-main)', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, background: 'var(--bg-surface)' }}>
            <thead>
              <tr style={{ background: 'var(--bg-surface-raised)', textAlign: 'left', color: 'var(--text-secondary)' }}>
                <th style={{ padding: '8px 12px', fontWeight: 600 }}>Slice</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Power BI DAX</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Warehouse SQL</th>
                <th style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>Δ</th>
              </tr>
            </thead>
            <tbody>
              {reconSliceRows.map((r, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--border-main)' }}>
                  <td style={{ padding: '8px 12px', fontWeight: 600 }}>{r.slice}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO }}>{Number(r.daxValue).toLocaleString()}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO }}>{Number(r.sqlValue).toLocaleString()}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: MONO, color: r.matched ? 'var(--color-success)' : 'var(--color-error)' }}>
                    {r.deltaPct}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {onOpenMetric && reconMetric && (
          <button type="button" style={BTN_SM} onClick={() => onOpenMetric(reconMetric)}>
            Open {reconMetric}
          </button>
        )}
        {reconQueries && (
          <button type="button" style={BTN_SM} onClick={() => setShowQueries((v) => !v)}>
            {showQueries ? 'Hide queries' : 'Show both queries'}
          </button>
        )}
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

      {showQueries && reconQueries && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
          {[
            ['Power BI DAX', reconQueries.dax_query],
            ['Warehouse SQL', reconQueries.sql_query],
          ].map(([t, q]) => (
            <div key={t}>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>{t}</div>
              <pre style={{ ...PANEL, margin: 0, fontFamily: MONO, fontSize: 11.5, whiteSpace: 'pre-wrap', display: 'block' }}>{q}</pre>
            </div>
          ))}
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
