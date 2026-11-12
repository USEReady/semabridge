/**
 * Overview tab of the project page: last run, conversion over time, what was
 * migrated, what needs review, and an About card.
 * Data: GET /api/projects/{id}/runs + /runs/{run_id}/report-summary and
 * GET /api/ontology/projects/{id}/health (loaded by useProjectPageData).
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ConversionBar, ConversionLegend, OntologyScore } from '../projects/projectHealth';
import {
  runId, isOk, conversionPct, measureTotal, durationLabel, relTime, shortDate, statusLabel,
} from './projectData';
import { card, lbl, muted, faint, th, td, statusTone, btnSmall, btnGhost } from './projectStyles';
import { Pill, Chip } from './projectUi';

const fmt = (n) => (Number(n) || 0).toLocaleString();
const usable = (s) => s && !s.crashed && s.counts;

function openRun(navigate, run) {
  navigate('/runs', { state: { runId: runId(run), autoExpand: true } });
}

function LastRunCard({ run, summary, runNumber, loading }) {
  const navigate = useNavigate();
  if (loading) return <div style={card}><span style={lbl}>Last run</span><span style={faint}>Loading runs…</span></div>;
  if (!run) {
    return (
      <div style={card}>
        <span style={lbl}>Last run</span>
        <span style={muted}>This project has not run yet. Use “Run now” to migrate it.</span>
      </div>
    );
  }
  const running = statusTone(run.status) === 'info';
  const counts = usable(summary) ? summary.counts : null;
  const pct = conversionPct(counts);
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={lbl}>Last run · #{runNumber}{relTime(run.started_at) ? ` · ${relTime(run.started_at)}` : ''}</span>
        <Pill tone={statusTone(run.status)}>{statusLabel(run.status)}</Pill>
        <span style={{ flex: 1 }} />
        <span style={muted}>{running ? '' : durationLabel(run)}</span>
      </div>
      {running && <span style={muted}>This run is still going. Results appear here when it finishes.</span>}
      {!running && counts && (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 30, fontWeight: 800, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
              {pct === null ? '—' : `${pct}%`}
            </span>
            <span style={muted}>of {fmt(measureTotal(counts))} measures converted</span>
          </div>
          <ConversionBar counts={counts} height={10} />
          <ConversionLegend counts={counts} />
        </>
      )}
      {!running && !counts && (
        <span style={muted}>
          {summary === undefined
            ? 'Loading the run report…'
            : 'The detailed report for this run is not available (for example after a server restart or a failed run).'}
          {' '}
          <button type="button" style={{ ...btnGhost, padding: 0 }} onClick={() => openRun(navigate, run)}>Open in Runs →</button>
        </span>
      )}
    </div>
  );
}

function TrendCard({ runs, summaries }) {
  // Successful runs with a report, oldest → newest, last 8.
  const points = runs
    .filter(r => isOk(r) && usable(summaries[runId(r)]))
    .map(r => conversionPct(summaries[runId(r)].counts))
    .filter(v => v !== null)
    .slice(0, 8)
    .reverse();
  const W = 200, H = 58, PAD = 5;
  let body;
  if (points.length < 2) {
    body = <span style={muted}>{points.length ? `${points[0]}% on the only run with a report.` : 'Shows up after runs with a report.'} The line appears after two.</span>;
  } else {
    const lo = Math.max(0, Math.min(...points) - 5);
    const hi = Math.min(100, Math.max(...points) + 5);
    const span = Math.max(1, hi - lo);
    const xy = points.map((v, i) => [
      (i / (points.length - 1)) * W,
      PAD + (1 - (v - lo) / span) * (H - 2 * PAD),
    ]);
    const d = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
    const [lx, ly] = xy[xy.length - 1];
    body = (
      <>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: '100%', height: 58, overflow: 'visible' }} aria-hidden="true">
          <path d={d} fill="none" stroke="var(--color-success)" strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
          <circle cx={lx} cy={ly} r="3" fill="var(--color-success)" />
        </svg>
        <span style={muted}>{points[0]}% → {points[points.length - 1]}% over {points.length} runs</span>
      </>
    );
  }
  return (
    <div style={card}>
      <span style={lbl}>Conversion over time</span>
      {body}
    </div>
  );
}

function targetLabel(project) {
  const t = project?.target || (Array.isArray(project?.targets) ? project.targets[0] : null);
  if (t && (t.database || t.schema)) return [t.database, t.schema].filter(Boolean).join('.');
  return project?.target_type || null;
}

function MigratedCard({ project, summary }) {
  const c = usable(summary) ? summary.counts : null;
  const models = usable(summary) ? (summary.source_files || []).length || 1 : null;
  const target = targetLabel(project);
  return (
    <div style={card}>
      <span style={lbl}>Migrated</span>
      {c ? (
        <>
          <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>{fmt(models)} model{models === 1 ? '' : 's'} · {fmt(c.tables)} tables</span>
          <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>{fmt(c.columns)} columns · {fmt(c.relationships)} relationships</span>
        </>
      ) : (
        <span style={muted}>Nothing to show until a run finishes with a report.</span>
      )}
      {target && <span style={{ ...muted, display: 'flex', alignItems: 'center', gap: 6 }}>Target: <Chip mono>{target}</Chip></span>}
    </div>
  );
}

function reviewRows(summary) {
  if (!usable(summary)) return [];
  const sections = summary.sections || {};
  const review = (sections.needs_review || []).map(item => ({
    name: item.name,
    why: (item.advisory_msgs || []).join(' ') || 'Flagged for a human check',
    confidence: null,
  }));
  const ai = (sections.ai_assisted || [])
    .filter(item => item.risky || (typeof item.confidence === 'number' && item.confidence < 0.8))
    .sort((a, b) => (a.confidence ?? 1) - (b.confidence ?? 1))
    .map(item => ({
      name: item.name,
      why: (item.advisory_msgs || [])[0] || (item.risky ? 'Rewritten by AI; uses a pattern that often needs a check' : 'Rewritten by AI with low confidence'),
      confidence: typeof item.confidence === 'number' ? item.confidence : null,
    }));
  return [...review, ...ai];
}

function NeedsReviewCard({ run, summary }) {
  const navigate = useNavigate();
  const rows = reviewRows(summary);
  const count = usable(summary) ? Number(summary.counts.needs_review) || 0 : 0;
  const shown = rows.slice(0, 8);
  return (
    <div style={{ ...card, padding: 0, gap: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px 8px' }}>
        <span style={lbl}>Needs review</span>
        {count > 0 && <Pill tone="warn">{fmt(count)}</Pill>}
        <span style={{ flex: 1 }} />
        {run && rows.length > 0 && (
          <button type="button" style={btnGhost} onClick={() => openRun(navigate, run)}>Open all →</button>
        )}
      </div>
      {!usable(summary) ? (
        <div style={{ ...muted, padding: '4px 14px 14px' }}>Nothing to review until a run finishes with a report.</div>
      ) : rows.length === 0 ? (
        <div style={{ ...muted, padding: '4px 14px 14px' }}>Nothing needs review in the last run.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr><th style={th}>Measure</th><th style={th}>Why</th><th style={th}>AI confidence</th><th style={th} /></tr>
            </thead>
            <tbody>
              {shown.map((r, i) => (
                <tr key={`${r.name}-${i}`}>
                  <td style={{ ...td, fontWeight: 600, color: 'var(--text-primary)' }}>{r.name}</td>
                  <td style={{ ...td, color: 'var(--text-secondary)' }}>{r.why}</td>
                  <td style={td}>{r.confidence === null ? <span style={faint}>—</span> : <Pill tone="ai">{r.confidence.toFixed(2)}</Pill>}</td>
                  <td style={{ ...td, textAlign: 'right' }}>
                    <button type="button" style={btnSmall} onClick={() => openRun(navigate, run)}>Check</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > shown.length && (
            <div style={{ ...faint, padding: '8px 14px' }}>+{fmt(rows.length - shown.length)} more in the run report</div>
          )}
        </div>
      )}
    </div>
  );
}

function AboutRow({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-primary)', minWidth: 0, flexWrap: 'wrap' }}>
      <span style={{ ...muted, width: 80, flex: 'none' }}>{label}</span>
      {children}
    </div>
  );
}

function ontologyText(h) {
  if (!h || h.score === null || h.score === undefined) return 'Not built yet';
  const names = (h.domains || []).map(d => d.domain).filter(Boolean);
  const parts = [names.length ? names.join(', ') : 'No domains'];
  if (h.conflicts) parts.push(`${h.conflicts} conflict${h.conflicts === 1 ? '' : 's'}`);
  if (h.duplicate_groups) parts.push(`${h.duplicate_groups} duplicate${h.duplicate_groups === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

function AboutCard({ project, health, folderName }) {
  const domainOwner = (health?.domains || []).map(d => d.owner).filter(Boolean)[0];
  const owner = project.owner_name || project.owner || project.owner_user_id || project.user_id || domainOwner;
  const tags = Array.isArray(project.tags) ? project.tags : [];
  return (
    <div style={card}>
      <span style={lbl}>About</span>
      {project.description && <span style={muted}>{project.description}</span>}
      <AboutRow label="Owner">{owner || <span style={faint}>—</span>}</AboutRow>
      <AboutRow label="Folder">{folderName || <span style={faint}>No folder</span>}</AboutRow>
      <AboutRow label="Tags">
        {tags.length ? tags.map(t => <Pill key={t} tone="n">{t}</Pill>) : <span style={faint}>None</span>}
      </AboutRow>
      <AboutRow label="Ontology">
        <OntologyScore health={health} size={22} />
        <span style={{ fontSize: 12 }}>{ontologyText(health)}</span>
      </AboutRow>
      <AboutRow label="Created">{shortDate(project.created_at)}</AboutRow>
    </div>
  );
}

export default function ProjectOverviewTab({ project, runs, runsLoading, summaries, health, folderName }) {
  const last = runs[0] || null;
  const lastSummary = last ? summaries[runId(last)] : undefined;
  // "Migrated" / "Needs review" describe the newest run that has a usable report.
  const reported = runs.find(r => usable(summaries[runId(r)]));
  const reportedSummary = reported ? summaries[runId(reported)] : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'stretch' }}>
        <div style={{ flex: '1.5 1 340px', minWidth: 0, display: 'flex' }}>
          <div style={{ flex: 1, display: 'flex' }}><LastRunCard run={last} summary={lastSummary} runNumber={runs.length} loading={runsLoading} /></div>
        </div>
        <div style={{ flex: '1 1 220px', minWidth: 0, display: 'flex' }}><div style={{ flex: 1, display: 'flex' }}><TrendCard runs={runs} summaries={summaries} /></div></div>
        <div style={{ flex: '1 1 220px', minWidth: 0, display: 'flex' }}><div style={{ flex: 1, display: 'flex' }}><MigratedCard project={project} summary={reportedSummary} /></div></div>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, alignItems: 'flex-start' }}>
        <div style={{ flex: '1.5 1 420px', minWidth: 0 }}><NeedsReviewCard run={reported} summary={reportedSummary} /></div>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}><AboutCard project={project} health={health} folderName={folderName} /></div>
      </div>
    </div>
  );
}
