/**
 * Verify: does what SemaBridge deployed give the same numbers as Power BI?
 * One page, three ways to check:
 *   questions -> business questions answered on the deployed view and compared with Power BI
 *   measures  -> certify individual measures (DAX vs warehouse rows)
 *   targets   -> the same measures on Snowflake and Databricks
 * The tab lives in ?tab= so links and the old pages' URLs land on the right one.
 */
import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { VERIFY_TABS, verifyTab } from '../utils/verifyTabs';

const GovernanceSuitePage = lazy(() => import('./GovernanceSuitePage'));
const OntologyPage = lazy(() => import('./OntologyPage'));

export default function VerifyPage() {
  const [params, setParams] = useSearchParams();
  const tab = verifyTab(`?${params.toString()}`);
  const choose = (id) => {
    const next = new URLSearchParams(params);
    next.set('tab', id);
    setParams(next, { replace: true });
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100%' }}>
      <div style={{ padding: '18px 24px 0', borderBottom: '1px solid var(--border-main)' }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: 'var(--text-primary)' }}>Verify</h1>
        <p style={{ margin: '4px 0 12px', fontSize: 13, color: 'var(--text-secondary)' }}>
          Does the deployed semantic layer give the same numbers as Power BI?
        </p>
        <div role="tablist" aria-label="Verify" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {VERIFY_TABS.map((t) => (
            <button
              key={t.id}
              id={`verify-tab-${t.id}`}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              title={t.hint}
              onClick={() => choose(t.id)}
              style={{
                padding: '8px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer', background: 'transparent',
                border: 'none', borderBottom: `2px solid ${tab === t.id ? 'var(--accent-blue)' : 'transparent'}`,
                color: tab === t.id ? 'var(--text-primary)' : 'var(--text-tertiary)',
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <Suspense fallback={<div style={{ padding: 24, fontSize: 13, color: 'var(--text-tertiary)' }}>Loading…</div>}>
          {tab === 'questions' && <GovernanceSuitePage mode="bi-truth" />}
          {tab === 'measures' && <OntologyPage mode="comparator" />}
          {tab === 'targets' && <GovernanceSuitePage mode="cross-platform" />}
        </Suspense>
      </div>
    </div>
  );
}
