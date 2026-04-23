import { useState, useEffect, useCallback } from 'react';
import { KeyRound, Plus, Trash2, RefreshCw, Eye, EyeOff, ShieldCheck, AlertCircle } from 'lucide-react';
import Modal from './common/Modal';
import { api } from '../utils/api';

function formatRelative(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

function toUpperSnake(raw) {
  return raw.trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

// ─── Add / Edit modal ────────────────────────────────────────────────────────

function SecretModal({ open, onClose, onSaved, editKey = '' }) {
  const [key, setKey] = useState(editKey);
  const [value, setValue] = useState('');
  const [showValue, setShowValue] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (open) {
      setKey(editKey);
      setValue('');
      setShowValue(false);
      setError('');
    }
  }, [open, editKey]);

  const normalizedPreview = toUpperSnake(key);

  const handleSubmit = async () => {
    if (!key.trim()) { setError('Key name is required.'); return; }
    if (!normalizedPreview) { setError('Key must contain at least one letter or digit.'); return; }
    if (!value.trim()) { setError('Secret value is required.'); return; }

    setSubmitting(true);
    setError('');
    try {
      const result = await api.saveSecret({ key: key.trim(), value: value.trim() });
      onSaved(result);
      onClose();
    } catch (err) {
      setError(err?.message || 'Failed to save secret.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editKey ? 'Update Secret' : 'Add API Secret'}
      size="md"
      footer={(
        <>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontSize: 13 }}
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            style={{ background: 'var(--accent-blue)', border: 'none', color: '#fff', borderRadius: 8, padding: '8px 14px', cursor: submitting ? 'not-allowed' : 'pointer', opacity: submitting ? 0.7 : 1, fontSize: 13, fontWeight: 600 }}
          >
            {submitting ? 'Saving…' : editKey ? 'Update Secret' : 'Save Secret'}
          </button>
        </>
      )}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {/* Key name */}
        <div>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
            Key Name
          </label>
          <input
            value={key}
            onChange={e => setKey(e.target.value)}
            onBlur={e => setKey(toUpperSnake(e.target.value) || e.target.value)}
            placeholder="GROQ_API_KEY"
            disabled={!!editKey}
            style={{
              width: '100%', padding: '10px 12px', borderRadius: 8,
              border: '1px solid var(--border-main)',
              background: editKey ? 'var(--bg-surface-raised)' : 'var(--bg-input)',
              color: 'var(--text-primary)', outline: 'none',
              fontFamily: 'monospace', fontSize: 13,
              boxSizing: 'border-box',
            }}
          />
          {key && !editKey && normalizedPreview !== key.trim().toUpperCase() && (
            <p style={{ margin: '5px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>
              Will be saved as: <strong style={{ color: 'var(--accent-blue)', fontFamily: 'monospace' }}>{normalizedPreview}</strong>
            </p>
          )}
          {!editKey && (
            <p style={{ margin: '5px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>
              Auto-normalized to UPPER_SNAKE_CASE. Used directly as an environment variable.
            </p>
          )}
        </div>

        {/* Value */}
        <div>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
            Secret Value
            {editKey && <span style={{ fontWeight: 400, marginLeft: 6, color: 'var(--text-tertiary)' }}>— re-enter to update</span>}
          </label>
          <div style={{ position: 'relative' }}>
            <input
              type={showValue ? 'text' : 'password'}
              value={value}
              onChange={e => setValue(e.target.value)}
              placeholder={editKey ? 'Enter new value to update…' : 'sk-…'}
              style={{
                width: '100%', padding: '10px 40px 10px 12px', borderRadius: 8,
                border: '1px solid var(--border-main)', background: 'var(--bg-input)',
                color: 'var(--text-primary)', outline: 'none',
                fontFamily: 'monospace', fontSize: 13, boxSizing: 'border-box',
              }}
            />
            <button
              type="button"
              onClick={() => setShowValue(v => !v)}
              style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--text-tertiary)', display: 'flex' }}
            >
              {showValue ? <EyeOff size={14} /> : <Eye size={14} />}
            </button>
          </div>
        </div>

        {/* Security notice */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 12px', borderRadius: 8, background: 'var(--color-accent-faint)', border: '1px solid var(--accent-blue)20' }}>
          <ShieldCheck size={14} color="var(--accent-blue)" style={{ flexShrink: 0, marginTop: 1 }} />
          <span style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            Values are stored securely and <strong>never displayed again</strong> after saving. Only a masked hint will be shown.
          </span>
        </div>

        {error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', border: '1px solid var(--color-error)30', color: 'var(--color-error)', fontSize: 12 }}>
            <AlertCircle size={13} />
            {error}
          </div>
        )}
      </div>
    </Modal>
  );
}

// ─── Main SecretsPanel ───────────────────────────────────────────────────────

export default function SecretsPanel() {
  const [secrets, setSecrets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editKey, setEditKey] = useState('');
  const [deletingKey, setDeletingKey] = useState(null);   // key pending confirm
  const [deletingInFlight, setDeletingInFlight] = useState(null); // key being deleted

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.listSecrets();
      setSecrets(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err?.message || 'Failed to load secrets.');
      setSecrets([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const openAdd = () => { setEditKey(''); setModalOpen(true); };
  const openEdit = (key) => { setEditKey(key); setModalOpen(true); };

  const handleSaved = (result) => {
    setSecrets(prev => {
      const existing = prev.find(s => s.key === result.key);
      if (existing) return prev.map(s => s.key === result.key ? result : s);
      return [...prev, result].sort((a, b) => a.key.localeCompare(b.key));
    });
  };

  const handleDelete = async (key) => {
    setDeletingInFlight(key);
    setDeletingKey(null);
    try {
      await api.deleteSecret(key);
      setSecrets(prev => prev.filter(s => s.key !== key));
    } catch (err) {
      setError(err?.message || `Failed to delete '${key}'.`);
    } finally {
      setDeletingInFlight(null);
    }
  };

  return (
    <div style={{ marginBottom: 32 }}>
      {/* Section header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>API Secrets</h2>
          <p style={{ fontSize: 12, margin: '4px 0 0', color: 'var(--text-secondary)' }}>
            Store API keys for LLM providers and third-party integrations. Values are encrypted at rest and never exposed after saving.
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            onClick={refresh}
            disabled={loading}
            title="Refresh"
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--accent-blue)', display: 'flex', alignItems: 'center' }}
          >
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={openAdd}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              background: 'var(--accent-blue)', color: '#fff',
              border: 'none', borderRadius: 8, padding: '7px 12px',
              fontSize: 12, fontWeight: 600, cursor: 'pointer',
            }}
          >
            <Plus size={13} />
            Add Secret
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', border: '1px solid var(--color-error)30', color: 'var(--color-error)', fontSize: 12 }}>
          <AlertCircle size={13} />
          {error}
        </div>
      )}

      {/* Secrets table */}
      <div style={{ border: '1px solid var(--border-main)', borderRadius: 12, overflow: 'hidden', background: 'var(--bg-surface)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'var(--bg-surface-raised)' }}>
              <th style={{ textAlign: 'left', padding: '11px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700 }}>Key</th>
              <th style={{ textAlign: 'left', padding: '11px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700 }}>Value</th>
              <th style={{ textAlign: 'left', padding: '11px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700 }}>Last Updated</th>
              <th style={{ textAlign: 'right', padding: '11px 16px', fontSize: 11, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700 }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan="4" style={{ padding: '20px 16px', color: 'var(--text-tertiary)', fontSize: 12 }}>Loading secrets…</td>
              </tr>
            ) : secrets.length === 0 ? (
              <tr>
                <td colSpan="4" style={{ padding: '28px 16px', textAlign: 'center' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
                    <KeyRound size={28} color="var(--text-tertiary)" />
                    <span style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>No secrets stored yet.</span>
                    <button
                      onClick={openAdd}
                      style={{ background: 'none', border: 'none', color: 'var(--accent-blue)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 0 }}
                    >
                      Add your first secret →
                    </button>
                  </div>
                </td>
              </tr>
            ) : (
              secrets.map(secret => (
                <tr key={secret.key} style={{ borderTop: '1px solid var(--border-main)' }}>
                  {/* Key */}
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <KeyRound size={13} color="var(--text-tertiary)" />
                      <span style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                        {secret.key}
                      </span>
                    </div>
                  </td>

                  {/* Hint */}
                  <td style={{ padding: '12px 16px' }}>
                    <span style={{
                      fontFamily: 'monospace', fontSize: 12,
                      color: 'var(--text-secondary)',
                      background: 'var(--bg-surface-raised)',
                      padding: '3px 8px', borderRadius: 5,
                      border: '1px solid var(--border-light)',
                      letterSpacing: 2,
                    }}>
                      {secret.hint}
                    </span>
                  </td>

                  {/* Updated */}
                  <td style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-tertiary)' }}>
                    {formatRelative(secret.updated_at)}
                  </td>

                  {/* Actions */}
                  <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                    {deletingKey === secret.key ? (
                      // Inline confirm state
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Delete?</span>
                        <button
                          onClick={() => handleDelete(secret.key)}
                          disabled={deletingInFlight === secret.key}
                          style={{ fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6, background: 'var(--color-error)', color: '#fff', border: 'none', cursor: 'pointer' }}
                        >
                          {deletingInFlight === secret.key ? '…' : 'Yes, delete'}
                        </button>
                        <button
                          onClick={() => setDeletingKey(null)}
                          style={{ fontSize: 11, padding: '4px 10px', borderRadius: 6, background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-main)', cursor: 'pointer' }}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <button
                          onClick={() => openEdit(secret.key)}
                          title="Update value"
                          style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'transparent', color: 'var(--text-secondary)', fontSize: 12, cursor: 'pointer', fontWeight: 500 }}
                        >
                          Update
                        </button>
                        <button
                          onClick={() => setDeletingKey(secret.key)}
                          title="Delete secret"
                          style={{ padding: 5, borderRadius: 6, border: 'none', background: 'transparent', color: 'var(--color-error)', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Add / Edit modal */}
      <SecretModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSaved={handleSaved}
        editKey={editKey}
      />
    </div>
  );
}
