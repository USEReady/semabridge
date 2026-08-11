/**
 * NeedsAttentionPanel — severity-tiered "what needs a human look" panel.
 *
 * Renders the dry-run response's `needs_attention` block (see
 * semabridge/api/services/severity_classifier.py), grouping the SAME
 * underlying signals DroppedFieldsPanel already shows (DropLedger records)
 * plus field-level collisions/validation issues, by how urgently they need
 * action rather than by which pipeline stage produced them:
 *
 *   critical — will break the deploy or silently produce wrong/missing data
 *   warning  — deploys fine, but the entity is gone or degraded, worth a look
 *   info     — intentional/by-design, safe to ignore
 *
 * Purely additive/read-only presentation over data the backend already
 * computes — no new detection logic lives here.
 *
 * Props:
 *   summary : { critical, warning, info, items: Array<{
 *                source, severity, entity_kind, entity_name, dataset,
 *                stage, validation_code, reason, detail, by_design }> }
 */
import { useState, useMemo } from 'react';
import { AlertOctagon, AlertTriangle, ChevronRight, Info } from 'lucide-react';

const SEVERITY_META = {
  critical: { label: 'Critical', color: '#ef4444', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.35)', Icon: AlertOctagon },
  warning: { label: 'Warning', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)', border: 'rgba(245,158,11,0.35)', Icon: AlertTriangle },
  info: { label: 'Info', color: 'var(--text-tertiary)', bg: 'transparent', border: 'var(--border-main)', Icon: Info },
};
const SEVERITY_ORDER = ['critical', 'warning', 'info'];

function severityMeta(severity) {
  return SEVERITY_META[String(severity || '').toLowerCase()] || SEVERITY_META.warning;
}

function groupBySeverity(items) {
  const buckets = { critical: [], warning: [], info: [] };
  items.forEach((item) => {
    const key = String(item?.severity || '').toLowerCase();
    (buckets[key] || buckets.warning).push(item);
  });
  return SEVERITY_ORDER.map((key) => ({ key, meta: SEVERITY_META[key], items: buckets[key] }))
    .filter((g) => g.items.length > 0);
}

function AttentionItemRow({ item }) {
  const meta = severityMeta(item?.severity);
  const Icon = meta.Icon;
  const sourceLabel = item?.source === 'collision' ? (item?.validation_code || 'Collision') : (item?.stage || 'Dropped');
  return (
    <div
      style={{
        padding: '10px 12px',
        borderRadius: 6,
        background: item?.severity === 'info' ? 'transparent' : 'var(--bg-surface)',
        border: `1px solid ${meta.border}`,
        opacity: item?.severity === 'info' ? 0.6 : 1,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 8,
      }}
    >
      <Icon size={13} style={{ color: meta.color, flexShrink: 0, marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
            {item?.entity_name || 'unknown'}
          </span>
          <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', fontWeight: 700, textTransform: 'uppercase' }}>
            {item?.entity_kind || 'entity'}
          </span>
          {item?.dataset && (
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
              in <span style={{ fontFamily: 'monospace' }}>{item.dataset}</span>
            </span>
          )}
          <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', fontWeight: 600, textTransform: 'uppercase' }}>
            {sourceLabel}
          </span>
        </div>
        <p style={{ margin: '4px 0 0 0', fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          {item?.reason || 'No reason recorded.'}
        </p>
        {item?.detail && (
          <p style={{ margin: '4px 0 0 0', fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'monospace', wordBreak: 'break-word' }}>
            {item.detail}
          </p>
        )}
      </div>
    </div>
  );
}

export default function NeedsAttentionPanel({ summary, defaultExpanded = true }) {
  const items = useMemo(() => (Array.isArray(summary?.items) ? summary.items.filter(Boolean) : []), [summary]);
  const [expanded, setExpanded] = useState(defaultExpanded);
  const groups = useMemo(() => groupBySeverity(items), [items]);

  const critical = summary?.critical ?? groups.find((g) => g.key === 'critical')?.items.length ?? 0;
  const warning = summary?.warning ?? groups.find((g) => g.key === 'warning')?.items.length ?? 0;
  const info = summary?.info ?? groups.find((g) => g.key === 'info')?.items.length ?? 0;

  if (items.length === 0) return null;

  const headerMeta = critical > 0 ? SEVERITY_META.critical : warning > 0 ? SEVERITY_META.warning : SEVERITY_META.info;
  const HeaderIcon = headerMeta.Icon;
  const countLabel = [
    critical > 0 ? `${critical} critical` : null,
    warning > 0 ? `${warning} warning` : null,
    info > 0 ? `${info} info` : null,
  ].filter(Boolean).join(' · ');

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
          border: `1px solid ${headerMeta.border}`,
          background: headerMeta.bg,
          cursor: 'pointer',
          textAlign: 'left',
          width: '100%',
        }}
      >
        <ChevronRight
          size={14}
          style={{ color: headerMeta.color, transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }}
        />
        <HeaderIcon size={14} style={{ color: headerMeta.color, flexShrink: 0 }} />
        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
          Needs Attention
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{countLabel}</span>
      </button>

      {expanded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, padding: '4px 4px 4px 8px' }}>
          {groups.map((group) => (
            <div key={group.key} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: group.meta.color, textTransform: 'uppercase', letterSpacing: 0.4 }}>
                {group.meta.label} · {group.items.length}
              </div>
              {group.items.map((item, idx) => (
                <AttentionItemRow key={`${item?.source}-${item?.entity_name}-${idx}`} item={item} />
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
