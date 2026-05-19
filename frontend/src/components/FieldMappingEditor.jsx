/**
 * FieldMappingEditor — modal for editing a single field mapping's target name
 * and data type.
 *
 * Props:
 *   row            : NormalizedRow | null  — the row being edited
 *   onSave         : (rowId: string, { target_name, target_data_type }) => void
 *   onClose        : () => void
 *   targetPlatform : string  — e.g. "snowflake", "fabric", "databricks"
 *   isSaving       : boolean — shows loading/disabled state while saving
 */
import { useState, useEffect } from 'react';
import { AlertTriangle, CheckCircle, Loader2 } from 'lucide-react';
import Modal from './common/Modal';
import { validateTargetName } from '../utils/validateTargetName';

// Common data type options used across the project
const DATA_TYPE_OPTIONS = [
  'VARCHAR',
  'TEXT',
  'NUMBER',
  'INTEGER',
  'BIGINT',
  'FLOAT',
  'DECIMAL',
  'BOOLEAN',
  'DATE',
  'TIMESTAMP',
  'TIMESTAMP_NTZ',
  'VARIANT',
  'OBJECT',
  'ARRAY',
];

const LABEL = {
  display: 'block',
  fontSize: 12,
  fontWeight: 600,
  color: 'var(--text-secondary)',
  marginBottom: 6,
};

const INPUT = {
  display: 'block',
  width: '100%',
  background: 'var(--bg-input)',
  border: '1px solid var(--border-main)',
  borderRadius: 8,
  color: 'var(--text-primary)',
  padding: '8px 12px',
  fontSize: 13,
  outline: 'none',
  fontFamily: 'inherit',
  boxSizing: 'border-box',
};

const INPUT_READONLY = {
  ...INPUT,
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-secondary)',
  cursor: 'default',
};

export default function FieldMappingEditor({
  row,
  onSave,
  onClose,
  targetPlatform = '',
  isSaving = false,
}) {
  const [targetName, setTargetName] = useState('');
  const [targetDataType, setTargetDataType] = useState('');
  const [synonyms, setSynonyms] = useState('');
  const [validationResult, setValidationResult] = useState(null);

  // Initialise controlled fields whenever the row changes
  useEffect(() => {
    if (!row) return;
    setTargetName(String(row.target_field || ''));
    setTargetDataType(String(row.target_type || ''));
    setSynonyms(Array.isArray(row.synonyms) ? row.synonyms.join(', ') : '');
    setValidationResult(null);
  }, [row]);

  // ── Handlers ────────────────────────────────────────────────────────────────

  function handleTargetNameChange(e) {
    const value = e.target.value;
    setTargetName(value);
    const result = validateTargetName(
      value,
      targetPlatform,
      row?.source_type || '',
      targetDataType,
    );
    setValidationResult(result);
  }

  function handleTargetDataTypeChange(e) {
    setTargetDataType(e.target.value);
    // Re-validate target name against the new data type
    if (targetName) {
      const result = validateTargetName(
        targetName,
        targetPlatform,
        row?.source_type || '',
        e.target.value,
      );
      setValidationResult(result);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    // Run validation on submit in case the user hasn't typed yet
    const result = validateTargetName(
      targetName,
      targetPlatform,
      row?.source_type || '',
      targetDataType,
    );
    setValidationResult(result);
    if (!result.isValid) return; // keep modal open, error already shown
    const synonymList = synonyms.split(',').map(s => s.trim()).filter(Boolean);
    onSave?.(row.id, { 
      target_name: targetName, 
      target_data_type: targetDataType,
      synonyms: synonymList
    });
  }

  function handleApplySuggestion() {
    if (!validationResult?.suggestion) return;
    const suggested = validationResult.suggestion;
    setTargetName(suggested);
    const result = validateTargetName(
      suggested,
      targetPlatform,
      row?.source_type || '',
      targetDataType,
    );
    setValidationResult(result);
  }

  // ── Derived state ────────────────────────────────────────────────────────────
  const isInvalid = validationResult !== null && !validationResult.isValid;
  const isValid   = validationResult !== null && validationResult.isValid;
  const submitDisabled = isSaving || isInvalid;

  // ── Footer ───────────────────────────────────────────────────────────────────
  const footer = (
    <>
      <button
        type="button"
        onClick={onClose}
        disabled={isSaving}
        style={{
          padding: '8px 16px',
          borderRadius: 8,
          border: '1px solid var(--border-main)',
          background: 'var(--bg-surface-raised)',
          color: 'var(--text-secondary)',
          fontSize: 13,
          fontWeight: 600,
          cursor: isSaving ? 'not-allowed' : 'pointer',
          opacity: isSaving ? 0.6 : 1,
        }}
      >
        Cancel
      </button>
      <button
        type="submit"
        form="field-mapping-editor-form"
        disabled={submitDisabled}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '8px 16px',
          borderRadius: 8,
          border: '1px solid var(--accent-blue)',
          background: 'var(--accent-blue)',
          color: '#fff',
          fontSize: 13,
          fontWeight: 700,
          cursor: submitDisabled ? 'not-allowed' : 'pointer',
          opacity: submitDisabled ? 0.55 : 1,
        }}
      >
        {isSaving && <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />}
        {isSaving ? 'Saving…' : 'Save'}
      </button>
    </>
  );

  return (
    <Modal
      open={Boolean(row)}
      onClose={isSaving ? undefined : onClose}
      title="Edit Field Mapping"
      size="md"
      footer={footer}
    >
      <form id="field-mapping-editor-form" onSubmit={handleSubmit}>
        {/* ── Read-only source info ─────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginBottom: 24 }}>
          <div style={{
            padding: '12px 14px',
            borderRadius: 8,
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--border-main)',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}>
            <div style={{
              fontSize: 11,
              fontWeight: 700,
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 2,
            }}>
              Source (read-only)
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
              {/* source_name */}
              <div>
                <label style={LABEL}>Source Name</label>
                <input
                  readOnly
                  tabIndex={-1}
                  value={String(row?.source_field || '')}
                  style={INPUT_READONLY}
                  aria-label="Source name (read-only)"
                />
              </div>

              {/* source_table */}
              <div>
                <label style={LABEL}>Source Table</label>
                <input
                  readOnly
                  tabIndex={-1}
                  value={String(row?.source_table_name || '—')}
                  style={INPUT_READONLY}
                  aria-label="Source table (read-only)"
                />
              </div>

              {/* source_type */}
              <div>
                <label style={LABEL}>Source Type</label>
                <input
                  readOnly
                  tabIndex={-1}
                  value={String(row?.source_type || '—')}
                  style={INPUT_READONLY}
                  aria-label="Source type (read-only)"
                />
              </div>
            </div>
          </div>
        </div>

        {/* ── Editable target fields ────────────────────────────────────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* target_name */}
          <div>
            <label htmlFor="fme-target-name" style={LABEL}>
              Target Name <span style={{ color: 'var(--color-error)' }}>*</span>
            </label>
            <input
              id="fme-target-name"
              type="text"
              value={targetName}
              onChange={handleTargetNameChange}
              disabled={isSaving}
              autoFocus
              style={{
                ...INPUT,
                borderColor: isInvalid
                  ? 'var(--color-error)'
                  : isValid
                    ? 'var(--color-success)'
                    : 'var(--border-main)',
                opacity: isSaving ? 0.6 : 1,
              }}
              placeholder="e.g. CUSTOMER_ID"
              aria-describedby={isInvalid ? 'fme-target-name-error' : undefined}
              aria-invalid={isInvalid}
            />

            {/* Inline validation feedback */}
            {isInvalid && (
              <div
                id="fme-target-name-error"
                style={{
                  marginTop: 6,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                }}
              >
                <div style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 6,
                  fontSize: 12,
                  color: 'var(--color-error)',
                }}>
                  <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
                  <span>{validationResult.message}</span>
                </div>
                {validationResult.suggestion && (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    fontSize: 12,
                    color: 'var(--text-secondary)',
                  }}>
                    <span>Suggestion:</span>
                    <button
                      type="button"
                      onClick={handleApplySuggestion}
                      style={{
                        fontFamily: 'monospace',
                        fontSize: 11,
                        fontWeight: 700,
                        padding: '2px 8px',
                        borderRadius: 4,
                        border: '1px solid var(--border-main)',
                        background: 'var(--bg-surface-raised)',
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                      }}
                    >
                      {validationResult.suggestion}
                    </button>
                    <span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>
                      (click to apply)
                    </span>
                  </div>
                )}
              </div>
            )}

            {isValid && (
              <div style={{
                marginTop: 6,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 12,
                color: 'var(--color-success)',
              }}>
                <CheckCircle size={13} />
                <span>Looks good</span>
              </div>
            )}
          </div>

          {/* target_data_type */}
          <div>
            <label htmlFor="fme-target-data-type" style={LABEL}>
              Target Data Type
            </label>
            {/* Render as a select with common types; user can also type a custom value */}
            <select
              id="fme-target-data-type"
              value={targetDataType}
              onChange={handleTargetDataTypeChange}
              disabled={isSaving}
              style={{
                ...INPUT,
                cursor: isSaving ? 'not-allowed' : 'pointer',
                opacity: isSaving ? 0.6 : 1,
                appearance: 'none',
                WebkitAppearance: 'none',
                backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E")`,
                backgroundRepeat: 'no-repeat',
                backgroundPosition: 'right 10px center',
                paddingRight: 32,
              }}
            >
              {/* Keep the current value even if it's not in the list */}
              {targetDataType && !DATA_TYPE_OPTIONS.includes(targetDataType.toUpperCase()) && (
                <option value={targetDataType}>{targetDataType}</option>
              )}
              {DATA_TYPE_OPTIONS.map((dt) => (
                <option key={dt} value={dt}>{dt}</option>
              ))}
            </select>
          </div>

          {/* synonyms */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <label htmlFor="fme-synonyms" style={LABEL}>
                Synonyms (comma separated)
              </label>
              <button
                type="button"
                onClick={() => {
                  const semanticMap = {
                    id: ["Identifier", "Key"], cust: ["Customer", "Client"], customer: ["Client", "Purchaser"],
                    acct: ["Account"], amt: ["Amount", "Value"], qty: ["Quantity", "Volume"],
                    quantity: ["Volume", "Count"], num: ["Number"], desc: ["Description", "Detail"],
                    dt: ["Date"], yr: ["Year"], mth: ["Month"], qtr: ["Quarter"], wk: ["Week"],
                    sales: ["Revenue", "Income"], revenue: ["Sales", "Turnover"], count: ["Total Number", "Tally"],
                    total: ["Sum", "Aggregate"], units: ["Volume", "Quantity"], sentiment: ["Feedback", "Opinion", "Rating"],
                    cost: ["Expense", "Expenditure"], price: ["Rate", "Value"], profit: ["Margin", "Gain"],
                    region: ["Area", "Territory"], cat: ["Category"], category: ["Type", "Class", "Grouping"],
                    prod: ["Product"], product: ["Item", "Good", "Merchandise"], ytd: ["Year To Date"],
                    mtd: ["Month To Date"], qtd: ["Quarter To Date"], mfg: ["Manufacturing", "Manufacturer"],
                    manufacturer: ["Producer", "Maker"], vendor: ["Supplier", "Provider"],
                  };
                  let titleForm = (row?.source_field || '').replace(/[^a-zA-Z0-9_\s]/g, ' ').replace(/_/g, ' ').trim();
                  titleForm = titleForm.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/([A-Z])([A-Z][a-z])/g, '$1 $2');
                  
                  let words = titleForm.split(/\s+/);
                  let syns = [];
                  words.forEach(word => {
                    let wLower = word.toLowerCase();
                    if (semanticMap[wLower]) {
                      semanticMap[wLower].forEach(alias => {
                        let expanded = titleForm.replace(new RegExp(`\\b${word}\\b`, 'gi'), alias);
                        if (expanded.toLowerCase() !== (row?.source_field || '').toLowerCase() && !syns.includes(expanded)) {
                          syns.push(expanded);
                        }
                      });
                    }
                  });
                  
                  if (syns.length > 0) {
                    let currentSyns = synonyms ? synonyms.split(',').map(s => s.trim()).filter(Boolean) : [];
                    let merged = [...new Set([...currentSyns, ...syns])].slice(0, 5);
                    setSynonyms(merged.join(', '));
                  }
                }}
                disabled={isSaving}
                style={{
                  background: 'rgba(56, 189, 248, 0.1)',
                  color: '#38bdf8',
                  border: '1px solid rgba(56, 189, 248, 0.2)',
                  borderRadius: 4,
                  fontSize: 10,
                  fontWeight: 600,
                  padding: '2px 8px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4
                }}
              >
                ✨ Auto Generate
              </button>
            </div>
            <input
              id="fme-synonyms"
              type="text"
              value={synonyms}
              onChange={(e) => setSynonyms(e.target.value)}
              disabled={isSaving}
              style={{ ...INPUT, marginTop: 4 }}
              placeholder="e.g. Sales, Total Revenue, turnover"
            />
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
              Add synonyms to help Snowflake Cortex Analyst discover this field.
            </div>
          </div>
        </div>
      </form>
    </Modal>
  );
}
