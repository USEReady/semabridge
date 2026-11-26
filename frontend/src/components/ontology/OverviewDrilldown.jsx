import React, { useMemo, useState } from 'react';
import { X, Search } from 'lucide-react';
import { authFetch } from '../../utils/api';
import { BTN, CARD, describeApiError } from './styles';
import { conceptRows, matchesQuery, metricRows, splitCollisions } from '../../utils/ontologyDrilldown';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace';
const TITLES = { metrics: 'Governed metrics', concepts: 'Business concepts', attention: 'Needs attention' };

function Pill({ children, tone = 'n' }) {
  const tones = {
    bad: { color: 'var(--color-error)', bg: 'var(--color-error-bg)' },
    warn: { color: 'var(--color-warning)', bg: 'var(--color-warning-bg)' },
    ok: { color: 'var(--color-success)', bg: 'var(--color-success-bg)' },
    n: { color: 'var(--text-secondary)', bg: 'var(--bg-surface-raised)' },
  };
  const t = tones[tone] || tones.n;
  return (
    <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, color: t.color, background: t.bg }}>
      {children}
    </span>
  );
}

function ConflictCard({ col, onResolved, onOpenDomain }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const domains = col.conflicting_domains || [];

  const resolve = async (body, label) => {
    setBusy(true);
    setMsg('');
    try {
      const res = await authFetch(`/api/ontology/projects/${encodeURIComponent(col.resolvableIn)}/conflict-resolution`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(describeApiError(data, `Could not save (${res.status})`));
      setMsg(`Saved: ${label}. Applied on the project's next sync.`);
      onResolved?.();
    } catch (err) {
      setMsg(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...CARD, padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>{col.item_name}</b>
        <Pill tone={col.kind === 'conflict' ? 'bad' : 'n'}>
          {col.kind === 'conflict' ? 'Different formulas' : 'Same name, same formula'}
        </Pill>
        <Pill>{col.item_type}</Pill>
      </div>
      {domains.map((d) => (
        <div key={d} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 34%) 1fr', gap: 10, fontSize: 12 }}>
          <button
            type="button"
            onClick={() => onOpenDomain?.(d)}
            style={{ textAlign: 'left', background: 'none', border: 'none', padding: 0, color: 'var(--accent-blue)', cursor: 'pointer', fontSize: 12 }}
            title="Open this domain"
          >
            {d}
          </button>
          <code style={{ fontFamily: MONO, fontSize: 11.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>
            {(col.expressions || {})[d] || '(no formula)'}
          </code>
        </div>
      ))}
      {col.kind === 'conflict' && col.resolvableIn && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>Resolve for {col.resolvableIn}:</span>
          {domains.map((d) => (
            <button key={d} type="button" disabled={busy} style={BTN}
              onClick={() => resolve({ mode: 'priority', priority: [d] }, `${d} wins`)}>
              Keep {d}
            </button>
          ))}
          <button type="button" disabled={busy} style={BTN}
            onClick={() => resolve({ mode: 'namespace' }, 'keep both, prefixed by domain')}>
            Keep both (prefixed)
          </button>
        </div>
      )}
      {col.kind === 'conflict' && !col.resolvableIn && (
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
          {col.projects.length > 1
            ? `Defined differently in ${col.projects.length} projects. Each project keeps its own definition; align the formulas if they should mean the same thing.`
            : 'Edit one of the domains to align the formulas.'}
        </span>
      )}
      {msg && <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{msg}</span>}
    </div>
  );
}

export default function OverviewDrilldown({ kind, domains, details, collisions, unresolved = [], onClose, onOpenDomain, onResolved }) {
  const [q, setQ] = useState('');
  const [attnTab, setAttnTab] = useState('conflicts');
  const domainProject = useMemo(
    () => Object.fromEntries(domains.map((d) => [d.domain, d.source_project_id || ''])),
    [domains]
  );
  const metrics = useMemo(() => metricRows(domains, details), [domains, details]);
  const concepts = useMemo(() => conceptRows(domains, details), [domains, details]);
  const { conflicts, shared } = useMemo(() => splitCollisions(collisions, domainProject), [collisions, domainProject]);

  const loading = domains.some((d) => !details[d.domain]);
  const rows = kind === 'metrics' ? metrics : kind === 'concepts' ? concepts : [];
  const shown = rows.filter((r) => matchesQuery(r, q)).slice(0, 500);
  const attnList = (attnTab === 'conflicts' ? conflicts : shared).filter((c) => matchesQuery({ n: c.item_name, ...(c.expressions || {}) }, q));

  return (
    <div
      role="dialog"
      aria-label={TITLES[kind]}
      onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 60, display: 'flex', justifyContent: 'flex-end' }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: 'min(760px, 100%)', height: '100%', background: 'var(--bg-surface)', borderLeft: '1px solid var(--border-main)', display: 'flex', flexDirection: 'column' }}
      >
        <div style={{ padding: '18px 20px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <h3 style={{ margin: 0, fontSize: 16, flex: 1 }}>{TITLES[kind]}</h3>
          <button type="button" onClick={onClose} aria-label="Close" style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)' }}>
            <X size={18} />
          </button>
        </div>
        <div style={{ padding: '12px 20px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 200, padding: '6px 10px', border: '1px solid var(--border-main)', borderRadius: 8 }}>
            <Search size={14} color="var(--text-tertiary)" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search names, domains, formulas"
              style={{ border: 'none', outline: 'none', background: 'transparent', color: 'var(--text-primary)', flex: 1, fontSize: 13 }} />
          </div>
          {kind === 'attention' && (
            <>
              <button type="button" style={{ ...BTN, ...(attnTab === 'conflicts' ? { borderColor: 'var(--accent-blue)' } : {}) }} onClick={() => setAttnTab('conflicts')}>
                Conflicts ({conflicts.length})
              </button>
              <button type="button" style={{ ...BTN, ...(attnTab === 'shared' ? { borderColor: 'var(--accent-blue)' } : {}) }} onClick={() => setAttnTab('shared')}>
                Shared names ({shared.length})
              </button>
            </>
          )}
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px 20px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {kind !== 'attention' && loading && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Loading definitions…</span>}
          {kind === 'metrics' && shown.map((r) => (
            <div key={r.key} style={{ ...CARD, padding: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <b style={{ fontSize: 13 }}>{r.name}</b>
                <button type="button" onClick={() => onOpenDomain?.(r.domain)}
                  style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent-blue)', cursor: 'pointer', fontSize: 12 }}>
                  {r.domain}
                </button>
                {r.boundToBi ? <Pill tone="ok">Bound to BI measure</Pill> : <Pill>Not bound</Pill>}
              </div>
              {r.formula && <code style={{ fontFamily: MONO, fontSize: 11.5, color: 'var(--text-secondary)', wordBreak: 'break-word' }}>{r.formula}</code>}
            </div>
          ))}
          {kind === 'concepts' && shown.map((r) => (
            <div key={r.key} style={{ ...CARD, padding: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <b style={{ fontSize: 13 }}>{r.name}</b>
              <button type="button" onClick={() => onOpenDomain?.(r.domain)}
                style={{ background: 'none', border: 'none', padding: 0, color: 'var(--accent-blue)', cursor: 'pointer', fontSize: 12 }}>
                {r.domain}
              </button>
              {r.table && <code style={{ fontFamily: MONO, fontSize: 11.5, color: 'var(--text-secondary)' }}>{r.table}</code>}
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{r.attributes} attributes</span>
            </div>
          ))}
          {kind !== 'attention' && !loading && shown.length === 0 && (
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Nothing matches.</span>
          )}
          {kind !== 'attention' && rows.length > 500 && (
            <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Showing the first 500; search to narrow.</span>
          )}
          {kind === 'attention' && (
            <>
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {attnTab === 'conflicts'
                  ? 'Same name, different formulas: the definitions disagree. Resolve the ones inside a project; across projects, align the formulas if they should match.'
                  : 'Same name and formula in several domains, usually one model synced by several projects. Nothing to resolve.'}
              </span>
              {attnList.map((c, i) => (
                <ConflictCard key={`${c.item_type}-${c.item_name}-${i}`} col={c} onResolved={onResolved} onOpenDomain={onOpenDomain} />
              ))}
              {attnList.length === 0 && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>None.</span>}
              {unresolved.length > 0 && attnTab === 'conflicts' && (
                <div style={{ ...CARD, padding: 12, fontSize: 12 }}>
                  <b>Unresolved references ({unresolved.length})</b>
                  <ul style={{ margin: '6px 0 0', paddingLeft: 18, color: 'var(--text-secondary)' }}>
                    {unresolved.slice(0, 50).map((u, i) => <li key={i}>{typeof u === 'string' ? u : JSON.stringify(u)}</li>)}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
