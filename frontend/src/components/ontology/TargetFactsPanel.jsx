// Read-only target facts for one project (GET /api/ontology/projects/{id}/target-facts):
// per measure, every metric-view occurrence with its CURRENT PLANNED expression and its
// LAST VERIFIED deployed expression, kept apart; plus why left-out measures were left
// out. Shown instead of the Snowflake-only parity tools when the target has no parity
// check (e.g. Databricks). Never runs anything.
import React, { useMemo, useState } from 'react';
import { MONO, INPUT_STYLE } from './styles';
import { Pill } from './ui';
import { sqlLabel, targetDisplayName, verifiedText } from '../../utils/targetFeatures.js';

function ExprBox({ text, muted }) {
  return (
    <div style={{
      fontFamily: MONO, fontSize: 11, padding: '6px 8px', borderRadius: 6,
      background: 'var(--bg-main)', border: '1px solid var(--border-main)',
      color: muted ? 'var(--text-tertiary)' : 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
    }}>
      {text || '—'}
    </div>
  );
}

function OccurrenceRow({ occ }) {
  const p = occ.planned;
  const v = occ.verified;
  const differs = p && v && v.status === 'verified' && p.expression !== v.expression;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, padding: '8px 0', borderTop: '1px solid var(--border-main)' }}>
      <div style={{ gridColumn: '1 / -1', fontSize: 11, color: 'var(--text-secondary)', display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontFamily: MONO }}>{occ.view}</span>
        <span>→</span>
        <span style={{ fontFamily: MONO, color: 'var(--text-primary)' }}>{occ.name}</span>
        {(p?.readiness === 'structure_only' || p?.readiness === 'INCOMPLETE') && <Pill tone="warn">INCOMPLETE (empty tables)</Pill>}
        {(p?.approximate || v?.approximate) && <Pill tone="warn">approximate</Pill>}
        {v?.note && v.status === 'verified' && <Pill tone="warn">live state unresolved</Pill>}
        {differs && <Pill tone="n">plan differs from deployed</Pill>}
      </div>
      <div>
        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>
          Current plan{p?.revision ? ` · revision ${p.revision}` : ''}
        </div>
        <ExprBox text={p ? p.expression : 'not in the current plan'} muted={!p} />
      </div>
      <div>
        <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', marginBottom: 4 }}>
          Last verified deployment · {verifiedText(v)}
        </div>
        <ExprBox text={v && v.status === 'verified' ? v.expression : (v?.note || 'no verified deployment')} muted={!(v && v.status === 'verified')} />
      </div>
    </div>
  );
}

export default function TargetFactsPanel({ facts, features, target, loading, error }) {
  const [query, setQuery] = useState('');
  const measures = useMemo(() => {
    const items = Object.values(facts?.items || {}).filter((i) => i.kind === 'measure');
    const q = query.trim().toLowerCase();
    return items
      .filter((i) => !q || String(i.name).toLowerCase().includes(q))
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  }, [facts, query]);
  const label = sqlLabel(features, 'SQL');
  const name = targetDisplayName(target);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        Live parity checks are not available for {name} yet. Below is each measure&apos;s {label}: the
        current plan and the last verified deployment, shown separately. Structural verification does not
        mean the numbers have been checked against Power BI.
      </div>
      {loading && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading…</div>}
      {error && <div style={{ fontSize: 12, color: 'var(--accent-red, #f87171)' }}>{error}</div>}
      {!loading && facts && !facts.available && (
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          {(facts.notes || []).join(' ') || 'Nothing planned or deployed yet.'}
        </div>
      )}
      {facts?.available && (
        <>
          <input
            type="search"
            placeholder="Filter measures…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ ...INPUT_STYLE, maxWidth: 280 }}
          />
          {(facts.notes || []).map((n) => (
            <div key={n} style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{n}</div>
          ))}
          {measures.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>No measures match.</div>}
          {measures.map((m) => (
            <div key={m.key || m.name} style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, fontWeight: 600 }}>
                <span>{m.name}</span>
                {m.left_out && <Pill tone="bad">left out</Pill>}
                {!m.left_out && (m.occurrences || []).length > 1 && <Pill tone="n">{m.occurrences.length} views</Pill>}
              </div>
              {m.left_out && (
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                  {m.left_out.detail || m.left_out.reason}
                </div>
              )}
              {(m.occurrences || []).map((occ) => <OccurrenceRow key={occ.occurrence_id} occ={occ} />)}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
