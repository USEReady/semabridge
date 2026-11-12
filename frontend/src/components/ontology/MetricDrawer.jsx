import React, { useEffect, useState } from 'react';
import { X, Sparkles, Code2, GitBranch, Pencil, Check } from 'lucide-react';
import { BTN, BTN_SM, BTN_PRIMARY, INPUT_STYLE, LABEL, MONO, plural } from './styles';
import { Pill } from './ui';

function QuickAction({ name, text, children }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        border: '1px solid var(--border-main)',
        borderRadius: 8,
        padding: '8px 10px',
      }}
    >
      <b style={{ fontSize: 12, minWidth: 42 }}>{name}</b>
      <span style={{ fontSize: 11.5, color: 'var(--text-secondary)', flex: 1, minWidth: 0 }}>{text}</span>
      {children}
    </div>
  );
}

function Chip({ children, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      style={{
        fontFamily: MONO,
        fontSize: 11,
        border: '1px solid var(--border-main)',
        background: 'var(--bg-surface)',
        color: 'var(--text-primary)',
        padding: '2px 7px',
        borderRadius: 5,
        cursor: onClick ? 'pointer' : 'default',
      }}
    >
      {children}
    </button>
  );
}

/**
 * Right-hand drawer for the selected metric: formula, lineage and the
 * "Before you change it" checks (Impact runs automatically).
 */
export default function MetricDrawer({
  metric,
  status,
  certification,
  guardrail,
  domainMetricNames,
  impact,
  impactLoading,
  lintIssues,
  lintLoading,
  lintRan,
  parity,
  onClose,
  onSelectMetric,
  onUpdateMeta,
  onImpactView,
  onParity,
  onLintView,
  onAsk,
  onEditYaml,
  onShowMap,
}) {
  const [editingMeta, setEditingMeta] = useState(false);
  const [descDraft, setDescDraft] = useState('');
  const [synDraft, setSynDraft] = useState('');
  const [stewardDraft, setStewardDraft] = useState('');
  const [rulesDraft, setRulesDraft] = useState('');
  const [savingMeta, setSavingMeta] = useState(false);

  useEffect(() => {
    setEditingMeta(false);
    setDescDraft(metric?.description || '');
    setSynDraft((metric?.synonyms || []).join(', '));
    setStewardDraft(metric?.data_steward || '');
    setRulesDraft((metric?.business_rules || []).join('\n'));
  }, [metric?.name, metric?.description, metric?.data_steward]);

  const handleSaveMeta = async () => {
    if (!onUpdateMeta) return;
    setSavingMeta(true);
    try {
      await onUpdateMeta(metric.name, {
        description: descDraft.trim() || null,
        synonyms: synDraft.split(',').map((s) => s.trim()).filter(Boolean),
        data_steward: stewardDraft.trim() || null,
        business_rules: rulesDraft.split(/\r?\n/).map((r) => r.trim()).filter(Boolean),
      });
      setEditingMeta(false);
    } finally {
      setSavingMeta(false);
    }
  };
  let impactText = 'Checking…';
  if (!impactLoading && impact) {
    const parts = [];
    const r = impact.affected_bi_reports?.length || 0;
    const a = impact.affected_ai_tools?.length || 0;
    const k = impact.affected_metrics?.length || 0;
    if (r) parts.push(plural(r, 'report'));
    if (a) parts.push(plural(a, 'AI agent'));
    if (k) parts.push(plural(k, 'metric'));
    impactText = parts.length ? parts.join(', ') : 'Nothing downstream';
  } else if (!impactLoading) {
    impactText = 'Not available';
  }

  let lintText = 'Checking…';
  if (!lintLoading && lintRan) lintText = lintIssues.length ? lintIssues[0].message : 'Passes';
  else if (!lintLoading) lintText = 'Not checked';

  return (
    <aside
      aria-label={`${metric.name} details`}
      style={{
        borderLeft: '1px solid var(--border-main)',
        background: 'var(--bg-surface)',
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        boxShadow: '-12px 0 24px -18px rgba(0,0,0,.35)',
        minWidth: 0,
        overflowY: 'auto',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <h4 style={{ margin: 0, fontSize: 14, fontFamily: MONO, wordBreak: 'break-word' }}>{metric.name}</h4>
        <Pill tone={status.tone}>{status.label}</Pill>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', padding: 2 }}
        >
          <X size={16} />
        </button>
      </div>

      {metric.label && metric.label !== metric.name && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: -6 }}>{metric.label}</div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <Pill tone={certification?.status === 'CERTIFIED' ? 'ok' : certification?.status === 'FAILED' ? 'bad' : 'n'}>
          {certification?.status === 'CERTIFIED' ? 'Certified' : certification?.status === 'FAILED' ? 'Failed' : 'Not checked'}
        </Pill>
        {certification?.certified_at && (
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
            {new Date(certification.certified_at).toLocaleDateString()} {certification.certified_by ? `by ${certification.certified_by}` : ''}
          </span>
        )}
        <span style={{ flex: 1 }} />
        {onUpdateMeta && !editingMeta && (
          <button type="button" style={BTN_SM} onClick={() => setEditingMeta(true)}>
            <Pencil size={11} /> Edit metadata
          </button>
        )}
      </div>

      {editingMeta ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 10, borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-app)' }}>
          <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            Business description
            <textarea rows={2} value={descDraft} onChange={(e) => setDescDraft(e.target.value)} style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', marginTop: 3 }} />
          </label>
          <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            Alternative names / synonyms (comma-separated)
            <input type="text" value={synDraft} onChange={(e) => setSynDraft(e.target.value)} style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', marginTop: 3 }} />
          </label>
          <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            Data steward / owner
            <input type="text" value={stewardDraft} onChange={(e) => setStewardDraft(e.target.value)} style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', marginTop: 3 }} />
          </label>
          <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            Business rules (one per line)
            <textarea rows={2} value={rulesDraft} onChange={(e) => setRulesDraft(e.target.value)} style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', marginTop: 3 }} />
          </label>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
            <button type="button" style={BTN_SM} onClick={() => setEditingMeta(false)}>Cancel</button>
            <button type="button" style={BTN_PRIMARY} disabled={savingMeta} onClick={handleSaveMeta}>
              <Check size={12} /> {savingMeta ? 'Saving…' : 'Save metadata'}
            </button>
          </div>
        </div>
      ) : (
        <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          {metric.description || 'No description yet.'}
        </span>
      )}

      <pre
        style={{
          margin: 0,
          padding: '10px 12px',
          borderRadius: 8,
          background: 'var(--bg-app)',
          border: '1px solid var(--border-main)',
          fontFamily: MONO,
          fontSize: 11.5,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          color: metric.sql_expression ? 'var(--text-primary)' : 'var(--text-tertiary)',
        }}
      >
        {metric.sql_expression || 'No formula defined'}
      </pre>

      {metric.derives_from?.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text-tertiary)' }}>
          Derived from
          {metric.derives_from.map((d) => (
            <Chip key={d} onClick={domainMetricNames.has(d) ? () => onSelectMetric(d) : undefined}>
              {d}
            </Chip>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 12px', fontSize: 12 }}>
        <span style={{ color: 'var(--text-tertiary)' }}>Owner</span>
        <span>{metric.data_steward || '—'}</span>
        <span style={{ color: 'var(--text-tertiary)' }}>Guardrail</span>
        <span style={{ fontFamily: guardrail !== '—' ? MONO : undefined, wordBreak: 'break-word' }}>{guardrail}</span>
        {metric.concept && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>Concept</span>
            <span>{metric.concept}</span>
          </>
        )}
        {(metric.maps_to || []).length > 0 && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>BI measures</span>
            <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {metric.maps_to.map((m) => (
                <Chip key={m}>{m}</Chip>
              ))}
            </span>
          </>
        )}
        {(metric.synonyms || []).length > 0 && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>Synonyms</span>
            <span>{metric.synonyms.join(', ')}</span>
          </>
        )}
        {(metric.business_rules || []).length > 0 && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>Rules</span>
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {metric.business_rules.map((r, idx) => (
                <li key={idx}>{r}</li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <span style={LABEL}>Before you change it</span>
        <QuickAction name="Impact" text={impactText}>
          <button type="button" style={BTN_SM} onClick={onImpactView} disabled={impactLoading}>
            View
          </button>
        </QuickAction>
        <QuickAction
          name="Parity"
          text={
            parity
              ? parity.drift
                ? `Power BI vs SQL: ${parity.deltaPct}% off on ${parity.slice}`
                : 'Power BI and SQL match'
              : 'Not checked yet'
          }
        >
          <button type="button" style={BTN_SM} onClick={onParity}>
            {parity?.drift ? 'Fix' : 'Check'}
          </button>
        </QuickAction>
        <QuickAction name="Lint" text={lintText}>
          {lintRan && !lintLoading && lintIssues.length === 0 ? (
            <Pill tone="ok">OK</Pill>
          ) : (
            <button type="button" style={BTN_SM} onClick={onLintView} disabled={lintLoading}>
              {lintIssues.length > 1 ? `${lintIssues.length} issues` : 'View'}
            </button>
          )}
        </QuickAction>
      </div>

      <button type="button" style={{ ...BTN, justifyContent: 'center' }} onClick={onAsk}>
        <Sparkles size={13} /> Ask AI about {metric.name}
      </button>
      <div style={{ display: 'flex', gap: 6 }}>
        <button type="button" style={{ ...BTN_SM, flex: 1 }} onClick={onEditYaml}>
          <Code2 size={12} /> Edit in YAML
        </button>
        <button type="button" style={{ ...BTN_SM, flex: 1 }} onClick={onShowMap}>
          <GitBranch size={12} /> Show on map
        </button>
      </div>
    </aside>
  );
}
