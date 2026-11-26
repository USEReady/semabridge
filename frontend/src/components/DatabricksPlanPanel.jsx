/**
 * DatabricksPlanPanel -- the backend's Databricks metric-view plan on the
 * dry-run screen (the planner is authoritative for final names and clashes).
 *
 * Shows: plan status (valid / stale / failed, the table check it rests on),
 * publication blockers, the views, what happened to saved names, and the
 * approximate-measure controls (approximate / strict, untick measures) with a
 * Re-plan button. Re-planning reruns only the planner from the dry run's
 * cached inputs (no new dry run, no Unity Catalog read). Responses are
 * ordered by revision: an older response never replaces a newer plan.
 *
 * Props:
 *   plan           : databricks_view_plan from the dry run (or a re-plan)
 *   projectId      : real project id, or 'preview' / 'preview-…' in the wizard
 *   rows | sections: the dry-run rows (their saved names go to the planner)
 *   plannerOptions : { calculate_mode, excluded_measures } chosen on screen (optional)
 *   onPlanChange(plan)            : a newer plan arrived
 *   onPlannerOptionsChange(opts)  : options changed (the wizard writes them to the project config)
 *   saveOptions    : true for a real project (options are saved to its config)
 */
import { useMemo, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle, ChevronRight, Info, RefreshCw } from 'lucide-react';
import { api } from '../utils/api';
import DatabricksDeploySection from './DatabricksDeploySection';
import {
  approximateMeasureChoices,
  buildReplanBody,
  excludedDependents,
  planRequestProjectId,
  planStatus,
} from '../utils/databricksPlan.js';

const BOX = {
  border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)',
  marginBottom: 12, fontSize: 12, color: 'var(--text-secondary)',
};
const ROW = { display: 'flex', gap: 8, alignItems: 'flex-start', padding: '8px 14px', borderTop: '1px solid var(--border-subtle)' };
const HEAD = {
  display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '10px 14px', background: 'none',
  border: 'none', color: 'var(--text-primary)', fontWeight: 700, fontSize: 13, cursor: 'pointer', textAlign: 'left',
};
const BTN = {
  display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 6,
  border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', color: 'var(--text-secondary)',
  fontSize: 11, fontWeight: 600, cursor: 'pointer',
};

const TABLE_STATUS = {
  bound: 'exists (bound)',
  proposed_not_created: 'proposed — not created',
  unchecked: 'proposed — not checked (no Databricks connection)',
};

const PRE = {
  margin: 0, padding: '8px 10px', background: 'var(--bg-surface-raised)', border: '1px solid var(--border-subtle)',
  borderRadius: 6, fontSize: 11, lineHeight: 1.45, overflowX: 'auto', whiteSpace: 'pre', color: 'var(--text-primary)',
};

const REQUEST_LABEL = {
  kept: 'kept', changed: 'could not be kept', conflict: 'conflict (blocks publishing)',
  not_placed: 'not in any view', not_applicable: 'not applicable on Databricks',
};

function Section({ title, count, children, defaultOpen = false, tone }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ borderTop: '1px solid var(--border-subtle)' }}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} style={{ ...HEAD, fontWeight: 600, fontSize: 12 }}>
        <ChevronRight size={13} style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
        <span style={{ color: tone || 'var(--text-primary)' }}>{title}</span>
        {count != null && <span style={{ color: 'var(--text-tertiary)', fontWeight: 400 }}>({count})</span>}
      </button>
      {open && <div>{children}</div>}
    </div>
  );
}

export default function DatabricksPlanPanel({
  plan,
  projectId = 'preview',
  rows = [],
  sections = null,
  plannerOptions = null,
  onPlanChange,
  onPlannerOptionsChange,
  saveOptions = false,
  deployProjectId = '',
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [failedRevision, setFailedRevision] = useState(0);
  const latestSent = useRef(0);

  const settings = plan?.settings || {};
  const [mode, setMode] = useState(plannerOptions?.calculate_mode || settings.calculate_mode || 'approximate');
  const [excluded, setExcluded] = useState(() => new Set(plannerOptions?.excluded_measures || settings.excluded_measures || []));
  const choices = useMemo(() => approximateMeasureChoices(plan), [plan]);
  const dependents = useMemo(() => excludedDependents(plan), [plan]);
  if (!plan) return null;

  const { status, basis, revision } = planStatus(plan);
  // The saved project deploys; the plan itself may sit under the dry run's preview id.
  const deployTarget = planRequestProjectId(deployProjectId) !== 'preview'
    ? planRequestProjectId(deployProjectId) : planRequestProjectId(projectId);
  const stale = status === 'stale' || (failedRevision > revision);
  const summary = plan.summary || {};
  const blockers = plan.blockers || [];
  const requests = (plan.name_requests || []).filter((r) => r.status !== 'kept' || r.origin === 'user');
  const optionsChanged = mode !== (settings.calculate_mode || 'approximate')
    || [...excluded].sort().join('|') !== [...(settings.excluded_measures || [])].sort().join('|');

  const replan = async () => {
    const options = { calculate_mode: mode, excluded_measures: [...excluded].sort() };
    const revisionSent = Date.now();
    latestSent.current = revisionSent;
    setBusy(true);
    setError('');
    try {
      onPlannerOptionsChange?.(options);
      const target = planRequestProjectId(projectId);
      if (saveOptions && target !== 'preview') {
        await api.saveDatabricksPlannerOptions(target, options);
      }
      const res = await api.replanDatabricks(target, buildReplanBody({ plan, rows, sections, planner: options, revision: revisionSent }));
      if (revisionSent !== latestSent.current) return;          // a newer request is in flight
      if (res?.status === 'valid' && res.plan) {
        if (!res.superseded) onPlanChange?.(res.plan);
      } else {
        setFailedRevision(revisionSent);
        setError(res?.error || 'Re-planning failed; the plan shown is out of date.');
      }
    } catch (err) {
      if (revisionSent === latestSent.current) {
        setFailedRevision(revisionSent);
        setError(err?.message || 'Re-planning failed; the plan shown is out of date.');
      }
    } finally {
      if (revisionSent === latestSent.current) setBusy(false);
    }
  };

  const toggle = (name) => setExcluded((prev) => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name); else next.add(name);
    return next;
  });

  return (
    <div style={BOX} data-testid="databricks-plan-panel">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-primary)' }}>Databricks metric-view plan</span>
        <span>
          {summary.views} view{summary.views === 1 ? '' : 's'} · {summary.measures_placed} measures placed
          {' '}({summary.approximate} approximate) · {summary.measures_left_out} left out
        </span>
        {stale ? (
          <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', color: '#f87171', fontWeight: 700 }}>
            <AlertTriangle size={13} /> Out of date
          </span>
        ) : (
          <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', color: '#4ade80', fontWeight: 600 }}>
            <CheckCircle size={13} /> Current
          </span>
        )}
      </div>
      {stale && (
        <div role="alert" style={{ ...ROW, color: '#fca5a5' }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            {error || plan?.meta?.error || 'The last re-plan failed.'} The names below are from the last successful plan and
            do not reflect your latest changes. Fix the issue and re-plan, or run the dry run again.
          </span>
        </div>
      )}
      <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
        <Info size={14} style={{ flexShrink: 0, marginTop: 1 }} />
        <span>
          The planner decides the final Databricks names per metric view; the original Power BI names stay as display names.
          {basis?.table_check_at ? ` Based on the Unity Catalog table check of ${new Date(basis.table_check_at).toLocaleString()}` : ''}
          {basis?.table_check === 'no_connection' ? ' (no Databricks connection: tables not checked)' : ''}
          {basis?.table_check_at ? '; tables are checked again before deploying.' : ''}
        </span>
      </div>

      <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
        <Info size={14} style={{ flexShrink: 0, marginTop: 1 }} />
        <span>
          Optional Databricks features in this plan:{' '}
          {(settings.capabilities || []).length
            ? settings.capabilities.map((c) => c.replace(/_/g, ' ')).join(', ')
            : 'none'}
          {settings.evidence?.status === 'valid'
            ? ` — capability proof from ${String(settings.evidence.completed_at || '').slice(0, 10)}`
              + (settings.evidence.dbsql_version ? `, DBSQL ${settings.evidence.dbsql_version}` : '')
              + ' (not re-checked during preview; checked again before deploying)'
              + (settings.evidence.not_enabled_by_admin?.length
                ? `; proven but not enabled by the admin: ${settings.evidence.not_enabled_by_admin.join(', ')}` : '')
            : settings.evidence?.reason ? ` — ${settings.evidence.reason}` : ''}
        </span>
      </div>

      {blockers.length > 0 && (
        <Section title="Publication blockers" count={blockers.length} defaultOpen tone="#f87171">
          {blockers.map((b, i) => (
            <div key={`${b.code}-${i}`} style={ROW}>
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1, color: '#f87171' }} />
              <span>
                <strong style={{ color: 'var(--text-primary)' }}>{b.code.replace(/_/g, ' ')}</strong>
                {b.scope === 'table' && b.views?.length ? ` (table ${b.table}; views: ${b.views.join(', ')})`
                  : b.view ? ` (${b.view})` : b.scope === 'model' ? ' (whole plan)' : ''}: {b.message}
              </span>
            </div>
          ))}
        </Section>
      )}

      <Section title="Views" count={(plan.views || []).length}>
        {(plan.views || []).map((v) => (
          <div key={v.name} style={{ ...ROW, flexDirection: 'column' }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <code style={{ color: 'var(--text-primary)', flexShrink: 0 }}>{v.name}</code>
              <span style={{ overflowWrap: 'anywhere' }}>
                {v.measures.length} measure{v.measures.length === 1 ? '' : 's'} · {v.fields.length} field{v.fields.length === 1 ? '' : 's'}
              </span>
            </div>
            {v.measures.length > 0 && (
              <details>
                <summary style={{ cursor: 'pointer', fontSize: 11 }}>Measures with their final Databricks SQL</summary>
                {v.measures.map((m) => (
                  <div key={m.name} style={{ fontSize: 11, padding: '3px 0', overflowWrap: 'anywhere' }}>
                    <code style={{ color: 'var(--text-primary)' }}>{m.name}</code>
                    <span style={{ color: 'var(--text-tertiary)' }}> ({m.source_name}) = </span>
                    <code>{m.expr}</code>
                  </div>
                ))}
              </details>
            )}
          </div>
        ))}
      </Section>

      {(plan.tables || []).length > 0 && (
        <Section title="Tables" count={plan.tables.length}>
          {plan.tables.map((t) => (
            <div key={t.dataset} style={ROW}>
              <span style={{ flex: '0 0 30%', overflowWrap: 'anywhere' }}><strong>{t.dataset}</strong></span>
              <code style={{ flex: '1 1 40%', overflowWrap: 'anywhere' }}>
                {[t.catalog, t.schema, t.name].filter(Boolean).join('.') || '(no location)'}
              </code>
              <span style={{ flex: '0 0 30%', color: t.status === 'bound' ? 'inherit' : '#fbbf24' }}>
                {TABLE_STATUS[t.status] || t.status}
              </span>
            </div>
          ))}
        </Section>
      )}

      <Section title="View definitions (YAML)" count={(plan.definitions || []).length}>
        {plan.meta?.definitions?.status && plan.meta.definitions.status !== 'written' ? (
          <div style={{ ...ROW, color: '#fca5a5' }}>
            <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
            <span>Not written: {plan.meta.definitions.error}</span>
          </div>
        ) : (
          <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
            <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
            <span>Exactly what would be sent to Databricks. Nothing is created until deploy.</span>
          </div>
        )}
        {(plan.definitions || []).map((d) => (
          <div key={d.view} style={{ ...ROW, flexDirection: 'column' }}>
            <details>
              <summary style={{ cursor: 'pointer' }}>
                <code style={{ color: 'var(--text-primary)' }}>{d.fqn}</code>
                {d.warnings?.length ? <span style={{ color: '#fbbf24' }}> · {d.warnings.length} note(s)</span> : null}
              </summary>
              {d.warnings?.length > 0 && (
                <ul style={{ margin: '6px 0', paddingLeft: 18, fontSize: 11 }}>
                  {d.warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
              <pre style={PRE}>{d.create_sql}</pre>
            </details>
          </div>
        ))}
      </Section>

      {requests.length > 0 && (
        <Section title="Saved names" count={requests.length}>
          {requests.map((r) => (
            <div key={r.source_path} style={ROW}>
              <span style={{ overflowWrap: 'anywhere', flex: '1 1 40%' }}>
                {r.source_path.replace(/^datasets\./, '').replace('.columns.', '[').replace(/^metrics\./, '')}
                {r.source_path.includes('.columns.') ? ']' : ''} → <code>{r.requested}</code>
                <span style={{ color: 'var(--text-tertiary)' }}>{r.origin === 'user' ? ' (your edit)' : ' (saved earlier)'}</span>
              </span>
              <span style={{ flex: '1 1 60%', color: r.status === 'conflict' ? '#f87171' : r.status === 'changed' ? '#fbbf24' : 'inherit' }}>
                {REQUEST_LABEL[r.status] || r.status}
                {r.views?.length ? `: ${r.views.map((v) => `${v.view} → ${v.final}`).join(', ')}` : ''}
                {r.reason ? ` — ${r.reason}` : ''}
              </span>
            </div>
          ))}
        </Section>
      )}

      <Section title="Approximate measures and mode" count={choices.length} defaultOpen={choices.length > 0}>
        <div style={ROW}>
          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>CALCULATE mode:</span>
          {['approximate', 'strict'].map((m) => (
            <label key={m} style={{ display: 'inline-flex', gap: 4, alignItems: 'center', cursor: 'pointer' }}>
              <input type="radio" name="calc-mode" checked={mode === m} onChange={() => setMode(m)} />
              {m === 'approximate' ? 'Approximate (publish with a warning)' : 'Strict (leave approximate measures out)'}
            </label>
          ))}
        </div>
        {choices.length === 0 && (
          <div style={ROW}><span style={{ color: 'var(--text-tertiary)' }}>No approximate measures in this plan.</span></div>
        )}
        {choices.map((c) => (
          <label key={c.source_name} style={{ ...ROW, cursor: 'pointer' }}>
            <input type="checkbox" checked={!excluded.has(c.source_name)} onChange={() => toggle(c.source_name)} />
            <span style={{ overflowWrap: 'anywhere' }}>
              <strong style={{ color: 'var(--text-primary)' }}>{c.source_name}</strong>
              {c.name ? <> → <code>{c.name}</code> ({c.view})</> : ' (unticked)'}
              {c.comment ? <span style={{ color: 'var(--text-tertiary)' }}> — {c.comment}</span> : null}
            </span>
          </label>
        ))}
        {dependents.length > 0 && (
          <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
            <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
            <span>
              Also left out because they are built on an unticked measure:{' '}
              {dependents.map((d) => d.measure).join(', ')}
            </span>
          </div>
        )}
        <div style={{ ...ROW, justifyContent: 'flex-end' }}>
          {error && !stale && <span style={{ color: '#f87171', marginRight: 'auto' }}>{error}</span>}
          <button type="button" onClick={replan} disabled={busy} style={{ ...BTN, opacity: busy ? 0.6 : 1 }}>
            <RefreshCw size={11} /> {busy ? 'Re-planning…' : optionsChanged ? 'Apply and re-plan' : 'Re-plan'}
          </button>
        </div>
      </Section>

      {deployTarget !== 'preview' && !stale ? (
        <DatabricksDeploySection projectId={deployTarget}
          cacheProjectId={plan?.meta?.cache_ref?.project_id || ''}
          modelSubdir={plan?.meta?.cache_ref?.model_subdir || ''} />
      ) : (
        <div style={{ ...ROW, color: 'var(--text-tertiary)' }}>
          <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            {stale ? 'Re-plan before deploying: the plan shown is out of date.'
              : 'Deploying is available once the project is saved and a dry run has been made in it.'}
          </span>
        </div>
      )}
    </div>
  );
}
