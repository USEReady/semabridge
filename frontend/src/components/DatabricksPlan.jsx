const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

function formatItem(item) {
  if (typeof item === 'string') return item;
  if (!item || typeof item !== 'object') return String(item ?? '');
  if (item.message) return item.table ? `${item.table} — ${item.message}` : String(item.message);
  if (item.reason) return item.name ? `${item.name} — ${item.reason}` : String(item.reason);
  if (item.name) return String(item.name);
  if (item.table) return String(item.table);
  if (item.dataset) return String(item.dataset);
  return JSON.stringify(item);
}

/**
 * Dry-run preview of Databricks Unity Catalog metric views
 * (backend: core/engine/deployment/databricks_preview.py -> DatabricksPublisher.plan):
 * per metric view — name, fact, joins, #dimensions, #measures (placeholders
 * highlighted with reason), skipped views with reason, missing tables, blocked items.
 */
export default function DatabricksPlan({ plan, style }) {
  if (!plan || typeof plan !== 'object') return null;

  const box = {
    marginTop: 10,
    padding: 10,
    borderRadius: 6,
    background: 'var(--bg-input)',
    fontSize: 12,
    color: 'var(--text-secondary)',
    overflowWrap: 'anywhere',
    ...style,
  };

  if (plan.error) {
    return (
      <div style={{ ...box, borderLeft: '3px solid var(--color-danger, #ef4444)' }}>
        <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
          Databricks metric views{plan.target ? ` (${plan.target})` : ''}
        </div>
        <div style={{ color: 'var(--color-danger, #ef4444)' }}>
          Could not build Databricks deployment plan: {String(plan.error)}
        </div>
      </div>
    );
  }

  const views = Array.isArray(plan.views) ? plan.views : [];
  const activeViews = views.filter((v) => !v?.skipped);
  const skippedViews = views.filter((v) => Boolean(v?.skipped));
  const missingTables = Array.isArray(plan.missing_tables) ? plan.missing_tables : [];
  const blocked = Array.isArray(plan.blocked) ? plan.blocked : [];
  const warnings = Array.isArray(plan.warnings) ? plan.warnings : [];
  const placeholderCount = activeViews.reduce((sum, v) => {
    const measures = Array.isArray(v?.measures) ? v.measures : [];
    return sum + measures.filter((m) => String(m?.status || '').toLowerCase() === 'placeholder').length;
  }, 0);

  const hasBlockers = blocked.length > 0 || missingTables.length > 0 || skippedViews.length > 0;
  const hasWarnings = warnings.length > 0 || placeholderCount > 0;
  const accent = hasBlockers
    ? 'var(--color-danger, #ef4444)'
    : (hasWarnings ? 'var(--color-warning)' : 'var(--color-success)');

  return (
    <div style={{ ...box, borderLeft: `3px solid ${accent}` }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Databricks metric views{plan.target ? ` · ${plan.target}` : ''}{' '}
        <span style={{ color: accent, fontWeight: 600 }}>
          {activeViews.length} view{activeViews.length === 1 ? '' : 's'}
          {skippedViews.length > 0 ? ` · ${skippedViews.length} skipped` : ''}
          {missingTables.length > 0 ? ` · ${missingTables.length} missing table${missingTables.length === 1 ? '' : 's'}` : ''}
          {blocked.length > 0 ? ` · ${blocked.length} blocked` : ''}
          {placeholderCount > 0 ? ` · ${placeholderCount} placeholder measure${placeholderCount === 1 ? '' : 's'}` : ''}
        </span>
        {plan.source === 'derived' && (
          <span
            style={{
              marginLeft: 8,
              fontSize: 10,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 4,
              background: 'var(--bg-surface, rgba(255,255,255,0.06))',
              color: 'var(--text-tertiary)',
            }}
          >
            derived preview
          </span>
        )}
      </div>

      {blocked.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--color-danger, #ef4444)', fontWeight: 600 }}>
            Blocked items:
          </div>
          {blocked.map((item, idx) => (
            <div
              key={`blk-${idx}`}
              style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14, color: 'var(--color-danger, #ef4444)' }}
            >
              ✕ {formatItem(item)}
            </div>
          ))}
        </div>
      )}

      {missingTables.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--color-danger, #ef4444)', fontWeight: 600 }}>
            Missing Unity Catalog source tables:
          </div>
          {missingTables.map((tbl, idx) => (
            <div
              key={`mt-${idx}`}
              style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14, color: 'var(--color-danger, #ef4444)' }}
            >
              ✕ {formatItem(tbl)}
            </div>
          ))}
        </div>
      )}

      {warnings.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--color-warning)', fontWeight: 600 }}>Warnings:</div>
          {warnings.map((w, idx) => (
            <div
              key={`w-${idx}`}
              style={{ fontSize: 11, paddingLeft: 14, color: 'var(--color-warning)' }}
            >
              ⚠ {formatItem(w)}
            </div>
          ))}
        </div>
      )}

      {activeViews.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {activeViews.map((view, idx) => {
            const joins = Array.isArray(view?.joins) ? view.joins : [];
            const dimensions = Array.isArray(view?.dimensions) ? view.dimensions : [];
            const measures = Array.isArray(view?.measures) ? view.measures : [];
            const placeholders = measures.filter(
              (m) => String(m?.status || '').toLowerCase() === 'placeholder',
            );
            return (
              <div
                key={`v-${view.name || idx}`}
                style={{
                  marginTop: idx === 0 ? 0 : 6,
                  paddingTop: idx === 0 ? 0 : 6,
                  borderTop: idx === 0 ? 'none' : '1px solid var(--border-subtle, rgba(128,128,128,0.18))',
                }}
              >
                <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                  <span style={{ fontFamily: MONO }}>{view.name || '(unnamed view)'}</span>
                  <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 500, color: 'var(--text-tertiary)' }}>
                    [{view.kind || 'metric_view'}] · fact: {view.fact || '—'} · {dimensions.length} dim
                    {dimensions.length === 1 ? '' : 's'} · {measures.length} measure
                    {measures.length === 1 ? '' : 's'}
                    {joins.length > 0 ? ` · ${joins.length} join${joins.length === 1 ? '' : 's'}` : ''}
                  </span>
                </div>
                {view.source_table && (
                  <div style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14, color: 'var(--text-tertiary)' }}>
                    source: {view.source_table}
                  </div>
                )}
                {joins.length > 0 && (
                  <div style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
                    {joins.map((j, jIdx) => (
                      <div key={`j-${jIdx}`}>
                        join {j.dataset || 'dim'} → {j.table || '—'}
                        {j.on ? ` ON ${j.on}` : ''}
                      </div>
                    ))}
                  </div>
                )}
                {placeholders.length > 0 && (
                  <div style={{ marginTop: 2, paddingLeft: 14 }}>
                    {placeholders.map((m, mIdx) => (
                      <div
                        key={`p-${mIdx}`}
                        style={{ fontFamily: MONO, fontSize: 11, color: 'var(--color-warning)' }}
                      >
                        ⚠ placeholder measure {m.name}
                        {m.reason ? ` — ${m.reason}` : ''}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {skippedViews.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ color: 'var(--color-danger, #ef4444)', fontWeight: 600 }}>
            Skipped views:
          </div>
          {skippedViews.map((view, idx) => (
            <div
              key={`s-${view.name || idx}`}
              style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14, color: 'var(--color-danger, #ef4444)' }}
            >
              ✕ {view.name || view.fact || '(unnamed)'}
              {view.fact ? ` (fact: ${view.fact})` : ''}
              {view.reason ? ` — ${view.reason}` : ''}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
