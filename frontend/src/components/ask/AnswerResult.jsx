import React from 'react';
import { toNumber } from './askApi';

/**
 * Shows a query result: a horizontal bar chart when the result is exactly one
 * label column + one numeric column, otherwise a compact table.
 */
function barShape(columns, rows) {
  if (columns?.length !== 2 || !rows?.length || rows.length > 30) return null;
  const numericCol = [0, 1].find((ci) => rows.every((r) => toNumber(r[ci]) !== null));
  if (numericCol === undefined) return null;
  const labelCol = numericCol === 0 ? 1 : 0;
  // Two numeric columns: not a "dimension + number" result.
  if (rows.every((r) => toNumber(r[labelCol]) !== null)) return null;
  return { labelCol, numericCol };
}

export default function AnswerResult({ columns, rows }) {
  if (!columns?.length) {
    return <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>The query returned no columns.</div>;
  }
  if (!rows?.length) {
    return <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>No rows matched this question.</div>;
  }

  const shape = barShape(columns, rows);
  if (shape) {
    const values = rows.map((r) => toNumber(r[shape.numericCol]));
    const max = Math.max(...values.map((v) => Math.abs(v)), 0) || 1;
    return (
      <div role="list" aria-label={`${columns[shape.numericCol]} by ${columns[shape.labelCol]}`} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', fontSize: 11, color: 'var(--text-tertiary)', gap: 10 }}>
          <span style={{ width: 160, flexShrink: 0 }}>{columns[shape.labelCol]}</span>
          <span>{columns[shape.numericCol]}</span>
        </div>
        {rows.map((r, i) => (
          <div key={i} role="listitem" style={{ display: 'grid', gridTemplateColumns: '160px minmax(0,1fr) auto', alignItems: 'center', gap: 10, fontSize: 12.5 }}>
            <span title={String(r[shape.labelCol] ?? '')} style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {String(r[shape.labelCol] ?? '—')}
            </span>
            <span style={{ height: 12, borderRadius: 3, background: 'var(--bg-surface-raised)', overflow: 'hidden' }}>
              <span style={{ display: 'block', height: '100%', width: `${Math.max(2, (Math.abs(values[i]) / max) * 100)}%`, background: 'var(--accent-blue)', borderRadius: 3 }} />
            </span>
            <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--text-primary)', fontWeight: 600, textAlign: 'right', minWidth: 64 }}>
              {String(r[shape.numericCol])}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 8, overflowX: 'auto', maxHeight: 360 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ background: 'var(--bg-surface-raised)', textAlign: 'left' }}>
            {columns.map((c, i) => (
              <th key={i} style={{ padding: '7px 10px', borderBottom: '1px solid var(--border-main)', fontWeight: 600, color: 'var(--text-secondary)', whiteSpace: 'nowrap', position: 'sticky', top: 0, background: 'var(--bg-surface-raised)' }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri} style={{ borderBottom: ri === rows.length - 1 ? 'none' : '1px solid var(--border-main)' }}>
              {r.map((cell, ci) => (
                <td key={ci} style={{ padding: '7px 10px', color: 'var(--text-primary)', whiteSpace: 'nowrap', textAlign: toNumber(cell) !== null ? 'right' : 'left', fontVariantNumeric: 'tabular-nums' }}>
                  {cell === null || cell === undefined ? <span style={{ color: 'var(--text-tertiary)' }}>NULL</span> : String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
