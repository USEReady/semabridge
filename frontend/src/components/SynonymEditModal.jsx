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
  onSave,
}) {
  const [synonyms, setSynonyms] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    setSynonyms(normalizeSynonyms(currentSynonyms));
    setInputValue('');
    setError('');
  }, [open, currentSynonyms]);

  const identity = useMemo(() => ({
    projectId,
    modelName,
    tableName,
    columnName,
  }), [projectId, modelName, tableName, columnName]);

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
      });
      onSave?.(response?.synonyms || synonyms);
      onClose?.();
    } catch (err) {
      setError(err?.message || 'Failed to save synonyms.');
    } finally {
      setLoading(false);
    }
  }

  async function reset() {
    setLoading(true);
    setError('');
    try {
      await api.deleteSynonymOverride(identity);
      onSave?.([]);
      onClose?.();
    } catch (err) {
      setError(err?.message || 'Failed to reset synonyms.');
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
        Save
      </button>
    </>
  );

  return (
    <Modal
      open={open}
      onClose={loading ? undefined : onClose}
      title={`Edit Synonyms - ${columnName || 'Field'}`}
      size="md"
      footer={footer}
    >
      <div style={{ display: 'grid', gap: 14 }}>
        <div style={{ display: 'grid', gap: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase' }}>
            Override Synonyms
          </div>
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            minHeight: 42,
            padding: 8,
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-input)',
          }}>
            {synonyms.length === 0 && (
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>No overrides yet</span>
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
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type synonym, press Enter or comma"
            style={{
              flex: 1,
              minWidth: 0,
              border: '1px solid var(--border-main)',
              borderRadius: 8,
              background: 'var(--bg-input)',
              color: 'var(--text-primary)',
              padding: '8px 10px',
              fontSize: 13,
              outline: 'none',
            }}
          />
          <button
            type="button"
            onClick={() => addSynonym()}
            disabled={!inputValue.trim()}
            title="Add synonym"
            style={{ ...buttonBase, width: 38, padding: 0, border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)', color: 'var(--text-primary)', opacity: inputValue.trim() ? 1 : 0.55 }}
          >
            <Plus size={14} />
          </button>
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
