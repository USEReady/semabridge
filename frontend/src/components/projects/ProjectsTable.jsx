/**
 * Table view of the Projects page: one row per project with the same numbers
 * as the summary band, so projects can be compared row by row.
 *
 * Columns: project (route, models) · last run · converted (bar + %) · AI ·
 * to review · ontology score · status. Expanding a row shows that project's
 * ontology at a glance (domains, metrics, duplicates, conflicts, glossary).
 */
import React, { useContext, useState } from 'react';
import { ChevronDown, ChevronRight, Play, Loader2 } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';
import { SyncContext } from '../../context/SyncContext';
import { ConversionBar, OntologyScore } from './projectHealth';

const th = {
  textAlign: 'left', fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase',
  color: 'var(--text-tertiary)', padding: '8px 10px', borderBottom: '1px solid var(--border-main)',
  whiteSpace: 'nowrap', position: 'sticky', top: 0, background: 'var(--bg-surface)', zIndex: 1,
};
const td = { padding: '9px 10px', borderBottom: '1px solid var(--border-light)', verticalAlign: 'middle', fontSize: 12 };
const num = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' };
const faint = { color: 'var(--text-tertiary)' };

function relTime(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? 'Yesterday' : `${days}d ago`;
}

function routeOf(project) {
  const src = project.source_type || project.source || '';
  const tgt = project.target_type || '';
  if (!src && !tgt) return '—';
  return `${src || '?'} → ${tgt || '?'}`;
}

function ontologyLine(h) {
  if (!h || h.score === null || h.score === undefined) return { main: 'Not built yet', sub: null };
  const n = (h.domains || []).length;
  const main = `${n} domain${n === 1 ? '' : 's'}`;
  let sub = 'clean';
  if (h.conflicts) sub = `${h.conflicts} conflict${h.conflicts === 1 ? '' : 's'}`;
  else if (h.duplicate_groups) sub = `${h.duplicate_groups} duplicate${h.duplicate_groups === 1 ? '' : 's'}`;
  else if (h.glossary_coverage_pct < 85) sub = `${Math.round(h.glossary_coverage_pct)}% described`;
  return { main, sub };
}

function OntologyStrip({ health, onOpen }) {
  if (!health || health.score === null || health.score === undefined) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, color: 'var(--text-secondary)' }}>
        <span>This project has no ontology yet. Build one to catch duplicate metrics and describe them for business users.</span>
        <span style={{ flex: 1 }} />
        <button type="button" onClick={onOpen} style={smallBtn}>Open ontology →</button>
      </div>
    );
  }
  const cells = [
    ['Domains', (health.domains || []).map(d => d.domain).join(', ') || '—',
      (health.domains || []).map(d => d.owner).filter(Boolean)[0] ? `owner: ${(health.domains || []).map(d => d.owner).filter(Boolean)[0]}` : null],
    ['Metrics', `${health.metric_count}`, health.stale ? `${health.stale} no longer in the model` : null],
    ['Duplicates', `${health.duplicate_groups} group${health.duplicate_groups === 1 ? '' : 's'}`, health.duplicate_examples?.[0] || null, health.duplicate_groups ? 'var(--color-warning)' : null],
    ['Conflicts', `${health.conflicts}`, health.conflict_examples?.length ? `${health.conflict_examples.join(', ')}` : null, health.conflicts ? 'var(--color-error)' : null],
    ['Glossary', `${Math.round(health.glossary_coverage_pct)}% described`, `${health.metric_count - health.described} need text`],
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr)) auto', gap: 14, alignItems: 'center' }}>
      {cells.map(([k, v, sub, color]) => (
        <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-tertiary)' }}>{k}</span>
          <b style={{ fontSize: 13, color: color || 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>{v}</b>
          {sub && <span style={{ ...faint, fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={sub}>{sub}</span>}
        </div>
      ))}
      <button type="button" onClick={onOpen} style={smallBtn}>Open ontology →</button>
    </div>
  );
}

const smallBtn = {
  border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)',
  borderRadius: 6, padding: '3px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
};

function Row({ project, summary, health, expanded, onToggleExpand, selected, onToggleSelect, onOpen, onOpenOntology, onRunNow, isRunning }) {
  const { activeRuns } = useContext(SyncContext);
  const myRun = activeRuns.find(r => String(r.projectId) === String(project.id) || String(r.project_id) === String(project.id));
  const status = myRun ? myRun.status : (project.status || 'draft');
  const running = isRunning || status === 'Running';
  const counts = summary?.counts;
  const onto = ontologyLine(health);
  const lastRun = relTime(summary?.last_run_at);

  return (
    <>
      <tr
        onClick={onOpen}
        style={{ cursor: 'pointer', background: expanded ? 'var(--bg-surface-raised)' : undefined }}
        onMouseEnter={e => { if (!expanded) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
        onMouseLeave={e => { e.currentTarget.style.background = expanded ? 'var(--bg-surface-raised)' : ''; }}
      >
        <td style={{ ...td, width: 28 }} onClick={e => e.stopPropagation()}>
          <input type="checkbox" aria-label={`Select ${project.name}`} checked={selected} onChange={() => onToggleSelect(project.id)} />
        </td>
        <td style={td}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <button
              type="button"
              aria-label={expanded ? 'Hide ontology' : 'Show ontology'}
              aria-expanded={expanded}
              onClick={e => { e.stopPropagation(); onToggleExpand(); }}
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--text-tertiary)', display: 'flex' }}
            >
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
            <div style={{ minWidth: 0 }}>
              <b style={{ color: 'var(--text-primary)', fontSize: 12.5 }}>{project.name}</b>
              <div style={{ ...faint, fontSize: 11, textTransform: 'capitalize' }}>
                {routeOf(project)}{summary?.models ? ` · ${summary.models} model${summary.models === 1 ? '' : 's'}` : ''}
              </div>
            </div>
          </div>
        </td>
        <td style={{ ...td, whiteSpace: 'nowrap' }}>
          {lastRun || <span style={faint}>Never</span>}
          {summary?.last_run_status === 'failed' && <div style={{ fontSize: 11, color: 'var(--color-error)' }}>failed</div>}
        </td>
        <td style={{ ...td, width: 190 }}>
          {counts ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1, minWidth: 70 }}><ConversionBar counts={counts} /></div>
              <span style={{ fontVariantNumeric: 'tabular-nums', minWidth: 34, textAlign: 'right' }}>
                {summary.conversion_pct !== null ? `${Math.round(summary.conversion_pct)}%` : '—'}
              </span>
            </div>
          ) : <span style={faint}>—</span>}
        </td>
        <td style={num}>{counts ? counts.ai_assisted.toLocaleString() : <span style={faint}>—</span>}</td>
        <td style={{ ...num, color: counts?.needs_review ? 'var(--color-warning)' : undefined }}>
          {counts ? counts.needs_review.toLocaleString() : <span style={faint}>—</span>}
        </td>
        <td style={td}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, whiteSpace: 'nowrap' }}>
            <OntologyScore health={health} />
            <span style={{ display: 'flex', flexDirection: 'column', lineHeight: 1.25 }}>
              <b style={{ fontSize: 11.5, color: health?.score != null ? 'var(--text-primary)' : 'var(--text-tertiary)', fontWeight: health?.score != null ? 600 : 400 }}>{onto.main}</b>
              {onto.sub && <span style={{ ...faint, fontSize: 11 }}>{onto.sub}</span>}
            </span>
          </div>
        </td>
        <td style={td}><StatusBadge status={status || 'draft'} size="sm" /></td>
        <td style={{ ...td, width: 40 }} onClick={e => e.stopPropagation()}>
          <button
            type="button"
            title={running ? 'Running…' : 'Run now'}
            disabled={running}
            onClick={onRunNow}
            style={{ ...smallBtn, display: 'inline-flex', alignItems: 'center', gap: 4, opacity: running ? 0.6 : 1 }}
          >
            {running ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} fill="currentColor" />} Run
          </button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} style={{ ...td, background: 'var(--bg-surface-raised)', padding: '10px 14px 12px 46px' }}>
            <OntologyStrip health={health} onOpen={onOpenOntology} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function ProjectsTable({
  projects, perProject = {}, ontologyHealth = {}, selectedIds, onToggleSelect,
  allSelected = false, someSelected = false, onToggleAll,
  onOpen, onOpenOntology, onRunNow, runningProjectIds,
}) {
  const [expanded, setExpanded] = useState(() => new Set());
  const toggle = (id) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div id="projects-grid-scroll" style={{ flex: 1, overflow: 'auto', border: '1px solid var(--border-main)', borderRadius: 9, background: 'var(--bg-surface)', marginBottom: 20 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
        <thead>
          <tr>
            <th style={th}>
              <input
                type="checkbox"
                aria-label="Select all projects"
                checked={allSelected}
                ref={el => { if (el) el.indeterminate = someSelected && !allSelected; }}
                onChange={() => onToggleAll?.()}
              />
            </th>
            <th style={th}>Project</th>
            <th style={th}>Last run</th>
            <th style={th}>Converted</th>
            <th style={{ ...th, textAlign: 'right' }} title="Measures converted with AI help (latest run)">AI</th>
            <th style={{ ...th, textAlign: 'right' }} title="Measures that need review (latest run)">Review</th>
            <th style={th} title="Ontology score 0–100: glossary and ownership coverage, minus duplicates and conflicts">Ontology</th>
            <th style={th}>Status</th>
            <th style={th} aria-label="Actions" />
          </tr>
        </thead>
        <tbody>
          {projects.map(p => (
            <Row
              key={p.id}
              project={p}
              summary={perProject[p.id]}
              health={ontologyHealth[p.id]}
              expanded={expanded.has(p.id)}
              onToggleExpand={() => toggle(p.id)}
              selected={selectedIds.has(p.id)}
              onToggleSelect={onToggleSelect}
              onOpen={() => onOpen(p)}
              onOpenOntology={() => onOpenOntology(p)}
              onRunNow={() => onRunNow(p)}
              isRunning={runningProjectIds.has(p.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
