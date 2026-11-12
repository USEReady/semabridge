/**
 * Projects page summary band: "how far along is our migration?"
 *
 * Five numbers (measures migrated, reports & models, AI help, run health,
 * ontology coverage), then a "needs attention" list and projects by route.
 * Data: GET /api/portfolio/summary (latest finished run of each project)
 * plus the per-project ontology health the page already loads.
 * Collapsible; the choice is remembered per browser.
 */
import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronRight, AlertTriangle, AlertCircle } from 'lucide-react';
import { ConversionBar, ConversionLegend } from './projectHealth';
import { successSeries } from './projectHealthUtils';

const COLLAPSE_KEY = 'semabridge:projects:summaryCollapsed';
const WINDOWS = [7, 30, 90];

function readCollapsed() {
  try { return localStorage.getItem(COLLAPSE_KEY) === 'true'; } catch { return false; }
}

const fmt = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString());

const card = {
  background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 9,
  padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0,
};
const label = {
  fontSize: 10, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', color: 'var(--text-tertiary)',
};
const big = { fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.1, fontVariantNumeric: 'tabular-nums' };
const muted = { fontSize: 11.5, color: 'var(--text-tertiary)' };

function Sparkline({ trend = [] }) {
  const ys = successSeries(trend);
  if (trend.filter(t => t.runs).length < 2) return <div style={{ height: 30 }} />;
  const w = 200, h = 30;
  const step = w / Math.max(1, ys.length - 1);
  const line = ys.map((v, i) => `${i ? 'L' : 'M'}${(i * step).toFixed(1)} ${(h - 3 - v * (h - 6)).toFixed(1)}`).join(' ');
  const endY = h - 3 - ys[ys.length - 1] * (h - 6);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: '100%', height: h, display: 'block' }} aria-hidden="true">
      <path d={`${line} L${w} ${h} L0 ${h}Z`} fill="var(--accent-blue)" opacity="0.12" />
      <path d={line} fill="none" stroke="var(--accent-blue)" strokeWidth="1.6" vectorEffect="non-scaling-stroke" />
      <circle cx={w} cy={endY} r="2.5" fill="var(--accent-blue)" />
    </svg>
  );
}

export default function PortfolioSummary({ summary, loading, error, ontologyHealth, days, onDaysChange, onOpenRuns }) {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggle = () => {
    setCollapsed(c => {
      try { localStorage.setItem(COLLAPSE_KEY, String(!c)); } catch { /* storage unavailable */ }
      return !c;
    });
  };

  const onto = useMemo(() => {
    const rows = Object.values(ontologyHealth || {}).filter(h => h && h.score !== null && h.score !== undefined);
    if (!rows.length) return null;
    const metrics = rows.reduce((s, h) => s + (h.metric_count || 0), 0);
    const described = rows.reduce((s, h) => s + (h.described || 0), 0);
    return {
      projects: rows.length,
      avg: Math.round(rows.reduce((s, h) => s + h.score, 0) / rows.length),
      metrics,
      coverage: metrics ? Math.round((100 * described) / metrics) : null,
      duplicates: rows.reduce((s, h) => s + (h.duplicate_groups || 0), 0),
      conflicts: rows.reduce((s, h) => s + (h.conflicts || 0), 0),
    };
  }, [ontologyHealth]);

  const m = summary?.migrated;
  const runs = summary?.runs;
  const ai = summary?.ai;
  const attention = summary?.attention || [];
  const routes = (summary?.routes || []).slice(0, 4);
  const maxRoute = Math.max(1, ...routes.map(r => r.projects));

  return (
    <section aria-label="Migration summary" style={{ marginBottom: 14, flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: collapsed ? 0 : 10 }}>
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'none', border: 'none', padding: 0, cursor: 'pointer', ...label }}
        >
          {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
          Migration summary
        </button>
        <span style={muted}>· latest run of each project</span>
        {collapsed && m && (
          <span style={{ ...muted, marginLeft: 6 }}>
            {fmt(m.measures_converted)} / {fmt(m.measures_total)} measures · {runs?.success_pct ?? '—'}% runs succeeded
          </span>
        )}
        <span style={{ flex: 1 }} />
        {!collapsed && (
          <div role="group" aria-label="Run window" style={{ display: 'inline-flex', gap: 2, padding: 2, borderRadius: 7, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)' }}>
            {WINDOWS.map(d => (
              <button
                key={d}
                type="button"
                onClick={() => onDaysChange(d)}
                aria-pressed={days === d}
                style={{
                  padding: '2px 9px', borderRadius: 5, border: 'none', cursor: 'pointer', fontSize: 11, fontWeight: 600,
                  background: days === d ? 'var(--bg-surface)' : 'transparent',
                  color: days === d ? 'var(--text-primary)' : 'var(--text-tertiary)',
                }}
              >
                {d}d
              </button>
            ))}
          </div>
        )}
      </div>

      {!collapsed && (
        error ? (
          <div style={{ ...card, ...muted }}>The summary could not be loaded. The project list below is unaffected.</div>
        ) : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 10 }}>
              <div style={card}>
                <span style={label}>Measures migrated</span>
                <span style={big}>
                  {loading && !m ? '…' : fmt(m?.measures_converted)}
                  <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-tertiary)' }}> / {fmt(m?.measures_total)}</span>
                </span>
                <ConversionBar counts={m} />
                {m && !m.measures_total ? (
                  <span style={muted} title="Run reports are kept per server session; older runs may have none. Run a project to fill this in.">
                    {m.projects_with_runs ? 'No run report data yet — run a project to fill this in' : 'No runs yet'}
                  </span>
                ) : (
                  <span style={muted}>
                    <b style={{ color: 'var(--text-primary)' }}>{m?.conversion_pct ?? '—'}%</b> converted
                    {m && m.projects_with_data < m.projects_with_runs
                      ? ` · ${m.projects_with_data} of ${m.projects_with_runs} projects reported`
                      : ''}
                  </span>
                )}
              </div>

              <div style={card}>
                <span style={label}>Reports &amp; models</span>
                <span style={big}>{loading && !m ? '…' : fmt(m?.models)}</span>
                <span style={muted}>{fmt(m?.tables)} tables · {fmt(m?.relationships)} relationships</span>
                <span style={muted}>{fmt(m?.columns)} columns</span>
              </div>

              <div style={card}>
                <span style={label}>AI help</span>
                <span style={{ ...big, color: 'var(--accent-purple)' }}>{loading && !ai ? '…' : fmt(ai?.measures_ai_assisted)}</span>
                <span style={muted}>
                  measures converted with AI{ai?.avg_confidence != null ? ` · avg confidence ${ai.avg_confidence}` : ''}
                </span>
                <span style={muted} title="Ask AI questions are not recorded yet">
                  Ask AI usage: {ai?.ask_ai ? `${fmt(ai.ask_ai.questions)} questions by ${fmt(ai.ask_ai.people)} people` : 'not tracked yet'}
                </span>
              </div>

              <button
                type="button"
                onClick={onOpenRuns}
                style={{ ...card, textAlign: 'left', cursor: 'pointer', font: 'inherit' }}
                title="Open Runs"
              >
                <span style={label}>Run health · {days}d</span>
                <span style={big}>
                  {runs?.success_pct ?? '—'}%<span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-tertiary)' }}> succeeded</span>
                </span>
                <Sparkline trend={runs?.trend} />
                <span style={muted}>
                  {fmt(runs?.total)} runs · <span style={{ color: runs?.failed ? 'var(--color-error)' : undefined }}>{fmt(runs?.failed)} failed</span>
                </span>
              </button>

              <button
                type="button"
                onClick={() => navigate('/ontology')}
                style={{ ...card, textAlign: 'left', cursor: 'pointer', font: 'inherit' }}
                title="Open Ontology"
              >
                <span style={label}>Ontology coverage</span>
                <span style={big}>
                  {onto?.coverage ?? '—'}%<span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-tertiary)' }}> described</span>
                </span>
                <span style={muted}>
                  {onto ? `${fmt(onto.metrics)} metrics in ${onto.projects} project${onto.projects === 1 ? '' : 's'} · avg score ${onto.avg}` : 'No project has an ontology yet'}
                </span>
                {onto && (
                  <span style={muted}>
                    <b style={{ color: onto.duplicates ? 'var(--color-warning)' : 'var(--text-primary)' }}>{onto.duplicates}</b> duplicate{onto.duplicates === 1 ? '' : 's'} ·{' '}
                    <b style={{ color: onto.conflicts ? 'var(--color-error)' : 'var(--text-primary)' }}>{onto.conflicts}</b> conflict{onto.conflicts === 1 ? '' : 's'}
                  </span>
                )}
              </button>
            </div>

            {(attention.length > 0 || routes.length > 0) && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 10, marginTop: 10 }}>
                <div style={card}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={label}>Needs attention</span>
                    <span style={{ fontSize: 10.5, fontWeight: 700, padding: '0 7px', borderRadius: 999, background: 'var(--color-warning-bg)', color: 'var(--color-warning)' }}>
                      {attention.length}
                    </span>
                  </div>
                  {attention.length === 0 && <span style={muted}>Nothing needs attention.</span>}
                  {attention.slice(0, 4).map((a, i) => (
                    <div
                      key={`${a.project_id}-${a.kind}`}
                      style={{
                        display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 10, alignItems: 'center',
                        padding: '6px 0', borderTop: i ? '1px solid var(--border-light)' : 'none', fontSize: 12,
                      }}
                    >
                      {a.severity === 'error'
                        ? <AlertCircle size={13} color="var(--color-error)" />
                        : <AlertTriangle size={13} color="var(--color-warning)" />}
                      <span
                        title={a.message}
                        style={{ minWidth: 0, color: 'var(--text-secondary)', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
                      >
                        <b style={{ color: 'var(--text-primary)' }}>{a.project_name}</b> · {a.message}
                      </span>
                      <button
                        type="button"
                        onClick={() => navigate(`/projects/${a.project_id}`)}
                        style={{
                          border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)',
                          borderRadius: 6, padding: '2px 8px', fontSize: 11, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
                        }}
                      >
                        {a.kind === 'failed' ? 'Open run' : a.kind === 'review' ? 'Review' : 'Finish setup'}
                      </button>
                    </div>
                  ))}
                </div>
                {routes.length > 0 && (
                  <div style={card}>
                    <span style={label}>By route</span>
                    {routes.map(r => (
                      <div key={r.route} style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 160px) 1fr 28px', gap: 8, alignItems: 'center', fontSize: 12 }}>
                        <span style={{ color: 'var(--text-secondary)', textTransform: 'capitalize', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.route}</span>
                        <i style={{ display: 'block', height: 8, borderRadius: 3, background: 'var(--accent-blue)', width: `${(100 * r.projects) / maxRoute}%` }} />
                        <span style={{ textAlign: 'right', color: 'var(--text-tertiary)', fontVariantNumeric: 'tabular-nums' }}>{r.projects}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div style={{ marginTop: 8 }}><ConversionLegend /></div>
          </>
        )
      )}
    </section>
  );
}
