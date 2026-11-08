import { useEffect, useMemo, useState } from 'react';
import { Loader2, Plus, RotateCcw, X } from 'lucide-react';
import Modal from './common/Modal';
import { api } from '../utils/api';

const buttonBase = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
  padding: '8px 12px',
  borderRadius: 8,
  fontSize: 12,
  fontWeight: 700,
  cursor: 'pointer',
};

function normalizeSynonyms(values) {
  const result = [];
  const seen = new Set();
  (values || []).forEach((value) => {
    const text = String(value || '').trim();
    const key = text.toLowerCase();
    if (text && !seen.has(key)) {
      seen.add(key);
      result.push(text);
    }
  });
  return result;
}

export default function SynonymEditModal({
  open,
  onClose,
  projectId,
  modelName,
  tableName,
  columnName,
  currentSynonyms = [],
  currentDescription = '',
  currentRules = [],
  currentSteward = '',
  onSave,
}) {
  const [synonyms, setSynonyms] = useState([]);
  const [description, setDescription] = useState('');
  const [businessRules, setBusinessRules] = useState([]);
  const [ruleInput, setRuleInput] = useState('');
  const [dataSteward, setDataSteward] = useState('');
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const identity = useMemo(() => ({
    projectId,
    modelName,
    tableName,
    columnName,
  }), [projectId, modelName, tableName, columnName]);

  useEffect(() => {
    if (!open) return;
    setSynonyms(normalizeSynonyms(currentSynonyms));
    setDescription(currentDescription || '');
    setBusinessRules(Array.isArray(currentRules) ? currentRules : []);
    setDataSteward(currentSteward || '');
    setInputValue('');
    setRuleInput('');
    setError('');

    // Fetch live backend override values if available
    if (projectId && modelName && tableName && columnName) {
      api.getSynonymOverride(identity)
        .then((res) => {
          if (res) {
            if (Array.isArray(res.synonyms) && res.synonyms.length > 0) {
              setSynonyms(normalizeSynonyms(res.synonyms));
            }
            if (res.description) setDescription(res.description);
            if (Array.isArray(res.business_rules) && res.business_rules.length > 0) {
              setBusinessRules(res.business_rules);
            }
            if (res.data_steward) setDataSteward(res.data_steward);
          }
        })
        .catch(() => {});
    }
  }, [open, currentSynonyms, currentDescription, currentRules, currentSteward, identity]);

  function addSynonym(raw = inputValue) {
    const parts = String(raw || '')
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean);
    if (!parts.length) return;
    setSynonyms((prev) => normalizeSynonyms([...prev, ...parts]));
    setInputValue('');
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      addSynonym();
    }
  }

  function removeSynonym(value) {
    setSynonyms((prev) => prev.filter((item) => item !== value));
  }

  function addRule() {
    const text = String(ruleInput || '').trim();
    if (!text) return;
    if (!businessRules.includes(text)) {
      setBusinessRules((prev) => [...prev, text]);
    }
    setRuleInput('');
  }

  function removeRule(index) {
    setBusinessRules((prev) => prev.filter((_, i) => i !== index));
  }

  async function save() {
    setLoading(true);
    setError('');
    try {
      const response = await api.saveSynonymOverride({
        project_id: projectId,
        model_name: modelName,
        table_name: tableName,
        column_name: columnName,
        synonyms,
        // '' (not null) so clearing the field actually clears it server-side.
        description: description.trim(),
        business_rules: businessRules,
        data_steward: dataSteward.trim(),
      });
      onSave?.(response?.synonyms || synonyms, {
        description: response?.description || description,
        business_rules: response?.business_rules || businessRules,
        data_steward: response?.data_steward || dataSteward,
      });
      onClose?.();
    } catch (err) {
      setError(err?.message || 'Failed to save ontology metadata.');
    } finally {
      setLoading(false);
    }
  }

  async function reset() {
    setLoading(true);
    setError('');
    try {
      await api.deleteSynonymOverride(identity);
      onSave?.([], { description: '', business_rules: [], data_steward: '' });
      onClose?.();
    } catch (err) {
      setError(err?.message || 'Failed to reset metadata.');
    } finally {
      setLoading(false);
    }
  }

  const footer = (
    <>
      <button
        type="button"
        onClick={onClose}
        disabled={loading}
        style={{ ...buttonBase, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', color: 'var(--text-secondary)' }}
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={reset}
        disabled={loading}
        style={{ ...buttonBase, border: '1px solid rgba(239, 68, 68, 0.45)', background: 'rgba(239, 68, 68, 0.10)', color: 'var(--color-error)' }}
      >
        <RotateCcw size={13} />
        Reset
      </button>
      <button
        type="button"
        onClick={save}
        disabled={loading}
        style={{ ...buttonBase, border: '1px solid var(--accent-blue)', background: 'var(--accent-blue)', color: '#fff' }}
      >
        {loading && <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />}
        Save Ontology
      </button>
    </>
  );

  return (
    <Modal
      open={open}
      onClose={loading ? undefined : onClose}
      title={`Ontology & Business Semantics - ${columnName || tableName || 'Field'}`}
      size="lg"
      footer={footer}
    >
      <div style={{ display: 'grid', gap: 16 }}>
        {/* Business Definition / Description */}
        <div style={{ display: 'grid', gap: 6 }}>
          <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
            Official Business Definition (Description)
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            placeholder="Official enterprise meaning of this metric/entity (e.g. Recognized revenue net of returns)..."
            style={{
              width: '100%',
              boxSizing: 'border-box',
              border: '1px solid var(--border-main)',
              borderRadius: 8,
              background: 'var(--bg-input)',
              color: 'var(--text-primary)',
              padding: '8px 10px',
              fontSize: 12,
              fontFamily: 'inherit',
              resize: 'vertical',
            }}
          />
        </div>

        {/* Synonyms */}
        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
            Synonyms & Preferred Labels (AI Search Terms)
          </div>
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            minHeight: 40,
            padding: 8,
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-input)',
          }}>
            {synonyms.length === 0 && (
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>No synonyms defined</span>
            )}
            {synonyms.map((synonym) => (
              <span
                key={synonym}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  maxWidth: '100%',
                  padding: '4px 8px',
                  borderRadius: 999,
                  border: '1px solid rgba(56, 189, 248, 0.35)',
                  background: 'rgba(56, 189, 248, 0.12)',
                  color: '#7dd3fc',
                  fontSize: 12,
                  fontWeight: 700,
                }}
              >
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{synonym}</span>
                <button
                  type="button"
                  onClick={() => removeSynonym(synonym)}
                  title="Remove synonym"
                  style={{ border: 'none', background: 'transparent', color: 'inherit', padding: 0, cursor: 'pointer', display: 'inline-flex' }}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <input
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type synonym, press Enter or comma (e.g. ARR, Topline, Sales)"
              style={{
                flex: 1,
                minWidth: 0,
                border: '1px solid var(--border-main)',
                borderRadius: 8,
                background: 'var(--bg-input)',
                color: 'var(--text-primary)',
                padding: '8px 10px',
                fontSize: 12,
                outline: 'none',
              }}
            />
            <button
              type="button"
              onClick={() => addSynonym()}
              disabled={!inputValue.trim()}
              title="Add synonym"
              style={{ ...buttonBase, width: 36, padding: 0, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', color: 'var(--text-primary)', opacity: inputValue.trim() ? 1 : 0.55 }}
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        {/* Business Rules & Constraints */}
        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
            Business Rules & Constraints (For AI Agent Grounding)
          </div>
          {businessRules.length > 0 && (
            <div style={{ display: 'grid', gap: 4 }}>
              {businessRules.map((rule, idx) => (
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '6px 10px',
                    borderRadius: 6,
                    background: 'rgba(255, 255, 255, 0.03)',
                    border: '1px solid var(--border-main)',
                    fontSize: 12,
                  }}
                >
                  <span style={{ color: 'var(--text-secondary)' }}>• {rule}</span>
                  <button
                    type="button"
                    onClick={() => removeRule(idx)}
                    title="Remove rule"
                    style={{ border: 'none', background: 'transparent', color: 'var(--text-tertiary)', cursor: 'pointer' }}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
            <input
              value={ruleInput}
              onChange={(e) => setRuleInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addRule(); } }}
              placeholder="e.g. Must exclude internal test accounts, Currency normalized to USD"
              style={{
                flex: 1,
                minWidth: 0,
                border: '1px solid var(--border-main)',
                borderRadius: 8,
                background: 'var(--bg-input)',
                color: 'var(--text-primary)',
                padding: '8px 10px',
                fontSize: 12,
                outline: 'none',
              }}
            />
            <button
              type="button"
              onClick={addRule}
              disabled={!ruleInput.trim()}
              title="Add rule"
              style={{ ...buttonBase, width: 36, padding: 0, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', color: 'var(--text-primary)', opacity: ruleInput.trim() ? 1 : 0.55 }}
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        {/* Data Steward / Business Owner */}
        <div style={{ display: 'grid', gap: 6 }}>
          <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
            Data Steward / Owner
          </label>
          <input
            value={dataSteward}
            onChange={(e) => setDataSteward(e.target.value)}
            placeholder="e.g. Commercial Finance Team (finance-ops@company.com)"
            style={{
              border: '1px solid var(--border-main)',
              borderRadius: 8,
              background: 'var(--bg-input)',
              color: 'var(--text-primary)',
              padding: '8px 10px',
              fontSize: 12,
              outline: 'none',
            }}
          />
        </div>

        {error && (
          <div style={{ border: '1px solid rgba(239, 68, 68, 0.45)', background: 'rgba(239, 68, 68, 0.10)', color: 'var(--color-error)', borderRadius: 8, padding: '8px 10px', fontSize: 12 }}>
            {error}
          </div>
        )}
      </div>
    </Modal>
  );
}
