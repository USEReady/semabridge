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
  const toCreate = tables.filter(t => t.action === 'create');
  const ties = tables.filter(t => Array.isArray(t.tie) && t.tie.length > 1);
  const bound = tables.length - toCreate.length;
  const accent = toCreate.length || ties.length ? 'var(--color-warning)' : 'var(--color-success)';

  return (
    <div style={{ ...box, borderLeft: `3px solid ${accent}` }}>
      <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Target tables{' '}
        <span style={{ color: accent, fontWeight: 600 }}>
          {bound} bound to existing · {toCreate.length} to be created
        </span>
      </div>
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
