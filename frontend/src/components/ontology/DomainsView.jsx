import React, { useState } from 'react';
import { RefreshCw, Search } from 'lucide-react';
import { BTN, BTN_SM, BTN_PRIMARY, CARD, INPUT_STYLE, MONO, plural } from './styles';
import { Pill, Segmented } from './ui';

const TH = {
  padding: '9px 14px',
  textAlign: 'left',
  fontSize: 11,
  fontWeight: 600,
  color: 'var(--text-tertiary)',
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  borderBottom: '1px solid var(--border-main)',
  whiteSpace: 'nowrap',
};
const TD = { padding: '10px 14px', fontSize: 12.5, verticalAlign: 'top', borderBottom: '1px solid var(--border-main)' };
const SUB = { fontSize: 11.5, color: 'var(--text-tertiary)', marginTop: 2, lineHeight: 1.4 };

function Empty({ children }) {
  return <div style={{ padding: 36, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>{children}</div>;
}

function DomainRail({ domains, selected, onSelect, issueCountFor }) {
  const [q, setQ] = useState('');
  const list = q.trim() ? domains.filter((d) => d.domain.toLowerCase().includes(q.toLowerCase())) : domains;
  return (
    <div style={{ borderRight: '1px solid var(--border-main)', padding: 10, display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <input
        type="text"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Filter models…"
        style={{ ...INPUT_STYLE, width: '100%', boxSizing: 'border-box', marginBottom: 6 }}
      />
      {list.map((d) => {
        const on = d.domain === selected;
        const issues = issueCountFor(d);
        return (
          <button
            key={d.domain}
            type="button"
            onClick={() => onSelect(d.domain)}
            style={{
              textAlign: 'left',
              padding: '8px 10px',
              borderRadius: 8,
              border: 'none',
              cursor: 'pointer',
              background: on ? 'color-mix(in srgb, var(--accent-blue) 12%, transparent)' : 'transparent',
              color: 'var(--text-primary)',
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
            }}
          >
            <b style={{ fontSize: 13, color: on ? 'var(--accent-blue)' : 'var(--text-primary)' }}>{d.domain}</b>
            <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {d.owner ? `${d.owner} · ` : ''}
                {plural(d.metric_count || 0, 'metric')}
              </span>
              {issues > 0 && <Pill tone="warn" style={{ padding: '0 6px' }}>{issues}</Pill>}
            </span>
          </button>
        );
      })}
      {list.length === 0 && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', padding: 8 }}>No match</div>}
    </div>
  );
}

export default function DomainsView({
  domains,
  selectedDomain,
  domainDetail,
  onSelectDomain,
  issueCountFor,
  subTab,
  setSubTab,
  search,
  setSearch,
  metrics,
  concepts,
  policies,
  rowInfo,
  selectedMetricName,
  onSelectMetric,
  onShowMap,
  onOpenItem,
  onSync,
  yaml,
  drawer,
}) {
  if (domains.length === 0) {
    return (
      <div style={CARD}>
        <Empty>No models yet. Use Generate from OSI or Import YAML above to add one.</Empty>
      </div>
    );
  }

  const info = domainDetail || domains.find((d) => d.domain === selectedDomain) || {};
  const counts = {
    metrics: domainDetail?.parsed?.metrics?.length ?? info.metric_count,
    concepts: domainDetail?.parsed?.concepts?.length ?? info.concept_count,
    policies: domainDetail?.parsed?.policies?.length ?? info.policy_count,
  };

  return (
    <div
      style={{
        ...CARD,
        display: 'grid',
        gridTemplateColumns: drawer ? '220px minmax(0, 1fr) 340px' : '220px minmax(0, 1fr)',
        minHeight: 520,
        overflow: 'hidden',
      }}
    >
      <DomainRail domains={domains} selected={selectedDomain} onSelect={onSelectDomain} issueCountFor={issueCountFor} />

      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div
          style={{
            padding: '12px 16px',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 10,
            alignItems: 'center',
            borderBottom: '1px solid var(--border-main)',
          }}
        >
          <b style={{ fontSize: 14 }}>{selectedDomain}</b>
          {info.version && <Pill tone="n">v{info.version}</Pill>}
          {info.owner && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Owner: {info.owner}</span>}
          <span style={{ flex: 1 }} />
          {subTab !== 'yaml' && (
            <div style={{ position: 'relative' }}>
              <Search size={12} style={{ position: 'absolute', left: 8, top: 9, color: 'var(--text-tertiary)' }} />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Filter…"
                style={{ ...INPUT_STYLE, paddingLeft: 24, width: 150 }}
              />
            </div>
          )}
          <button type="button" style={BTN_SM} onClick={onSync} title="Update this model from the latest OSI model, keeping your edits">
            <RefreshCw size={11} /> Sync with OSI
          </button>
          <Segmented
            size="sm"
            value={subTab}
            onChange={setSubTab}
            options={[
              { id: 'metrics', label: 'Metrics', count: counts.metrics },
              { id: 'concepts', label: 'Concepts', count: counts.concepts },
              { id: 'policies', label: 'Policies', count: counts.policies },
              { id: 'yaml', label: 'YAML' },
            ]}
          />
        </div>

        <div style={{ overflow: 'auto', flex: 1 }}>
          {subTab === 'metrics' &&
            (metrics.length === 0 ? (
              <Empty>{search ? 'No metrics match this filter.' : 'No metrics in this model yet.'}</Empty>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={TH}>Metric</th>
                    <th style={TH}>Status</th>
                    <th style={TH}>Certification</th>
                    <th style={TH}>Used in</th>
                    <th style={TH}>Guardrail</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.map((m) => {
                    const r = rowInfo(m);
                    const sel = m.name === selectedMetricName;
                    const certTone = r.cert?.status === 'CERTIFIED' ? 'ok' : r.cert?.status === 'FAILED' ? 'bad' : 'n';
                    const certLabel = r.cert?.status === 'CERTIFIED' ? 'Certified' : r.cert?.status === 'FAILED' ? 'Failed' : 'Not checked';
                    return (
                      <tr
                        key={m.name}
                        onClick={() => onSelectMetric(m.name)}
                        style={{
                          cursor: 'pointer',
                          background: sel ? 'color-mix(in srgb, var(--accent-blue) 8%, transparent)' : undefined,
                          boxShadow: sel ? 'inset 3px 0 0 var(--accent-blue)' : undefined,
                        }}
                      >
                        <td style={TD}>
                          <b style={{ fontFamily: MONO, fontSize: 12.5 }}>{m.name}</b>
                          <div style={SUB}>
                            {m.description ||
                              (m.derives_from?.length ? `Derived from ${plural(m.derives_from.length, 'metric')}` : m.label || '—')}
                          </div>
                        </td>
                        <td style={TD}>
                          <Pill tone={r.status.tone}>{r.status.label}</Pill>
                        </td>
                        <td style={TD} title={r.cert?.certification_badge || ''}>
                          <Pill tone={certTone}>{certLabel}</Pill>
                        </td>
                        <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                          {r.usedIn === '—' ? (
                            <span style={{ color: 'var(--text-tertiary)' }}>—</span>
                          ) : (
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onShowMap();
                              }}
                              title="Show on the map"
                              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text-primary)', fontSize: 12.5, textDecoration: 'underline dotted', textUnderlineOffset: 3 }}
                            >
                              {r.usedIn}
                            </button>
                          )}
                        </td>
                        <td style={{ ...TD, color: 'var(--text-secondary)', fontFamily: r.guardrail !== '—' ? MONO : undefined, fontSize: 11.5, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.guardrail}>
                          {r.guardrail}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ))}

          {subTab === 'concepts' &&
            (concepts.length === 0 ? (
              <Empty>No concepts in this domain.</Empty>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={TH}>Concept</th>
                    <th style={TH}>Type</th>
                    <th style={TH}>Table</th>
                    <th style={TH}>Attributes</th>
                  </tr>
                </thead>
                <tbody>
                  {concepts.map((c) => (
                    <tr key={c.name} onClick={() => onOpenItem({ type: 'concept', data: c })} style={{ cursor: 'pointer' }}>
                      <td style={TD}>
                        <b>{c.name}</b>
                        <div style={SUB}>
                          {c.description || '—'}
                          {c.synonyms?.length > 0 && ` · also: ${c.synonyms.join(', ')}`}
                        </div>
                      </td>
                      <td style={TD}>
                        <Pill tone="info">{(c.concept_type || 'ENTITY').toLowerCase()}</Pill>
                      </td>
                      <td style={{ ...TD, fontFamily: MONO, fontSize: 11.5, color: 'var(--text-secondary)' }}>{c.physical_table || '—'}</td>
                      <td style={{ ...TD, color: 'var(--text-secondary)' }}>{(c.attributes || []).length || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}

          {subTab === 'policies' &&
            (policies.length === 0 ? (
              <Empty>
                No policies yet. Add a <code>policies:</code> block in YAML to enforce a filter on this domain.
              </Empty>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={TH}>Policy</th>
                    <th style={TH}>Filter</th>
                    <th style={TH}>Applies to</th>
                  </tr>
                </thead>
                <tbody>
                  {policies.map((p) => (
                    <tr key={p.name} onClick={() => onOpenItem({ type: 'policy', data: p })} style={{ cursor: 'pointer' }}>
                      <td style={TD}>
                        <b>{p.name}</b>
                        {p.description && <div style={SUB}>{p.description}</div>}
                      </td>
                      <td style={{ ...TD, fontFamily: MONO, fontSize: 11.5 }}>{p.filter_expression}</td>
                      <td style={{ ...TD, color: 'var(--text-secondary)' }}>
                        {(p.applies_to || []).length ? p.applies_to.join(', ') : 'Everything in this domain'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}

          {subTab === 'yaml' && (
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', fontFamily: MONO }}>
                  {selectedDomain}.ontology.yaml
                </span>
                <span style={{ flex: 1 }} />
                <button type="button" onClick={yaml.onValidate} style={BTN}>
                  Validate
                </button>
                <button type="button" onClick={yaml.onSave} style={BTN_PRIMARY}>
                  Save changes
                </button>
              </div>
              {yaml.validationResult && (
                <div
                  style={{
                    padding: '8px 12px',
                    borderRadius: 8,
                    fontSize: 12,
                    background: yaml.validationResult.valid ? 'var(--color-success-bg)' : 'var(--color-error-bg)',
                    color: yaml.validationResult.valid ? 'var(--color-success)' : 'var(--color-error)',
                  }}
                >
                  {yaml.validationResult.valid
                    ? `Valid: ${yaml.validationResult.concept_count} concepts, ${yaml.validationResult.metric_count} metrics`
                    : (yaml.validationResult.errors || [yaml.validationResult.error]).join('; ')}
                </div>
              )}
              <textarea
                value={yaml.value}
                onChange={(e) => yaml.onChange(e.target.value)}
                spellCheck={false}
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  height: 440,
                  padding: 14,
                  borderRadius: 8,
                  background: 'var(--bg-app)',
                  border: '1px solid var(--border-main)',
                  color: 'var(--text-primary)',
                  fontFamily: MONO,
                  fontSize: 12,
                  lineHeight: 1.6,
                  resize: 'vertical',
                }}
              />
            </div>
          )}
        </div>
      </div>

      {drawer}
    </div>
  );
}
