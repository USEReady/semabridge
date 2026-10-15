import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { api } from '../utils/api';
import PanelBoundary from './common/PanelBoundary';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

const KINDS = [
  ['metrics', 'metric', 'metrics'],
  ['dimensions', 'dimension', 'dimensions'],
  ['relationships', 'relationship', 'relationships'],
  ['tables', 'table', 'tables'],
];

const asList = (value) => (Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : []);
const text = (value) => (value == null ? '' : String(value));

function bucketOf(delta, key) {
  const bucket = delta?.kinds?.[key];
  return bucket && typeof bucket === 'object' ? bucket : {};
}

function countAll(delta, field) {
  return KINDS.reduce((sum, [key]) => sum + asList(bucketOf(delta, key)[field]).length, 0);
}

const STATUS_STYLE = {
  deployed: { color: '#22c55e', label: 'deployed' },
  'will be deployed': { color: '#3b82f6', label: 'will be deployed' },
  placeholder: { color: '#f59e0b', label: 'NULL placeholder' },
  'not deployed': { color: '#f59e0b', label: 'not deployed' },
  'kept in Snowflake': { color: 'var(--text-tertiary)', label: 'kept in Snowflake' },
};

function StatusPill({ item }) {
  const style = STATUS_STYLE[item.status];
  if (!style) return null;
  return (
    <span
      title={text(item.status_reason)}
      style={{
        fontSize: 10, padding: '1px 7px', borderRadius: 4, fontWeight: 700,
        color: style.color, border: `1px solid ${style.color}`, whiteSpace: 'nowrap',
      }}
    >
      {style.label}
    </span>
  );
}

function ShowValue({ runContext, item }) {
  const [state, setState] = useState({ loading: false, value: undefined, error: null });
  if (!runContext?.runId || !item.query) return null;
  const load = async () => {
    setState({ loading: true, value: undefined, error: null });
    try {
      const res = await api.getUpsertMetricValue(runContext.projectId, runContext.runId, runContext.model, item.name);
      setState({ loading: false, value: res?.value, error: null });
    } catch (err) {
      setState({ loading: false, value: undefined, error: err?.message || 'Query failed' });
    }
  };
  const formatted = typeof state.value === 'number'
    ? state.value.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : text(state.value);
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <button
        type="button"
        onClick={load}
        disabled={state.loading}
        style={{
          fontSize: 11, padding: '2px 8px', borderRadius: 5, cursor: 'pointer',
          border: '1px solid var(--border-main)', background: 'transparent', color: 'var(--text-secondary)',
        }}
      >
        {state.loading ? 'Querying…' : 'Show value'}
      </button>
      {state.value !== undefined && (
        <strong style={{ fontFamily: MONO, color: 'var(--text-primary)' }}>{state.value === null ? 'NULL' : formatted}</strong>
      )}
      {state.error && <span style={{ color: 'var(--color-error)' }}>{state.error}</span>}
    </span>
  );
}

function ChangeItem({ item, kindLabel, change, runContext }) {
  const isMetric = kindLabel === 'metric';
  return (
    <div style={{
      padding: '8px 10px', borderRadius: 6, background: 'var(--bg-surface)',
      border: '1px solid var(--border-subtle, var(--border-main))', display: 'grid', gap: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <strong style={{ color: 'var(--text-primary)' }}>{text(item.name)}</strong>
        <span style={{ fontSize: 10, color: 'var(--text-tertiary)', textTransform: 'uppercase', fontWeight: 700 }}>{kindLabel}</span>
        {item.table && <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>in <span style={{ fontFamily: MONO }}>{text(item.table)}</span></span>}
        <StatusPill item={item} />
        {isMetric && change !== 'removed' && <ShowValue runContext={runContext} item={item} />}
      </div>
      {item.status === 'placeholder' && item.status_reason && (
        <div style={{ fontSize: 11, color: '#f59e0b' }}>{text(item.status_reason)}</div>
      )}
      {item.status === 'not deployed' && item.status_reason && (
        <div style={{ fontSize: 11, color: '#f59e0b' }}>{text(item.status_reason)}</div>
      )}
      {change === 'modified' && item.old_expression != null && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>
          {text(item.old_expression)} <span style={{ color: 'var(--text-tertiary)' }}>→</span> {text(item.new_expression)}
        </div>
      )}
      {change === 'modified' && item.old_definition && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>
          {text(item.old_definition)} → {text(item.new_definition)}
        </div>
      )}
      {change === 'modified' && Array.isArray(item.columns_added) && item.columns_added.length > 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>columns added: {item.columns_added.map(text).join(', ')}</div>
      )}
      {change === 'modified' && Array.isArray(item.columns_removed) && item.columns_removed.length > 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>columns removed: {item.columns_removed.map(text).join(', ')}</div>
      )}
      {change === 'added' && isMetric && item.sql_expression && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>
          <span style={{ color: 'var(--text-tertiary)' }}>expression:</span> {text(item.sql_expression)}
        </div>
      )}
      {change === 'added' && isMetric && item.expression && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: 'var(--text-tertiary)', overflowWrap: 'anywhere' }}>
          source DAX: {text(item.expression)}
        </div>
      )}
    </div>
  );
}

function Collapsible({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ marginTop: 10 }}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, padding: 0, border: 'none',
          background: 'transparent', cursor: 'pointer', fontSize: 12, fontWeight: 700,
          color: 'var(--text-primary)',
        }}
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {title}
      </button>
      {open && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  );
}

function DeltaBody({ delta, modelLabel, isDryRun, runContext }) {
  if (delta.error) {
    return <div style={{ fontSize: 12, color: 'var(--color-error)', margin: '10px 0' }}>UPSERT: {text(delta.error)}</div>;
  }

  const added = countAll(delta, 'added');
  const modified = countAll(delta, 'modified');
  const removed = countAll(delta, 'removed');
  const unchangedByKind = KINDS.map(([key, , plural]) => [plural, Number(bucketOf(delta, key).unchanged) || 0]);
  const unchanged = unchangedByKind.reduce((sum, [, n]) => sum + n, 0);
  const noChanges = !delta.has_changes && !delta.first_deployment;

  const summary = (
    <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', margin: '10px 0 6px' }}>
      {isDryRun ? 'Upsert dry run' : 'Upsert sync'} · {text(modelLabel) || 'model'} · {added} added · {modified} modified · {removed} removed · {unchanged} unchanged
    </div>
  );

  if (noChanges) {
    return (
      <>
        {summary}
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          {isDryRun
            ? 'No changes in the PBIX since the last sync. Nothing will be deployed.'
            : 'No changes in the PBIX since the last sync. Nothing deployed.'}
        </div>
      </>
    );
  }

  const groups = [
    ['added', isDryRun ? 'Will be added' : 'Added'],
    ['modified', isDryRun ? 'Will be modified' : 'Modified'],
    ['removed', 'Removed from PBIX (kept in Snowflake)'],
  ];

  return (
    <>
      {summary}
      {delta.first_deployment && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          First deployment of this model: there is no earlier sync to compare with, so everything is new.
        </div>
      )}
      <Collapsible title="What changed" defaultOpen>
        <div style={{ display: 'grid', gap: 12 }}>
          {groups.map(([change, label]) => {
            const items = KINDS.flatMap(([key, kindLabel]) => asList(bucketOf(delta, key)[change]).map(item => ({ item, kindLabel })));
            if (!items.length) return null;
            return (
              <div key={change} style={{ display: 'grid', gap: 6 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
                  {label} · {items.length}
                </div>
                {items.map(({ item, kindLabel }, idx) => (
                  <ChangeItem
                    key={`${change}-${kindLabel}-${text(item.name)}-${idx}`}
                    item={item}
                    kindLabel={kindLabel}
                    change={change}
                    runContext={runContext}
                  />
                ))}
              </div>
            );
          })}
        </div>
      </Collapsible>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 10 }}>
        Unchanged: {unchangedByKind.map(([plural, n]) => `${n} ${plural}`).join(' · ')}
      </div>
    </>
  );
}

/**
 * UPSERT run / dry-run view: only what changed since the last deployment.
 * Without a delta (COPY runs) it renders `children` — the regular view —
 * untouched. With one, the regular view moves into a collapsed
 * "Full run details" section at the bottom. A rendering error in the delta
 * part never takes the page (or the regular details) down with it.
 */
export default function UpsertChangesView({ delta, modelLabel, isDryRun = false, runContext = null, children }) {
  if (!delta || typeof delta !== 'object') return children ?? null;
  return (
    <div>
      <PanelBoundary name="UPSERT changes">
        <DeltaBody delta={delta} modelLabel={modelLabel} isDryRun={isDryRun} runContext={runContext} />
      </PanelBoundary>
      {children && (
        <Collapsible title={isDryRun ? 'Full dry-run details' : 'Full run details'}>{children}</Collapsible>
      )}
    </div>
  );
}
