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
import { Edit2, GitMerge, Zap, CheckCircle, Tags, AlertTriangle, PlusCircle, SkipForward, X, ChevronRight, Info } from 'lucide-react';
import StatusBadge from './common/StatusBadge';
import DroppedFieldsPanel from './common/DroppedFieldsPanel';
import SmartSearchBar from './common/SmartSearchBar';
import { matchesSmartQuery } from './common/smartSearchQuery.js';
import { api } from '../utils/api';
import SynonymEditModal from './SynonymEditModal';
import { suggestCollisionResolutions, sanitizeMappingName, shortDeterministicHash, buildSourceFingerprint } from '../utils/projectHelpers';
import {
  groupRowsByFieldType,
  normalizeFieldTypeKey,
  computeStatusFacets,
  hasConversionData,
  rowConversionOutcome,
  hasComplexityTierData,
  rowComplexityTier,
  rowNeedsReview,
  CONVERSION_FACET_LABELS,
  COMPLEXITY_TIER_FACET_LABELS,
} from '../utils/mappingFilterUtils';
import { getStaticRiskBadge, getSelfReportedEstimateText, getEnrichmentUnverifiableCaveat } from '../utils/riskLabels';

// ─── Shared helper ────────────────────────────────────────────────────────────
export function isBlockingRow(row) {
  return String(row?.status || '').toLowerCase() === 'collision';
}

// Shared between TableHeader and MappingRow so the two never drift apart.
// The Status and Action columns are fixed pixel widths (not fr/minmax) on
// purpose: Status needs to be wide enough to hold a stacked Status badge +
// risk badge + confidence/caveat line without any of them bleeding into the
// Action column, and Action needs to stay put regardless of how much (or
// how little) content the row stacks above it in Status.
const MAPPING_ROW_GRID_COLUMNS = '1.5fr 1.5fr minmax(180px, 0.8fr) 172px 170px';

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
      gridTemplateColumns: MAPPING_ROW_GRID_COLUMNS,
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
      <div>Synonyms</div>
      <div style={{ textAlign: 'center' }}>Status</div>
      <div style={{ textAlign: 'right' }}>Action</div>
    </div>
  );
}

// ─── Single mapping row ───────────────────────────────────────────────────────
// ─── Single mapping row ───────────────────────────────────────────────────────
function MappingRow({ row, onEdit, onSynonymEdit, expandedCollision, setExpandedCollision, allRows, pendingRenames, setPendingRenames, applyingRename, onApplyRename }) {
  const [expandedMeasure, setExpandedMeasure] = useState(false);
  const badgeCfg = STATUS_BADGE_MAP[String(row.status || '').toLowerCase()]
    ?? { status: 'draft', label: row.status };
  const isCollision = String(row.status || '').toLowerCase() === 'collision';
  // Three SEPARATE, never-blended signals -- see utils/riskLabels.js. Each
  // is independently null/absent for most rows; none ever substitutes
  // for another.
  const staticRiskBadge = getStaticRiskBadge(row);
  const selfReportedEstimateText = getSelfReportedEstimateText(row);
  const enrichmentUnverifiableCaveat = getEnrichmentUnverifiableCaveat(row);
  const isMeasure = String(row.field_type || row.entity_kind || '').toLowerCase() === 'measure';
  const hasDetails = isMeasure || (Array.isArray(row.synonyms) && row.synonyms.length > 0);
  const isPanelOpen = (isCollision && expandedCollision === row.id) || (hasDetails && expandedMeasure);
  // Full text is always available via the native tooltip (title attribute);
  // only the short form is derived here for the always-visible row label, so
  // the caveat is shortened for display without ever being dropped.
  const selfReportedEstimatePct = selfReportedEstimateText?.match(/(\d+)%/)?.[1] ?? null;

  return (
    <div style={{ borderBottom: '1px solid var(--border-main)' }}>
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: MAPPING_ROW_GRID_COLUMNS,
        gap: '1rem',
        padding: '12px 14px',
        // Top-aligned, not centered: however many signals Status stacks
        // (badge / risk badge / confidence / caveat), Action and the other
        // columns stay pinned to the same y-position at the top of the row.
        alignItems: 'start',
        background: isCollision ? 'rgba(239, 68, 68, 0.04)' : 'transparent',
      }}
    >
      {/* Source Field */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <div style={{
          fontSize: 12,
          fontWeight: 600,
          color: 'var(--text-primary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          flexShrink: 0,
        }}>
          {row.source_field}
        </div>
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', minWidth: 0 }}>
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
              maxWidth: 120,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {row.source_table_name}
            </span>
          )}
        </div>
      </div>

      {/* Target Field */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
        <div style={{
          fontSize: 12,
          fontWeight: 600,
          color: row.target_field ? 'var(--text-primary)' : 'var(--text-tertiary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          fontStyle: row.target_field ? 'normal' : 'italic',
          flexShrink: 0,
        }}>
          {row.target_field || '— unmapped —'}
        </div>
        {row.target_type && !isMeasure && (
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            <TypeBadge type={row.target_type} />
          </div>
        )}
        {isCollision && (() => {
          // Only show a suggestion chip if it differs from the conflicting target name
          const { suggestions } = suggestCollisionResolutions(row, allRows);
          const best = suggestions.find(s => s.id !== 'hash');
          if (!best || best.label === row.target_field) return null;
          return (
            <div style={{ fontSize: 10, color: '#fbbf24', fontWeight: 600, marginLeft: 8, whiteSpace: 'nowrap' }}>
              → {best.label}
            </div>
          );
        })()}
      </div>

      {/* Synonyms */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0, overflow: 'hidden' }}>
          {(row.synonym_overrides || row.synonymOverrides || []).slice(0, 2).map((synonym) => (
            <span
              key={synonym}
              title={synonym}
              style={{
                maxWidth: 78,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontSize: 10,
                fontWeight: 700,
                padding: '2px 6px',
                borderRadius: 999,
                border: '1px solid rgba(56, 189, 248, 0.35)',
                background: 'rgba(56, 189, 248, 0.12)',
                color: '#7dd3fc',
              }}
            >
              {synonym}
            </span>
          ))}
          {(row.synonym_overrides || row.synonymOverrides || []).length > 2 && (
            <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>
              +{(row.synonym_overrides || row.synonymOverrides || []).length - 2}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => onSynonymEdit?.(row)}
          title="Edit synonyms"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            width: 24,
            height: 24,
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised)',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          <Tags size={12} />
        </button>
      </div>

      {/* Status / Risk / Confidence -- one fixed-width, self-contained
          column (168px, see MAPPING_ROW_GRID_COLUMNS). minWidth: 0 keeps a
          long risk-tier label from forcing the grid track itself wider than
          168px (the default grid-item min-width is content-based, which is
          exactly what let the risk badge bleed into the Action column). */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, minWidth: 0, width: '100%', boxSizing: 'border-box' }}>
        <StatusBadge status={badgeCfg.status} label={badgeCfg.label} size="sm" />
        {/* Static risk tier -- metric-only, backend-computed from static
            validators only (schema-reference check, type-safety
            validator, unreachable-dimension detector, DropLedger
            reconciliation). Deliberately its own badge, never merged into
            the Status badge above: this is "will the SQL run", not
            "is the identifier mapped/named correctly". wrap lets the
            longest label ("Failed a static check — predicted failure")
            break onto multiple lines inside the column instead of forcing
            the pill (and the column) wider than its allotted width. */}
        {staticRiskBadge && (
          <StatusBadge status={staticRiskBadge.status} label={staticRiskBadge.label} size="sm" wrap />
        )}
        {/* AI self-reported estimate -- Tier-5-only, never shown for a
            Tier 1-4 (deterministic) metric since llm_self_reported_
            confidence is always null there. Deliberately NOT a colored
            StatusBadge pill: this is the LLM's own unverified guess, not
            a validated risk signal, and must never be visually confused
            with the badge above it. Shown as a short always-visible form
            with the full "(not independently verified)" qualifier moved to
            the native tooltip (title) -- getSelfReportedEstimateText()
            still always returns the full qualified string, so the caveat
            is reachable on hover/focus, never removed, and row height no
            longer depends on how long that sentence happens to be. */}
        {selfReportedEstimateText && (
          <span
            title={selfReportedEstimateText}
            tabIndex={0}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 3,
              fontSize: 10,
              fontWeight: 500,
              fontStyle: 'italic',
              color: 'var(--text-tertiary)',
              whiteSpace: 'nowrap',
              cursor: 'help',
            }}
          >
            AI estimate: {selfReportedEstimatePct}%
            <Info size={10} style={{ flexShrink: 0 }} />
          </span>
        )}
        {/* Enrichment-column-unverifiable caveat -- a metric whose SQL
            already translated correctly, referencing a flag column
            (e.g. IS_YTD) that only exists on the enriched view
            _create_enriched_view builds at real-deploy time. Dry-run has
            no live connection to confirm it, so this is neither a risk
            signal (staticRiskBadge) nor an AI guess
            (selfReportedEstimateText) -- a third, distinct, non-error
            tone (info, not warning/error) so it can't be mistaken for
            either. Same short-label + tooltip treatment as the estimate
            above, for the same row-height reason; getEnrichmentUnverifiableCaveat()
            still returns the full sentence, surfaced via title. */}
        {enrichmentUnverifiableCaveat && (
          <span
            title={enrichmentUnverifiableCaveat}
            tabIndex={0}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 3,
              fontSize: 10,
              fontWeight: 500,
              color: '#3B82F6',
              whiteSpace: 'nowrap',
              cursor: 'help',
            }}
          >
            Enrichment unverifiable
            <Info size={10} style={{ flexShrink: 0 }} />
          </span>
        )}
      </div>

      {/* Action */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, flexWrap: 'nowrap' }}>
        {isCollision ? (
          <button
            type="button"
            onClick={() => setExpandedCollision(expandedCollision === row.id ? null : row.id)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              padding: '4px 10px',
              borderRadius: 6,
              border: expandedCollision === row.id
                ? '1px solid rgba(251,191,36,0.55)'
                : '1px solid rgba(239, 68, 68, 0.45)',
              background: expandedCollision === row.id
                ? 'rgba(251,191,36,0.12)'
                : 'rgba(239, 68, 68, 0.10)',
              color: expandedCollision === row.id ? '#fbbf24' : 'var(--color-error)',
              fontSize: 11,
              fontWeight: 700,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              transition: 'all 0.2s',
            }}
          >
            {expandedCollision === row.id ? <><X size={11} /> Close</> : <><ChevronRight size={11} /> Fix</>}
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 6 }}>
            {hasDetails && (
              <button
                type="button"
                onClick={() => setExpandedMeasure(!expandedMeasure)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 5,
                  padding: '4px 10px',
                  borderRadius: 6,
                  border: expandedMeasure
                    ? '1px solid rgba(56, 189, 248, 0.55)'
                    : '1px solid rgba(56, 189, 248, 0.45)',
                  background: expandedMeasure
                    ? 'rgba(56, 189, 248, 0.15)'
                    : 'rgba(56, 189, 248, 0.05)',
                  color: '#7dd3fc',
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  transition: 'all 0.2s',
                }}
              >
                {expandedMeasure ? (isMeasure ? 'Hide SQL' : 'Hide Details') : (isMeasure ? 'View SQL' : 'View Details')}
              </button>
            )}
            <button
              type="button"
              onClick={() => onEdit?.(row.id)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                padding: '4px 10px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-secondary)',
                fontSize: 11,
                fontWeight: 600,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
                transition: 'all 0.2s',
              }}
            >
              <Edit2 size={11} />
              {row.target_field ? 'Edit' : 'Map'}
            </button>
          </div>
        )}
      </div>
    </div>
    {/* ── Inline collision fix panel ── */}
    {isCollision && expandedCollision === row.id && (
      <div style={{ padding: '0 14px 10px' }}>
        <CollisionPanel
          row={row}
          allRows={allRows}
          pendingRename={pendingRenames[row.id] ?? ''}
          onRenameChange={val => setPendingRenames(prev => ({ ...prev, [row.id]: val }))}
          onApply={() => onApplyRename(row.id)}
          applying={applyingRename === row.id}
        />
      </div>
    )}
    {/* ── Inline measure translation panel ── */}
    {hasDetails && expandedMeasure && (
      <div style={{ padding: '0 14px 10px' }}>
        <MeasureTranslationPanel row={row} />
      </div>
    )}
    </div>
  );
}

// ─── Measure translation detail panel ─────────────────────────────────────────
const SYNONYM_SOURCE_LABELS = {
  manual_override: 'Manual Override',
  tmsl_authored: 'TMSL-Authored',
  auto_generated: 'Auto-Generated',
  report_alias: 'Report Alias',
};

function synonymSourceLabel(source) {
  if (SYNONYM_SOURCE_LABELS[source]) return SYNONYM_SOURCE_LABELS[source];
  const word = String(source || '').replace(/[_-]+/g, ' ').trim();
  return word ? word.replace(/\b\w/g, (c) => c.toUpperCase()) : 'Unknown Source';
}

function MeasureTranslationPanel({ row }) {
  const isFailed = row.sync_enabled === false || !!row.sync_failure_reason;
  const isMeasure = String(row.field_type || row.entity_kind || '').toLowerCase() === 'measure';
  const [showAllSynonymSources, setShowAllSynonymSources] = useState(false);

  // A synonym only shows under "Report Aliases" when its source is confirmed
  // as a genuine PBIX report-layer visual alias. Anything else (manual
  // override, TMSL-authored, mechanically auto-generated) is a coincidental
  // lookalike at best and must not be visually indistinguishable from a real
  // report alias — it's hidden behind an explicit toggle instead, sub-labeled
  // by its actual source. Driven entirely by row.synonym_sources; no
  // hardcoded field or source names.
  const allSynonyms = Array.isArray(row.synonyms) ? row.synonyms : [];
  const synonymSources = row.synonym_sources || {};
  const reportAliases = allSynonyms.filter((s) => synonymSources[s] === 'report_alias');
  const otherSynonyms = allSynonyms.filter((s) => synonymSources[s] !== 'report_alias');

  return (
    <div style={{
      gridColumn: '1 / -1',
      margin: '0 0 4px',
      padding: '14px 16px',
      borderRadius: 8,
      background: 'rgba(56, 189, 248, 0.03)',
      border: '1px solid rgba(56, 189, 248, 0.15)',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      {/* ── Status Banner (Measures only) ── */}
      {isMeasure && (
        isFailed ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 12px',
            borderRadius: 6,
            background: 'rgba(239, 68, 68, 0.08)',
            border: '1px solid rgba(239, 68, 68, 0.25)',
            color: 'var(--color-error)',
            fontSize: 11,
            fontWeight: 600,
          }}>
            <AlertTriangle size={13} style={{ flexShrink: 0 }} />
            <span>Translation Warning: {row.sync_failure_reason || 'This measure is too complex to translate to SQL automatically.'}</span>
          </div>
        ) : (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '8px 12px',
            borderRadius: 6,
            background: 'rgba(34, 197, 94, 0.08)',
            border: '1px solid rgba(34, 197, 94, 0.25)',
            color: 'var(--color-success)',
            fontSize: 11,
            fontWeight: 600,
          }}>
            <CheckCircle size={13} style={{ flexShrink: 0 }} />
            <span>Translated successfully to Snowflake SQL</span>
          </div>
        )
      )}

      {/* ── Report Aliases (Columns and Measures if present) — genuine
          report-layer matches only; anything else is behind the toggle below ── */}
      {reportAliases.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Report Aliases</span>
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 4,
            padding: '10px 12px',
            borderRadius: 6,
            background: 'var(--bg-main)',
            border: '1px solid var(--border-main)',
            color: 'var(--text-secondary)',
            fontSize: 12,
          }}>
            {reportAliases.map((alias, index) => (
              <div key={`${row.id}-${alias}-${index}`} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ color: 'var(--text-tertiary)' }}>•</span>
                <span>{alias}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Other synonym sources — hidden by default. This page has no
          role gating (any authenticated user can reach it), so a
          manual-override/TMSL-authored/auto-generated synonym must never be
          shown next to genuine report aliases without an explicit,
          deliberate action to reveal it. ── */}
      {otherSynonyms.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <button
            type="button"
            onClick={() => setShowAllSynonymSources((prev) => !prev)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
              alignSelf: 'flex-start',
              padding: '3px 8px',
              borderRadius: 5,
              border: '1px solid var(--border-main)',
              background: 'transparent',
              color: 'var(--text-tertiary)',
              fontSize: 10,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            {showAllSynonymSources
              ? 'Hide other synonym sources'
              : `Show all synonym sources (${otherSynonyms.length})`}
          </button>
          {showAllSynonymSources && (
            <div style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
              padding: '10px 12px',
              borderRadius: 6,
              background: 'var(--bg-main)',
              border: '1px solid var(--border-main)',
              color: 'var(--text-secondary)',
              fontSize: 12,
            }}>
              {otherSynonyms.map((alias, index) => (
                <div key={`${row.id}-other-${alias}-${index}`} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ color: 'var(--text-tertiary)' }}>•</span>
                  <span>{alias}</span>
                  <span style={{
                    fontSize: 9,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.03em',
                    color: 'var(--text-tertiary)',
                    border: '1px solid var(--border-main)',
                    borderRadius: 4,
                    padding: '1px 5px',
                    marginLeft: 'auto',
                  }}>
                    {synonymSourceLabel(synonymSources[alias])}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Side-by-Side Code Blocks (Measures only) ── */}
      {isMeasure && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: '12px',
          marginTop: (reportAliases.length > 0 || otherSynonyms.length > 0) ? '6px' : '0',
        }}>
          {/* Source DAX */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Source Expression (DAX)</span>
            <div style={{
              fontFamily: 'monospace',
              fontSize: 11,
              padding: '10px 12px',
              borderRadius: 6,
              background: 'var(--bg-main)',
              border: '1px solid var(--border-main)',
              color: '#a5f3fc',
              minHeight: '48px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}>
              {row.measure_expression || '—'}
            </div>
          </div>

          {/* Target SQL */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Snowflake Translation (SQL)</span>
            <div style={{
              fontFamily: 'monospace',
              fontSize: 11,
              padding: '10px 12px',
              borderRadius: 6,
              background: 'var(--bg-main)',
              border: '1px solid var(--border-main)',
              color: isFailed ? 'var(--text-tertiary)' : '#86efac',
              minHeight: '48px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}>
              {isFailed ? '— Translation unavailable —' : (row.target_expression || '—')}
            </div>
          </div>
        </div>
      )}

      {/* ── Dependencies (Measures only) ── */}
      {isMeasure && Array.isArray(row.depends_on_measures) && row.depends_on_measures.length > 0 && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 11,
          color: 'var(--text-secondary)',
        }}>
          <GitMerge size={12} style={{ color: 'var(--text-tertiary)' }} />
          <span style={{ fontWeight: 600 }}>Depends on measures:</span>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {row.depends_on_measures.map(dep => (
              <span key={dep} style={{
                fontFamily: 'monospace',
                fontSize: 10,
                padding: '2px 6px',
                borderRadius: 4,
                background: 'rgba(255,255,255,0.05)',
                border: '1px solid var(--border-main)',
                color: 'var(--text-primary)',
              }}>
                {dep}
              </span>
            ))}
          </div>
        </div>
      )}
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

// ─── Collision resolution panel ───────────────────────────────────────────────
function CollisionPanel({ row, allRows, pendingRename, onRenameChange, onApply, applying }) {
  const { suggestions, peers } = suggestCollisionResolutions(row, allRows);
  const peerLabel = peers.length > 0
    ? peers.map(p => `${p.source_table_name ? p.source_table_name + '.' : ''}${p.source_field}`).join(', ')
    : 'another field';

  const inputValid = pendingRename.trim().length > 0 &&
    /^[A-Za-z_][A-Za-z0-9_]*$/.test(pendingRename.trim());

  return (
    <div style={{
      gridColumn: '1 / -1',
      margin: '0 0 4px',
      padding: '14px 16px',
      borderRadius: 8,
      background: 'rgba(251,191,36,0.06)',
      border: '1px solid rgba(251,191,36,0.30)',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      {/* ── Why is this a collision? ── */}
      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        <span style={{ fontWeight: 700, color: '#fbbf24' }}>Naming conflict</span>
        {' '}— <code style={{ fontSize: 11, background: 'rgba(0,0,0,0.25)', padding: '1px 5px', borderRadius: 3 }}>{row.target_field || row.source_field}</code>
        {' '}is also claimed by{' '}
        <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{peerLabel}</span>.
        {' '}Choose a unique target name for this field:
      </div>

      {/* ── Suggestion chips ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>Suggestions:</span>
        {suggestions.map((s) => (
          <button
            key={s.id}
            type="button"
            title={s.description}
            onClick={() => onRenameChange(s.label)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              padding: '4px 10px',
              borderRadius: 6,
              border: pendingRename === s.label
                ? '1px solid #fbbf24'
                : '1px solid rgba(251,191,36,0.35)',
              background: pendingRename === s.label
                ? 'rgba(251,191,36,0.20)'
                : 'rgba(251,191,36,0.07)',
              color: pendingRename === s.label ? '#fbbf24' : 'var(--text-secondary)',
              fontSize: 11,
              fontWeight: 700,
              fontFamily: 'monospace',
              cursor: 'pointer',
              transition: 'all 0.15s',
            }}
          >
            {s.id === 'table_prefix' && <ChevronRight size={10} />}
            {s.label}
          </button>
        ))}
      </div>

      {/* ── Manual input + Apply ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <input
          type="text"
          value={pendingRename}
          onChange={e => onRenameChange(sanitizeMappingName(e.target.value) || e.target.value.toUpperCase())}
          placeholder="Or type a custom name…"
          style={{
            flex: 1,
            padding: '6px 10px',
            borderRadius: 6,
            border: pendingRename && !inputValid
              ? '1px solid rgba(239,68,68,0.6)'
              : '1px solid var(--border-main)',
            background: 'var(--bg-main)',
            color: 'var(--text-primary)',
            fontSize: 12,
            fontFamily: 'monospace',
            outline: 'none',
          }}
        />
        <button
          type="button"
          disabled={!inputValid || applying}
          onClick={onApply}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '6px 14px',
            borderRadius: 6,
            border: '1px solid rgba(34,197,94,0.45)',
            background: inputValid && !applying ? 'rgba(34,197,94,0.12)' : 'rgba(34,197,94,0.04)',
            color: inputValid && !applying ? 'var(--color-success)' : 'var(--text-tertiary)',
            fontSize: 12,
            fontWeight: 700,
            cursor: inputValid && !applying ? 'pointer' : 'not-allowed',
            whiteSpace: 'nowrap',
            transition: 'all 0.15s',
          }}
        >
          {applying
            ? <span style={{ width: 11, height: 11, border: '2px solid currentColor', borderTopColor: 'transparent', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
            : <CheckCircle size={12} />}
          {applying ? 'Applying…' : 'Apply rename'}
        </button>
      </div>

      {pendingRename && !inputValid && (
        <div style={{ fontSize: 11, color: 'var(--color-error)' }}>
          Use only letters, numbers, and underscores. Must start with a letter or underscore.
        </div>
      )}
    </div>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function DryRunMappingTable({
  mappings = [],
  onEdit,
  onSynonymUpdate,
  projectId = 'preview',
  modelName = '',
  onBulkResolved,     // (resolvedMap: Record<rowId, suggestedTarget>) => void
  summary,
  relationships = [],
  schemaConflicts = [],       // DIMENSION_MISSING conflicts from backend
  compatibilityScore = null,  // 0–100 float from backend
  onReSync,                   // () => void — trigger a re-sync after auto-add
  droppedEntities = [],       // DropRecord[] — entities excluded from deployed DDL (see DropLedger)
}) {
  // ── Tier 1 (field type, single-select) / Tier 2 (status, multi-select) ──────
  const [activeFieldType, setActiveFieldType] = useState('all');
  const [activeStatuses, setActiveStatuses] = useState(() => new Set());
  const [needsReviewOnly, setNeedsReviewOnly] = useState(false);
  const [search, setSearch] = useState('');
  const [useRegex, setUseRegex] = useState(false);
  const [bulkResolving, setBulkResolving] = useState(false);
  const [bulkToast, setBulkToast] = useState(null); // { type: 'success'|'error', msg }
  const [synonymModal, setSynonymModal] = useState(null);
  const [schemaFixing, setSchemaFixing] = useState({}); // { conflictId: 'pending'|'done'|'error' }
  const [schemaToast, setSchemaToast] = useState(null);
  // ── Per-collision inline fix panel ─────────────────────────────────────────
  const [expandedCollision, setExpandedCollision] = useState(null); // rowId
  const [pendingRenames, setPendingRenames]       = useState({});   // { rowId: newName }
  const [applyingRename, setApplyingRename]       = useState(null); // rowId

  // ── Tier 1 groups: whatever field-type values are actually in the data ─────
  const fieldGroups = useMemo(() => groupRowsByFieldType(mappings), [mappings]);
  // Kept for the two places that still want a raw measure/column split (badges, search haystack).
  const { columns, measures } = useMemo(() => {
    const cols = [];
    const meas = [];
    mappings.forEach((row) => {
      const kind = String(row?.field_type || row?.entity_kind || '').toLowerCase();
      if (kind === 'measure' || kind === 'metric') meas.push(row); else cols.push(row);
    });
    return { columns: cols, measures: meas };
  }, [mappings]);

  // ── Reset both filter tiers whenever a *new* dry run lands (not on row edits —
  //    edits only touch `mappings`/`detectedMappings`, never the `summary` object).
  //    Adjusted during render (React's documented pattern for "reset state when a
  //    prop changes") rather than in an effect, to avoid an extra render pass. ──
  const [lastSummary, setLastSummary] = useState(summary);
  if (summary !== lastSummary) {
    setLastSummary(summary);
    setActiveFieldType('all');
    setActiveStatuses(new Set());
    setNeedsReviewOnly(false);
  }

  // ── Rows in scope for Tier 2 facet computation (the selected Tier 1 group) ──
  const scopedRows = useMemo(() => {
    const group = fieldGroups.find((g) => g.key === activeFieldType);
    return group ? group.rows : mappings;
  }, [fieldGroups, activeFieldType, mappings]);

  // ── Tier 2 options: real statuses present in scope, plus conversion outcome
  //    facets (only if this scope actually has expressions to convert), plus
  //    the always-present schema-issues toggle (structurally not a row status) ──
  const statusFacets = useMemo(() => computeStatusFacets(scopedRows), [scopedRows]);
  const showConversionFacets = useMemo(() => hasConversionData(scopedRows), [scopedRows]);
  const showComplexityTierFacets = useMemo(() => hasComplexityTierData(scopedRows), [scopedRows]);
  const tier2Options = useMemo(() => {
    const options = statusFacets.map((f) => ({ ...f, kind: 'status' }));
    if (showConversionFacets) {
      Object.entries(CONVERSION_FACET_LABELS).forEach(([key, label]) => {
        const count = scopedRows.filter((r) => rowConversionOutcome(r) === key).length;
        if (count > 0) options.push({ key, label, count, kind: 'conversion' });
      });
    }
    if (showComplexityTierFacets) {
      Object.entries(COMPLEXITY_TIER_FACET_LABELS).forEach(([key, label]) => {
        const count = scopedRows.filter((r) => rowComplexityTier(r) === key).length;
        if (count > 0) options.push({ key, label, count, kind: 'complexity_tier' });
      });
    }
    if (schemaConflicts.length > 0) {
      options.push({ key: 'schema_issues', label: 'Schema Issues', count: schemaConflicts.length, kind: 'schema' });
    }
    return options;
  }, [statusFacets, showConversionFacets, showComplexityTierFacets, scopedRows, schemaConflicts.length]);

  const needsReviewCount = useMemo(() => scopedRows.filter(rowNeedsReview).length, [scopedRows]);

  const toggleStatus = useCallback((key) => {
    setActiveStatuses((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  // ── Derived counts (over all mappings + schema issues) ──────────────────────
  const counts = useMemo(() => {
    const c = { all: mappings.length, auto: 0, manual: 0, unmapped: 0, collision: 0, schema_issues: schemaConflicts.length };
    mappings.forEach((row) => {
      const s = String(row?.status || '').toLowerCase();
      if (c[s] !== undefined) c[s] += 1;
    });
    return c;
  }, [mappings, schemaConflicts]);

  // ── Auto-add handler ─────────────────────────────────────────────────────────
  const handleAutoAdd = useCallback(async (conflict) => {
    const cid = conflict.conflict_id;
    setSchemaFixing(prev => ({ ...prev, [cid]: 'pending' }));
    setSchemaToast(null);
    try {
      await api.addMissingDimensionColumn(
        projectId,
        conflict.model_name,
        conflict.column_name,
        'VARCHAR',
      );
      setSchemaFixing(prev => ({ ...prev, [cid]: 'done' }));
      setSchemaToast({ type: 'success', msg: `Column '${conflict.column_name}' added. Re-running sync…` });
      setTimeout(() => {
        setSchemaToast(null);
        onReSync?.();
      }, 1500);
    } catch (err) {
      setSchemaFixing(prev => ({ ...prev, [cid]: 'error' }));
      setSchemaToast({ type: 'error', msg: `Failed: ${err.message}` });
      setTimeout(() => setSchemaToast(null), 5000);
    }
  }, [projectId, onReSync]);

  // ── Per-collision apply rename ───────────────────────────────────────────────
  const applyRename = useCallback(async (rowId) => {
    const newName = (pendingRenames[rowId] || '').trim();
    if (!newName) return;
    setApplyingRename(rowId);
    try {
      await onEdit?.(rowId, {
        target_field: newName,
        target_name:  newName,
        status:       'manual',
        collision_detected: false,
      });
      setExpandedCollision(null);
      setPendingRenames(prev => { const n = { ...prev }; delete n[rowId]; return n; });
    } finally {
      setApplyingRename(null);
    }
  }, [pendingRenames, onEdit]);

  // ── Bulk resolve handler — uses table-prefix names, not hashes ──────────────
  const handleBulkResolve = useCallback(() => {
    const collisionRows = mappings.filter(
      r => String(r?.status || '').toLowerCase() === 'collision'
    );
    if (!collisionRows.length) return;

    setBulkResolving(true);
    setBulkToast(null);

    try {
      const resolvedMap = {};
      const assignedNames = new Set(
        mappings
          .filter(r => String(r?.status || '').toLowerCase() !== 'collision')
          .map(r => String(r?.target_field || r?.target_name || '').trim().toUpperCase())
          .filter(Boolean)
      );

      collisionRows.forEach(row => {
        const { suggestions } = suggestCollisionResolutions(row, mappings);
        let candidate = '';
        let suggestionIdx = 0;

        if (suggestions.length > 0) {
          candidate = suggestions[0].label;
          while (assignedNames.has(candidate.toUpperCase()) && suggestionIdx < suggestions.length - 1) {
            suggestionIdx += 1;
            candidate = suggestions[suggestionIdx].label;
          }
        } else {
          candidate = row.target_field || row.source_field || '';
        }

        if (assignedNames.has(candidate.toUpperCase())) {
          const base = candidate;
          const hash = shortDeterministicHash(buildSourceFingerprint({}, row)).slice(0, 6).toUpperCase();
          candidate = `${base}_${hash}`;
          
          let counter = 2;
          while (assignedNames.has(candidate.toUpperCase())) {
            candidate = `${base}_${hash}_${counter}`;
            counter += 1;
          }
        }

        resolvedMap[row.id] = candidate;
        assignedNames.add(candidate.toUpperCase());
      });

      onBulkResolved?.(resolvedMap);
      setBulkToast({
        type: 'success',
        msg: `${collisionRows.length} collision${collisionRows.length !== 1 ? 's' : ''} resolved — unique names applied. Review each field to confirm.`,
      });
    } catch (err) {
      setBulkToast({ type: 'error', msg: `Resolve failed: ${err.message}` });
    } finally {
      setBulkResolving(false);
      setTimeout(() => setBulkToast(null), 6000);
    }
  }, [mappings, onBulkResolved]);

  const openSynonymModal = useCallback(async (row) => {
    const tableName = row.source_table_name || row.measure_source_tables?.[0] || row.parent_table || '';
    const modalData = {
      rowId: row.id,
      projectId: row.project_id || projectId || 'preview',
      modelName: row.model_name || row.modelName || modelName || 'default',
      tableName,
      columnName: row.source_field,
      currentSynonyms: row.synonym_overrides || row.synonymOverrides || [],
    };
    setSynonymModal(modalData);
    try {
      const response = await api.getSynonymOverride(modalData);
      setSynonymModal((current) => current?.rowId === row.id
        ? { ...current, currentSynonyms: response?.synonyms || [] }
        : current);
    } catch {
      // Missing override table or row should not block the edit dialog.
    }
  }, [projectId, modelName]);

  const handleSynonymSave = useCallback((synonyms) => {
    if (!synonymModal?.rowId) return;
    onSynonymUpdate?.(synonymModal.rowId, synonyms);
  }, [synonymModal, onSynonymUpdate]);

  // ── Filtered rows ───────────────────────────────────────────────────────────
  // Tier 2 is OR-across-selections; an empty selection means "show all" for that tier.
  const matchesTier2 = useCallback((row) => {
    if (activeStatuses.size === 0) return true;
    const status = String(row?.status || '').toLowerCase().trim() || 'unknown';
    if (activeStatuses.has(status)) return true;
    const conversionKey = rowConversionOutcome(row);
    if (conversionKey && activeStatuses.has(conversionKey)) return true;
    const tierKey = rowComplexityTier(row);
    if (tierKey && activeStatuses.has(tierKey)) return true;
    // 'schema_issues' never matches a row — it has no per-row status; selecting it
    // alongside real statuses still shows those via OR, selecting it alone shows none
    // (the always-visible schema-conflicts panel below carries that information instead).
    return false;
  }, [activeStatuses]);

  const filterRow = useCallback((row) => {
    if (activeFieldType !== 'all' && normalizeFieldTypeKey(row) !== activeFieldType) return false;
    if (!matchesTier2(row)) return false;
    if (needsReviewOnly && !rowNeedsReview(row)) return false;
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
  }, [activeFieldType, matchesTier2, needsReviewOnly, search, useRegex]);

  // ── Visible field-type sections: the selected Tier 1 group, or all of them ──
  const visibleGroups = useMemo(() => {
    const groups = fieldGroups.filter((g) => g.key !== 'all'
      && (activeFieldType === 'all' || activeFieldType === g.key));
    return groups.map((g) => ({ ...g, filteredRows: g.rows.filter(filterRow) }));
  }, [fieldGroups, activeFieldType, filterRow]);

  // ── Summary bar values ──────────────────────────────────────────────────────
  const totalFields  = summary?.total_fields  ?? mappings.length;
  const autoMapped   = summary?.auto_mapped   ?? counts.auto;
  const unmappedCnt  = summary?.unmapped      ?? counts.unmapped;
  const collisionCnt = summary?.collisions    ?? counts.collision;
  const score = typeof compatibilityScore === 'number' ? compatibilityScore : null;
  const scoreColor = score === null ? 'var(--text-tertiary)'
    : score >= 90 ? 'var(--color-success)'
    : score >= 70 ? '#f59e0b'
    : '#ef4444';

  // Build a plain-English breakdown to show next to the score
  const totalIssues = counts.collision + counts.unmapped + schemaConflicts.length;
  const scoreLabel = score === null ? null
    : score === 100 ? 'All checks passed'
    : totalIssues === 0 ? (score >= 90 ? 'Minor issues detected' : 'Issues detected')
    : `${totalIssues} issue${totalIssues !== 1 ? 's' : ''} found — ${
        counts.collision > 0 ? `${counts.collision} collision${counts.collision !== 1 ? 's' : ''}` : ''
      }${counts.collision > 0 && (counts.unmapped > 0 || schemaConflicts.length > 0) ? ', ' : ''}${
        counts.unmapped > 0 ? `${counts.unmapped} unmapped` : ''
      }${counts.unmapped > 0 && schemaConflicts.length > 0 ? ', ' : ''}${
        schemaConflicts.length > 0 ? `${schemaConflicts.length} schema gap${schemaConflicts.length !== 1 ? 's' : ''}` : ''
      }`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── Compatibility score bar ─────────────────────────────────────────── */}
      {score !== null && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '10px 14px',
          borderRadius: 8,
          background: 'var(--bg-surface)',
          border: `1px solid ${scoreColor}40`,
        }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>
            Sync Compatibility
          </span>
          <div style={{ flex: 1, height: 8, borderRadius: 4, background: 'var(--bg-main)', overflow: 'hidden' }}>
            <div style={{
              height: '100%',
              width: `${score}%`,
              borderRadius: 4,
              background: scoreColor,
              transition: 'width 0.6s ease',
            }} />
          </div>
          <span style={{ fontSize: 13, fontWeight: 800, color: scoreColor, minWidth: 46, textAlign: 'right' }}>
            {score}%
          </span>
          {scoreLabel && (
            <span style={{ fontSize: 11, color: score === 100 ? 'var(--color-success)' : 'var(--text-tertiary)' }}>
              {scoreLabel}
            </span>
          )}
        </div>
      )}

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
        {schemaConflicts.length > 0 && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>·</span>
            <span style={{ color: '#f59e0b', fontWeight: 600 }}>
              {schemaConflicts.length} schema issue{schemaConflicts.length !== 1 ? 's' : ''}
            </span>
          </>
        )}
      </div>

      <DroppedFieldsPanel entries={droppedEntities} />

      {/* ── Controls row: two-tier filter + search + Resolve All ─────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {/* Tier 1 — field type (single-select), built from whatever field types are in the data */}
          <div className="filter-tabs" style={{ marginBottom: 0 }}>
            {fieldGroups.map((group) => {
              const active = activeFieldType === group.key;
              const count = group.rows.length;
              return (
                <button
                  key={group.key}
                  type="button"
                  className={`filter-tab${active ? ' active' : ''}`}
                  onClick={() => setActiveFieldType(group.key)}
                >
                  {group.label} ({count})
                </button>
              );
            })}
          </div>

          {/* Tier 2 — status (multi-select, OR), scoped to the selected Tier 1 group, plus
              a "Needs Review" quick toggle. Options here are whatever statuses/conversion
              outcomes are actually present — nothing is hardcoded to a known connector. */}
          <div className="filter-checks">
            <button
              type="button"
              className={`filter-check needs-review${needsReviewOnly ? ' active' : ''}`}
              onClick={() => setNeedsReviewOnly((prev) => !prev)}
            >
              Needs Review ({needsReviewCount})
            </button>
            {tier2Options.map((option) => {
              const checked = activeStatuses.has(option.key);
              return (
                <button
                  key={option.key}
                  type="button"
                  className={`filter-check${checked ? ' active' : ''}${option.key === 'collision' ? ' collision-check' : ''}`}
                  onClick={() => toggleStatus(option.key)}
                >
                  {option.label} ({option.count})
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
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

          <div style={{ width: 300 }}>
            <SmartSearchBar
              value={search}
              onChange={setSearch}
              useRegex={useRegex}
              onToggleRegex={setUseRegex}
              placeholder="Search fields..."
            />
          </div>
        </div>
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
          {/* One section per field-type group present in the data (Tier 1 = 'all' shows every
              group; a specific selection isolates just that one). 'measure' keeps its existing
              cyan "fx" branding; any other/unforeseen group key gets the neutral default look. */}
          {visibleGroups.map((group) => {
            const isMeasureGroup = group.key === 'measure';
            return (
              <div
                key={group.key}
                style={{
                  border: isMeasureGroup ? '1px solid rgba(56, 189, 248, 0.3)' : '1px solid var(--border-main)',
                  borderRadius: 10,
                  overflow: 'hidden',
                  background: 'var(--bg-surface)',
                  marginBottom: 16,
                }}
              >
                <div style={{
                  padding: '10px 14px',
                  background: isMeasureGroup ? 'rgba(56, 189, 248, 0.05)' : 'rgba(255, 255, 255, 0.03)',
                  borderBottom: isMeasureGroup ? '1px solid rgba(56, 189, 248, 0.2)' : '1px solid var(--border-main)',
                  fontSize: 11,
                  fontWeight: 800,
                  color: isMeasureGroup ? '#7dd3fc' : 'var(--text-primary)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}>
                  {isMeasureGroup
                    ? <div style={{ fontSize: 14, color: '#7dd3fc' }}>fx</div>
                    : <div style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--accent-blue)' }} />}
                  {group.label} ({group.filteredRows.length})
                </div>
                <TableHeader />
                {group.filteredRows.length === 0 ? (
                  <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
                    No {group.label.toLowerCase()} match the current filter or search.
                  </div>
                ) : (
                  group.filteredRows.map(row => (
                    <MappingRow
                      key={row.id}
                      row={row}
                      onEdit={onEdit}
                      onSynonymEdit={openSynonymModal}
                      allRows={mappings}
                      expandedCollision={expandedCollision}
                      setExpandedCollision={setExpandedCollision}
                      pendingRenames={pendingRenames}
                      setPendingRenames={setPendingRenames}
                      applyingRename={applyingRename}
                      onApplyRename={applyRename}
                    />
                  ))
                )}
              </div>
            );
          })}
        </>
      )}

      {/* ── Schema Issues panel (DIMENSION_MISSING conflicts) ───────────────── */}
      {activeStatuses.has('schema_issues') && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {schemaToast && (
            <div style={{
              padding: '8px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600,
              background: schemaToast.type === 'success' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
              border: `1px solid ${schemaToast.type === 'success' ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)'}`,
              color: schemaToast.type === 'success' ? '#4ade80' : '#f87171',
            }}>
              {schemaToast.msg}
            </div>
          )}
          {schemaConflicts.length === 0 ? (
            <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
              ✓ No schema issues detected
            </div>
          ) : schemaConflicts.map((conflict) => {
            const fixState = schemaFixing[conflict.conflict_id];
            const isDone = fixState === 'done';
            const isPending = fixState === 'pending';
            return (
              <div key={conflict.conflict_id} style={{
                padding: '14px 16px',
                borderRadius: 8,
                background: 'var(--bg-surface)',
                border: `1px solid ${isDone ? 'rgba(34,197,94,0.4)' : 'rgba(245,158,11,0.35)'}`,
                opacity: isDone ? 0.6 : 1,
                transition: 'all 0.2s',
              }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                  <AlertTriangle size={15} style={{ color: '#f59e0b', flexShrink: 0, marginTop: 2 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <span style={{ fontFamily: 'monospace', fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
                        {conflict.column_name}
                      </span>
                      <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 4, background: 'rgba(245,158,11,0.15)', color: '#f59e0b', fontWeight: 700, border: '1px solid rgba(245,158,11,0.3)' }}>
                        DIMENSION MISSING
                      </span>
                      {conflict.model_name && (
                        <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                          in <span style={{ fontFamily: 'monospace' }}>{conflict.model_name}</span>
                        </span>
                      )}
                    </div>
                    <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                      {conflict.description}
                    </p>
                    {/* Resolution options */}
                    <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                      {/* Option A: Skip */}
                      <div style={{
                        padding: '8px 12px',
                        borderRadius: 6,
                        border: '1px solid var(--border-main)',
                        background: 'var(--bg-main)',
                        flex: '1 1 200px',
                        minWidth: 180,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <SkipForward size={12} style={{ color: 'var(--text-tertiary)' }} />
                          <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)' }}>
                            Option A — Skip
                          </span>
                        </div>
                        <p style={{ margin: 0, fontSize: 11, color: 'var(--text-tertiary)' }}>
                          Exclude from Snowflake DIMENSIONS. Column absent from semantic queries.
                        </p>
                        <div style={{ marginTop: 6, fontSize: 11, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
                          Default behaviour (no action needed)
                        </div>
                      </div>
                      {/* Option B: Auto-add */}
                      <div style={{
                        padding: '8px 12px',
                        borderRadius: 6,
                        border: isDone ? '1px solid rgba(34,197,94,0.45)' : '1px solid rgba(56,189,248,0.35)',
                        background: isDone ? 'rgba(34,197,94,0.08)' : 'rgba(56,189,248,0.08)',
                        flex: '1 1 200px',
                        minWidth: 180,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                          <PlusCircle size={12} style={{ color: isDone ? '#4ade80' : '#7dd3fc' }} />
                          <span style={{ fontSize: 11, fontWeight: 700, color: isDone ? '#4ade80' : '#7dd3fc' }}>
                            Option B — Auto-add to Snowflake
                          </span>
                        </div>
                        <p style={{ margin: 0, fontSize: 11, color: 'var(--text-secondary)' }}>
                          Run <code style={{ fontSize: 10 }}>ALTER TABLE ADD COLUMN</code> on the physical table, then re-sync to include in DIMENSIONS.
                        </p>
                        <button
                          type="button"
                          disabled={isPending || isDone}
                          onClick={() => handleAutoAdd(conflict)}
                          style={{
                            marginTop: 8,
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 5,
                            padding: '5px 12px',
                            borderRadius: 5,
                            border: 'none',
                            background: isDone ? 'rgba(34,197,94,0.2)' : isPending ? 'rgba(56,189,248,0.1)' : 'rgba(56,189,248,0.2)',
                            color: isDone ? '#4ade80' : '#7dd3fc',
                            fontSize: 11,
                            fontWeight: 700,
                            cursor: (isPending || isDone) ? 'not-allowed' : 'pointer',
                            opacity: (isPending || isDone) ? 0.8 : 1,
                            transition: 'all 0.15s',
                          }}
                        >
                          {isPending ? (
                            <>
                              <span style={{ width: 10, height: 10, border: '2px solid #7dd3fc', borderTopColor: 'transparent', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
                              Adding…
                            </>
                          ) : isDone ? (
                            <>✓ Added — re-syncing</>
                          ) : (
                            <><PlusCircle size={11} /> Add Column &amp; Re-sync</>
                          )}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Relationships section ─────────────────────────────────────────────── */}
      {!activeStatuses.has('schema_issues') && (
        <RelationshipsSection relationships={relationships} />
      )}
      <SynonymEditModal
        open={Boolean(synonymModal)}
        onClose={() => setSynonymModal(null)}
        projectId={synonymModal?.projectId || projectId}
        modelName={synonymModal?.modelName || modelName || 'default'}
        tableName={synonymModal?.tableName || ''}
        columnName={synonymModal?.columnName || ''}
        currentSynonyms={synonymModal?.currentSynonyms || []}
        onSave={handleSynonymSave}
      />
    </div>
  );
}
