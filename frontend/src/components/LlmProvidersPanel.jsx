import { useState, useEffect, useCallback } from 'react';
import { KeyRound, Eye, EyeOff, RefreshCw, AlertCircle, Trash2, Loader2, ShieldCheck, Sparkles, Check } from 'lucide-react';
import StatusBadge from './common/StatusBadge';
import { api } from '../utils/api';

const PROVIDER_LABELS = {
  openai: 'OpenAI',
  gemini: 'Gemini',
  groq: 'Groq',
  featherless: 'Featherless',
  anthropic: 'Anthropic',
};

function badgeForSource(source) {
  if (source === 'settings') return { status: 'connected', label: 'Configured via Settings' };
  if (source === 'env') return { status: 'configured', label: 'Configured via .env' };
  return { status: 'disconnected', label: 'Not configured' };
}

// One provider's local, not-yet-saved UI state (key input, discovery
// results, model selection) — kept separate from the server-reported
// `providers` status array so typing/discovering doesn't require
// refetching status, and so a save can update just that provider's
// status entry in place (no page reload, no full refetch needed).
function initialRowState() {
  return {
    keyInput: '',
    showKey: false,
    savingKey: false,
    deletingKey: false,
    saveKeyError: '',

    discovering: false,
    discoverError: '',
    discoveredModels: null, // null = not discovered yet this session
    truncated: false,
    totalAvailable: null,
    modelFilter: '',

    selectedModel: '',
    savingModel: false,
    saveModelError: '',
  };
}

export default function LlmProvidersPanel() {
  const [providers, setProviders] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [rowState, setRowState] = useState({}); // provider -> initialRowState() shape

  const patchRow = (provider, patch) => {
    setRowState(prev => ({ ...prev, [provider]: { ...(prev[provider] || initialRowState()), ...patch } }));
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError('');
    try {
      const data = await api.getLlmProviders();
      const list = Array.isArray(data) ? data : [];
      setProviders(list);
      setRowState(prev => {
        const next = { ...prev };
        for (const p of list) {
          const existing = next[p.provider] || initialRowState();
          next[p.provider] = { ...existing, selectedModel: existing.selectedModel || p.model || '' };
        }
        return next;
      });
    } catch (err) {
      setLoadError(err?.message || 'Failed to load LLM provider configuration.');
      setProviders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const getRow = (provider) => rowState[provider] || initialRowState();

  const updateProviderStatus = (provider, patch) => {
    setProviders(prev => prev.map(p => (p.provider === provider ? { ...p, ...patch } : p)));
  };

  const handleSaveKey = async (provider) => {
    const row = getRow(provider);
    const key = row.keyInput.trim();
    if (!key) {
      patchRow(provider, { saveKeyError: 'API key is required.' });
      return;
    }
    patchRow(provider, { savingKey: true, saveKeyError: '' });
    try {
      await api.saveLlmProviderApiKey(provider, key);
      // Reflect "configured via Settings" immediately — no page reload,
      // no refetch of the whole panel needed. The raw key is deliberately
      // not kept in state; only a locally-derived hint is shown, matching
      // SecretsPanel's "never displayed again" convention.
      updateProviderStatus(provider, { source: 'settings', configured: true, hint: maskHint(key) });
      patchRow(provider, {
        savingKey: false,
        keyInput: '',
        showKey: false,
        // A new key can point at a different account/tier — last
        // discovery results no longer necessarily apply.
        discoveredModels: null,
        discoverError: '',
      });
    } catch (err) {
      patchRow(provider, { savingKey: false, saveKeyError: err?.message || 'Failed to save API key.' });
    }
  };

  const handleDeleteKey = async (provider) => {
    patchRow(provider, { deletingKey: true });
    try {
      await api.deleteLlmProviderApiKey(provider);
      // Whether this reverts to "env" or "none" depends on whether a
      // .env value exists underneath, which this component has no way
      // to know client-side — refetch that provider's real status
      // rather than guessing.
      const fresh = await api.getLlmProviders();
      const freshStatus = Array.isArray(fresh) ? fresh.find(p => p.provider === provider) : null;
      if (freshStatus) {
        setProviders(prev => prev.map(p => (p.provider === provider ? freshStatus : p)));
      }
      patchRow(provider, { deletingKey: false, discoveredModels: null, selectedModel: '', discoverError: '' });
    } catch (err) {
      patchRow(provider, { deletingKey: false, saveKeyError: err?.message || 'Failed to remove key.' });
    }
  };

  const handleDiscover = async (provider) => {
    patchRow(provider, { discovering: true, discoverError: '' });
    try {
      const result = await api.discoverLlmProviderModels(provider);
      patchRow(provider, {
        discovering: false,
        discoveredModels: Array.isArray(result?.models) ? result.models : [],
        truncated: !!result?.truncated,
        totalAvailable: typeof result?.total_available === 'number' ? result.total_available : null,
      });
    } catch (err) {
      // err.message carries the backend's distinct detail text for each
      // case (400 no key anywhere / 401 bad key / 502 network or provider
      // API error) — surfaced verbatim rather than a generic failure.
      patchRow(provider, { discovering: false, discoverError: err?.message || 'Model discovery failed.' });
    }
  };

  const handleSaveModel = async (provider) => {
    const row = getRow(provider);
    if (!row.selectedModel) return;
    patchRow(provider, { savingModel: true, saveModelError: '' });
    try {
      await api.saveLlmProviderModel(provider, row.selectedModel);
      updateProviderStatus(provider, { model: row.selectedModel });
      patchRow(provider, { savingModel: false });
    } catch (err) {
      patchRow(provider, { savingModel: false, saveModelError: err?.message || 'Failed to save model.' });
    }
  };

  return (
    <div style={{ marginBottom: 32 }}>
      {/* Section header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>LLM Providers</h2>
          <p style={{ fontSize: 12, margin: '4px 0 0', color: 'var(--text-secondary)' }}>
            Configure API keys and models for Tier 5 DAX-to-SQL translation. A key saved here takes priority
            over a matching key in <code style={{ fontFamily: 'monospace' }}>.env</code> for that provider;
            providers left unconfigured here keep working from <code style={{ fontFamily: 'monospace' }}>.env</code> if set there.
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          title="Refresh"
          style={{ background: 'none', border: 'none', cursor: loading ? 'default' : 'pointer', padding: 4, color: 'var(--accent-blue)', display: 'flex', alignItems: 'center' }}
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {loadError && (
        <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, padding: '10px 12px', borderRadius: 8, background: 'var(--color-error-bg)', border: '1px solid var(--color-error)30', color: 'var(--color-error)', fontSize: 12 }}>
          <AlertCircle size={13} />
          {loadError}
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {loading && providers.length === 0 ? (
          <div style={{ padding: '20px 16px', color: 'var(--text-tertiary)', fontSize: 12, border: '1px solid var(--border-main)', borderRadius: 12, background: 'var(--bg-surface)' }}>
            Loading provider configuration…
          </div>
        ) : (
          Object.keys(PROVIDER_LABELS).map((provider) => {
            const status = providers.find(p => p.provider === provider) || { provider, source: 'none', configured: false, model: null, hint: null };
            const row = getRow(provider);
            const badge = badgeForSource(status.source);
            const modelOptions = row.discoveredModels ?? (status.model ? [status.model] : []);
            const filteredOptions = row.modelFilter
              ? modelOptions.filter(m => m.toLowerCase().includes(row.modelFilter.toLowerCase()))
              : modelOptions;

            return (
              <div
                key={provider}
                style={{ border: '1px solid var(--border-main)', borderRadius: 12, background: 'var(--bg-surface)', padding: 16 }}
              >
                {/* Row header: name + status */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <KeyRound size={14} color="var(--text-tertiary)" />
                    <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{PROVIDER_LABELS[provider]}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <StatusBadge status={badge.status} label={badge.label} size="sm" />
                    {status.hint && (
                      <span style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-secondary)', background: 'var(--bg-surface-raised)', padding: '3px 8px', borderRadius: 5, border: '1px solid var(--border-light)', letterSpacing: 1 }}>
                        {status.hint}
                      </span>
                    )}
                    {status.source === 'settings' && (
                      <button
                        onClick={() => handleDeleteKey(provider)}
                        disabled={row.deletingKey}
                        title="Remove Settings key (revert to .env, if any)"
                        style={{ padding: 5, borderRadius: 6, border: 'none', background: 'transparent', color: 'var(--color-error)', cursor: row.deletingKey ? 'default' : 'pointer', display: 'flex', alignItems: 'center' }}
                      >
                        {row.deletingKey ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                      </button>
                    )}
                  </div>
                </div>

                {/* API key input */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: row.saveKeyError ? 6 : 0 }}>
                  <div style={{ position: 'relative', flex: 1 }}>
                    <input
                      type={row.showKey ? 'text' : 'password'}
                      value={row.keyInput}
                      onChange={e => patchRow(provider, { keyInput: e.target.value, saveKeyError: '' })}
                      placeholder={status.source === 'settings' ? 'Enter a new key to replace the saved one…' : 'sk-…'}
                      style={{
                        width: '100%', padding: '9px 40px 9px 12px', borderRadius: 8,
                        border: '1px solid var(--border-main)', background: 'var(--bg-input)',
                        color: 'var(--text-primary)', outline: 'none',
                        fontFamily: 'monospace', fontSize: 13, boxSizing: 'border-box',
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => patchRow(provider, { showKey: !row.showKey })}
                      style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--text-tertiary)', display: 'flex' }}
                    >
                      {row.showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                  <button
                    onClick={() => handleSaveKey(provider)}
                    disabled={row.savingKey || !row.keyInput.trim()}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      background: 'var(--accent-blue)', color: '#fff', border: 'none', borderRadius: 8,
                      padding: '9px 14px', fontSize: 12, fontWeight: 600,
                      cursor: (row.savingKey || !row.keyInput.trim()) ? 'not-allowed' : 'pointer',
                      opacity: (row.savingKey || !row.keyInput.trim()) ? 0.6 : 1,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {row.savingKey ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                    Save Key
                  </button>
                </div>
                {row.saveKeyError && (
                  <div style={{ marginTop: 6, display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--color-error)' }}>
                    <AlertCircle size={12} />
                    {row.saveKeyError}
                  </div>
                )}

                {/* Discover models + model selector */}
                <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border-light)' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <button
                      onClick={() => handleDiscover(provider)}
                      disabled={row.discovering || status.source === 'none'}
                      title={status.source === 'none' ? 'Save an API key first' : 'Fetch the real, currently-available models for this key'}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        background: 'transparent', border: '1.5px solid var(--accent-blue)',
                        color: 'var(--accent-blue)', borderRadius: 8, padding: '8px 12px',
                        fontSize: 12, fontWeight: 600,
                        cursor: (row.discovering || status.source === 'none') ? 'not-allowed' : 'pointer',
                        opacity: (row.discovering || status.source === 'none') ? 0.5 : 1,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {row.discovering ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                      {row.discovering ? 'Discovering…' : 'Discover Models'}
                    </button>

                    <select
                      value={row.selectedModel}
                      onChange={e => patchRow(provider, { selectedModel: e.target.value, saveModelError: '' })}
                      disabled={filteredOptions.length === 0}
                      style={{
                        flex: 1, minWidth: 200, padding: '8px 10px', borderRadius: 8,
                        border: '1px solid var(--border-main)', background: 'var(--bg-input)',
                        color: 'var(--text-primary)', fontSize: 12, fontFamily: 'monospace',
                      }}
                    >
                      {filteredOptions.length === 0 ? (
                        <option value="">
                          {row.discoveredModels === null ? 'No model selected — click Discover Models' : 'No models match your search'}
                        </option>
                      ) : (
                        filteredOptions.map(m => <option key={m} value={m}>{m}</option>)
                      )}
                    </select>

                    <button
                      onClick={() => handleSaveModel(provider)}
                      disabled={row.savingModel || !row.selectedModel || row.selectedModel === status.model}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
                        color: 'var(--text-primary)', borderRadius: 8, padding: '8px 12px',
                        fontSize: 12, fontWeight: 600,
                        cursor: (row.savingModel || !row.selectedModel || row.selectedModel === status.model) ? 'not-allowed' : 'pointer',
                        opacity: (row.savingModel || !row.selectedModel || row.selectedModel === status.model) ? 0.5 : 1,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {row.savingModel ? <Loader2 size={13} className="animate-spin" /> : 'Save Model'}
                    </button>
                  </div>

                  {/* Search box — only useful once there's a list worth narrowing, and
                      essential when the list is truncated (Featherless can have far
                      more real models than the capped response shows). */}
                  {modelOptions.length > 10 && (
                    <input
                      value={row.modelFilter}
                      onChange={e => patchRow(provider, { modelFilter: e.target.value })}
                      placeholder="Filter models…"
                      style={{
                        marginTop: 8, width: '100%', padding: '7px 10px', borderRadius: 8,
                        border: '1px solid var(--border-main)', background: 'var(--bg-input)',
                        color: 'var(--text-primary)', fontSize: 12, boxSizing: 'border-box',
                      }}
                    />
                  )}

                  {/* Honest truncation notice — never present a capped list as if it
                      were the complete set. */}
                  {row.truncated && (
                    <p style={{ margin: '6px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>
                      Showing {row.discoveredModels?.length ?? 0} of {row.totalAvailable} models. Use the filter above to find one outside this list.
                    </p>
                  )}
                  {!row.truncated && row.discoveredModels !== null && (
                    <p style={{ margin: '6px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>
                      {row.discoveredModels.length} model{row.discoveredModels.length === 1 ? '' : 's'} available.
                    </p>
                  )}

                  {row.discoverError && (
                    <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', borderRadius: 8, background: 'var(--color-error-bg)', border: '1px solid var(--color-error)30', color: 'var(--color-error)', fontSize: 11 }}>
                      <AlertCircle size={12} />
                      {row.discoverError}
                    </div>
                  )}
                  {row.saveModelError && (
                    <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--color-error)' }}>
                      <AlertCircle size={12} />
                      {row.saveModelError}
                    </div>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Security notice — same convention as SecretsPanel */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 12px', borderRadius: 8, background: 'var(--color-accent-faint)', border: '1px solid var(--accent-blue)20', marginTop: 12 }}>
        <ShieldCheck size={14} color="var(--accent-blue)" style={{ flexShrink: 0, marginTop: 1 }} />
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          Keys are encrypted at rest and <strong>never displayed again</strong> after saving. Only a masked hint is shown.
        </span>
      </div>
    </div>
  );
}

function maskHint(value) {
  if (!value || value.length <= 4) return '••••';
  return `••••${value.slice(-4)}`;
}
