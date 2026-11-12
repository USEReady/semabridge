/**
 * Wizard step 4 — Ontology review, BEFORE the dry run.
 *
 * Reads the selected source models (no target calls), then shows:
 *  - duplicates: measures with the same formula under different names
 *    (or the same measure copied into several reports) -> Merge / Keep separate
 *  - conflicts: same name, different formula, vs. the existing ontology
 *    (use the governed definition / keep as a new metric / decide later)
 *    or between the selected models (reported; first definition kept)
 *  - glossary gaps: business metrics with no description -> write one
 *  - new metrics: not in any domain yet; they go into the new domain
 * "Save ontology" stores the result as a new domain (attached to the project
 * once it exists). The step is optional: Continue without saving skips it.
 *
 * State lives in the parent (`review`, `setReview`) so Back/Next keeps it.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, AlertTriangle, CheckCircle2, Info, GitMerge, RefreshCw } from 'lucide-react';
import { api } from '../../utils/api';

const card = {
  background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 9,
  padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0,
};
const label = { fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--text-tertiary)' };
const big = { fontSize: 22, fontWeight: 700, lineHeight: 1.1, fontVariantNumeric: 'tabular-nums', color: 'var(--text-primary)' };
const faint = { fontSize: 11.5, color: 'var(--text-tertiary)' };
const chip = {
  fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 11, padding: '1px 6px', borderRadius: 5,
  border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', color: 'var(--text-secondary)',
  maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block',
};
const input = {
  fontSize: 12, padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border-main)',
  background: 'var(--bg-input)', color: 'var(--text-primary)', outline: 'none', minWidth: 0,
};
function btn(variant = 'secondary', active = false) {
  return {
    display: 'inline-flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap',
    padding: '4px 10px', borderRadius: 6, fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
    border: variant === 'primary' || active ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
    background: variant === 'primary' ? 'var(--accent-blue)' : active ? 'var(--color-accent-faint)' : 'var(--bg-surface)',
    color: variant === 'primary' ? '#fff' : active ? 'var(--accent-blue)' : 'var(--text-primary)',
  };
}

function SummaryCards({ s }) {
  const items = [
    ['Measures found', s.measures_found, `→ ${s.business_metrics} unique business metrics`],
    ['Duplicates', s.duplicate_groups, `${s.duplicate_measures} extra copies`, s.duplicate_groups ? 'var(--color-warning)' : null],
    ['Conflicts', s.conflicts, 'same name, different formula', s.conflicts ? 'var(--color-error)' : null],
    ['Glossary', `${Math.round(s.glossary_coverage_pct)}%`, 'have a business description'],
    ['Already governed', s.governed, 'match an existing metric', s.governed ? 'var(--color-success)' : null],
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
      {items.map(([k, v, sub, color]) => (
        <div key={k} style={card}>
          <span style={label}>{k}</span>
          <span style={{ ...big, color: color || big.color }}>{v}</span>
          <span style={faint}>{sub}</span>
        </div>
      ))}
    </div>
  );
}

function DuplicateGroup({ group, decision, onChange }) {
  const merged = decision?.decision === 'merge';
  const separate = decision?.decision === 'separate';
  const name = decision?.name ?? group.suggested_id;
  return (
    <div style={{ ...card, gap: 8, borderColor: merged ? 'var(--color-success)' : 'var(--border-main)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>{group.suggested_name}</b>
        <span style={{ fontSize: 10.5, fontWeight: 700, padding: '1px 7px', borderRadius: 999, background: 'var(--color-warning-bg)', color: 'var(--color-warning)' }}>
          {group.kind === 'same_name' ? `same measure in ${group.members.length} models` : `${group.members.length} names, one formula`}
        </span>
        <span style={{ flex: 1 }} />
        <span style={chip} title={group.expression}>{group.expression}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {group.members.map((m, i) => (
          <div key={`${m.name}-${m.source}-${i}`} style={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 1fr) minmax(140px, 1fr)', gap: 10, fontSize: 12 }}>
            <span style={{ color: 'var(--text-primary)' }}>{m.label}</span>
            <span style={faint}>{m.source || '—'}</span>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {merged ? (
          <>
            <CheckCircle2 size={13} color="var(--color-success)" />
            <span style={{ fontSize: 12 }}>Merged as <b>{name}</b>; the other names become synonyms.</span>
            <span style={{ flex: 1 }} />
            <button type="button" style={btn()} onClick={() => onChange(null)}>Undo</button>
          </>
        ) : (
          <>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }} htmlFor={`dup-${group.id}`}>Keep as</label>
            <input
              id={`dup-${group.id}`}
              style={{ ...input, width: 180 }}
              value={name}
              onChange={e => onChange({ decision: decision?.decision || null, name: e.target.value })}
            />
            <span style={faint}>{separate ? 'Kept as separate metrics.' : ''}</span>
            <span style={{ flex: 1 }} />
            <button type="button" style={btn('secondary', separate)} onClick={() => onChange({ decision: 'separate', name })}>Keep separate</button>
            <button type="button" style={btn('primary')} disabled={!name.trim()} onClick={() => onChange({ decision: 'merge', name: name.trim() })}>
              <GitMerge size={12} /> Merge
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function ConflictCard({ conflict, decision, onChange }) {
  if (conflict.kind === 'source') {
    return (
      <div style={{ ...card, gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <AlertTriangle size={13} color="var(--color-error)" />
          <b style={{ fontSize: 13 }}>{conflict.name}</b>
          <span style={faint}>has different formulas in the selected models</span>
        </div>
        {conflict.variants.map((v, i) => (
          <div key={i} style={{ display: 'grid', gridTemplateColumns: '160px 1fr', gap: 10, fontSize: 12, alignItems: 'center' }}>
            <span style={faint}>{v.source || '—'}</span>
            <span style={chip} title={v.expression}>{v.expression}</span>
          </div>
        ))}
        <span style={faint}>The first definition is kept. Rename one of them in the source report, or fix it in the project's Ontology tab afterwards.</span>
      </div>
    );
  }
  const res = decision?.resolution || null;
  const newName = decision?.new_name ?? conflict.suggested_new_name;
  const ex = conflict.existing || {};
  const inc = conflict.incoming || {};
  return (
    <div style={{ ...card, gap: 8, borderColor: res ? 'var(--border-main)' : 'var(--color-error)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <AlertTriangle size={13} color="var(--color-error)" />
        <b style={{ fontSize: 13 }}>{conflict.name}</b>
        <span style={faint}>is already defined differently in the {ex.domain} domain</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div style={{ border: '1px solid var(--border-main)', borderRadius: 7, padding: 8, display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
          <span style={label}>{ex.domain} · governed</span>
          <span style={chip} title={ex.expression}>{ex.expression || '—'}</span>
          <span style={faint}>{ex.owner ? `Owner: ${ex.owner}` : 'No owner set'}</span>
        </div>
        <div style={{ border: '1px solid var(--border-main)', borderRadius: 7, padding: 8, display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
          <span style={label}>{inc.source || 'Selected model'}</span>
          <span style={chip} title={inc.expression}>{inc.expression || '—'}</span>
          <span style={faint}>{inc.description || 'No description'}</span>
        </div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
        <button type="button" style={btn('secondary', res === 'use_existing')} onClick={() => onChange({ resolution: 'use_existing' })}>
          Use the {ex.domain} definition
        </button>
        <button type="button" style={btn('secondary', res === 'keep_new')} onClick={() => onChange({ resolution: 'keep_new', new_name: newName })}>
          Keep as a new metric
        </button>
        {res === 'keep_new' && (
          <input
            aria-label="New metric name"
            style={{ ...input, width: 220 }}
            value={newName}
            onChange={e => onChange({ resolution: 'keep_new', new_name: e.target.value })}
          />
        )}
        <button type="button" style={btn('secondary', res === 'defer')} onClick={() => onChange({ resolution: 'defer' })}>
          Decide later
        </button>
      </div>
    </div>
  );
}

function GlossaryGaps({ gaps, descriptions, onChange, onFillAll, onClearAll }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? gaps : gaps.slice(0, 25);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={faint}>Suggested descriptions are pre-filled from metric names &amp; formulas — edit freely before saving.</span>
        <span style={{ flex: 1 }} />
        <button type="button" style={btn()} onClick={onFillAll}>Use all suggestions</button>
        <button type="button" style={btn()} onClick={onClearAll}>Clear all</button>
      </div>
      <div style={{ ...card, padding: 0, gap: 0 }}>
        {shown.map((g, i) => {
          const val = descriptions[g.name] ?? '';
          const isSuggested = Boolean(g.suggested_description && val === g.suggested_description);
          return (
            <div key={`${g.name}-${i}`} style={{ display: 'grid', gridTemplateColumns: 'minmax(160px, 220px) 1fr auto', gap: 10, padding: '8px 12px', borderTop: i ? '1px solid var(--border-light)' : 'none', alignItems: 'center' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600 }}>{g.label}</div>
                <div style={{ ...faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={g.expression}>{g.source} · {g.expression}</div>
              </div>
              <input
                aria-label={`Description for ${g.label}`}
                style={{ ...input, width: '100%' }}
                placeholder={g.suggested_description || 'What does this metric mean to the business?'}
                value={val}
                onChange={e => onChange(g.name, e.target.value)}
              />
              {isSuggested ? (
                <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999, background: 'var(--color-accent-faint)', color: 'var(--accent-blue)' }}>Suggested</span>
              ) : g.suggested_description && !val ? (
                <button type="button" style={btn()} onClick={() => onChange(g.name, g.suggested_description)}>Suggest</button>
              ) : (
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', padding: '2px 6px' }}>Edited</span>
              )}
            </div>
          );
        })}
        {gaps.length > 25 && (
          <div style={{ padding: 10, borderTop: '1px solid var(--border-light)' }}>
            <button type="button" style={btn()} onClick={() => setShowAll(v => !v)}>
              {showAll ? 'Show fewer' : `Show all ${gaps.length}`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export function StepOntologyReview({ review, setReview, sourceConnector, buildRequest, projectName, projectId }) {
  const [tab, setTab] = useState('duplicates');
  const [sfSim, setSfSim] = useState({ loading: false, data: null, error: '' });
  const request = useMemo(() => buildRequest(), [buildRequest]);
  const signature = useMemo(() => JSON.stringify(request), [request]);
  const patch = useCallback((p) => setReview(prev => ({ ...prev, ...(typeof p === 'function' ? p(prev) : p) })), [setReview]);

  const analyze = useCallback(async () => {
    patch({ status: 'loading', message: '', signature, saved: null, saveError: '' });
    try {
      const res = await api.analyzeOntologyReview({ ...request, project_id: projectId || undefined });
      if (res?.supported === false) {
        patch({ status: 'unsupported', message: res.message || '', analysis: null });
        return;
      }
      const suggested = {};
      for (const g of res?.glossary_gaps || []) {
        if (g.name && g.suggested_description) suggested[g.name] = g.suggested_description;
      }
      patch(prev => ({
        status: 'ready',
        analysis: res,
        merges: {},
        conflicts: {},
        descriptions: suggested,
        domainName: prev.domainName || projectName || '',
      }));
    } catch (err) {
      const raw = err?.message || '';
      // A 404 here means the running backend predates the Ontology review
      // endpoints (it needs a restart), not that the files are missing.
      const message = /\b404\b/.test(raw)
        ? 'The backend does not have the Ontology review yet. Restart the backend server (make dev / uvicorn) and try again, or continue without the review.'
        : (raw || 'The selected models could not be read.');
      patch({ status: 'error', message });
    }
  }, [patch, request, signature, projectId, projectName]);

  useEffect(() => {
    if (review.signature !== signature && review.status !== 'loading') analyze();
  }, [signature]); // eslint-disable-line react-hooks/exhaustive-deps

  const a = review.analysis;
  const dups = a?.duplicates || [];
  const conflicts = a?.conflicts || [];
  const gaps = a?.glossary_gaps || [];
  const unassigned = a?.unassigned || [];
  const exactMergeable = dups.filter(d => !review.merges[d.id]);
  const decided = Object.values(review.merges).filter(m => m?.decision).length
    + Object.values(review.conflicts).filter(c => c?.resolution).length;
  const openConflicts = conflicts.filter(c => c.kind === 'ontology' && !review.conflicts[c.id]?.resolution).length;

  const setMerge = (id, v) => patch(prev => ({ merges: { ...prev.merges, [id]: v }, saved: null }));
  const setConflict = (id, v) => patch(prev => ({ conflicts: { ...prev.conflicts, [id]: { ...prev.conflicts[id], ...v } }, saved: null }));
  const setDescription = (name, text) => patch(prev => ({ descriptions: { ...prev.descriptions, [name]: text }, saved: null }));
  const mergeAll = () => patch(prev => {
    const merges = { ...prev.merges };
    dups.forEach(d => { if (!merges[d.id]) merges[d.id] = { decision: 'merge', name: d.suggested_id }; });
    return { merges, saved: null };
  });

  const save = async () => {
    patch({ saving: true, saveError: '' });
    try {
      const body = {
        analysis_id: a.analysis_id,
        ...request,
        project_id: projectId || undefined,
        domain_name: review.domainName.trim(),
        owner: review.owner.trim() || undefined,
        merges: dups
          .filter(d => review.merges[d.id]?.decision === 'merge')
          .map(d => ({ name: review.merges[d.id].name, members: [...new Set(d.members.map(m => m.name))] })),
        conflicts: conflicts
          .filter(c => c.kind === 'ontology' && review.conflicts[c.id]?.resolution)
          .map(c => ({ name: c.incoming.name, resolution: review.conflicts[c.id].resolution, new_name: review.conflicts[c.id].new_name })),
        descriptions: Object.fromEntries(Object.entries(review.descriptions).filter(([, v]) => String(v || '').trim())),
      };
      const res = await api.applyOntologyReview(body);
      patch({ saving: false, saved: res });
    } catch (err) {
      patch({ saving: false, saveError: err?.message || 'The ontology could not be saved.' });
    }
  };

  // ── states ──────────────────────────────────────────────────────────────
  const header = (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
      <div style={{ flex: 1, minWidth: 260 }}>
        <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0, color: 'var(--text-primary)' }}>Review business definitions</h2>
        <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', margin: '4px 0 0', maxWidth: 680 }}>
          Before the dry run, we compare the measures in the models you chose with each other and with your existing ontology,
          so each business metric is migrated once, under one name, with a description. This step is optional.
        </p>
      </div>
      {review.status === 'ready' && (
        <button type="button" style={btn()} onClick={analyze}><RefreshCw size={12} /> Read again</button>
      )}
    </div>
  );

  if (review.status === 'loading' || review.status === 'idle') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {header}
        <div style={{ ...card, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <Loader2 size={15} className="animate-spin" />
          <span style={{ fontSize: 12.5 }}>Reading the measures in the selected models…</span>
        </div>
      </div>
    );
  }
  if (review.status === 'unsupported' || review.status === 'error') {
    const unsupported = review.status === 'unsupported';
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {header}
        <div style={{ ...card, flexDirection: 'row', alignItems: 'flex-start', gap: 10 }}>
          {unsupported ? <Info size={15} color="var(--color-info)" /> : <AlertTriangle size={15} color="var(--color-error)" />}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12.5 }}>
            <span>{review.message}</span>
            <span style={faint}>
              {unsupported
                ? `Continue to run the dry run${sourceConnector ? ` for ${sourceConnector}` : ''}. You can review the ontology from the project's Ontology tab afterwards.`
                : 'Check that the selected files exist, then try again, or continue without the review.'}
            </span>
            {!unsupported && <div><button type="button" style={btn()} onClick={analyze}><RefreshCw size={12} /> Try again</button></div>}
          </div>
        </div>
      </div>
    );
  }

  const fillAllSuggestions = () => {
    const next = { ...(review.descriptions || {}) };
    for (const g of gaps) {
      if (g.name && g.suggested_description) next[g.name] = g.suggested_description;
    }
    patch({ descriptions: next, saved: null });
  };
  const clearAllDescriptions = () => patch({ descriptions: {}, saved: null });

  const runSnowflakeCheck = async () => {
    setSfSim({ loading: true, data: null, error: '' });
    try {
      const candidateMap = new Map();
      for (const g of gaps) candidateMap.set(g.name, { name: g.name, label: g.label, expression: g.expression || '' });
      for (const u of unassigned) {
        if (!candidateMap.has(u)) candidateMap.set(u, { name: u, label: u, expression: '' });
      }
      const res = await api.checkSnowflakeSimilarity({
        project_id: projectId || undefined,
        metrics: Array.from(candidateMap.values()).slice(0, 150),
      });
      setSfSim({ loading: false, data: res, error: '' });
      setTab('snowflake');
    } catch (err) {
      setSfSim({ loading: false, data: null, error: err?.message || 'Snowflake similarity check failed.' });
    }
  };

  const sfMatches = sfSim.data?.matches || [];
  const sfHitCount = sfMatches.filter(m => m.status !== 'NEW').length;

  const tabs = [
    ['duplicates', 'Duplicates', dups.length, 'var(--color-warning)'],
    ['conflicts', 'Conflicts', conflicts.length, 'var(--color-error)'],
    ['glossary', 'Glossary gaps', gaps.length, null],
    ['new', 'New metrics', unassigned.length, null],
    ['snowflake', 'Snowflake match', sfSim.data ? sfHitCount : '?', sfHitCount ? 'var(--accent-blue)' : null],
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {header}
      {(a.errors || []).length > 0 && (
        <div style={{ ...card, flexDirection: 'row', gap: 8, alignItems: 'center', borderColor: 'var(--color-warning)' }}>
          <AlertTriangle size={13} color="var(--color-warning)" />
          <span style={{ fontSize: 12 }}>
            {a.errors.length} model{a.errors.length === 1 ? '' : 's'} could not be read: {a.errors.map(e => e.source).join(', ')}. They are left out of this review.
          </span>
        </div>
      )}
      <SummaryCards s={a.summary} />

      <div role="tablist" style={{ display: 'flex', gap: 18, borderBottom: '1px solid var(--border-main)' }}>
        {tabs.map(([id, text, n, color]) => (
          <button
            key={id}
            role="tab"
            type="button"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: '6px 0 9px', marginBottom: -1, fontSize: 12.5,
              borderBottom: `2px solid ${tab === id ? 'var(--accent-blue)' : 'transparent'}`,
              color: tab === id ? 'var(--text-primary)' : 'var(--text-tertiary)', fontWeight: tab === id ? 700 : 500,
              display: 'inline-flex', gap: 6, alignItems: 'center',
            }}
          >
            {text}
            <span style={{ fontSize: 10.5, fontWeight: 700, padding: '0 6px', borderRadius: 999, background: n && color ? `color-mix(in srgb, ${color} 15%, transparent)` : 'var(--bg-surface-raised)', color: n && color ? color : 'var(--text-tertiary)' }}>{n}</span>
          </button>
        ))}
      </div>

      {tab === 'duplicates' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {dups.length === 0 && <span style={faint}>No duplicate measures found.</span>}
          {dups.length > 0 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={faint}>Merging keeps one metric bound to every copy, so all reports get the same business name and description.</span>
              <span style={{ flex: 1 }} />
              {exactMergeable.length > 0 && (
                <button type="button" style={btn()} onClick={mergeAll}><GitMerge size={12} /> Merge all ({exactMergeable.length})</button>
              )}
            </div>
          )}
          {dups.map(d => (
            <DuplicateGroup key={d.id} group={d} decision={review.merges[d.id]} onChange={v => setMerge(d.id, v)} />
          ))}
        </div>
      )}

      {tab === 'conflicts' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {conflicts.length === 0 && <span style={faint}>No conflicting definitions.</span>}
          {conflicts.map(c => (
            <ConflictCard key={c.id} conflict={c} decision={review.conflicts[c.id]} onChange={v => setConflict(c.id, v)} />
          ))}
        </div>
      )}

      {tab === 'glossary' && (
        gaps.length === 0
          ? <span style={faint}>Every business metric has a description.</span>
          : (
            <>
              <span style={faint}>Descriptions are saved to the ontology and sent to the target as comments on each metric.</span>
              <GlossaryGaps
                gaps={gaps}
                descriptions={review.descriptions}
                onChange={setDescription}
                onFillAll={fillAllSuggestions}
                onClearAll={clearAllDescriptions}
              />
            </>
          )
      )}

      {tab === 'new' && (
        <div style={{ ...card }}>
          <span style={faint}>These metrics are not in any domain yet. Saving adds them to the new domain below.</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {unassigned.slice(0, 200).map(n => <span key={n} style={chip}>{n}</span>)}
            {unassigned.length > 200 && <span style={faint}>+{unassigned.length - 200} more</span>}
          </div>
        </div>
      )}

      {tab === 'snowflake' && (
        <div style={{ ...card, gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={faint}>
              Checks whether incoming metrics already exist or are similar in Snowflake Semantic Views / ontology.
            </span>
            <span style={{ flex: 1 }} />
            <button type="button" style={btn('primary')} disabled={sfSim.loading} onClick={runSnowflakeCheck}>
              {sfSim.loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              {sfSim.loading ? 'Checking Snowflake…' : 'Check Snowflake'}
            </button>
          </div>
          {sfSim.error && <span style={{ fontSize: 12, color: 'var(--color-error)' }}>{sfSim.error}</span>}
          {sfSim.data && (
            <>
              <span style={faint}>
                {sfSim.data.snowflake_connected
                  ? `Connected to Snowflake · ${sfSim.data.views_scanned} semantic view(s) scanned · ${sfHitCount} existing/similar metric(s) found.`
                  : `Snowflake offline (${sfSim.data.snowflake_error || 'no credentials'}) · matched against governed ontology (${sfHitCount} hit(s)).`}
              </span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 280, overflowY: 'auto' }}>
                {sfMatches.map((m, i) => (
                  <div key={`${m.name}-${i}`} style={{ display: 'grid', gridTemplateColumns: 'minmax(160px, 1fr) auto minmax(180px, 1fr)', gap: 10, alignItems: 'center', fontSize: 12, padding: '6px 8px', borderRadius: 6, background: 'var(--bg-surface-raised)' }}>
                    <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.label}</strong>
                    <span style={{
                      fontSize: 10.5, fontWeight: 700, padding: '1px 8px', borderRadius: 999,
                      background: m.status === 'EXACT_MATCH' ? 'var(--color-success-bg)' : m.status === 'SIMILAR_EXISTS' ? 'var(--color-warning-bg)' : 'var(--bg-surface)',
                      color: m.status === 'EXACT_MATCH' ? 'var(--color-success)' : m.status === 'SIMILAR_EXISTS' ? 'var(--color-warning)' : 'var(--text-tertiary)',
                    }}>
                      {m.status === 'EXACT_MATCH' ? 'Already in Snowflake' : m.status === 'SIMILAR_EXISTS' ? `Similar (${m.similarity_pct}%)` : 'New'}
                    </span>
                    <span style={{ ...faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={m.matched_view || ''}>
                      {m.matched_metric ? `${m.matched_metric} · ${m.matched_view}` : '—'}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Save bar */}
      <div style={{ ...card, flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap', position: 'sticky', bottom: 0, boxShadow: 'var(--shadow-md)' }}>
        <label htmlFor="onto-domain" style={{ fontSize: 12, fontWeight: 600 }}>Model</label>
        <input
          id="onto-domain"
          style={{ ...input, width: 200 }}
          value={review.domainName}
          onChange={e => patch({ domainName: e.target.value, saved: null })}
          placeholder="e.g. Sales"
        />
        <label htmlFor="onto-owner" style={{ fontSize: 12, fontWeight: 600 }}>Owner</label>
        <input
          id="onto-owner"
          style={{ ...input, width: 160 }}
          value={review.owner}
          onChange={e => patch({ owner: e.target.value, saved: null })}
          placeholder="Team or person"
        />
        <span style={faint}>
          {decided} decision{decided === 1 ? '' : 's'} made
          {openConflicts ? ` · ${openConflicts} conflict${openConflicts === 1 ? '' : 's'} still open` : ''}
        </span>
        <span style={{ flex: 1 }} />
        {review.saved ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--color-success)' }}>
            <CheckCircle2 size={14} />
            {review.saved.synced_existing
              ? <>This project already has domain <b>{review.saved.domain}</b>; the review was merged into it ({review.saved.metric_count} metrics). Continue to the dry run.</>
              : <>Saved as domain <b>{review.saved.domain}</b> ({review.saved.metric_count} metrics). Continue to the dry run.</>}
          </span>
        ) : (
          <button type="button" style={btn('primary')} disabled={review.saving || !review.domainName.trim()} onClick={save}>
            {review.saving ? <Loader2 size={12} className="animate-spin" /> : null} Save ontology
          </button>
        )}
      </div>
      {review.saveError && (
        <div style={{ ...card, flexDirection: 'row', gap: 8, alignItems: 'center', borderColor: 'var(--color-error)', fontSize: 12 }}>
          <AlertTriangle size={13} color="var(--color-error)" /> {review.saveError}
        </div>
      )}
      {!review.saved && (
        <span style={faint}>Continue without saving to skip this review; the project's ontology will show as not reviewed.</span>
      )}
    </div>
  );
}
