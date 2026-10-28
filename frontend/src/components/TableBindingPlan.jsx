import { useState } from 'react';
import { AlertTriangle, Database, Layers, ArrowRight, Loader2, CheckCircle2 } from 'lucide-react';
import { api } from '../utils/api';

const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

function rows(count) {
  return count == null ? 'row count unknown' : `${Number(count).toLocaleString()} rows`;
}

/**
 * Dry-run preview of how each model table binds to the target schema
 * (backend: SnowflakeSchemaManager.plan_source_tables):
 * - Blockers when a table already exists with incompatible columns, offering:
 *   a. Map to another existing table (choose from compatible candidates)
 *   b. Use a different schema for this model's data
 * - Tables a sync would create, and ties between equally matching existing tables.
 */
export default function TableBindingPlan({
  plan,
  blockers: explicitBlockers,
  projectId,
  modelName,
  onReSync,
  style,
}) {
  if (!plan || typeof plan !== 'object') return null;

  const [selectedTables, setSelectedTables] = useState({});
  const [schemaInputs, setSchemaInputs] = useState({});
  const [submittingAction, setSubmittingAction] = useState({}); // { [blockerKey]: 'map' | 'schema' | null }
  const [actionErrors, setActionErrors] = useState({});
  const [actionSuccess, setActionSuccess] = useState({});

  const box = {
    marginTop: 10,
    padding: 12,
    borderRadius: 8,
    background: 'var(--bg-input)',
    fontSize: 12,
    color: 'var(--text-secondary)',
    overflowWrap: 'anywhere',
    ...style,
  };

  const rawBlockers = explicitBlockers || plan.blockers || [];
  const blockedFromTables = Array.isArray(plan.tables)
    ? plan.tables.filter((t) => t.action === 'blocked' && t.blocker).map((t) => t.blocker)
    : [];
  const blockers = Array.isArray(rawBlockers) && rawBlockers.length > 0 ? rawBlockers : blockedFromTables;

  const handleApplyMapping = async (blocker, idx) => {
    const key = blocker.dataset_name || `blocker-${idx}`;
    const targetTable = selectedTables[key];
    if (!targetTable) {
      setActionErrors((prev) => ({ ...prev, [key]: 'Please select a compatible table from the dropdown.' }));
      return;
    }

    setSubmittingAction((prev) => ({ ...prev, [key]: 'map' }));
    setActionErrors((prev) => ({ ...prev, [key]: null }));

    try {
      const pid = projectId || 'preview';
      await api.saveTableBinding(pid, {
        modelName: modelName || 'default',
        dataset: blocker.dataset_name,
        table: targetTable,
      });
      setActionSuccess((prev) => ({ ...prev, [key]: `Mapped to ${targetTable}. Re-running dry run...` }));
      if (onReSync) {
        setTimeout(() => onReSync(), 400);
      }
    } catch (err) {
      setActionErrors((prev) => ({ ...prev, [key]: err.message || 'Failed to save table binding.' }));
    } finally {
      setSubmittingAction((prev) => ({ ...prev, [key]: null }));
    }
  };

  const handleApplySchema = async (blocker, idx) => {
    const key = blocker.dataset_name || `blocker-${idx}`;
    const newSchema = (schemaInputs[key] || '').trim();
    if (!newSchema) {
      setActionErrors((prev) => ({ ...prev, [key]: 'Please enter a schema name.' }));
      return;
    }

    setSubmittingAction((prev) => ({ ...prev, [key]: 'schema' }));
    setActionErrors((prev) => ({ ...prev, [key]: null }));

    try {
      const pid = projectId || 'preview';
      await api.saveSourceDataSchema(pid, newSchema);
      setActionSuccess((prev) => ({ ...prev, [key]: `Source data schema set to ${newSchema}. Re-running dry run...` }));
      if (onReSync) {
        setTimeout(() => onReSync(), 400);
      }
    } catch (err) {
      setActionErrors((prev) => ({ ...prev, [key]: err.message || 'Failed to save schema.' }));
    } finally {
      setSubmittingAction((prev) => ({ ...prev, [key]: null }));
    }
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
  const toCreate = tables.filter((t) => t.action === 'create');
  const ties = tables.filter((t) => Array.isArray(t.tie) && t.tie.length > 1);
  const bound = tables.length - toCreate.length - blockers.length;
  const accent = blockers.length > 0 ? 'var(--color-error)' : toCreate.length || ties.length ? 'var(--color-warning)' : 'var(--color-success)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* ── Blockers Section ── */}
      {blockers.map((blocker, idx) => {
        const key = blocker.dataset_name || `blocker-${idx}`;
        const headline = blocker.headline || `${blocker.table_name || blocker.dataset_name} already exists with different columns.`;
        const candidates = blocker.candidates || [];
        const isSubmittingMap = submittingAction[key] === 'map';
        const isSubmittingSchema = submittingAction[key] === 'schema';
        const error = actionErrors[key];
        const success = actionSuccess[key];

        return (
          <div
            key={key}
            id={`blocker-${blocker.dataset_name || idx}`}
            style={{
              borderRadius: 8,
              border: '1px solid rgba(239, 68, 68, 0.4)',
              background: 'rgba(239, 68, 68, 0.04)',
              padding: 14,
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            {/* Blocker Header */}
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <div
                style={{
                  background: 'rgba(239, 68, 68, 0.15)',
                  color: 'var(--color-error)',
                  borderRadius: 6,
                  padding: 6,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <AlertTriangle size={18} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--color-error)' }}>
                  {headline}
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                  {blocker.message || `${blocker.table_name} exists in Snowflake with incompatible columns. The sync cannot start while a blocker is open.`}
                </div>
              </div>
            </div>

            {error && (
              <div style={{ fontSize: 12, color: 'var(--color-error)', padding: '6px 10px', background: 'rgba(239, 68, 68, 0.08)', borderRadius: 6 }}>
                {error}
              </div>
            )}

            {success && (
              <div style={{ fontSize: 12, color: 'var(--color-success)', display: 'flex', alignItems: 'center', gap: 6 }}>
                <CheckCircle2 size={14} />
                {success}
              </div>
            )}

            {/* Two Resolutions Grid */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                gap: 12,
                marginTop: 2,
              }}
            >
              {/* Resolution (a): Map to compatible table */}
              <div
                style={{
                  border: '1px solid var(--border-main)',
                  borderRadius: 8,
                  padding: 12,
                  background: 'var(--bg-surface)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, color: 'var(--text-primary)', fontSize: 12 }}>
                  <Database size={14} style={{ color: 'var(--accent-blue)' }} />
                  Option (a): Map to an existing table
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.4 }}>
                  Choose from compatible existing tables. Saved as the project's binding.
                </div>

                <div style={{ display: 'flex', gap: 6, marginTop: 'auto' }}>
                  <select
                    id={`select-candidate-${key}`}
                    value={selectedTables[key] || ''}
                    onChange={(e) => setSelectedTables((prev) => ({ ...prev, [key]: e.target.value }))}
                    disabled={candidates.length === 0 || isSubmittingMap || isSubmittingSchema}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      borderRadius: 6,
                      border: '1px solid var(--border-main)',
                      background: 'var(--bg-input)',
                      color: 'var(--text-primary)',
                      fontSize: 12,
                      cursor: candidates.length ? 'pointer' : 'not-allowed',
                    }}
                  >
                    <option value="">
                      {candidates.length > 0 ? '-- Select compatible table --' : 'No compatible tables found'}
                    </option>
                    {candidates.map((cand) => (
                      <option key={cand} value={cand}>
                        {cand}
                      </option>
                    ))}
                  </select>

                  <button
                    type="button"
                    id={`btn-apply-map-${key}`}
                    onClick={() => handleApplyMapping(blocker, idx)}
                    disabled={!selectedTables[key] || isSubmittingMap || isSubmittingSchema}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '6px 12px',
                      borderRadius: 6,
                      border: 'none',
                      background: 'var(--accent-blue)',
                      color: '#fff',
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: selectedTables[key] && !isSubmittingMap ? 'pointer' : 'not-allowed',
                      opacity: selectedTables[key] && !isSubmittingMap ? 1 : 0.6,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {isSubmittingMap ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : <ArrowRight size={12} />}
                    Map & Re-run
                  </button>
                </div>
              </div>

              {/* Resolution (b): Use different schema */}
              <div
                style={{
                  border: '1px solid var(--border-main)',
                  borderRadius: 8,
                  padding: 12,
                  background: 'var(--bg-surface)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, color: 'var(--text-primary)', fontSize: 12 }}>
                  <Layers size={14} style={{ color: 'var(--accent-blue)' }} />
                  Option (b): Use a different schema
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.4 }}>
                  The schema must already exist; SemaBridge never creates schemas.
                </div>

                <div style={{ display: 'flex', gap: 6, marginTop: 'auto' }}>
                  <input
                    type="text"
                    id={`input-schema-${key}`}
                    placeholder="e.g. DATA_SCHEMA"
                    value={schemaInputs[key] || ''}
                    onChange={(e) => setSchemaInputs((prev) => ({ ...prev, [key]: e.target.value }))}
                    disabled={isSubmittingMap || isSubmittingSchema}
                    style={{
                      flex: 1,
                      padding: '6px 8px',
                      borderRadius: 6,
                      border: '1px solid var(--border-main)',
                      background: 'var(--bg-input)',
                      color: 'var(--text-primary)',
                      fontSize: 12,
                    }}
                  />

                  <button
                    type="button"
                    id={`btn-apply-schema-${key}`}
                    onClick={() => handleApplySchema(blocker, idx)}
                    disabled={!schemaInputs[key]?.trim() || isSubmittingMap || isSubmittingSchema}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '6px 12px',
                      borderRadius: 6,
                      border: 'none',
                      background: 'var(--accent-blue)',
                      color: '#fff',
                      fontSize: 11,
                      fontWeight: 600,
                      cursor: schemaInputs[key]?.trim() && !isSubmittingSchema ? 'pointer' : 'not-allowed',
                      opacity: schemaInputs[key]?.trim() && !isSubmittingSchema ? 1 : 0.6,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {isSubmittingSchema ? <Loader2 size={12} style={{ animation: 'spin 1s linear infinite' }} /> : <ArrowRight size={12} />}
                    Set & Re-run
                  </button>
                </div>
              </div>
            </div>
          </div>
        );
      })}

      {/* ── Standard Target Tables Summary ── */}
      <div style={{ ...box, borderLeft: `3px solid ${accent}` }}>
        <div style={{ fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
          Target tables{' '}
          <span style={{ color: accent, fontWeight: 600 }}>
            {bound} bound to existing · {toCreate.length} to be created
            {blockers.length > 0 && ` · ${blockers.length} blocked`}
          </span>
        </div>
        {toCreate.length > 0 && (
          <div style={{ marginTop: 4 }}>
            <div style={{ color: 'var(--text-primary)' }}>Empty tables this sync will create (no existing table matches):</div>
            {toCreate.map((t) => (
              <div key={`c-${t.dataset}`} style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
                {t.dataset} → {t.table}
              </div>
            ))}
          </div>
        )}
        {ties.map((t) => (
          <div key={`t-${t.dataset}`} style={{ marginTop: 4 }}>
            <div style={{ color: 'var(--color-warning)' }}>
              Tie for {t.dataset}: bound to {t.table}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11, paddingLeft: 14 }}>
              {t.tie.map((c) => `${c.table} (${rows(c.row_count)})`).join(' · ')}
            </div>
          </div>
        ))}
        {tables
          .filter((t) => t.action === 'bind' && t.matched_by === 'name')
          .map((t) => (
            <div key={`n-${t.dataset}`} style={{ marginTop: 4 }}>
              {t.dataset} → {t.table} (matched by name only; columns differ)
            </div>
          ))}
      </div>
    </div>
  );
}
