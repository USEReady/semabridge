import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import StatusBadge from './common/StatusBadge';

/**
 * Clean, business-facing view of a run's report (JSON from
 * api.getRunReportSummary / api.getRunModelReportSummary), shown as the
 * default "Summary" tab in ProjectJobsPage's "View Report Preview" modal
 * (the raw Markdown is a "Raw Report" tab away for anyone who wants the
 * full downloadable-style text instead). Collapsed: one row per category
 * with just a name and a count -- the "clean summary" a business user
 * wants at a glance. Expanded: the full per-item detail (advisory
 * messages, drop reasons) for that category only.
 *
 * Ported from demo_version_ref verbatim -- this component is fully
 * data-driven off the `data` shape build_run_report_data() returns, with
 * no assumptions specific to a single-file vs. multi-file run baked in
 * (the caller decides whether `data` describes the whole run or one
 * model's own scoped slice of it). `needs_review` currently always
 * arrives empty (DAX-feature's _classify_metrics() doesn't yet implement
 * demo_version's third classification tier -- see the
 * followup_needs_review_classification memory) but the section is kept
 * here, not removed, so no further UI change is needed once that lands.
 */

const SECTION_META = {
  standard: {
    label: 'Standard Conversion',
    status: 'success',
    blurb: 'Converted automatically using built-in conversion rules. No AI involved.',
  },
  ai_assisted: {
    label: 'AI-Assisted Conversion',
    status: 'running',
    blurb: 'Too complex for standard rules, so AI interpreted these instead. We recommend verifying these numbers.',
  },
  needs_review: {
    label: 'Needs Review',
    status: 'warning',
    blurb: 'These converted, but need a quick human check before you rely on the numbers.',
  },
  dropped: {
    label: 'Not Included',
    status: 'error',
    blurb: "These couldn't be included in the converted model.",
  },
  excluded_by_design: {
    label: 'No Action Needed',
    status: 'draft',
    blurb: "These weren't included on purpose — they're not part of your real business data, so there's nothing to review here.",
  },
};

function AccordionSection({ sectionKey, count, children, defaultOpen }) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  const meta = SECTION_META[sectionKey];
  if (count === 0) return null;

  return (
    <div
      style={{
        border: '1px solid var(--border-main)',
        borderRadius: 8,
        marginBottom: 10,
        overflow: 'hidden',
        background: 'var(--bg-surface)',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <span style={{ fontWeight: 600, fontSize: 14, color: 'var(--text-primary)' }}>{meta.label}</span>
        </div>
        <StatusBadge status={meta.status} label={String(count)} size="sm" />
      </button>
      {open && (
        <div style={{ padding: '0 16px 16px 16px', borderTop: '1px solid var(--border-subtle)' }}>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '12px 0' }}>{meta.blurb}</p>
          {children}
        </div>
      )}
    </div>
  );
}

function MetricList({ entries, renderDetail }) {
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {entries.map((entry, index) => (
        <li
          key={`${entry.name}-${index}`}
          style={{
            padding: '10px 12px',
            borderRadius: 6,
            background: 'var(--bg-surface-raised)',
            fontSize: 13,
          }}
        >
          <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{entry.name}</div>
          {renderDetail(entry)}
        </li>
      ))}
    </ul>
  );
}

function AdvisoryDetail(entry) {
  if (!entry.advisory_msgs || entry.advisory_msgs.length === 0) return null;
  return (
    <div style={{ marginTop: 4, color: 'var(--text-secondary)', fontStyle: 'italic' }}>
      {entry.advisory_msgs.join(' ')}
    </div>
  );
}

function AiAssistedDetail(entry) {
  const bits = [];
  if (typeof entry.confidence === 'number') {
    bits.push(`AI's self-reported confidence: ${Math.round(entry.confidence * 100)}%`);
  }
  if (entry.risky) {
    bits.push('Matches a pattern that has caused problems before — review closely.');
  }
  if (entry.advisory_msgs?.length) bits.push(...entry.advisory_msgs);
  if (!bits.length) return null;
  return (
    <div style={{ marginTop: 4, color: 'var(--text-secondary)', fontStyle: 'italic' }}>
      {bits.join(' ')}
    </div>
  );
}

function DroppedSection({ groups }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {groups.map((group) => (
        <div key={group.stage}>
          <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 6 }}>
            {group.label} ({group.items.length})
          </div>
          <MetricList
            entries={group.items}
            renderDetail={(entry) => (
              <div style={{ marginTop: 4, color: 'var(--text-secondary)' }}>{entry.reason}</div>
            )}
          />
        </div>
      ))}
    </div>
  );
}

export default function RunReportSummary({ data }) {
  if (!data) return null;

  if (data.crashed) {
    // `unavailable` means the run itself completed fine -- its detailed
    // report data just isn't available in this session anymore (e.g. after
    // a server restart), which reads very differently from a genuine
    // pre-extraction crash. See run_report_service.py's
    // _gather_run_report_data() for why these can't be told apart from
    // run.results alone and need this explicit signal.
    return (
      <div style={{ padding: 16 }}>
        <p style={{ color: 'var(--text-primary)', marginBottom: 8 }}>
          {data.unavailable
            ? 'This run completed, but its detailed report data is no longer available.'
            : 'This run did not reach the point of reading your source file.'}
        </p>
        <p style={{ color: data.unavailable ? 'var(--text-secondary)' : 'var(--color-error)' }}>{data.error}</p>
      </div>
    );
  }

  const { counts, sections } = data;

  return (
    <div style={{ padding: '4px 2px' }}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 16,
          padding: '12px 16px',
          marginBottom: 16,
          borderRadius: 8,
          background: 'var(--bg-surface-raised)',
          fontSize: 12,
          color: 'var(--text-secondary)',
        }}
      >
        <span><strong style={{ color: 'var(--text-primary)' }}>{counts.tables}</strong> tables</span>
        <span><strong style={{ color: 'var(--text-primary)' }}>{counts.columns}</strong> columns</span>
        <span><strong style={{ color: 'var(--text-primary)' }}>{counts.calculations}</strong> calculations found</span>
        {counts.relationships > 0 && (
          <span><strong style={{ color: 'var(--text-primary)' }}>{counts.relationships}</strong> relationships</span>
        )}
        <span style={{ marginLeft: 'auto', fontWeight: 600, color: 'var(--text-primary)' }}>
          {counts.converted_total} of {counts.calculations} calculations converted
        </span>
      </div>

      <AccordionSection sectionKey="standard" count={counts.standard}>
        <MetricList entries={sections.standard} renderDetail={AdvisoryDetail} />
      </AccordionSection>

      <AccordionSection sectionKey="ai_assisted" count={counts.ai_assisted}>
        <MetricList entries={sections.ai_assisted} renderDetail={AiAssistedDetail} />
      </AccordionSection>

      <AccordionSection sectionKey="needs_review" count={counts.needs_review}>
        <MetricList entries={sections.needs_review} renderDetail={AdvisoryDetail} />
      </AccordionSection>

      <AccordionSection sectionKey="dropped" count={counts.dropped}>
        <DroppedSection groups={sections.dropped} />
      </AccordionSection>

      <AccordionSection sectionKey="excluded_by_design" count={counts.excluded_by_design}>
        <DroppedSection groups={sections.excluded_by_design} />
      </AccordionSection>

      {counts.converted_total === 0 && counts.dropped === 0 && counts.excluded_by_design === 0 && (
        <p style={{ color: 'var(--text-secondary)', fontSize: 13 }}>Nothing to report for this run.</p>
      )}
    </div>
  );
}
