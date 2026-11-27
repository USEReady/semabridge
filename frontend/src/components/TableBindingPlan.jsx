const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

function rows(count) {
  return count == null ? 'row count unknown' : `${Number(count).toLocaleString()} rows`;
}

/**
 * Dry-run preview of how each model table binds to the target schema
 * (backend: SnowflakeSchemaManager.plan_source_tables): tables a sync would
 * create, and ties between equally matching existing tables.
 */
export default function TableBindingPlan({ plan, style }) {
  if (!plan || typeof plan !== 'object') return null;
  if (plan.platform === 'databricks') return <DatabricksTablePlan plan={plan} style={style} />;

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
      <div style={{ ...box, borderLeft: '3px solid var(--color-warning)' }}>
        <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Target tables</div>
        <div>Could not check the target schema: {String(plan.error)}</div>
      </div>
    );
  }

  const tables = Array.isArray(plan.tables) ? plan.tables : [];
  const blocked = tables.filter(t => t.action === 'blocked');
  const toCreate = tables.filter(t => t.action === 'create');
  const ties = tables.filter(t => Array.isArray(t.tie) && t.tie.length > 1);
  const warnings = Array.isArray(plan.warnings) ? plan.warnings : [];
  const bound = tables.length - toCreate.length - blocked.length;
  const accent = blocked.length
    ? 'var(--color-danger, #ef4444)'
    : (toCreate.length || ties.length || warnings.length ? 'var(--color-warning)' : 'var(--color-success)');

  return (
    <div style={{ ...box, borderLeft: `3px solid ${accent}` }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Target tables{' '}
        <span style={{ color: accent, fontWeight: 600 }}>
          {bound} bound to existing · {toCreate.length} to be created
          {blocked.length > 0 ? ` · ${blocked.length} blocked` : ''}
        </span>
      </div>
      {blocked.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--color-danger, #ef4444)', fontWeight: 600 }}>
            Blocked due to existing table collision (columns differ):
          </div>
          {blocked.map(t => (
            <div key={`b-${t.dataset}`} style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14, color: 'var(--color-danger, #ef4444)' }}>
              {t.dataset} ✕ {t.table}{t.message ? ` — ${t.message}` : ''}
            </div>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--color-warning)', fontWeight: 600 }}>
            Tables already used by another project (same database.schema.table):
          </div>
          {warnings.map((w, idx) => (
            <div key={`w-${idx}`} style={{ fontSize: 11, paddingLeft: 14, color: 'var(--color-warning)' }}>
              {w.type === 'other_project_placeholder_table' ? '⚠ ' : ''}
              {w.message || `Table ${w.table} is also bound by project ${w.other_project_id}`}
            </div>
          ))}
        </div>
      )}
      {toCreate.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--text-primary)' }}>Empty tables this sync will create (no existing table matches):</div>
          {toCreate.map(t => (
            <div key={`c-${t.dataset}`} style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
              {t.dataset} → {t.table}
            </div>
          ))}
        </div>
      )}
      {ties.map(t => (
        <div key={`t-${t.dataset}`} style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--color-warning)' }}>
            Tie for {t.dataset}: bound to {t.table}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
            {t.tie.map(c => `${c.table} (${rows(c.row_count)})`).join(' · ')}
          </div>
        </div>
      ))}
      {tables
        .filter(t => t.action === 'bind' && t.matched_by === 'name')
        .map(t => (
          <div key={`n-${t.dataset}`} style={{ marginTop: 4 }}>
            {t.dataset} → {t.table} (matched by name only; columns differ)
          </div>
        ))}
    </div>
  );
}

const MATCHED_BY = {
  mapping: 'explicit mapping',
  'exact name': 'same name and columns',
  columns: 'matching columns',
  'model namespace': "this model's table",
  saved: 'saved binding',
};

/**
 * Databricks planner path (backend: target_backends/databricks/tables.py):
 * each Power BI table is bound to an existing Unity Catalog table, or uses an
 * empty table (created at deploy, nothing during a dry run) until the real table
 * exists, or is skipped -- per the project's on_missing_source setting.
 */
function DatabricksTablePlan({ plan, style }) {
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
  const tables = Array.isArray(plan.tables) ? plan.tables : [];
  const warnings = (Array.isArray(plan.warnings) ? plan.warnings : []).map((w) => (typeof w === 'string' ? w : w?.message)).filter(Boolean);
  const bound = tables.filter((t) => t.action === 'bind');
  const stubs = tables.filter((t) => t.action === 'stub');
  const skipped = tables.filter((t) => t.action === 'skip');
  const sources = Array.isArray(plan.source_schemas) ? plan.source_schemas.join(', ') : '';
  const accent = plan.error ? 'var(--color-warning)'
    : (stubs.length || skipped.length || warnings.length ? 'var(--color-warning)' : 'var(--color-success)');

  return (
    <div style={{ ...box, borderLeft: `3px solid ${accent}` }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Source tables (Unity Catalog){' '}
        {tables.length > 0 && (
          <span style={{ color: accent, fontWeight: 600 }}>
            {bound.length} bound · {stubs.length} stub{skipped.length ? ` · ${skipped.length} skipped` : ''}
          </span>
        )}
      </div>
      {sources && <div style={{ fontSize: 11 }}>Searched: <span style={{ fontFamily: MONO }}>{sources}</span></div>}
      {plan.error && <div>Could not check Unity Catalog: {String(plan.error)}</div>}
      {bound.length > 0 && (
        <div style={{ marginTop: 4 }}>
          {bound.map((t) => (
            <div key={`b-${t.dataset}`} style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
              {t.dataset} → {t.table}{' '}
              <span style={{ fontFamily: 'inherit', color: 'var(--text-tertiary)' }}>
                ({MATCHED_BY[t.matched_by] || t.matched_by || 'matched'})
                {Array.isArray(t.missing_columns) && t.missing_columns.length > 0
                  ? ` · missing columns: ${t.missing_columns.join(', ')}` : ''}
              </span>
            </div>
          ))}
        </div>
      )}
      {stubs.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--text-primary)' }}>
            No matching table yet: an empty table is created at deploy, or reused if SemaBridge created it earlier (nothing is created during a dry run). To use real data, point Source Schemas at your real tables; a later sync then uses them:
          </div>
          {stubs.map((t) => (
            <div key={`s-${t.dataset}`} style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
              {t.dataset}{Array.isArray(t.stub_columns) ? ` (${t.stub_columns.length} columns)` : ''}
            </div>
          ))}
        </div>
      )}
      {skipped.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ color: 'var(--text-primary)' }}>No matching table: skipped (on_missing_source = skip):</div>
          {skipped.map((t) => (
            <div key={`k-${t.dataset}`} style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>{t.dataset}</div>
          ))}
        </div>
      )}
      {warnings.length > 0 && (
        <div style={{ marginTop: 4 }}>
          {warnings.map((w, idx) => (
            <div key={`w-${idx}`} style={{ fontSize: 11, color: 'var(--color-warning)' }}>⚠ {w}</div>
          ))}
        </div>
      )}
    </div>
  );
}
