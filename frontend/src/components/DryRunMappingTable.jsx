/**
 * DryRunMappingTable — renders field-level dry-run mapping results as a
 * filterable, searchable table with a summary bar.
 * Shows columns and measures separately, and displays detected relationships.
 *
 * Props:
 *   mappings      : NormalizedRow[]  — array of normalised field mapping rows
 *   onEdit        : (rowId: string) => void  — called when user clicks Edit/Map
 *   summary       : { total_fields, auto_mapped, unmapped, collisions }
 *   relationships : Array<{ source, target, joinType, condition, confidence }>
 */
import { useState, useMemo, useCallback } from 'react';
import { Edit2, GitMerge, Zap, CheckCircle } from 'lucide-react';
import StatusBadge from './common/StatusBadge';
import SmartSearchBar, { matchesSmartQuery } from './common/SmartSearchBar';
import { api } from '../utils/api';

// ─── Filter tab definitions ───────────────────────────────────────────────────
const MAPPING_FILTERS = [
  { id: 'all',       label: 'All' },
  { id: 'auto',      label: 'Auto' },
  { id: 'manual',    label: 'Manual' },
  { id: 'unmapped',  label: 'Unmapped' },
  { id: 'collision', label: 'Collision' },
];

// ─── Shared helper ────────────────────────────────────────────────────────────
export function isBlockingRow(row) {
  return String(row?.status || '').toLowerCase() === 'collision';
}

// ─── Type badge ───────────────────────────────────────────────────────────────
function TypeBadge({ type }) {
  const label = String(type || 'unknown');
  const normalized = label.toLowerCase();
  let hash = 0;
  for (let i = 0; i < normalized.length; i += 1) {
    hash = ((hash << 5) - hash) + normalized.charCodeAt(i);
    hash |= 0;
  }
  const hue = Math.abs(hash) % 360;
  const color = `hsl(${hue}, 72%, 58%)`;
  return (
    <span style={{
      fontFamily: 'monospace',
      fontSize: 10,
      fontWeight: 700,
      padding: '2px 6px',
      borderRadius: 4,
      background: `${color}20`,
      color,
      border: `1px solid ${color}35`,
      whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  );
}

// ─── Measure badge ────────────────────────────────────────────────────────────
function MeasureBadge() {
  return (
    <span style={{
      fontSize: 10,
      fontWeight: 700,
      padding: '2px 6px',
      borderRadius: 999,
      border: '1px solid rgba(56, 189, 248, 0.45)',
      background: 'rgba(56, 189, 248, 0.14)',
      color: '#7dd3fc',
      whiteSpace: 'nowrap',
    }}>
      fx Measure
    </span>
  );
}

// ─── Status badge map ─────────────────────────────────────────────────────────
const STATUS_BADGE_MAP = {
  auto:      { status: 'success', label: 'Auto' },
  manual:    { status: 'running', label: 'Manual' },
  auto_resolved: { status: 'success', label: 'Auto-Resolved' },
  unmapped:  { status: 'draft',   label: 'Unmapped' },
  collision: { status: 'error',   label: 'Collision' },
};

// ─── Shared table header ──────────────────────────────────────────────────────
function TableHeader() {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1.5fr 1.5fr 0.9fr 0.7fr',
      gap: '1rem',
      padding: '10px 14px',
      background: 'var(--bg-surface-raised)',
      borderBottom: '1px solid var(--border-main)',
      fontSize: 11,
      fontWeight: 700,
      color: 'var(--text-tertiary)',
      textTransform: 'uppercase',
      letterSpacing: '0.06em',
    }}>
      <div>Source Field</div>
      <div>Target Field</div>
      <div>Status</div>
      <div>Action</div>
    </div>
  );
}

// ─── Single mapping row ───────────────────────────────────────────────────────
function MappingRow({ row, onEdit }) {
  const badgeCfg = STATUS_BADGE_MAP[String(row.status || '').toLowerCase()]
    ?? { status: 'draft', label: row.status };
  const isCollision = String(row.status || '').toLowerCase() === 'collision';
  const isMeasure = String(row.field_type || row.entity_kind || '').toLowerCase() === 'measure';

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '1.5fr 1.5fr 0.9fr 0.7fr',
        gap: '1rem',
        padding: '10px 14px',
        borderBottom: '1px solid var(--border-main)',
        alignItems: 'center',
        background: isCollision ? 'rgba(239, 68, 68, 0.04)' : 'transparent',
      }}
    >
      {/* Source Field */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        <div style={{
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--text-primary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {row.source_field}
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
          {isMeasure ? <MeasureBadge /> : <TypeBadge type={row.source_type} />}
          {!isMeasure && row.source_table_name && (
            <span style={{
              fontSize: 10,
              fontWeight: 600,
              padding: '2px 6px',
              borderRadius: 4,
              background: 'rgba(56, 189, 248, 0.12)',
              color: '#7dd3fc',
              border: '1px solid rgba(56, 189, 248, 0.35)',
              whiteSpace: 'nowrap',
              maxWidth: 140,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {row.source_table_name}
            </span>
          )}
          {isMeasure && row.measure_source_tables?.length > 0 && (
            <span style={{
              fontSize: 10,
              color: 'var(--text-tertiary)',
              fontStyle: 'italic',
            }}>
              {row.measure_source_tables.join(', ')}
            </span>
          )}
        </div>
        {isMeasure && row.measure_expression && (
          <div style={{
            fontSize: 10,
            color: 'var(--text-tertiary)',
            fontFamily: 'monospace',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            maxWidth: 220,
          }}>
            {row.measure_expression}
          </div>
        )}
      </div>

      {/* Target Field */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
        <div style={{
          fontSize: 12,
          fontWeight: 600,
          color: row.target_field ? 'var(--text-primary)' : 'var(--text-tertiary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          fontStyle: row.target_field ? 'normal' : 'italic',
        }}>
          {row.target_field || '— unmapped —'}
        </div>
        {row.target_type && !isMeasure && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
            <TypeBadge type={row.target_type} />
          </div>
        )}
        {isCollision && row.suggested_target_name && (
          <div style={{ fontSize: 10, color: 'var(--color-error)', lineHeight: 1.4 }}>
            Suggestion: <span style={{ fontWeight: 700 }}>{row.suggested_target_name}</span>
          </div>
        )}
      </div>

      {/* Status */}
      <div>
        <StatusBadge status={badgeCfg.status} label={badgeCfg.label} size="sm" />
      </div>

      {/* Action */}
      <div>
        <button
          type="button"
          onClick={() => onEdit?.(row.id)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 5,
            padding: '5px 10px',
            borderRadius: 6,
            border: isCollision
              ? '1px solid rgba(239, 68, 68, 0.45)'
              : '1px solid var(--border-main)',
            background: isCollision
              ? 'rgba(239, 68, 68, 0.10)'
              : 'var(--bg-surface-raised)',
            color: isCollision ? 'var(--color-error)' : 'var(--text-secondary)',
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            whiteSpace: 'nowrap',
          }}
        >
          <Edit2 size={11} />
          {row.target_field ? 'Edit' : 'Map'}
        </button>
      </div>
    </div>
  );
}

// ─── Relationships section ────────────────────────────────────────────────────
function RelationshipsSection({ relationships }) {
  const [expanded, setExpanded] = useState(true);
  if (!relationships || relationships.length === 0) return null;

  return (
    <div style={{
      border: '1px solid var(--border-main)',
      borderRadius: 10,
      overflow: 'hidden',
      background: 'var(--bg-surface)',
    }}>
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 14px',
          background: 'var(--bg-surface-raised)',
          border: 'none',
          borderBottom: expanded ? '1px solid var(--border-main)' : 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <GitMerge size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
          Detected Relationships
        </span>
        <span style={{
          fontSize: 11,
          fontWeight: 600,
          padding: '1px 7px',
          borderRadius: 999,
          background: 'var(--accent-blue)20',
          color: 'var(--accent-blue)',
        }}>
          {relationships.length}
        </span>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--text-tertiary)' }}>
          {expanded ? '▲ Hide' : '▼ Show'}
        </span>
      </button>

      {expanded && (
        <div>
          {relationships.map((rel, i) => (
            <div
              key={i}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr auto 1fr auto',
                gap: 8,
                padding: '10px 14px',
                borderBottom: i < relationships.length - 1 ? '1px solid var(--border-main)' : 'none',
                alignItems: 'center',
                fontSize: 12,
              }}
            >
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {rel.source}
              </div>
              <div style={{
                padding: '2px 8px',
                borderRadius: 4,
                background: 'var(--bg-surface-raised)',
                border: '1px solid var(--border-main)',
                fontSize: 10,
                fontWeight: 700,
                color: 'var(--text-secondary)',
                whiteSpace: 'nowrap',
              }}>
                {rel.joinType || rel.join_type || 'JOIN'}
              </div>
              <div style={{ fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {rel.target}
              </div>
              <div style={{
                fontSize: 10,
                fontWeight: 600,
                padding: '2px 7px',
                borderRadius: 999,
                background: 'var(--accent-blue)14',
                color: 'var(--accent-blue)',
                whiteSpace: 'nowrap',
              }}>
                {rel.confidence || 'auto'}
              </div>
              {rel.condition && (
                <div style={{
                  gridColumn: '1 / -1',
                  fontSize: 10,
                  fontFamily: 'monospace',
                  color: 'var(--text-tertiary)',
                  padding: '4px 8px',
                  borderRadius: 4,
                  background: 'var(--bg-main)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {rel.condition}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function DryRunMappingTable({
  mappings = [],
  onEdit,
  onBulkResolved,     // (resolvedMap: Record<rowId, suggestedTarget>) => void
  summary,
  relationships = [],
}) {
  const [activeFilter, setActiveFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [useRegex, setUseRegex] = useState(false);
  const [bulkResolving, setBulkResolving] = useState(false);
  const [bulkToast, setBulkToast] = useState(null); // { type: 'success'|'error', msg }

  // ── Split columns vs measures ───────────────────────────────────────────────
  const { columns, measures } = useMemo(() => {
    const cols = [];
    const meas = [];
    mappings.forEach((row) => {
      const kind = String(row?.field_type || row?.entity_kind || '').toLowerCase();
      if (kind === 'measure' || kind === 'metric') {
        meas.push(row);
      } else {
        cols.push(row);
      }
    });
    return { columns: cols, measures: meas };
  }, [mappings]);

  // ── Derived counts (over all mappings) ──────────────────────────────────────
  const counts = useMemo(() => {
    const c = { all: mappings.length, auto: 0, manual: 0, unmapped: 0, collision: 0 };
    mappings.forEach((row) => {
      const s = String(row?.status || '').toLowerCase();
      if (c[s] !== undefined) c[s] += 1;
    });
    return c;
  }, [mappings]);

  // ── Bulk resolve handler ─────────────────────────────────────────────────────
  const handleBulkResolve = useCallback(async () => {
    const collisions = mappings
      .filter(r => String(r?.status || '').toLowerCase() === 'collision')
      .map(r => ({
        entity_name:    r.source_table_name || '',
        field_name:     r.source_field      || '',
        current_target: r.target_field      || r.source_field || '',
      }));

    if (!collisions.length) return;

    setBulkResolving(true);
    setBulkToast(null);
    try {
      const data = await api.bulkResolve(collisions);

      // Build a map from field identity → suggested target for the parent to consume
      const resolvedMap = {};
      const resolvedList = data.resolved || [];
      resolvedList.forEach((resolved) => {
        // Match by entity + field name back to the row
        const matchedRow = mappings.find(
          r => r.source_table_name === resolved.entity_name
            && r.source_field      === resolved.field_name
        );
        if (matchedRow?.id) {
          resolvedMap[matchedRow.id] = resolved.suggested_target;
        }
      });

      onBulkResolved?.(resolvedMap);
      setBulkToast({ type: 'success', msg: `${resolvedList.length} collision${resolvedList.length !== 1 ? 's' : ''} resolved with deterministic hashes.` });
    } catch (err) {
      setBulkToast({ type: 'error', msg: `Resolve failed: ${err.message}` });
    } finally {
      setBulkResolving(false);
      setTimeout(() => setBulkToast(null), 4000);
    }
  }, [mappings, onBulkResolved]);

  // ── Filtered rows ───────────────────────────────────────────────────────────
  const filterRow = (row) => {
    if (activeFilter !== 'all' && String(row?.status || '').toLowerCase() !== activeFilter) return false;
    if (search.trim()) {
      const haystack = [
        row.source_field,
        row.target_field,
        row.source_type,
        row.target_type,
        row.source_table_name,
        row.status,
        row.measure_expression,
        ...(row.measure_source_tables || []),
      ].filter(Boolean).join(' ');
      if (!matchesSmartQuery(haystack, search, useRegex)) return false;
    }
    return true;
  };

  const filteredColumns = useMemo(() => columns.filter(filterRow), [columns, activeFilter, search, useRegex]);
  const filteredMeasures = useMemo(() => measures.filter(filterRow), [measures, activeFilter, search, useRegex]);

  // ── Summary bar values ──────────────────────────────────────────────────────
  const totalFields  = summary?.total_fields  ?? mappings.length;
  const autoMapped   = summary?.auto_mapped   ?? counts.auto;
  const unmappedCnt  = summary?.unmapped      ?? counts.unmapped;
  const collisionCnt = summary?.collisions    ?? counts.collision;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── Summary bar ──────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '8px 12px',
        borderRadius: 8,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        fontSize: 12,
        color: 'var(--text-secondary)',
        flexWrap: 'wrap',
      }}>
        <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
          {totalFields} field{totalFields !== 1 ? 's' : ''}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>—</span>
        <span style={{ color: 'var(--text-tertiary)', fontWeight: 600 }}>
          {columns.length} column{columns.length !== 1 ? 's' : ''}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>·</span>
        <span style={{ color: '#7dd3fc', fontWeight: 600 }}>
          {measures.length} measure{measures.length !== 1 ? 's' : ''}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>·</span>
        <span style={{ color: 'var(--color-success)', fontWeight: 600 }}>
          {autoMapped} auto
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>·</span>
        <span style={{ color: 'var(--text-tertiary)', fontWeight: 600 }}>
          {unmappedCnt} unmapped
        </span>
        {collisionCnt > 0 && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>·</span>
            <span style={{ color: 'var(--color-error)', fontWeight: 600 }}>
              {collisionCnt} collision{collisionCnt !== 1 ? 's' : ''}
            </span>
          </>
        )}
      </div>

      {/* ── Controls row: filter tabs + search + Resolve All ─────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <div className="filter-tabs" style={{ marginBottom: 0 }}>
          {MAPPING_FILTERS.map((filter) => {
            const count = counts[filter.id] ?? counts.all;
            const active = activeFilter === filter.id;
            const isCollisionTab = filter.id === 'collision';
            return (
              <button
                key={filter.id}
                type="button"
                className={`filter-tab${active ? ' active' : ''}${isCollisionTab ? ' collision-tab' : ''}`}
                onClick={() => setActiveFilter(filter.id)}
              >
                {filter.label} ({count})
              </button>
            );
          })}
        </div>
        <div style={{ flex: '1 1 200px', minWidth: 160, maxWidth: 320 }}>
          <SmartSearchBar
            value={search}
            onChange={setSearch}
            useRegex={useRegex}
            onToggleRegex={setUseRegex}
            placeholder="Search fields..."
          />
        </div>

        {/* ── Resolve All button — only shown when collisions exist ─────── */}
        {counts.collision > 0 && (
          <button
            id="bulk-resolve-btn"
            type="button"
            disabled={bulkResolving}
            onClick={handleBulkResolve}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 7,
              border: '1px solid rgba(251,191,36,0.55)',
              background: bulkResolving
                ? 'rgba(251,191,36,0.06)'
                : 'rgba(251,191,36,0.12)',
              color: '#fbbf24',
              fontSize: 12,
              fontWeight: 700,
              cursor: bulkResolving ? 'not-allowed' : 'pointer',
              whiteSpace: 'nowrap',
              transition: 'background 0.15s, opacity 0.15s',
              opacity: bulkResolving ? 0.7 : 1,
            }}
          >
            {bulkResolving
              ? <>
                  <span style={{
                    width: 11, height: 11, border: '2px solid #fbbf24',
                    borderTopColor: 'transparent', borderRadius: '50%',
                    display: 'inline-block',
                    animation: 'spin 0.7s linear infinite',
                  }} />
                  Resolving…
                </>
              : <><Zap size={12} /> Resolve All ({counts.collision})</>}
          </button>
        )}
      </div>

      {/* ── Bulk-resolve toast ───────────────────────────────────────────────── */}
      {bulkToast && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '9px 14px',
          borderRadius: 8,
          border: bulkToast.type === 'success'
            ? '1px solid rgba(34,197,94,0.4)'
            : '1px solid rgba(239,68,68,0.4)',
          background: bulkToast.type === 'success'
            ? 'rgba(34,197,94,0.09)'
            : 'rgba(239,68,68,0.09)',
          color: bulkToast.type === 'success' ? 'var(--color-success)' : 'var(--color-error)',
          fontSize: 12,
          fontWeight: 600,
        }}>
          {bulkToast.type === 'success' && <CheckCircle size={13} />}
          {bulkToast.msg}
        </div>
      )}

      {/* ── Columns table ────────────────────────────────────────────────────── */}
      {mappings.length === 0 ? (
        <div style={{
          padding: '48px 24px',
          textAlign: 'center',
          color: 'var(--text-tertiary)',
          fontSize: 13,
          border: '1px solid var(--border-main)',
          borderRadius: 10,
          background: 'var(--bg-surface)',
        }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 6 }}>
            No fields found for the selected models
          </div>
          <div>Go back to Step 3 and select different models, then run the dry run again.</div>
        </div>
      ) : (
        <>
          {/* Columns section */}
          {columns.length > 0 && (
            <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, overflow: 'hidden', background: 'var(--bg-surface)' }}>
              <div style={{
                padding: '8px 14px',
                background: 'var(--bg-surface-raised)',
                borderBottom: '1px solid var(--border-main)',
                fontSize: 11,
                fontWeight: 700,
                color: 'var(--text-secondary)',
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
              }}>
                Columns ({columns.length})
              </div>
              <TableHeader />
              {filteredColumns.length === 0 ? (
                <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                  No columns match the current filter or search.
                </div>
              ) : (
                filteredColumns.map(row => (
                  <MappingRow key={row.id} row={row} onEdit={onEdit} />
                ))
              )}
            </div>
          )}

          {/* Measures section */}
          {measures.length > 0 && (
            <div style={{ border: '1px solid rgba(56, 189, 248, 0.35)', borderRadius: 10, overflow: 'hidden', background: 'var(--bg-surface)' }}>
              <div style={{
                padding: '8px 14px',
                background: 'rgba(56, 189, 248, 0.08)',
                borderBottom: '1px solid rgba(56, 189, 248, 0.25)',
                fontSize: 11,
                fontWeight: 700,
                color: '#7dd3fc',
                textTransform: 'uppercase',
                letterSpacing: '0.06em',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}>
                <span>fx</span>
                <span>Measures ({measures.length})</span>
              </div>
              <TableHeader />
              {filteredMeasures.length === 0 ? (
                <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                  No measures match the current filter or search.
                </div>
              ) : (
                filteredMeasures.map(row => (
                  <MappingRow key={row.id} row={row} onEdit={onEdit} />
                ))
              )}
            </div>
          )}
        </>
      )}

      {/* ── Relationships section ─────────────────────────────────────────────── */}
      <RelationshipsSection relationships={relationships} />
    </div>
  );
}