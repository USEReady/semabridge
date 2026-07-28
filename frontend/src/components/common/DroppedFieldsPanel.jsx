/**
 * DroppedFieldsPanel — "what got dropped and why" transparency panel.
 *
 * Renders a flat list of DropRecord-shaped entries (see
 * semabridge/core/drop_ledger.py) grouped by pipeline stage, entirely
 * data-driven off whatever `stage`/`reason` values are actually present —
 * no fixed vocabulary is assumed beyond a friendly label for the five known
 * stages. `by_design` entries (intentional exclusions, e.g. auto-hidden date
 * tables) are visually de-emphasized rather than hidden, since they're still
 * useful context but not something the user needs to act on.
 *
 * Used on both the pre-deploy dry-run mapping page and the post-deployment
 * run summary — same shape, same component, two call sites.
 *
 * Props:
 *   entries : Array<{ entity_kind, entity_name, dataset, stage, reason, detail, by_design }>
 *   title   : optional heading override (default "Dropped Fields")
 */
import { useState, useMemo } from 'react';
import { AlertTriangle, ChevronRight, Info } from 'lucide-react';

const STAGE_LABELS = {
  extraction: 'Extraction',
  dax_translation: 'DAX Translation',
  schema_validation: 'Schema Validation',
  ddl_emission: 'DDL Emission',
  ddl_deployment: 'DDL Deployment',
};

// Known stages sort first, in pipeline order; any stage not in this list
// (future stages, typos, etc.) is appended after, in first-seen order —
// nothing here assumes the five known stages are the only ones possible.
const STAGE_ORDER = Object.keys(STAGE_LABELS);

function stageLabel(stage) {
  const key = String(stage || '').toLowerCase().trim();
  if (STAGE_LABELS[key]) return STAGE_LABELS[key];
  const word = key.replace(/[_-]+/g, ' ').trim();
  return word ? word.replace(/\b\w/g, (c) => c.toUpperCase()) : 'Unknown Stage';
}

function groupByStage(entries) {
  const order = [];
  const buckets = new Map();
  entries.forEach((entry) => {
    const key = String(entry?.stage || 'unknown').toLowerCase().trim() || 'unknown';
    if (!buckets.has(key)) {
      buckets.set(key, []);
      order.push(key);
    }
    buckets.get(key).push(entry);
  });
  const known = STAGE_ORDER.filter((k) => order.includes(k));
  const unknown = order.filter((k) => !STAGE_ORDER.includes(k));
  return [...known, ...unknown].map((key) => ({
    key,
    label: stageLabel(key),
    entries: buckets.get(key),
  }));
}

function DropEntryRow({ entry }) {
  const byDesign = Boolean(entry?.by_design);
  return (
    <div
      style={{
        padding: '10px 12px',
        borderRadius: 6,
        background: byDesign ? 'transparent' : 'var(--bg-surface)',
        border: `1px solid ${byDesign ? 'var(--border-main)' : 'rgba(245,158,11,0.3)'}`,
        opacity: byDesign ? 0.6 : 1,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
      }}
    >
      {byDesign ? (
        <Info size={13} style={{ color: 'var(--text-tertiary)', flexShrink: 0, marginTop: 2 }} />
      ) : (
        <AlertTriangle size={13} style={{ color: '#f59e0b', flexShrink: 0, marginTop: 2 }} />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
            {entry?.entity_name || 'unknown'}
          </span>
          <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', fontWeight: 700, textTransform: 'uppercase' }}>
            {entry?.entity_kind || 'entity'}
          </span>
          {entry?.dataset && (
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              in <span style={{ fontFamily: 'monospace' }}>{entry.dataset}</span>
            </span>
          )}
          {byDesign && (
            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', fontWeight: 600 }}>
              By Design
            </span>
          )}
        </div>
        <p style={{ margin: '4px 0 0 0', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          {entry?.reason || 'No reason recorded.'}
        </p>
        {entry?.detail && (
          <p style={{ margin: '4px 0 0 0', fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace', wordBreak: 'break-word' }}>
            {entry.detail}
          </p>
        )}
      </div>
    </div>
  );
}

export default function DroppedFieldsPanel({ entries = [], title = 'Dropped Fields', defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const groups = useMemo(() => groupByStage(entries.filter(Boolean)), [entries]);
  const total = entries.length;
  const actionableCount = entries.filter((e) => !e?.by_design).length;

  if (total === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 12px',
          borderRadius: 8,
          border: '1px solid rgba(245,158,11,0.35)',
          background: 'rgba(245,158,11,0.08)',
          cursor: 'pointer',
          textAlign: 'left',
          width: '100%',
        }}
      >
        <ChevronRight
          size={14}
          style={{ color: '#f59e0b', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }}
        />
        <AlertTriangle size={14} style={{ color: '#f59e0b', flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
          {title}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
          {actionableCount > 0
            ? `${actionableCount} excluded from the deployed model${total > actionableCount ? `, ${total - actionableCount} by design` : ''}`
            : `${total} by design`}
        </span>
      </button>

      {expanded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, padding: '4px 4px 4px 8px' }}>
          {groups.map((group) => (
            <div key={group.key} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.4 }}>
                {group.label} · {group.entries.length}
              </div>
              {group.entries.map((entry, idx) => (
                <DropEntryRow key={`${entry?.entity_kind}-${entry?.entity_name}-${idx}`} entry={entry} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
