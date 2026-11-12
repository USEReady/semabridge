import React from 'react';
import { FlaskConical } from 'lucide-react';
import { BTN, BTN_SM, CARD, MONO } from './styles';
import { CATEGORY_LABEL } from './healthChecks';
import { Pill, Segmented } from './ui';
import {
  ImpactReport,
  WhatIfSimulator,
  ParityPanel,
  AccessPanel,
  QualityIssues,
  CollisionDetail,
  SimpleList,
} from './GovernancePanels';

const RING = {
  breaking: { glyph: '!', color: 'var(--color-error)' },
  warning: { glyph: '!', color: 'var(--color-warning)' },
  pending: { glyph: '–', color: 'var(--text-tertiary)' },
  passing: { glyph: '✓', color: 'var(--color-success)' },
};

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'impact', label: 'Impact' },
  { id: 'parity', label: 'Parity' },
  { id: 'quality', label: 'Quality' },
  { id: 'access', label: 'Access' },
];

function Chain({ items, more }) {
  return (
    <span style={{ display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: 5 }}>
      {items.map((it, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span style={{ color: 'var(--text-tertiary)' }}>→</span>}
          <span
            style={{
              fontFamily: MONO,
              fontSize: 10.5,
              padding: '1px 6px',
              borderRadius: 5,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
            }}
          >
            {it}
          </span>
        </React.Fragment>
      ))}
      {more > 0 && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>+{more}</span>}
    </span>
  );
}

export default function HealthView({
  rows,
  filter,
  setFilter,
  expandedId,
  setExpandedId,
  gov,
  metricOptions,
  onRunLint,
  onExpandParity,
  onOpenMetric,
  hideWhatIf = false,
}) {
  const displayRows = hideWhatIf ? rows.filter((r) => r.kind !== 'whatIf' && r.kind !== 'impact') : rows;
  const counts = displayRows.reduce((acc, r) => ({ ...acc, [r.severity]: (acc[r.severity] || 0) + 1 }), {});
  const visible = filter === 'all' ? displayRows : displayRows.filter((r) => r.category === filter);
  const whatIfOpen = !hideWhatIf && expandedId === 'whatif';

  const renderDetail = (row) => {
    switch (row.kind) {
      case 'collision':
        return <CollisionDetail collision={row.payload} onOpenMetric={onOpenMetric} />;
      case 'unresolved':
        return <SimpleList items={row.payload} />;
      case 'loadError':
        return <SimpleList items={[row.payload]} />;
      case 'lint':
        return <QualityIssues issues={row.payload} onOpenMetric={onOpenMetric} gov={gov} />;
      case 'impact':
        return <ImpactReport report={row.payload} onPickSuggestion={(s) => gov?.onRunBlast?.(s)} />;
      case 'whatIf':
        return <WhatIfSimulator gov={gov} metricOptions={metricOptions} />;
      case 'parity':
        return (
          <ParityPanel
            gov={gov}
            onOpenMetric={onOpenMetric}
            metricOptions={metricOptions}
            dimensionOptions={gov?.dimensionOptions || []}
            semanticViewOptions={gov?.semanticViewOptions || []}
          />
        );
      case 'access':
        return <AccessPanel gov={gov} />;
      default:
        return null;
    }
  };

  const onAction = (row) => {
    if (row.kind === 'lintRun') {
      onRunLint();
      return;
    }
    if (row.kind === 'parity' && expandedId !== row.id) onExpandParity();
    setExpandedId(expandedId === row.id ? null : row.id);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
        <Pill tone="bad">{counts.breaking || 0} breaking</Pill>
        <Pill tone="warn">{counts.warning || 0} {counts.warning === 1 ? 'warning' : 'warnings'}</Pill>
        <Pill tone="ok">{counts.passing || 0} passing</Pill>
        {counts.pending > 0 && <Pill tone="n">{counts.pending} not checked</Pill>}
        <span style={{ flex: 1 }} />
        {!hideWhatIf && (
          <button
            type="button"
            onClick={() => setExpandedId(whatIfOpen ? null : 'whatif')}
            style={{ ...BTN, ...(whatIfOpen ? { borderColor: 'var(--accent-blue)', color: 'var(--accent-blue)' } : {}) }}
            title="Test a hypothetical rename, drop or formula change"
          >
            <FlaskConical size={13} /> What if…
          </button>
        )}
        <Segmented options={FILTERS} value={filter} onChange={setFilter} />
      </div>

      {whatIfOpen && (
        <div style={{ ...CARD, padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>What if…</div>
          <WhatIfSimulator gov={gov} metricOptions={metricOptions} />
        </div>
      )}

      <div style={{ ...CARD, overflow: 'hidden' }}>
        {visible.length === 0 && (
          <div style={{ padding: 28, textAlign: 'center', fontSize: 13, color: 'var(--text-tertiary)' }}>
            Nothing in this category.
          </div>
        )}
        {visible.map((row, i) => {
          const ring = RING[row.severity];
          const open = expandedId === row.id;
          return (
            <div key={row.id} style={{ borderTop: i ? '1px solid var(--border-main)' : 'none' }}>
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '26px 1fr auto auto',
                  alignItems: 'center',
                  gap: 12,
                  padding: '12px 16px',
                }}
              >
                <span
                  aria-hidden
                  style={{
                    width: 22,
                    height: 22,
                    borderRadius: 999,
                    border: `1.5px solid ${ring.color}`,
                    color: ring.color,
                    display: 'grid',
                    placeItems: 'center',
                    fontSize: 12,
                    fontWeight: 800,
                  }}
                >
                  {ring.glyph}
                </span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>{row.title}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
                    {row.chain ? <Chain items={row.chain} more={row.more} /> : row.detail}
                  </div>
                </div>
                <Pill tone="n">{CATEGORY_LABEL[row.category]}</Pill>
                <button type="button" style={{ ...BTN_SM, minWidth: 72 }} onClick={() => onAction(row)}>
                  {open ? 'Collapse' : row.action}
                </button>
              </div>
              {open && (
                <div style={{ padding: '0 16px 16px 54px' }}>{renderDetail(row)}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
