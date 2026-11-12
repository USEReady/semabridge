import React from 'react';
import { ShieldCheck } from 'lucide-react';
import { LABEL, pill } from './askStyles';

const ITEM = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  width: '100%',
  textAlign: 'left',
  padding: '7px 9px',
  borderRadius: 8,
  border: '1px solid transparent',
  background: 'none',
  color: 'var(--text-secondary)',
  fontSize: 12.5,
  lineHeight: 1.4,
  cursor: 'pointer',
};

/**
 * Right rail: dynamic suggestions derived from the project's ontology and
 * verified queries persisted per project in the database.
 */
export default function AskRail({ suggestions = [], certified = [], onPick, disabled }) {
  return (
    <aside
      aria-label="Suggested questions"
      style={{
        borderLeft: '1px solid var(--border-main)',
        padding: '16px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        overflowY: 'auto',
        minHeight: 0,
      }}
    >
      <span style={LABEL}>Try asking</span>
      {suggestions.length === 0 ? (
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '4px 9px', lineHeight: 1.45 }}>
          Select a project with an ontology to generate prompt suggestions.
        </span>
      ) : (
        suggestions.map((q) => (
          <button key={q} type="button" style={ITEM} disabled={disabled} onClick={() => onPick(q)} className="ask-rail-item">
            {q}
          </button>
        ))
      )}

      <span style={{ ...LABEL, marginTop: 14 }}>Certified questions</span>
      {certified.length === 0 ? (
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: '4px 9px', lineHeight: 1.45 }}>
          Certified queries for this project appear here.
        </span>
      ) : (
        certified.map((c) => (
          <button key={c.question} type="button" style={ITEM} disabled={disabled} onClick={() => onPick(c.question)} className="ask-rail-item">
            <span style={{ flex: 1, minWidth: 0 }}>{c.question}</span>
            <span style={pill('ok')} title={`Saved as a verified query (${c.target || 'SNOWFLAKE'})`}>
              <ShieldCheck size={11} /> VQR
            </span>
          </button>
        ))
      )}
    </aside>
  );
}
